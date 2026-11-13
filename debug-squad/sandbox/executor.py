"""Sandboxed test execution."""

from __future__ import annotations

import contextlib
import platform
import re
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from config.settings import Settings, get_settings
from models.schemas import SandboxResult, TestCase, TestOutcome
from sandbox.sandbox_config import (
    BackendInfo,
    build_seatbelt_profile,
    detect_backend,
    docker_command,
)

CODE_FILENAME = "submitted_code.py"
TEST_FILENAME = "test_submitted_code.py"
PROFILE_FILENAME = "sandbox.sb"

_IS_DARWIN = platform.system() == "Darwin"


def _signal_name(number: int) -> str:
    """Human-readable signal name, e.g. 'SIGXCPU (CPU time limit exceeded)'."""
    try:
        signal_enum = signal.Signals(number)
    except ValueError:
        return f"signal {number}"
    descriptions = {
        "SIGXCPU": "CPU time limit exceeded",
        "SIGKILL": "killed - most likely the memory limit",
        "SIGSEGV": "segmentation fault",
        "SIGXFSZ": "file size limit exceeded",
        "SIGTERM": "terminated",
    }
    note = descriptions.get(signal_enum.name)
    return f"{signal_enum.name} ({note})" if note else signal_enum.name


@dataclass
class ParsedTestOutcome:
    name: str
    outcome: TestOutcome
    message: str = ""


def _make_limiter(settings: Settings):
    """Return a preexec_fn applying rlimits to the child process."""

    cpu_seconds = max(1, settings.sandbox_cpu_seconds)
    memory_bytes = max(64, settings.sandbox_memory_mb) * 1024 * 1024

    def limit() -> None:  # pragma: no cover
        def _try(which, soft, hard=None):
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(which, (soft, hard if hard is not None else soft))

        _try(resource.RLIMIT_CPU, cpu_seconds, cpu_seconds + 2)
        _try(resource.RLIMIT_FSIZE, 8 * 1024 * 1024)
        _try(resource.RLIMIT_NOFILE, 256)
        if hasattr(resource, "RLIMIT_NPROC"):
            _try(resource.RLIMIT_NPROC, 64)
        # RLIMIT_AS breaks CPython start-up on macOS, so it is only set elsewhere.
        if hasattr(resource, "RLIMIT_AS") and not _IS_DARWIN:
            _try(resource.RLIMIT_AS, memory_bytes)

    return limit


_RESULT_LINE = re.compile(
    r"^(?P<file>\S+\.py)::(?P<name>\S+)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
)

_STATUS_MAP = {
    "PASSED": TestOutcome.PASSED,
    "FAILED": TestOutcome.FAILED,
    "ERROR": TestOutcome.ERROR,
    "SKIPPED": TestOutcome.SKIPPED,
    "XFAIL": TestOutcome.SKIPPED,
    "XPASS": TestOutcome.PASSED,
}


def parse_pytest_output(stdout: str) -> list[ParsedTestOutcome]:
    """Extract per-test outcomes from verbose pytest output."""
    outcomes: list[ParsedTestOutcome] = []
    for line in stdout.splitlines():
        match = _RESULT_LINE.match(line.strip())
        if match:
            outcomes.append(
                ParsedTestOutcome(
                    name=match.group("name"),
                    outcome=_STATUS_MAP.get(match.group("status"), TestOutcome.ERROR),
                )
            )
    return outcomes


def extract_failure_messages(stdout: str) -> dict[str, str]:
    """Pull each failing test's assertion detail out of pytest's FAILURES block."""
    messages: dict[str, str] = {}
    blocks = re.split(r"^_{5,}\s+(\S+?)\s+_{5,}$", stdout, flags=re.MULTILINE)
    for index in range(1, len(blocks) - 1, 2):
        name = blocks[index].strip()
        body = blocks[index + 1]
        body = re.split(r"^=+ (short test summary|warnings summary)", body, flags=re.MULTILINE)[0]
        messages[name] = body.strip()[:1500]
    return messages


class SandboxExecutor:
    """Runs untrusted code in the strongest available isolation."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.backend: BackendInfo = detect_backend(self.settings)


    @property
    def available(self) -> bool:
        return self.backend.available


    def _build_command(self, work_dir: Path) -> list[str]:
        """Assemble the argv for the chosen backend."""
        pytest_args = [
            "-m", "pytest", TEST_FILENAME,
            "-p", "no:cacheprovider",
            "-v", "--tb=short", "--no-header",
            "-W", "ignore",
        ]

        if self.backend.name == "docker":
            return docker_command(work_dir, self.settings, TEST_FILENAME)

        python = self.settings.python_executable
        base = [python, "-I", *pytest_args]

        if self.backend.name == "seatbelt":
            profile_path = work_dir / PROFILE_FILENAME
            profile_path.write_text(
                build_seatbelt_profile(work_dir),
                encoding="utf-8",
            )
            return ["/usr/bin/sandbox-exec", "-f", str(profile_path), *base]

        return base

    def run_tests(self, code: str, test_source: str) -> SandboxResult:
        """Execute `test_source` against `code`."""
        if not self.backend.available:
            return SandboxResult(
                executed=False,
                backend=self.backend.name,
                unavailable_reason=self.backend.description,
            )

        work_dir = Path(tempfile.mkdtemp(prefix="debugsquad-")).resolve()
        started = time.perf_counter()
        try:
            (work_dir / CODE_FILENAME).write_text(code, encoding="utf-8")
            (work_dir / TEST_FILENAME).write_text(test_source, encoding="utf-8")

            command = self._build_command(work_dir)
            environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(work_dir),
                "TMPDIR": str(work_dir),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(work_dir),
                "PYTHONHASHSEED": "0",
                "NO_COLOR": "1",
            }

            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=self.settings.sandbox_timeout_seconds,
                    env=environment,
                    preexec_fn=_make_limiter(self.settings)
                    if self.backend.name != "docker"
                    else None,
                )
            except subprocess.TimeoutExpired as exc:
                return SandboxResult(
                    executed=True,
                    backend=self.backend.name,
                    timed_out=True,
                    exit_code=None,
                    stdout=(exc.stdout or b"").decode(errors="replace")
                    if isinstance(exc.stdout, bytes)
                    else (exc.stdout or ""),
                    stderr=(
                        f"Execution exceeded the "
                        f"{self.settings.sandbox_timeout_seconds}s sandbox timeout "
                        "and was killed. This usually means an infinite loop in "
                        "the code under test."
                    ),
                    duration_seconds=time.perf_counter() - started,
                )
            except OSError as exc:
                return SandboxResult(
                    executed=False,
                    backend=self.backend.name,
                    unavailable_reason=f"Could not start the sandbox: {exc}",
                    duration_seconds=time.perf_counter() - started,
                )

            # A negative exit code means a signal; SIGXCPU is our own CPU limit firing.
            killed_by_signal = (
                completed.returncode is not None and completed.returncode < 0
            )
            stderr = completed.stderr[-8000:]
            if killed_by_signal:
                signal_name = _signal_name(-completed.returncode)
                stderr = (
                    f"The sandboxed process was killed by {signal_name} after "
                    f"{time.perf_counter() - started:.1f}s. This is the sandbox's "
                    f"resource limit firing (CPU limit "
                    f"{self.settings.sandbox_cpu_seconds}s, memory "
                    f"{self.settings.sandbox_memory_mb}MB) and usually means an "
                    f"infinite loop or runaway allocation in the code under test."
                    + (f"\n\n{stderr}" if stderr.strip() else "")
                )

            return SandboxResult(
                executed=True,
                backend=self.backend.name,
                exit_code=completed.returncode,
                stdout=completed.stdout[-20000:],
                stderr=stderr,
                timed_out=killed_by_signal,
                duration_seconds=time.perf_counter() - started,
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


    def run_and_annotate(
        self, code: str, tests: list[TestCase], test_source: str
    ) -> tuple[SandboxResult, list[TestCase], dict[str, str]]:
        """Run the tests and write the real outcome back onto each TestCase."""
        result = self.run_tests(code, test_source)
        if not result.executed:
            for test in tests:
                test.outcome = TestOutcome.NOT_EXECUTED
                test.failure_message = result.unavailable_reason
            return result, tests, {}

        outcomes = {o.name: o.outcome for o in parse_pytest_output(result.stdout)}
        failures = extract_failure_messages(result.stdout)

        collection_failed = (
            not outcomes
            and result.exit_code not in (0, None)
        )

        for test in tests:
            if test.name in outcomes:
                test.outcome = outcomes[test.name]
                if test.outcome in (TestOutcome.FAILED, TestOutcome.ERROR):
                    test.failure_message = failures.get(test.name, "")
            elif collection_failed:
                test.outcome = TestOutcome.ERROR
                test.failure_message = (
                    "The generated test file could not be collected by pytest. "
                    "See the raw output for the collection error."
                )
            else:
                test.outcome = TestOutcome.NOT_EXECUTED
                test.failure_message = (
                    "pytest did not report a result for this test."
                )

        return result, tests, failures


def build_test_module(tests: list[TestCase], extra_imports: str = "") -> str:
    """Assemble generated TestCases into one runnable pytest module."""
    header = [
        '"""Generated by the Debug Squad Test-Writer agent."""',
        "",
        "import pytest  # noqa: F401 - used by pytest.raises in some tests",
        "",
    ]
    if extra_imports:
        header.append(extra_imports)
        header.append("")
    body = []
    for test in tests:
        body.append(f"# {test.category}: {test.description}")
        body.append(f"# oracle: {test.oracle}")
        body.append(test.code.strip())
        body.append("")
        body.append("")
    return "\n".join(header + body).rstrip() + "\n"
