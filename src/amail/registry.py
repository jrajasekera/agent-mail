"""Mailbox lifecycle: registration, revival, caller identity, presence."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from amail import identity, names


@dataclass(frozen=True)
class Agent:
    id: int
    name: str
    harness: str
    session_key: str
    pid: int
    pid_start: str
    route: dict
    cwd: str | None
    branch: str | None
    status: str
    task: str | None
    created_at: str
    last_seen: str


def handle(agent: Agent) -> str:
    return f"{agent.name}@{agent.id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_agent(row: sqlite3.Row) -> Agent:
    d = dict(row)
    d["route"] = json.loads(d["route"])
    return Agent(**d)


def get(conn: sqlite3.Connection, agent_id: int) -> Agent | None:
    row = conn.execute("SELECT * FROM agents WHERE id = ?",
                       (agent_id,)).fetchone()
    return _row_to_agent(row) if row else None


def detect_branch(cwd: str) -> str | None:
    out = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                         capture_output=True, text=True)
    branch = out.stdout.strip()
    return branch if out.returncode == 0 and branch else None


def register(conn: sqlite3.Connection, env: Mapping[str, str],
             home: Path | None = None) -> Agent:
    ident = identity.resolve(env)
    reap(conn, home)                       # frees names; outside the write txn
    cwd = env.get("PWD") or os.getcwd()    # slow work stays outside the lock
    branch = detect_branch(cwd)
    native = ident.session_key.split(":", 1)[1]
    route = ({"kind": "codex_queue", "thread": native}
             if ident.harness == "codex" else {"kind": "doorbell"})
    now = _now()
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM agents WHERE session_key = ?",
                               (ident.session_key,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE agents SET pid=?, pid_start=?, route=?, cwd=?,"
                    " branch=?, last_seen=?,"
                    " status = CASE WHEN status='offline' THEN 'working'"
                    "               ELSE status END"
                    " WHERE id=?",
                    (ident.pid, ident.pid_start, json.dumps(route), cwd,
                     branch, now, row["id"]))
                agent_id = row["id"]
            else:
                name = names.allocate(conn)
                cur = conn.execute(
                    "INSERT INTO agents (name, harness, session_key, pid,"
                    " pid_start, route, cwd, branch, status, created_at,"
                    " last_seen) VALUES (?,?,?,?,?,?,?,?, 'working', ?, ?)",
                    (name, ident.harness, ident.session_key, ident.pid,
                     ident.pid_start, json.dumps(route), cwd, branch,
                     now, now))
                agent_id = cur.lastrowid
            conn.execute("COMMIT")
            return get(conn, agent_id)
        except (sqlite3.IntegrityError, sqlite3.OperationalError):
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            time.sleep(0.02 * (attempt + 1))   # lost a race; re-observe
    raise RuntimeError("registration contended; retry")


def current_agent(conn: sqlite3.Connection,
                  env: Mapping[str, str]) -> Agent | None:
    native_row = None
    try:
        native_row = conn.execute(
            "SELECT * FROM agents WHERE session_key = ?",
            (identity.resolve(env).session_key,)).fetchone()
    except RuntimeError:
        pass
    if "AMAIL_AGENT_ID" in env:
        pinned = int(env["AMAIL_AGENT_ID"])
        row = conn.execute("SELECT * FROM agents WHERE id = ?",
                           (pinned,)).fetchone()
        if row is None:
            raise LookupError(f"AMAIL_AGENT_ID={pinned} does not exist")
        if native_row is not None and native_row["id"] != pinned:
            raise LookupError(
                f"identity conflict: AMAIL_AGENT_ID={pinned} but this"
                f" session's mailbox is agent {native_row['id']}")
        return _row_to_agent(row)
    return _row_to_agent(native_row) if native_row else None


def reap(conn: sqlite3.Connection, home: Path | None = None) -> int:
    return 0
