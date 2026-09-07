"""Event-specific harness hook adapters. Plain stdout is context on
SessionStart but NOT on Stop (Claude wants structured continuation JSON;
Codex wants JSON/empty) — so each (harness, event) emits its own shape."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone

from amail import db, mail, registry, routing

REARM_NOTE = ("[amail] no mail watcher armed — after triage, re-arm with:"
              " amail wait")


def _payload(stdin_text: str) -> dict:
    try:
        loaded = json.loads(stdin_text or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _announce(conn, agent) -> list[str]:
    headers = mail.unannounced(conn, agent)
    mail.mark_announced(conn, agent, [h.id for h in headers])
    return [routing.header_line(h) for h in headers]


def _rearm_note(payload: dict) -> str | None:
    bg = payload.get("background_tasks")
    if not isinstance(bg, list):
        return None                       # unknown — say nothing
    if any("amail wait" in json.dumps(task) for task in bg):
        return None                       # armed — no nag
    return REARM_NOTE


def run_hook(harness: str, event: str, env: Mapping[str, str],
             stdin_text: str) -> tuple[int, str]:
    payload = _payload(stdin_text)
    env = dict(env)
    if harness == "codex" and payload.get("session_id"):
        env["CODEX_THREAD_ID"] = str(payload["session_id"])
    home = db.amail_home(env)
    conn = db.connect(home)
    try:
        if event == "sessionstart":
            agent = registry.register(conn, env, home)
            if harness == "claude" and env.get("CLAUDE_ENV_FILE"):
                with open(env["CLAUDE_ENV_FILE"], "a") as f:
                    f.write(f"export AMAIL_AGENT_ID={agent.id}\n")
            return 0, "\n".join(_announce(conn, agent))
        if harness == "claude" and event == "stop":
            agent = registry.current_agent(conn, env)
            if agent is None:
                return 0, ""
            lines = _announce(conn, agent)   # marking bounds continuation
            if not lines:
                return 0, ""
            note = _rearm_note(payload)
            if note:
                lines.append(note)
            return 0, json.dumps({"decision": "block",
                                  "reason": "\n".join(lines)})
        return 0, ""                          # codex stop and unknown events
    finally:
        conn.close()


def log_failure(env: Mapping[str, str], exc: BaseException) -> None:
    try:
        home = db.amail_home(env)
        stamp = datetime.now(timezone.utc).isoformat()
        with open(home / "hook.log", "a") as f:
            f.write(f"{stamp} {type(exc).__name__}: {exc}\n")
    except OSError:
        pass
