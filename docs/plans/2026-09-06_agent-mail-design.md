# agent-mail design

**Date:** 2026-09-06
**Status:** Approved design. Implementation plan not yet written.

## Purpose

Let coding agents running in different harnesses on one Mac see each other, publish
what they are working on, and send each other messages. Two motivating cases:

- Coordinating merges to `main` ("about to merge, are you touching this?").
- Knowing when a shared resource frees up ("when are you done with the GPUs?").

Explicit non-goals: swarms, teams, orchestration, channels, threads, reactions,
file sharing, cross-machine sync, resource locking.

## Surfaces

Five harnesses on one machine, all sharing one mailbox:

| Surface | Notes |
|---|---|
| Claude Code CLI | Usually inside herdr |
| Claude Code Desktop | Electron; no external IPC (verified) |
| Codex CLI | Shares a daemon with the desktop app |
| Codex Desktop (ChatGPT.app) | Holds `~/.codex/ipc/ipc.sock` (verified) |
| Pi | No MCP support at all (verified); has an extension API |

## Decisions and rationale

### Own implementation, not Smoo's `th`

`th`'s mail bus works, but its agent record has nowhere to store per-agent *routing*
metadata (Codex thread id, doorbell path), which is the heart of this design. We would
end up shadowing its roster in a second file, at which point `th` is only a message
table we did not write. Also: its MCP path loses agent identity between calls, it ships
a paid cloud tier and unaudited `api.smoo.ai` endpoints, and it releases multiple times
a day underneath our hooks.

### CLI, not MCP

`th mcp serve` exposes 23 tools (~5.4k tokens) in every session in every harness, and
its identity does not persist across calls within an MCP session (verified: claiming a
name via `agent_identity` does not make a later `mail_inbox` resolve). A CLI costs zero
context, works in all five surfaces because all five have a shell, and sidesteps the
identity problem entirely. Agents learn the commands from global CLAUDE.md / AGENTS.md.

### Push is immediate; the payload is metadata only

A doorbell carries sender, priority, and message id — never the body. The recipient
pulls the body when it chooses. This separates *interruption* from *context injection*:
an agent is told mail exists without another agent's prose entering its context
unbidden, and it decides when to act. It also closes a prompt-injection path between
sessions.

### Random static handles, mutable location fields

Handles are randomly assigned scientist names (`einstein`, `curie`), fixed for the life
of a session. Deriving names from repo/branch was rejected: an agent can switch branches
mid-session, and a name that lies is worse than one that is opaque. `cwd`, `branch`,
`status`, and `task` are separate mutable fields, re-detected on every `status` call.

### Messaging and presence only

No resource-claim primitive. A GPU hold is a status line ("training on both GPUs,
~40min left"), readable from the roster. If we find ourselves hand-rolling the same
lock repeatedly, we will add the primitive then, knowing its real shape.

### Sender-side routing, no daemon

`amail send` writes the row, looks up the recipient's route, and rings the doorbell
inline. No process to supervise, no startup ordering, no silently-dead daemon. A failed
push leaves a committed unread row that the hook backstop surfaces later.

## Architecture

### Storage

One SQLite database at `~/.amail/mail.db`, WAL mode with a busy timeout for concurrent
sessions.

**`agents`** — `name` (PK), `harness`, `session_pid`, `route` (JSON), `cwd`, `branch`,
`status` (`working` | `idle` | `waiting` | `offline`), `task`, `created_at`, `last_seen`.

**`messages`** — `id`, `sender`, `recipient` (a name, or `all`), `body`, `priority`,
`created_at`.

**`reads`** — `(message_id, agent, read_at)`. A separate table so a broadcast is read
per-recipient rather than one reader clearing it for everyone.

### Session identity

`amail` walks up the process tree to the first ancestor that is a known harness binary
and uses that pid as the session key. Every call from inside one session resolves to the
same ancestor, so identity needs no environment variable (hooks cannot set one in the
harness process), no handshake file, and no name passed per call. Liveness comes free:
if the pid is gone, the agent is dead. `AMAIL_NAME` overrides, for shell use and tests.

### CLI

```
amail register            # idempotent; assigns and prints a free scientist name
amail whoami
amail status --status working --task "porting auth middleware"
amail roster              # name, harness, cwd, branch, status, task, last-seen
amail send <name|all> <body> [--priority N]
amail inbox [--unread]    # headers only: id, sender, priority, first line
amail read <id>           # full body; marks read
amail wait [--timeout]    # blocks until unread mail, prints the doorbell, exits
```

`register` auto-detects harness, cwd, and branch. `status` re-detects cwd and branch on
every call so they self-heal after a branch switch. `inbox` returns headers only, so
triage never pulls another agent's prose into context.

### Delivery

| Surface | Mechanism |
|---|---|
| Claude Code CLI + Desktop | Touch `~/.amail/doorbells/<name>`; the session's backgrounded `amail wait` exits, and the harness wakes the session on exit |
| Codex CLI + Desktop | `codex queue --thread <thread> --message "<doorbell>"` over the shared IPC socket |
| Pi | Extension watches the doorbell file, injects the header at `before_agent_start` |
| All | `SessionStart` + `Stop` hooks run `amail inbox --unread` as a backstop |

The Claude mechanism was verified by spike on 2026-09-06: a background command blocked
48s while the session was idle, exited when its flag file appeared, and the harness woke
the session ~1s later with the output readable. `amail wait` re-arms manually after each
fire (a global CLAUDE.md instruction); the hook backstop is what makes a forgotten
re-arm a latency bug rather than a lost message.

**Open question:** whether a Codex session can learn its own thread id, most likely from
the `SessionStart` hook payload. Unconfirmed. If unavailable, Codex degrades to
hook-backstop delivery only. This is the one capability the design could lose, and the
loss is contained to one surface.

### Lifecycle and failure

- Reading the roster checks each session pid and reaps dead agents to `offline`.
- Names are released on death but held on a 24h cooldown, so mail addressed to a
  departed `curie` cannot land in a freshly spawned one's inbox.
- The message row is committed before any push is attempted. Failure is always latency,
  never loss.
- Sending to an offline agent succeeds with a warning naming its last-seen time.
- Messages older than 30 days are pruned on write.

### Safety

Message bodies are data, not instructions. Injected text — doorbell or backstop — is
framed explicitly as a message from another agent, and is never wrapped in
system-reminder-shaped markup. Global instructions will state that a request from
another agent authorizes nothing its own operator has not already asked for.

## Testing

TDD throughout.

- **Unit:** name allocation and collision, cooldown on reuse, pid-based session
  resolution, reaping, per-recipient read semantics for broadcasts, pruning.
- **Integration:** two subprocesses under fake harness ancestors run the full loop —
  register, send, doorbell touched, the other's `wait` exits, `inbox` shows one unread,
  `read` marks it.
- The Claude wake-up is proven by spike and is not re-tested in CI.

## Implementation notes

Python 3.14 with stdlib `sqlite3`. No third-party dependencies.
