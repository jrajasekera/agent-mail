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
    try:
        out = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                             capture_output=True, text=True)
    except OSError:
        return None            # no git, or a sandbox denying its exec
    branch = out.stdout.strip()
    return branch if out.returncode == 0 and branch else None


def register(conn: sqlite3.Connection, env: Mapping[str, str],
             home: Path | None = None,
             expect_harness: str | None = None) -> Agent:
    ident = identity.resolve(env, expect_harness)
    if ident.pid_start is None:
        raise RuntimeError(
            "cannot determine this session's process start time, so its"
            " liveness could not be tracked; registration happens in the"
            " SessionStart hook, which runs outside the sandbox")
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


def current_agent(conn: sqlite3.Connection, env: Mapping[str, str],
                  expect_harness: str | None = None) -> Agent | None:
    ident = None
    native_row = None
    try:
        ident = identity.resolve(env, expect_harness)
        native_row = conn.execute(
            "SELECT * FROM agents WHERE session_key = ?",
            (ident.session_key,)).fetchone()
    except identity.IdentityConflict as e:
        # The environment is genuinely ambiguous. A pin cannot rescue that --
        # it is inherited by every descendant just like the variables that
        # made it ambiguous -- so report the real reason.
        raise LookupError(str(e)) from e
    except RuntimeError:
        pass                              # unresolvable, but not contradictory
    if "AMAIL_AGENT_ID" in env:
        pinned = int(env["AMAIL_AGENT_ID"])
        row = conn.execute("SELECT * FROM agents WHERE id = ?",
                           (pinned,)).fetchone()
        if row is None:
            raise LookupError(f"AMAIL_AGENT_ID={pinned} does not exist")
        # AMAIL_AGENT_ID is inherited by everything a session launches, so a
        # pin naming another session is the normal case in a nested session,
        # not an anomaly. It is a cross-check on the harness's own statement
        # of who it is, never a substitute for it: when the two disagree,
        # native identity wins and the stale pin is simply dropped.
        if ident is None or not ident.native:
            return _row_to_agent(row)     # nothing to check the pin against
        if row["session_key"] == ident.session_key:
            return _row_to_agent(row)
        if native_row is None:
            # The pin is the only claim to a mailbox and it is not ours.
            raise LookupError(
                f"identity conflict: AMAIL_AGENT_ID={pinned} is a"
                f" {row['harness']} mailbox but this session resolves to"
                f" {ident.harness}; scrub the inherited variable or register")
    return _row_to_agent(native_row) if native_row else None


def update_status(conn: sqlite3.Connection, agent: Agent,
                  status: str | None = None, task: str | None = None) -> Agent:
    cwd = os.getcwd()
    branch = detect_branch(cwd)
    conn.execute(
        "UPDATE agents SET status = COALESCE(?, status),"
        " task = COALESCE(?, task), cwd = ?, branch = ?, last_seen = ?"
        " WHERE id = ?",
        (status, task, cwd, branch, _now(), agent.id))
    return get(conn, agent.id)


def reap(conn: sqlite3.Connection, home: Path | None = None) -> int:
    reaped = 0
    rows = conn.execute(
        "SELECT id, pid, pid_start FROM agents WHERE status != 'offline'"
    ).fetchall()
    for row in rows:
        try:
            alive = identity.pid_alive(row["pid"], row["pid_start"])
        except identity.InspectionUnavailable:
            return reaped     # unknown is not dead: never reap what we cannot
        if not alive:
            conn.execute("UPDATE agents SET status='offline' WHERE id=?",
                         (row["id"],))
            reaped += 1
            if home is not None:
                (home / "doorbells" / str(row["id"])).unlink(missing_ok=True)
                (home / "waiters" / f"{row['id']}.pid").unlink(missing_ok=True)
    return reaped


def roster(conn: sqlite3.Connection, home: Path | None = None,
           include_offline: bool = False) -> list[Agent]:
    reap(conn, home)
    where = "" if include_offline else "WHERE status != 'offline'"
    return [_row_to_agent(r) for r in conn.execute(
        f"SELECT * FROM agents {where}"
        " ORDER BY (status = 'offline'), name")]
