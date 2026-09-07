# agent-mail

Presence and messaging for coding agents running in different harnesses on one machine.

Agents see who else is working, on what, and can message each other — across Claude Code
(CLI and Desktop), Codex (CLI and Desktop), and Pi. One SQLite mailbox, one CLI, no
daemon, no network.

    amail roster
    amail send curie "about to merge to main, are you touching it?" --priority 1
    amail inbox

Status: implemented — four-surface MVP (Claude Code CLI/Desktop, Codex
CLI/Desktop), Pi pending. Live-harness gates: [docs/acceptance-gates.md](docs/acceptance-gates.md).
Install: [docs/install.md](docs/install.md).
Design: [docs/plans/2026-09-06_agent-mail-design.md](docs/plans/2026-09-06_agent-mail-design.md).
