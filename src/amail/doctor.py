"""Diagnose identity, mailbox access, routes, watcher state. Labels what
it cannot know rather than guessing."""
from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from amail import identity, registry, waiter


def report(conn: sqlite3.Connection, env: Mapping[str, str],
           home: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        ident = identity.resolve(env)
        out.append(("identity", f"{ident.session_key} (pid {ident.pid})"))
    except Exception as e:
        out.append(("identity", f"unresolvable: {e}"))
    agent = None
    try:
        agent = registry.current_agent(conn, env)
        out.append(("agent", registry.handle(agent) if agent
                    else "not registered"))
    except LookupError as e:
        out.append(("agent", f"error: {e}"))
    writable = os.access(home, os.W_OK) and os.access(home / "doorbells",
                                                      os.W_OK)
    out.append(("mailbox", f"{home / 'mail.db'}"
                f" ({'writable' if writable else 'NOT writable'})"))
    out.append(("codex binary", shutil.which("codex") or "not on PATH"))
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
