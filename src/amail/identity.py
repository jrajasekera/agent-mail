"""Who am I? Env vars first (verified by spike), pid ancestry as fallback.

A caller that already knows its harness — every hook does, it is argv[2] —
passes `expect_harness`. That probe is tried first, and a resolution that
still disagrees raises rather than picking a winner: a Codex process which
inherited a Claude session's env must never adopt the parent's mailbox.
"""
from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

HARNESS_BINARIES = {"claude": "claude", "codex": "codex", "pi": "pi"}

PS = "/bin/ps"          # absolute: a GUI-launched harness has a stripped PATH


class InspectionUnavailable(RuntimeError):
    """This process could not be inspected at all — a sandbox denied it, or
    the mechanism is missing. Distinct from "we looked and the pid is gone":
    we know nothing, and a caller must not mistake that for a dead process."""


def _ps(pid: int, field: str) -> str:
    try:
        return subprocess.run([PS, "-o", f"{field}=", "-p", str(pid)],
                              capture_output=True, text=True).stdout.strip()
    except OSError as e:
        raise InspectionUnavailable(
            f"cannot run {PS}: {e.strerror or e}") from e


# --- process inspection without exec ---------------------------------------
#
# Codex runs its commands under a seatbelt profile that denies exec of
# /bin/ps, which is exactly where we most need to know who our harness is.
# The same facts come out of sysctl(KERN_PROC_PID), which that profile does
# allow, and which costs no subprocess.

_CTL_KERN, _KERN_PROC, _KERN_PROC_PID = 1, 14, 1
_KINFO_SIZE = 648                  # sizeof(struct kinfo_proc), arm64/x86_64
_OFF_START, _OFF_COMM, _OFF_PPID = 0, 243, 560
_COMM_LEN = 17                     # MAXCOMLEN + 1

_libc = ctypes.CDLL(None, use_errno=True)
_libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                         ctypes.c_void_p, ctypes.c_size_t]


@dataclass(frozen=True)
class Proc:
    pid: int
    comm: str
    ppid: int
    start: str
    """Formatted exactly as `ps -o lstart=` renders it, so rows written by
    either mechanism compare equal and need no migration."""


def kinfo(pid: int) -> Proc | None:
    """One process, or None if there is no such pid. Raises
    InspectionUnavailable if the kernel will not answer at all."""
    mib = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid)
    buf = ctypes.create_string_buffer(_KINFO_SIZE)
    size = ctypes.c_size_t(_KINFO_SIZE)
    if _libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0:
        err = ctypes.get_errno()
        raise InspectionUnavailable(f"sysctl(kern.proc.pid): {os.strerror(err)}")
    if size.value == 0:
        return None                                   # no such process
    if size.value != _KINFO_SIZE:                     # never trust the offsets
        raise InspectionUnavailable(
            f"unexpected kinfo_proc size {size.value}")
    sec, = struct.unpack_from("<q", buf.raw, _OFF_START)
    comm = buf.raw[_OFF_COMM:_OFF_COMM + _COMM_LEN].split(b"\0", 1)[0]
    ppid, = struct.unpack_from("<i", buf.raw, _OFF_PPID)
    return Proc(pid, comm.decode(errors="replace"), ppid,
                time.strftime("%a %b %e %H:%M:%S %Y", time.localtime(sec)))


def ancestry(pid: int) -> list[Proc]:
    """`pid` and its ancestors, nearest first, stopping at init."""
    chain: list[Proc] = []
    for _ in range(40):
        info = kinfo(pid)
        if info is None:
            break
        chain.append(info)
        if info.ppid <= 1:
            break
        pid = info.ppid
    return chain


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
    InspectionUnavailable when the process cannot be inspected at all."""
    try:
        info = kinfo(pid)
    except InspectionUnavailable:
        return _ps(pid, "lstart") or None          # non-Darwin, or odd kernel
    return info.start if info else None


def pid_alive(pid: int, expected_start: str) -> bool:
    return pid_start(pid) == expected_start


def _ps_field(pid: int, field: str) -> str:
    return _ps(pid, field)


def _walk_with_ps(pid: int) -> tuple[str, int] | None:
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


def walk_to_harness(pid: int) -> tuple[str, int] | None:
    """The nearest ancestor that *is* a harness process, by executable name.

    Read through sysctl, not ps. This is the only signal a Codex session has
    for its own harness pid — `CODEX_THREAD_ID` names the thread, nothing
    names the process — and it is read from inside Codex's sandbox, which
    denies exec of /bin/ps. Walking with ps there does not fail loudly: the
    caller falls back to `os.getppid()`, the sandbox shell that exits a
    moment later, and the next reap marks a live session offline.

    Names are compared exactly against the kernel's `p_comm`, so the helper
    processes a harness spawns beside itself (`codex-code-mode-host`) do not
    shadow the session process.
    """
    try:
        chain = ancestry(pid)
    except InspectionUnavailable:
        return _walk_with_ps(pid)         # non-Darwin, or an odd kernel
    for proc in chain:
        for harness, binary in HARNESS_BINARIES.items():
            if proc.comm == binary:
                return harness, proc.pid
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


def _nearest_harness(env: Mapping[str, str], claimants: set[str],
                     chain: Sequence[Proc]) -> str | None:
    """Which claimant owns the *nearest* ancestor. Nearest is what matters:
    codex-in-claude and claude-in-codex are both real nestings, and only
    distance tells them apart.

    Claude is matched by pid, not by name: its launcher execs a versioned
    binary, so its comm is a version string like "2.1.263" and never
    "claude". CLAUDE_PID is exported in both nesting directions.
    """
    claude_pid = None
    if "claude" in claimants and env.get("CLAUDE_PID", "").isdigit():
        claude_pid = int(env["CLAUDE_PID"])
    for proc in chain:
        if claude_pid is not None and proc.pid == claude_pid:
            return "claude"
        if "codex" in claimants and proc.comm.startswith("codex"):
            return "codex"
    return None


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
        nearest = _nearest_harness(env, set(hits), ancestry(os.getpid()))
    except InspectionUnavailable:
        nearest = None
    if nearest is not None:
        return hits[nearest]
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
