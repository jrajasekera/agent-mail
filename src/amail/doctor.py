"""Diagnose identity, mailbox access, routes, watcher state. Labels what
it cannot know rather than guessing."""
from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from amail import identity, registry, waiter

CODEX_SANDBOX_FIX = (
    "the mailbox is outside Codex's writable roots, so every amail command"
    " needs a per-command escalation; add it once in ~/.codex/config.toml:"
    " [sandbox_workspace_write] writable_roots = [\"~/.amail\"]")

CODEX_FIRST_TURN = (
    "no thread id yet — a Codex session has no thread id until its first"
    " turn, so this session has no mailbox and cannot be sent to; it"
    " registers automatically on the first turn")


def _codex_before_first_turn(env: Mapping[str, str]) -> bool:
    """Under Codex, but the thread the route needs does not exist yet."""
    if "CODEX_THREAD_ID" in env or "AMAIL_SESSION_KEY" in env:
        return False
    try:
        found = identity.walk_to_harness(os.getpid())
    except identity.InspectionUnavailable:
        return False                  # cannot verify; do not invent a warning
    return found is not None and found[0] == "codex"


def report(conn: sqlite3.Connection | None, env: Mapping[str, str],
           home: Path, mailbox_error: str | None = None,
           ) -> list[tuple[str, str]]:
    """`conn` is None when the mailbox could not be opened — the one state
    doctor most needs to explain, so it reports rather than refusing."""
    out: list[tuple[str, str]] = []
    try:
        ident = identity.resolve(env)
        out.append(("identity", f"{ident.session_key} (pid {ident.pid})"))
    except Exception as e:
        out.append(("identity", f"unresolvable: {e}"))
    agent = None
    if conn is None:
        out.append(("agent", "unknown (mailbox unreadable)"))
    else:
        try:
            agent = registry.current_agent(conn, env)
            out.append(("agent", registry.handle(agent) if agent
                        else "not registered"))
        except LookupError as e:
            out.append(("agent", f"error: {e}"))
    if mailbox_error:
        out.append(("mailbox", mailbox_error))
        if env.get("CODEX_SANDBOX"):
            out.append(("codex sandbox", CODEX_SANDBOX_FIX))
    else:
        writable = os.access(home, os.W_OK) and os.access(home / "doorbells",
                                                          os.W_OK)
        out.append(("mailbox", f"{home / 'mail.db'}"
                    f" ({'writable' if writable else 'NOT writable'})"))
    out.append(("codex binary", shutil.which("codex") or "not on PATH"))
    if _codex_before_first_turn(env):
        out.append(("codex mailbox", CODEX_FIRST_TURN))
    if agent:
        lock = home / "waiters" / f"{agent.id}.pid"
        armed = "not armed"
        if lock.exists():
            try:
                pid = int(lock.read_text().strip())
                armed = (f"armed (pid {pid})" if waiter._pid_running(pid)
                         else "stale lock")
            except ValueError:
                armed = "stale lock"
        out.append(("watcher", armed))
    else:
        out.append(("watcher", "n/a (not registered)"))
    out.append(("hook trust", "cannot verify here"))
    out.append(("codex idle wake", "cannot verify here"))
    out.append(("sandbox write access from harness", "cannot verify here"))
    return out
