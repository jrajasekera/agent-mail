"""Block until unannounced mail. A real blocking process, never a bash
sleep; SQLite polling is the correctness path, kqueue only latency."""
from __future__ import annotations

import os
import select
import sqlite3
import time
from pathlib import Path

from amail import mail, routing
from amail.registry import Agent


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True          # EPERM: it exists, we just may not signal it
    except (ProcessLookupError, ValueError):
        return False


def _acquire_lock(lock: Path) -> None:
    if lock.exists():
        try:
            other = int(lock.read_text().strip())
        except ValueError:
            other = 0
        if other and _pid_running(other):
            raise RuntimeError(
                f"amail wait already armed for this agent (pid {other})")
    lock.write_text(str(os.getpid()))


def _block_on_dir(dirfd: int, interval: float) -> None:
    try:
        kq = select.kqueue()
    except (AttributeError, OSError):
        time.sleep(interval)
        return
    try:
        ev = select.kevent(
            dirfd, filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
            fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND)
        kq.control([ev], 1, interval)
    finally:
        kq.close()


def wait(conn: sqlite3.Connection, agent: Agent, home: Path,
         timeout: float | None = None,
         poll_interval: float = 1.0) -> list[mail.Header]:
    lock = home / "waiters" / f"{agent.id}.pid"
    _acquire_lock(lock)
    bell = routing.doorbell_path(home, agent.id)
    deadline = None if timeout is None else time.monotonic() + timeout
    dirfd = os.open(bell.parent, os.O_RDONLY)
    try:
        while True:
            headers = mail.unannounced(conn, agent)
            if headers:
                mail.mark_announced(conn, agent, [h.id for h in headers])
                bell.unlink(missing_ok=True)
                return headers
            if deadline is not None and time.monotonic() >= deadline:
                return []
            bell.unlink(missing_ok=True)   # consume stale ring, then block
            _block_on_dir(dirfd, poll_interval)
    finally:
        os.close(dirfd)
        lock.unlink(missing_ok=True)
