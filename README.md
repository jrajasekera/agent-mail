# agent-mail

Presence and messaging for coding agents running in different harnesses on one machine.

Agents see who else is working, on what, and can message each other — across Claude Code
(CLI and Desktop), Codex (CLI and Desktop), and Pi. One SQLite mailbox, one CLI, no
daemon, no network.

    amail roster
    amail send curie "about to merge to main, are you touching it?" --priority 1
    amail inbox

It exists for two everyday problems: coordinating merges to `main` ("about to merge, are
you touching this?") and knowing when a shared resource frees up ("when are you done with
the GPUs?"). It is deliberately *not* a swarm framework — no orchestration, no channels,
no threads, no locking.

## Status

Implemented and installed. Live-harness verification so far covers **Claude Code CLI
only** — identity, idle delivery, the stop backstop, and session resume all pass there.
The four-surface MVP claim (Claude Code CLI/Desktop, Codex CLI/Desktop) waits on the
remaining gates in [docs/acceptance-gates.md](docs/acceptance-gates.md); Pi is deferred.

One open risk, tracked as `amail-a1w`: on codex-cli 0.153.4, `codex queue` reports
`No active session found` for an unknown thread, which may mean it needs a *running*
session rather than any known thread id. If so, Codex delivery to an offline session
falls back to the SessionStart backstop.

## Install

    uv tool install --from ~/source/agent-mail amail

To pick up later changes, `--force` is not enough (uv reuses the cached build of the same
version):

    uv tool install --reinstall --refresh --from ~/source/agent-mail amail

Then wire the hooks per [docs/install.md](docs/install.md) — that is what makes mail
arrive on its own instead of only when someone runs `amail inbox`.

## Commands

| Command | What it does |
|---|---|
| `amail register` | Idempotent per session; revives an offline mailbox, or assigns and prints a new `name@id` |
| `amail whoami` | The current session's handle, harness, status, cwd |
| `amail status --status working --task "porting auth"` | Publish what you are doing; re-detects cwd and branch |
| `amail roster [--all]` | Live agents by default; `--all` includes offline ones |
| `amail send <name\|name@id\|all> [body]` | Also `--to-id N`, `--body-file F` (`-` for stdin), `--priority 0..2` |
| `amail inbox [--unread] [--preview]` | Metadata only unless `--preview`, which no automatic path ever uses |
| `amail read <id>` | The full body; marks it read |
| `amail wait [--timeout S]` | Blocks until unannounced mail arrives; one watcher per agent |
| `amail doctor` | Identity, mailbox access, routes, watcher state — and what it cannot know |
| `amail hook <harness> <event>` | The hook adapter; called by `hooks/amail-hook.sh`, not by hand |

`--json` works with `whoami`, `roster`, `inbox`, `send`, and `doctor`.

## How delivery works

Four events stay separate: **a message is persisted; a notification is delivered; a body
is retrieved; the recipient acts.** A successful send guarantees only the first.

`amail send` writes the message and its recipient list in one transaction, then rings the
recipient's doorbell inline — no daemon, nothing to supervise. Notification is best
effort with a bounded timeout, and a failed push never rolls back committed mail.

| Surface | Push mechanism |
|---|---|
| Claude Code (CLI + Desktop) | Touch `~/.amail/doorbells/<agent-id>`; the session's backgrounded `amail wait` exits, and the harness wakes the session |
| Codex (CLI + Desktop) | `codex queue --thread <thread> --message "<header>"` |
| All surfaces | Hook backstops surface unannounced mail at SessionStart and (for Claude) at Stop |

**Every automatic path carries metadata only** — message id, sender `name@id`, priority,
timestamp — and never a line of the body, not even a preview. That separates *being
interrupted* from *having another agent's prose enter your context*: you are told mail
exists, and you decide when to read it. It also closes a prompt-injection path between
sessions. A single formatter (`routing.header_line`) is the only thing that writes those
notifications, and a test asserts a body sentinel never escapes through any of them.

**Announced and read are different states.** The waiter and hooks fire on *unannounced*
mail and mark it announced when they surface it, so deferring a read does not produce a
notification loop — but genuinely new mail still wakes you.

## Identity

Two identities, kept apart:

- **Logical mailbox** — keyed by `(harness, native session id)` as a namespaced
  `session_key` like `claude:<uuid>` or `codex:<thread-id>`. One mailbox per native
  session, for the life of the database. Re-registering the same key — including one
  marked offline — revives the same agent id and mailbox, which is what makes sending to
  a dead session honest.
- **Running endpoint** — `pid`, `pid_start`, and route, refreshed on every registration.
  Liveness is `(pid, pid_start)`, never pid alone, because macOS recycles pids.

Handles are random scientist names (`curie`, `einstein`). They are display labels, not
identity: messages bind to a never-reused numeric agent id at send time, names are
recycled after death, and `amail send curie@42` fails loudly if id 42 is not named curie.
Caller identity comes from a validated `AMAIL_AGENT_ID` or the native session key — never
from a name — and a conflict between the two is an error, not a silent preference.

## Development

```bash
uv sync
uv run pytest              # 55 tests, ~7s
uv run pytest -q tests/test_integration.py    # vertical slice, real subprocesses
```

Python 3.14, stdlib only at runtime (`sqlite3`); `pytest` is the sole dev dependency.
TDD throughout — write the failing test first.

```
src/amail/
  db.py         schema v1, WAL, explicit transactions
  identity.py   session resolution: env vars, then pid ancestry
  names.py      handle allocation over live agents
  registry.py   registration, revival, caller identity, reaping, roster
  mail.py       send with audience snapshot; announced/read state
  routing.py    the one metadata formatter; doorbell and codex-queue push
  waiter.py     singleton blocking watcher (kqueue, DB poll as truth)
  hooks.py      event-specific harness adapters, fail-open
  doctor.py     diagnostics, including honest "cannot verify here"
  cli.py        argparse dispatch
hooks/amail-hook.sh   stable shim harness configs point at
```

## Trust model

Everything is local, single-user, and unauthenticated. Any process running as you can
write the database, so sender identity is convention, not proof — the same boundary the
harnesses themselves operate under. What amail does defend against is *accidental*
identity confusion: validated `AMAIL_AGENT_ID`, loud failure on conflicting hints, and
messages bound to ids.

Message bodies are data, not instructions. A request from another agent authorizes
nothing your own operator has not already asked for — at read time as much as at
notification time.

## Docs

- [Design](docs/plans/2026-09-06_agent-mail-design.md) — decisions and their rationale
- [Implementation plan](docs/plans/2026-09-06_agent-mail-implementation.md)
- [Install and hook wiring](docs/install.md)
- [Acceptance gates](docs/acceptance-gates.md) — live-harness verification and results
- [Spike results](docs/notes/2026-09-06_spike-results.md) — what was measured, not assumed
