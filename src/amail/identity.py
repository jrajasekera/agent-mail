"""Who am I? Env vars first (verified by spike), pid ancestry as fallback.

A caller that already knows its harness — every hook does, it is argv[2] —
passes `expect_harness`. That probe is tried first, and a resolution that
still disagrees raises rather than picking a winner: a Codex process which
inherited a Claude session's env must never adopt the parent's mailbox.
"""
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


class IdentityConflict(RuntimeError):
    """The environment resolves to a harness other than the one we were
    invoked as. Never resolved by precedence — the caller must scrub the
    inherited variables or pass an explicit key."""


Probe = tuple[str, str, int]        # harness, native key, pid


def _probe_explicit(env: Mapping[str, str]) -> Probe | None:
    if "AMAIL_SESSION_KEY" in env:
        return (env.get("AMAIL_HARNESS", "shell"),
                env["AMAIL_SESSION_KEY"], int(env["AMAIL_PID"]))
    return None


def _probe_claude(env: Mapping[str, str]) -> Probe | None:
    if "CLAUDE_CODE_SESSION_ID" in env and "CLAUDE_PID" in env:
        return ("claude", env["CLAUDE_CODE_SESSION_ID"],
                int(env["CLAUDE_PID"]))
    return None


def _probe_codex(env: Mapping[str, str]) -> Probe | None:
    if "CODEX_THREAD_ID" in env:
        pid = int(env.get("AMAIL_PID", "0")) or _fallback_pid()
        return ("codex", env["CODEX_THREAD_ID"], pid)
    return None


NATIVE_PROBES = {"claude": _probe_claude, "codex": _probe_codex}
_PROBE_ORDER = (_probe_claude, _probe_codex)


def _first_hit(env: Mapping[str, str],
               expect_harness: str | None) -> Probe | None:
    explicit = _probe_explicit(env)
    if explicit is not None:
        return explicit
    preferred = NATIVE_PROBES.get(expect_harness or "")
    if preferred is not None:                 # our own harness answers first
        hit = preferred(env)
        if hit is not None:
            return hit
    for probe in _PROBE_ORDER:
        hit = probe(env)
        if hit is not None:
            return hit
    return None


def resolve(env: Mapping[str, str],
            expect_harness: str | None = None) -> SessionIdentity:
    hit = _first_hit(env, expect_harness)
    if hit is not None:
        ident = _finish(env, *hit)
    else:
        found = walk_to_harness(os.getpid())
        if found:
            harness, pid = found
        else:
            harness, pid = "shell", os.getppid()
        ident = _finish(env, harness, f"pid:{pid}", pid)
    if expect_harness and ident.harness != expect_harness:
        raise IdentityConflict(                # never log env contents here
            f"invoked as {expect_harness} but this environment resolves to"
            f" {ident.harness}; refusing to pick a winner")
    return ident
