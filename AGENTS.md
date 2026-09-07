# Agent Instructions

> **Project guidance below mirrors [CLAUDE.md](CLAUDE.md). Keep the two in sync.**
> The full rationale for every rule here lives in
> [docs/plans/2026-09-06_agent-mail-design.md](docs/plans/2026-09-06_agent-mail-design.md).

## What this project is

`amail` gives coding agents in different harnesses on one Mac presence and messaging over
a shared SQLite mailbox at `~/.amail/mail.db`. No daemon: `amail send` writes the row and
rings the recipient's doorbell inline. Codex sessions are reached with
`codex queue --thread <id> --message <metadata header>`; Claude sessions by touching
`~/.amail/doorbells/<agent-id>`, which ends their backgrounded `amail wait`.

```
src/amail/
  db.py         schema v1, WAL, explicit transactions, version gate
  identity.py   session resolution: harness env vars, then pid ancestry
  names.py      scientist-name allocation over live agents
  registry.py   registration, revival, caller identity, reaping, roster
  mail.py       send with audience snapshot; announced/read state
  routing.py    the one metadata formatter; doorbell + codex-queue push
  waiter.py     singleton blocking watcher (kqueue; DB poll is the truth)
  hooks.py      event-specific harness adapters, fail-open
  doctor.py     diagnostics, including honest "cannot verify here"
  cli.py        argparse dispatch over the modules above
hooks/amail-hook.sh   stable shim that harness hook configs point at
```

## Build & test

```bash
uv sync
uv run pytest              # full suite (55 tests, ~7s)
```

Reinstalling the CLI after a change — `--force` is NOT enough, uv reuses the cached build
of the same version:

```bash
uv tool install --reinstall --refresh --from ~/source/agent-mail amail
```

Python 3.14, **stdlib only at runtime**. `pytest` is the only dev dependency. TDD: write
the failing test, watch it fail, then implement.

## Behavioral contracts

Breaking one of these is a bug even if the suite still passes.

- **Metadata only on every automatic path.** Doorbell contents, waiter output, hook
  output, and `codex queue` arguments carry message id, sender `name@id`, priority, and
  timestamp — never any part of the body, not even a preview. Everything goes through
  `routing.header_line`; `tests/test_sentinel.py` guards it. `amail inbox --preview` is
  the only body-bearing path and no hook may use it.
- **Announced != read.** The waiter and hooks fire on *unannounced* mail and mark it
  announced when surfacing it, so deferring a read never loops; new mail still wakes.
- **Messages bind to agent ids.** Names are display labels, recycled after death, never
  caller identity. Caller identity is a validated `AMAIL_AGENT_ID` or the native session
  key, and a conflict between them raises rather than picking a winner.
- **A mailbox outlives its endpoint.** Re-registering a `session_key`, including an
  offline one, revives the same agent id. Reaping never destroys mailboxes.
- **Liveness is `(pid, pid_start)`,** never pid alone. `last_seen` is last amail activity,
  not process health.
- **Send persists before it notifies.** `routing.ring` returns `False` on any failure and
  never raises — the row is already committed.
- **Hooks are fail-open** (always exit 0, log one bounded body-free line to
  `~/.amail/hook.log`), and `hooks/amail-hook.sh`'s command line must never change —
  Codex trusts hooks per command hash, so an edit silently disables them until re-approved.
- **Transactions:** read-then-decide writes run in `BEGIN IMMEDIATE` with bounded retry;
  process inspection and git subprocesses stay outside writer locks.
- **`amail wait` must stay a genuinely blocking process,** never a bash `sleep` loop.
  kqueue is latency; the SQLite poll is correctness.
- **Never capture raw `env`** into bodies, headers, or logs — Codex exports API keys into
  every command's environment.
- Tests that spawn subprocesses must scrub ambient identity via
  `tests/conftest.py::clean_env`; the suite often runs inside a harness session. All state
  lives under `~/.amail/` (mode 0700), overridable with `AMAIL_HOME`.

## Verification

Unit tests are not proof the product works. Surface behavior is defined by
[docs/acceptance-gates.md](docs/acceptance-gates.md), which is manual and per-harness — do
not claim a surface works until its gate is recorded there with a date and a version.

---

## Issue tracking

This project uses **bd** (beads) for issue tracking. Run `bd prime` for full workflow context.

> **Architecture in one line:** Issues live in a local Dolt database
> (`.beads/dolt/`); cross-machine sync uses `bd dolt push/pull` (a
> git-compatible protocol), stored under `refs/dolt/data` on your git
> remote — separate from `refs/heads/*` where your code lives.
> `.beads/issues.jsonl` is a passive export, not the wire protocol.
>
> See [SYNC_CONCEPTS.md](https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md)
> for the one-screen overview and anti-patterns (don't treat JSONL as the
> source of truth; don't `bd import` during normal operation; don't
> reach for third-party Dolt hosting before trying the default).

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
