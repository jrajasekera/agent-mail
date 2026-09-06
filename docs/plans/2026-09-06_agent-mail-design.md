# agent-mail design

**Date:** 2026-09-06
**Status:** Approved design, revised same day after verification spikes and review.
Implementation plan not yet written.
**Spike evidence:** [../notes/2026-09-06_spike-results.md](../notes/2026-09-06_spike-results.md)

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
| Claude Code Desktop | Electron shell, but each session runs its own `claude` binary process (verified) |
| Codex CLI | Shares `~/.codex` SQLite stores with the desktop app — **not** a daemon (verified; the CLI TUI runs its own in-process app-server) |
| Codex Desktop (ChatGPT.app) | Holds `~/.codex/ipc/ipc.sock`, but amail never needs it — enqueue is a store write |
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

### Random static handles over stable agent ids

Handles are randomly assigned scientist names (`einstein`, `curie`), fixed for the life
of a session. Deriving names from repo/branch was rejected: an agent can switch branches
mid-session, and a name that lies is worse than one that is opaque. `cwd`, `branch`,
`status`, and `task` are separate mutable fields, re-detected on every `status` call.

Underneath the name, every registration gets a unique, never-reused **agent id**, and
messages are addressed to ids, resolved from the name at send time. This replaces the
earlier name-cooldown scheme: a cooldown only narrowed the misdelivery window (24h
cooldown vs 30-day message retention left old mail addressed to a recycled `curie`
landing in a new `curie`'s inbox). With id addressing, names can be recycled freely and
misrouting is impossible by construction.

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

**`agents`** — `id` (PK, never reused), `name` (unique among live agents), `harness`,
`session_key` (harness session id where available, else pid), `pid`, `pid_start`
(process start time, for pid-reuse disambiguation), `route` (JSON), `cwd`, `branch`,
`status` (`working` | `idle` | `waiting` | `offline`), `task`, `created_at`,
`last_seen`.

**`messages`** — `id`, `sender_id`, `recipient_id` (an agent id, or NULL for
broadcast), `body`, `priority`, `created_at`.

**`reads`** — `(message_id, agent_id, read_at)`. A separate table so a broadcast is
read per-recipient rather than one reader clearing it for everyone.

Broadcasts are scoped by registration time: an agent's inbox includes a broadcast only
if `messages.created_at >= agents.created_at`, so a fresh session does not inherit
history addressed to a room it was not in.

**Priority** is an integer 0–2 (0 = FYI, 1 = normal, 2 = urgent), default 1. It is
purely advisory: it appears in the doorbell and inbox headers and changes nothing about
delivery.

### Session identity

Identity comes from the harness's own environment, verified by spike on 2026-09-06:

- **Claude Code (CLI and Desktop):** every Bash tool invocation carries
  `CLAUDE_CODE_SESSION_ID` (a per-session UUID) and `CLAUDE_PID` (the per-session
  `claude` process). Additionally, the `SessionStart` hook receives a
  `CLAUDE_ENV_FILE` path; anything the hook exports there (e.g. `AMAIL_NAME`) appears
  in every subsequent Bash call. Registration can therefore happen entirely in the
  hook, before the model runs a single command. (The earlier premise that "hooks
  cannot set an environment variable in the harness process" was falsified by spike.)
- **Codex (CLI and Desktop):** every model-run shell command carries
  `CODEX_THREAD_ID` (= session id), so `amail register` reads its own route directly —
  no thread-id handshake. Hook payloads carry `session_id` too.
- **Pi:** no known env var; fall back to walking the process tree to the harness
  binary and using that pid as the session key.

Liveness uses `(pid, pid_start)` rather than pid alone — macOS recycles pids, and
`ps -o lstart=` is a stable, second-resolution start time (verified). If the pid is
gone or its start time differs, the agent is dead. `AMAIL_NAME` overrides identity for
shell use and tests (and is what the Claude `SessionStart` hook pins via
`CLAUDE_ENV_FILE`).

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
triage never pulls another agent's prose into context. `send` resolves the name to the
live agent's id at send time. `read` ends its output with a one-line re-arm reminder
("re-arm with `amail wait`") — a nudge at the exact moment it matters, not just a
global-instructions rule.

**`amail wait` must be a genuinely blocking process** (kqueue/poll on the doorbell
file), never a `sleep` loop: Claude Code auto-backgrounds a foreground command that
outlives its timeout, *except* bare `sleep` and a few other patterns, which it kills.

### Delivery

| Surface | Mechanism |
|---|---|
| Claude Code CLI + Desktop | Touch `~/.amail/doorbells/<agent-id>`; the session's backgrounded `amail wait` exits, and the harness wakes the session on exit |
| Codex CLI + Desktop | `codex queue --thread <thread> --message "<doorbell>"` — a durable write to `~/.codex/queue_1.sqlite`; succeeds even when no Codex process is running, arrives as a user message, fails cleanly (exit 1) on a bad thread id |
| Pi | Extension watches the doorbell file, injects the header at `before_agent_start` |
| All | `SessionStart` + `Stop` hooks run `amail inbox --unread` as a backstop |

The Claude mechanism was verified by spike twice on 2026-09-06: first a 48 s block,
then a **45-minute block** (2,698 s) while the session sat idle — the watcher exited
within seconds of the flag appearing and the harness woke the session immediately.
`amail wait` re-arms manually after each fire; the Claude `Stop` hook payload includes
a `background_tasks` list, so the backstop can *detect* an un-armed watcher and tell
the agent to re-arm, making a forgotten re-arm a latency bug rather than a lost
message.

Codex hook payloads and stdout injection are verified: hook stdout (plain or
`additionalContext`) lands in model context, and Codex supports `SessionStart`, `Stop`,
and ten other events. **Constraint:** Codex trusts hooks per command hash — any edit to
the configured command silently disables the hook until re-approved interactively. The
amail hook must therefore invoke a stable wrapper script (edit the script, never the
hook line).

Known deployment failure mode: `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` removes
Claude-side push entirely. The hook backstop covers it; degradation is to breakpoint
delivery, never silence.

### Lifecycle and failure

- Reading the roster checks each session's `(pid, pid_start)` and reaps dead agents to
  `offline`.
- Names are released on death and may be recycled immediately — id-based addressing
  makes reuse safe (no cooldown needed).
- The message row is committed before any push is attempted. Failure is always latency,
  never loss.
- Sending to an offline agent succeeds with a warning naming its last-seen time.
- Messages older than 30 days are pruned on write.

### Safety

Message bodies are data, not instructions. Injected text — doorbell or backstop — is
framed explicitly as a message from another agent, and is never wrapped in
system-reminder-shaped markup. Global instructions will state that a request from
another agent authorizes nothing its own operator has not already asked for.

**Trust model, stated plainly:** everything is local, same-user, and unauthenticated.
`AMAIL_NAME` overrides identity and the database is writable by any process running as
the user, so sender identity is convention, not proof. That is acceptable for a
single-user machine and is the same trust boundary the harnesses themselves operate
under; amail does not pretend otherwise.

**Hygiene:** Codex exports API keys (`CODEX_OPENROUTER_API_KEY`,
`CODEX_RUNPOD_API_KEY`) into every shell command's environment. Never capture raw
`env` output into message bodies or logs.

## Open questions

Both remaining items are one-off manual checks, neither blocks implementation:

- Does `codex queue` to an *idle interactive* session trigger a turn immediately, or
  wait for a boundary? (Binary strings suggest turn-on-idle, message-boundary delivery
  mid-turn; unverified live. Affects doorbell chattiness etiquette, not correctness.)
- Confirm `CODEX_THREAD_ID` and `CLAUDE_PID`/`CLAUDE_CODE_SESSION_ID` appear in
  *interactive TTY* sessions — both verified headless only, and both near-certain
  since the env injection lives in shared core code.

## Testing

TDD throughout.

- **Unit:** name allocation and collision, id-based addressing across name reuse,
  session resolution (env-var and pid-fallback paths), `(pid, pid_start)` reaping,
  per-recipient read semantics and registration-time scoping for broadcasts, pruning.
- **Integration:** two subprocesses under fake harness environments run the full loop —
  register, send, doorbell touched, the other's `wait` exits, `inbox` shows one unread,
  `read` marks it.
- The Claude wake-up (including the 45-minute idle block) is proven by spike and is not
  re-tested in CI.

## Implementation notes

Python 3.14 with stdlib `sqlite3`. No third-party dependencies.
