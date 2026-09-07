"""Who am I? Env vars first (verified by spike), pid ancestry as fallback."""
from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

HARNESS_BINARIES = {"claude": "claude", "codex": "codex", "pi": "pi"}


@dataclass(frozen=True)
class SessionIdentity:
    harness: str
    session_key: str
    pid: int
    pid_start: str


def pid_start(pid: int) -> str | None:
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return out or None


def pid_alive(pid: int, expected_start: str) -> bool:
    return pid_start(pid) == expected_start


def _ps_field(pid: int, field: str) -> str:
    return subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()


def walk_to_harness(pid: int) -> tuple[str, int] | None:
    for _ in range(20):
        comm = os.path.basename(_ps_field(pid, "comm"))
        for harness, binary in HARNESS_BINARIES.items():
            if comm == binary:
                return harness, pid
        ppid_s = _ps_field(pid, "ppid")
        if not ppid_s or ppid_s == "0":
            return None
        pid = int(ppid_s)
    return None


def _finish(env: Mapping[str, str], harness: str, native_key: str,
            pid: int) -> SessionIdentity:
    start = env.get("AMAIL_PID_START") or pid_start(pid)
    if start is None:
        raise RuntimeError(f"cannot determine start time of pid {pid}")
    return SessionIdentity(harness, f"{harness}:{native_key}", pid, start)


def _fallback_pid() -> int:
    found = walk_to_harness(os.getpid())
    return found[1] if found else os.getppid()


def resolve(env: Mapping[str, str]) -> SessionIdentity:
    if "AMAIL_SESSION_KEY" in env:
        return _finish(env, env.get("AMAIL_HARNESS", "shell"),
                       env["AMAIL_SESSION_KEY"], int(env["AMAIL_PID"]))
    if "CLAUDE_CODE_SESSION_ID" in env and "CLAUDE_PID" in env:
        return _finish(env, "claude", env["CLAUDE_CODE_SESSION_ID"],
                       int(env["CLAUDE_PID"]))
    if "CODEX_THREAD_ID" in env:
        pid = int(env.get("AMAIL_PID", "0")) or _fallback_pid()
        return _finish(env, "codex", env["CODEX_THREAD_ID"], pid)
    found = walk_to_harness(os.getpid())
    if found:
        harness, pid = found
        return _finish(env, harness, f"pid:{pid}", pid)
    pid = os.getppid()
    return _finish(env, "shell", f"pid:{pid}", pid)
