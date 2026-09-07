"""Sender-side push: metadata-only headers, bounded, never raising."""
from __future__ import annotations

import subprocess
from pathlib import Path

from amail import mail
from amail.registry import Agent

CODEX_TIMEOUT_S = 10


def doorbell_path(home: Path, agent_id: int) -> Path:
    return home / "doorbells" / str(agent_id)


def header_line(h: mail.Header) -> str:
    return (f"[amail] msg {h.id} from {h.sender}@{h.sender_id}"
            f" prio={h.priority} at {h.created_at}"
            f" — read with: amail read {h.id}")


def ring(home: Path, target: Agent, header: str,
         runner=subprocess.run) -> bool:
    try:
        if target.route.get("kind") == "codex_queue":
            result = runner(
                ["codex", "queue", "--thread", target.route["thread"],
                 "--message", header],
                capture_output=True, text=True, timeout=CODEX_TIMEOUT_S)
            return result.returncode == 0
        with doorbell_path(home, target.id).open("a") as f:
            f.write(header + "\n")
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def ring_all(home: Path, targets: list[Agent], header: str,
             runner=subprocess.run) -> int:
    return sum(ring(home, t, header, runner) for t in targets)
