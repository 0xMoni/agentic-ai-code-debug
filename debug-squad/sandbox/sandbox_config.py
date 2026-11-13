"""Sandbox backend detection and policy generation."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from config.settings import Settings, get_settings

SEATBELT_BINARY = "/usr/bin/sandbox-exec"


@dataclass
class BackendInfo:
    """What isolation is actually available right now."""

    name: str
    available: bool
    isolation: str
    """none | limited | strong"""
    description: str
    network_blocked: bool = False
    filesystem_confined: bool = False
    detail: str = ""

    @property
    def is_adequate(self) -> bool:
        """True when this backend is fit to run untrusted code."""
        return self.available and self.network_blocked and self.filesystem_confined

    @property
    def badge(self) -> str:
        if not self.available:
            return "Unavailable"
        return {"strong": "Isolated", "limited": "Weak isolation", "none": "Unsafe"}[
            self.isolation
        ]


def _docker_available() -> tuple[bool, str]:
    binary = shutil.which("docker")
    if not binary:
        return False, "docker is not installed or not on PATH"
    try:
        result = subprocess.run(
            [binary, "info", "--format", "{{.ServerVersion}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return False, "docker is installed but `docker info` timed out"
    except OSError as exc:
        return False, f"docker could not be executed: {exc}"
    if result.returncode != 0:
        return False, "docker is installed but the daemon is not running"
    return True, f"docker daemon {result.stdout.strip()}"


def _seatbelt_available() -> tuple[bool, str]:
    if platform.system() != "Darwin":
        return False, "sandbox-exec is macOS-only"
    if not Path(SEATBELT_BINARY).exists():
        return False, f"{SEATBELT_BINARY} not found"
    return True, "macOS sandbox-exec (seatbelt) available"


def describe_backends() -> list[BackendInfo]:
    """Describe every backend, available or not - used by the Settings page."""
    docker_ok, docker_detail = _docker_available()
    seatbelt_ok, seatbelt_detail = _seatbelt_available()
    return [
        BackendInfo(
            name="docker",
            available=docker_ok,
            isolation="strong",
            description=(
                "Container with --network none, read-only root filesystem, "
                "memory and CPU caps, non-root user, no added capabilities."
            ),
            network_blocked=True,
            filesystem_confined=True,
            detail=docker_detail,
        ),
        BackendInfo(
            name="seatbelt",
            available=seatbelt_ok,
            isolation="strong",
            description=(
                "macOS kernel sandbox. Network denied and writes confined to a "
                "single temporary directory, enforced by the kernel."
            ),
            network_blocked=True,
            filesystem_confined=True,
            detail=seatbelt_detail,
        ),
        BackendInfo(
            name="subprocess",
            available=True,
            isolation="limited",
            description=(
                "Resource limits and a timeout only. No network or filesystem "
                "isolation - not adequate for untrusted code. Must be chosen "
                "explicitly; `auto` will not select it."
            ),
            network_blocked=False,
            filesystem_confined=False,
            detail="always available, but intentionally not auto-selected",
        ),
    ]


def detect_backend(settings: Settings | None = None) -> BackendInfo:
    """Resolve the configured backend to one that actually works."""
    settings = settings or get_settings()
    requested = (settings.sandbox_backend or "auto").lower()
    backends = {b.name: b for b in describe_backends()}

    if requested == "disabled":
        return BackendInfo(
            name="disabled",
            available=False,
            isolation="none",
            description="Code execution turned off by configuration.",
            detail="SANDBOX_BACKEND=disabled",
        )

    if requested in backends:
        chosen = backends[requested]
        if chosen.available:
            return chosen
        return BackendInfo(
            name="disabled",
            available=False,
            isolation="none",
            description=f"Requested backend '{requested}' is not available.",
            detail=chosen.detail,
        )

    for name in ("docker", "seatbelt"):
        if backends[name].available:
            return backends[name]

    return BackendInfo(
        name="disabled",
        available=False,
        isolation="none",
        description=(
            "No isolated backend found. Install Docker, or set "
            "SANDBOX_BACKEND=subprocess to run without isolation "
            "(not recommended for untrusted code)."
        ),
        detail=(
            f"docker: {backends['docker'].detail}; "
            f"seatbelt: {backends['seatbelt'].detail}"
        ),
    )


def build_seatbelt_profile(work_dir: Path) -> str:
    """Generate a seatbelt profile confining the run to `work_dir`."""
    # Seatbelt matches resolved paths: /tmp is really /private/tmp on macOS.
    work = str(work_dir.resolve())

    return f"""(version 1)
;; Deny everything by default; no network at all.
(deny default)
(deny network*)

(allow process-exec)
(allow process-fork)
(allow signal (target self))

;; Python may read what it needs to start, but write only to its own folder.
(allow file-read*)
(allow file* (subpath "{work}"))

(allow file-read-data file-write-data
    (literal "/dev/null")
    (literal "/dev/zero")
    (literal "/dev/dtracehelper"))
(allow file-read-data
    (literal "/dev/random")
    (literal "/dev/urandom"))
(allow file-ioctl (literal "/dev/dtracehelper"))

(allow sysctl-read)
(allow mach-lookup)
(allow mach-task-name)
(allow process-info-pidinfo)
(allow process-info-setcontrol)
"""


DOCKER_IMAGE = "python:3.13-slim"


def docker_command(
    work_dir: Path, settings: Settings, test_filename: str
) -> list[str]:
    """Build the `docker run` argv for one sandboxed pytest run."""
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    return [
        "docker",
        "run",
        "--rm",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=16m,noexec",
        "--memory", f"{settings.sandbox_memory_mb}m",
        "--memory-swap", f"{settings.sandbox_memory_mb}m",
        "--cpus", "0.5",
        "--pids-limit", "64",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", f"{uid}:{gid}",
        "--workdir", "/work",
        "--volume", f"{work_dir.resolve()}:/work:rw",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "HOME=/tmp",
        DOCKER_IMAGE,
        "python", "-I", "-m", "pytest", test_filename,
        "-p", "no:cacheprovider",
        "-v", "--tb=short", "--no-header",
        "-W", "ignore",
    ]
