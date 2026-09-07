"""Sender-side push: metadata-only headers, bounded, never raising."""
from __future__ import annotations

import subprocess
from pathlib import Path

from amail import mail
from amail.registry import Agent

CODEX_TIMEOUT_S = 10

PUSHED = "pushed"
FAILED = "failed"
UNREACHABLE = "unreachable"
"""The endpoint cannot be reached now OR later. `codex queue` fails this way
only for a thread id with no rollout — one whose session died before Codex
made it resumable — and such a thread can never be resumed, so unlike an
ordinary push failure there is no hook or waiter backstop behind it."""

_NO_ROLLOUT = "no rollout found for thread id"


def doorbell_path(home: Path, agent_id: int) -> Path:
    return home / "doorbells" / str(agent_id)


def header_line(h: mail.Header) -> str:
    return (f"[amail] msg {h.id} from {h.sender}@{h.sender_id}"
            f" prio={h.priority} at {h.created_at}"
            f" — read with: amail read {h.id}")


def ring(home: Path, target: Agent, header: str,
         runner=subprocess.run) -> str:
    """PUSHED, FAILED, or UNREACHABLE. Never raises: the row is already
    committed, so a push problem is news, not an error."""
    try:
        if target.route.get("kind") == "codex_queue":
            result = runner(
                ["codex", "queue", "--thread", target.route["thread"],
                 "--message", header],
                capture_output=True, text=True, timeout=CODEX_TIMEOUT_S)
            if result.returncode == 0:
                return PUSHED
            if _NO_ROLLOUT in ((result.stderr or "") + (result.stdout or "")):
                return UNREACHABLE
            return FAILED
        with doorbell_path(home, target.id).open("a") as f:
            f.write(header + "\n")
        return PUSHED
    except (OSError, subprocess.SubprocessError):
        return FAILED


def ring_all(home: Path, targets: list[Agent], header: str,
             runner=subprocess.run) -> tuple[int, list[Agent]]:
    """(how many were pushed, which ones can never be reached)."""
    pushed, unreachable = 0, []
    for target in targets:
        outcome = ring(home, target, header, runner)
        if outcome == PUSHED:
            pushed += 1
        elif outcome == UNREACHABLE:
            unreachable.append(target)
    return pushed, unreachable
