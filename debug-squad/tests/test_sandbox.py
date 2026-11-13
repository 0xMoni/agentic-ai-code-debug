"""The sandbox's isolation claims, proved by actually trying to escape."""

from __future__ import annotations

import pytest

from config.settings import Settings
from models.schemas import TestOutcome
from sandbox.executor import SandboxExecutor, parse_pytest_output

PROBE = '''"""Each test passes only if the escape is refused."""

import os
import socket

import pytest


def test_outbound_connection_is_refused():
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 80), timeout=3)


def test_dns_lookup_is_refused():
    with pytest.raises(OSError):
        socket.getaddrinfo("example.com", 80)


def test_write_above_the_sandbox_is_refused():
    # Not "~": HOME is remapped inside the sandbox, so it is writable by design.
    target = os.path.join(os.path.dirname(os.getcwd()), "debug_squad_escape_probe.txt")
    with pytest.raises(OSError):
        with open(target, "w") as handle:
            handle.write("escaped")


def test_write_to_a_system_folder_is_refused():
    with pytest.raises(OSError):
        with open("/etc/debug_squad_escape_probe", "w") as handle:
            handle.write("escaped")


def test_writing_inside_the_sandbox_still_works():
    with open("inside.txt", "w") as handle:
        handle.write("fine")
    assert os.path.exists("inside.txt")
'''


@pytest.fixture
def executor() -> SandboxExecutor:
    sandbox = SandboxExecutor(Settings(sandbox_timeout_seconds=30, sandbox_cpu_seconds=15))
    if not sandbox.backend.is_adequate:
        pytest.skip(f"no isolated sandbox on this machine ({sandbox.backend.name})")
    return sandbox


def test_every_escape_attempt_is_refused(executor):
    result = executor.run_tests("# nothing to import\n", PROBE)
    assert result.executed, result.unavailable_reason
    outcomes = {o.name: o.outcome for o in parse_pytest_output(result.stdout)}

    escapes = [
        "test_outbound_connection_is_refused",
        "test_dns_lookup_is_refused",
        "test_write_above_the_sandbox_is_refused",
        "test_write_to_a_system_folder_is_refused",
    ]
    not_blocked = [name for name in escapes if outcomes.get(name) is not TestOutcome.PASSED]
    assert not not_blocked, f"escapes NOT blocked: {not_blocked}\n{result.stdout[-1500:]}"
    assert outcomes.get("test_writing_inside_the_sandbox_still_works") is TestOutcome.PASSED


def test_runaway_code_is_stopped(executor):
    result = executor.run_tests("x = 1\n", "def test_spin():\n    while True:\n        pass\n")
    assert result.executed
    assert result.timed_out, "an infinite loop must be killed by the sandbox limits"


def test_nothing_runs_without_an_isolated_backend():
    sandbox = SandboxExecutor(Settings(sandbox_backend="disabled"))
    result = sandbox.run_tests("x = 1\n", "def test_ok():\n    assert True\n")
    assert result.executed is False
    assert result.unavailable_reason
