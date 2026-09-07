"""Messages and per-recipient announced/read state. The audience is
snapshotted in the send transaction and is the single source of truth."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from amail import registry
from amail.registry import Agent

RETENTION_DAYS = 30
MAX_BODY_BYTES = 65536


@dataclass(frozen=True)
class Header:
    id: int
    sender_id: int
    sender: str
    priority: int
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def preview(body: str) -> str:
    lines = body.splitlines()
    return (lines[0] if lines else "")[:80]


def _validate_body(body: str) -> None:
    if not body or not body.strip():
        raise ValueError("empty message body")
    if len(body.encode()) > MAX_BODY_BYTES:
        raise ValueError(f"body exceeds {MAX_BODY_BYTES // 1024} KiB (64 KiB)")


def _offline_warning(row) -> str | None:
    if row["status"] != "offline":
        return None
    return (f"{row['name']}@{row['id']} is offline (last seen"
            f" {row['last_seen']}); queued — delivered if that session resumes")


def _resolve_audience(conn, sender, recipient, to_id):
    """Returns (audience_rows, warning). Runs INSIDE the send transaction."""
    if to_id is None and recipient and "@" in recipient:
        name_part, _, id_part = recipient.rpartition("@")
        to_id = int(id_part)
        row = conn.execute("SELECT * FROM agents WHERE id = ?",
                           (to_id,)).fetchone()
        if row is None:
            raise ValueError(f"no agent with id {to_id}")
        if row["name"] != name_part:
            raise ValueError(f"agent {to_id} is not named {name_part!r}")
        return [row], _offline_warning(row)
    if to_id is not None:
        row = conn.execute("SELECT * FROM agents WHERE id = ?",
                           (to_id,)).fetchone()
        if row is None:
            raise ValueError(f"no agent with id {to_id}")
        return [row], _offline_warning(row)
    if recipient == "all":
        return conn.execute(
            "SELECT * FROM agents WHERE status != 'offline' AND id != ?",
            (sender.id,)).fetchall(), None
    row = conn.execute(
        "SELECT * FROM agents WHERE name = ? AND status != 'offline'",
        (recipient,)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM agents WHERE name = ?"
            " ORDER BY last_seen DESC LIMIT 1", (recipient,)).fetchone()
        if row is None:
            raise ValueError(f"no agent named {recipient!r}")
    return [row], _offline_warning(row)


def send(conn: sqlite3.Connection, sender: Agent,
         recipient: str | None = None, body: str = "", priority: int = 1,
         to_id: int | None = None) -> tuple[int, list[Agent], str | None]:
    _validate_body(body)
    registry.reap(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=RETENTION_DAYS)).isoformat()
        conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
        audience, warning = _resolve_audience(conn, sender, recipient, to_id)
        cur = conn.execute(
            "INSERT INTO messages (sender_id, body, priority, created_at)"
            " VALUES (?,?,?,?)", (sender.id, body, priority, _now()))
        msg_id = cur.lastrowid
        for row in audience:
            conn.execute(
                "INSERT INTO message_recipients (message_id, agent_id)"
                " VALUES (?,?)", (msg_id, row["id"]))
        conn.execute("UPDATE agents SET last_seen = ? WHERE id = ?",
                     (_now(), sender.id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    targets = [registry._row_to_agent(r) for r in audience
               if r["status"] != "offline"]
    return msg_id, targets, warning


_HEADER_SQL = (
    "SELECT m.id, m.sender_id, a.name AS sender, m.priority, m.created_at"
    " FROM message_recipients r"
    " JOIN messages m ON m.id = r.message_id"
    " JOIN agents a ON a.id = m.sender_id"
    " WHERE r.agent_id = ? AND r.read_at IS NULL {extra}"
    " ORDER BY m.priority DESC, m.id")


def _headers(rows) -> list[Header]:
    return [Header(r["id"], r["sender_id"], r["sender"], r["priority"],
                   r["created_at"]) for r in rows]


def unread(conn: sqlite3.Connection, agent: Agent) -> list[Header]:
    return _headers(conn.execute(_HEADER_SQL.format(extra=""), (agent.id,)))


def unannounced(conn: sqlite3.Connection, agent: Agent) -> list[Header]:
    return _headers(conn.execute(
        _HEADER_SQL.format(extra="AND r.announced_at IS NULL"), (agent.id,)))


def mark_announced(conn: sqlite3.Connection, agent: Agent,
                   message_ids: list[int]) -> None:
    now = _now()
    for mid in message_ids:
        conn.execute(
            "UPDATE message_recipients SET announced_at = ?"
            " WHERE message_id = ? AND agent_id = ? AND announced_at IS NULL",
            (now, mid, agent.id))


def read(conn: sqlite3.Connection, agent: Agent,
         message_id: int) -> tuple[str, str, int]:
    row = conn.execute(
        "SELECT m.body, m.priority, m.sender_id, a.name AS sender"
        " FROM message_recipients r"
        " JOIN messages m ON m.id = r.message_id"
        " JOIN agents a ON a.id = m.sender_id"
        " WHERE r.message_id = ? AND r.agent_id = ?",
        (message_id, agent.id)).fetchone()
    if row is None:
        raise KeyError(f"no message {message_id} for {registry.handle(agent)}")
    now = _now()
    conn.execute(
        "UPDATE message_recipients SET read_at = COALESCE(read_at, ?),"
        " announced_at = COALESCE(announced_at, ?)"
        " WHERE message_id = ? AND agent_id = ?",
        (now, now, message_id, agent.id))
    conn.execute("UPDATE agents SET last_seen = ? WHERE id = ?",
                 (now, agent.id))
    return (f"{row['sender']}@{row['sender_id']}", row["body"],
            row["priority"])
