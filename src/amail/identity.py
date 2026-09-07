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

PS = "/bin/ps"          # absolute: a GUI-launched harness has a stripped PATH


class InspectionUnavailable(RuntimeError):
    """`ps` could not be executed at all — a sandbox denied it, or it is
    missing. Distinct from "ps ran and the pid is gone": we know nothing,
    and a caller must not mistake that for a dead process."""


def _ps(pid: int, field: str) -> str:
    try:
        return subprocess.run([PS, "-o", f"{field}=", "-p", str(pid)],
                              capture_output=True, text=True).stdout.strip()
    except OSError as e:
        raise InspectionUnavailable(
            f"cannot run {PS}: {e.strerror or e}") from e


@dataclass(frozen=True)
class SessionIdentity:
    harness: str
    session_key: str
    pid: int
    pid_start: str | None
    """None when process inspection is unavailable here. Enough to identify an
    existing mailbox (that is the session_key's job); not enough to open one."""
    native: bool = True
    """False when the harness was inferred from pid ancestry rather than
    named by the session itself. A guess is not grounds to reject a pin."""


def pid_start(pid: int) -> str | None:
    """The process start time, or None if no such process. Raises
    InspectionUnavailable when ps itself cannot run."""
    return _ps(pid, "lstart") or None


def pid_alive(pid: int, expected_start: str) -> bool:
    return pid_start(pid) == expected_start


def _ps_field(pid: int, field: str) -> str:
    return _ps(pid, field)


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
            pid: int, native: bool = True) -> SessionIdentity:
    start = env.get("AMAIL_PID_START")
    if not start:
        try:
            start = pid_start(pid)
        except InspectionUnavailable:
            start = None          # identity still resolves; registration will not
    return SessionIdentity(harness, f"{harness}:{native_key}", pid, start,
                           native)


def _fallback_pid() -> int:
    try:
        found = walk_to_harness(os.getpid())
    except InspectionUnavailable:
        return os.getppid()
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
    hits = {h: probe(env) for h, probe in NATIVE_PROBES.items()}
    hits = {h: v for h, v in hits.items() if v is not None}
    if len(hits) == 1:
        return next(iter(hits.values()))
    if not hits:
        return None
    # Two harnesses claim this environment. A Codex session launched from a
    # Claude session's shell inherits CLAUDE_* and exports CODEX_THREAD_ID of
    # its own, so probe order alone would hand it the parent's mailbox. Env is
    # inheritable; the process tree is not, so let the tree decide.
    try:
        found = walk_to_harness(os.getpid())
    except InspectionUnavailable:
        found = None
    if found is not None and found[0] in hits:
        return hits[found[0]]
    raise IdentityConflict(                    # never log env contents here
        f"this environment names {', '.join(sorted(hits))} at once and the"
        f" process tree does not say which is real; scrub the inherited"
        f" variables or set AMAIL_SESSION_KEY")


def resolve(env: Mapping[str, str],
            expect_harness: str | None = None) -> SessionIdentity:
    hit = _first_hit(env, expect_harness)
    if hit is not None:
        ident = _finish(env, *hit)
    else:
        try:
            found = walk_to_harness(os.getpid())
        except InspectionUnavailable as e:
            raise InspectionUnavailable(          # nothing else identifies us
                f"no harness environment variables and {e}") from e
        if found:
            harness, pid = found
        else:
            harness, pid = "shell", os.getppid()
        ident = _finish(env, harness, f"pid:{pid}", pid, native=False)
    if expect_harness and ident.harness != expect_harness:
        raise IdentityConflict(                # never log env contents here
            f"invoked as {expect_harness} but this environment resolves to"
            f" {ident.harness}; refusing to pick a winner")
    return ident
