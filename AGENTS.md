# Agent Instructions

> This is the single source of agent guidance for this repo. `CLAUDE.md` is a symlink to
> this file — do not replace it with a copy. The full rationale for every rule here lives
> in [docs/plans/2026-09-06_agent-mail-design.md](docs/plans/2026-09-06_agent-mail-design.md).

## What this project is

`amail` gives coding agents in different harnesses on one Mac presence and messaging over
a shared SQLite mailbox at `~/.amail/mail.db`. No daemon: `amail send` writes the row and
rings the recipient's doorbell inline. Codex sessions are reached with
`codex queue --thread <id> --message <metadata header>`; Claude sessions by touching
`~/.amail/doorbells/<agent-id>`, which ends their backgrounded `amail wait`.

Read the design doc before making design-level changes — it records why each decision was
made.

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

Tables: `agents`, `messages`, and `message_recipients` — the last is the single
authoritative audience (inbox visibility, read authorization, announcement state, and
push targeting all read it), snapshotted in the same transaction that inserts the
message.

## Build & test

```bash
uv sync                    # create/refresh .venv
uv run pytest              # full suite (55 tests, ~7s)
uv run pytest tests/test_mail.py -v
```

Reinstalling the CLI after a change — `--force` is NOT enough, uv reuses the cached build
of the same version:

```bash
uv tool install --reinstall --refresh --from ~/source/agent-mail amail
```

Manual smoke test against a throwaway mailbox (never touches `~/.amail`):

```bash
export AMAIL_HOME=/tmp/amail-smoke
amail register && amail doctor
rm -rf /tmp/amail-smoke
```

Python 3.14, **stdlib only at runtime** (`sqlite3`, `argparse`, `subprocess`, `select`,
`json`). `pytest` is the only dev dependency. Do not add a runtime dependency without
saying why in the commit.

## Behavioral contracts

These are behavioral contracts, not style preferences. Breaking one is a bug even if the
suite still passes.

- **Metadata only on every automatic path.** Doorbell contents, waiter output, hook
  output, and `codex queue` arguments carry message id, sender `name@id`, priority, and
  timestamp — never any part of the body, not even a preview. Everything goes through
  `routing.header_line`; `tests/test_sentinel.py` guards it. Body previews exist only
  behind `amail inbox --preview`, which no hook or automatic path may use.
- **Announced != read.** The waiter and hooks fire on *unannounced* mail and mark it
  announced when they surface it. Previously announced mail must never re-fire a watcher
  or re-block a stop; genuinely new mail must. Announcement state is bookkeeping and
  never hides unread mail from `amail inbox`.
- **Messages bind to agent ids.** Names are display labels, recycled after death, and are
  never caller identity. Caller identity is a validated `AMAIL_AGENT_ID` or the native
  session key; a conflict between them raises, it does not pick a winner.
- **A mailbox outlives its endpoint.** Re-registering a `session_key` — including an
  offline one — revives the same agent id. Reaping marks endpoints offline; it never
  destroys mailboxes.
- **Liveness is `(pid, pid_start)`,** never pid alone (macOS recycles pids). `last_seen`
  means last amail activity, not process health.
- **Send persists before it notifies.** Push is best effort with a bounded timeout;
  `routing.ring` returns `False` on any failure and never raises, because the row is
  already committed.
- **Hooks are fail-open.** `amail hook` exits 0 no matter what and logs one bounded,
  body-free line to `~/.amail/hook.log`. A broken amail must never break a session.
  Harness hook configs point at `hooks/amail-hook.sh` and that command line must never
  change — Codex trusts hooks per command hash, so an edit silently disables them until
  re-approved.
- **Transactions:** every read-then-decide write runs inside `BEGIN IMMEDIATE` with
  bounded retry. Process inspection and git subprocesses stay outside writer locks.
- **`amail wait` is a real blocking process,** never a bash `sleep` loop (Claude Code
  kills bare sleeps but backgrounds genuine long-running commands). kqueue is a latency
  optimization; the SQLite poll is the correctness path.
- **Never capture raw `env`** into message bodies, headers, or logs — Codex exports API
  keys into every command's environment.
- **Tests that spawn subprocesses must scrub ambient identity** with
  `tests/conftest.py::clean_env`; the suite often runs *inside* a harness session.
- All state lives under `~/.amail/` (mode 0700), overridable with `AMAIL_HOME` — tests
  depend on that override.

## Testing & verification

TDD: write the failing test, watch it fail, then implement. Unit tests are not proof the
product works — the surface behavior is defined by
[docs/acceptance-gates.md](docs/acceptance-gates.md), which is manual and per-harness.
Do not claim a surface works until its gate is recorded there with a date and a version.

Track work in beads (`br ready`, `br create`); open gates and known risks are already
filed.

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
## Beads Rust Issue Tracker

This repository uses Beads Rust (`br`) for durable task tracking. Use the
globally installed `br` skill for the full workflow.

```bash
br info --json
br ready --json
br show <id> --json
br update <id> --claim --json
br close <id> --reason "Completed"
br sync --status --json
br sync --flush-only
```

Tracker mutations auto-flush `.beads/issues.jsonl`, and normal issue commands
auto-import locally changed JSONL. `br` does not run Git, use Dolt, install
hooks, or run a daemon. Prefer ready leaf tasks; open children can make a parent
epic appear blocked as a hierarchy rollup.

Before handoff, run relevant quality gates, update only completed tracker work,
verify `br sync --status --json`, and inspect `git status`. Do not commit or push
without explicit authorization.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `br` for task tracking. Do not run git commits, git pushes, or tracker synchronization unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `br info --json`; use the same conservative git policy unless active instructions say otherwise.
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
   br sync --flush-only
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->
