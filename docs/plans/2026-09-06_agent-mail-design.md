# agent-mail design

**Date:** 2026-09-06
**Status:** Approved design, revised after verification spikes and a pre-implementation
review. Implementation plan: [2026-09-06_agent-mail-implementation.md](2026-09-06_agent-mail-implementation.md).
**Spike evidence:** [../notes/2026-09-06_spike-results.md](../notes/2026-09-06_spike-results.md)
**Review:** [../notes/agent-mail-preimplementation-review.md](../notes/agent-mail-preimplementation-review.md)

## Purpose

Let coding agents running in different harnesses on one Mac see each other, publish
what they are working on, and send each other messages. Two motivating cases:

- Coordinating merges to `main` ("about to merge, are you touching this?").
- Knowing when a shared resource frees up ("when are you done with the GPUs?").

Explicit non-goals: swarms, teams, orchestration, channels, threads, reactions,
file sharing, cross-machine sync, resource locking.

The four distinct events the design keeps separate: **a message is persisted; a
notification is delivered; a body is retrieved; the recipient acts.** A successful
send implies only the first.

## Surfaces

Five harnesses on one machine, all sharing one mailbox. The initial deliverable is a
**four-surface MVP with Pi pending**:

| Surface | Notes |
|---|---|
| Claude Code CLI | Usually inside herdr |
| Claude Code Desktop | Electron shell, but each session runs its own `claude` binary process (verified) |
| Codex CLI | Shares `~/.codex` SQLite stores with the desktop app — **not** a daemon (verified; the CLI TUI runs its own in-process app-server) |
| Codex Desktop (ChatGPT.app) | Holds `~/.codex/ipc/ipc.sock`, but amail never needs it — enqueue is a store write |
| Pi (deferred) | No MCP support at all (verified); has an extension API |

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
name via `agent_identity` does not make a later `mail_inbox` resolve). A CLI avoids
that persistent tool-schema footprint (its own commands and outputs still enter
context when used, but only when used), works in all five surfaces because all five
have a shell, and sidesteps the identity problem entirely. Agents learn the commands
from global CLAUDE.md / AGENTS.md.

### Push is immediate; the payload is metadata only

A doorbell carries message id, sender id and handle, priority, and creation time —
**never any part of the body, including previews**. The recipient pulls the body when
it chooses. This separates *interruption* from *context injection*: an agent is told
mail exists without another agent's prose entering its context unbidden, and it
decides when to act. It also closes a prompt-injection path between sessions.

This rule binds **every automatic path** — doorbell, waiter output, hook backstops,
and `codex queue` arguments — through one shared metadata formatter. Body previews
exist only behind an explicit opt-in flag (`amail inbox --preview`) that no hook or
automatic path ever uses.

### Announced and read are separate states

The design lets a recipient be notified and *defer* reading. That requires two
per-recipient states, not one:

- **announced** — this recipient has been notified that the message exists.
- **read** — this recipient retrieved the body (`read_at` means tool-level retrieval,
  not that the model understood or acted on it).

The waiter and hook backstops wake on **unannounced** mail and mark it announced when
they surface it. Announced-but-unread mail does not re-fire the waiter; genuinely new
mail does. Invariant:

> Previously announced mail can remain unread without generating new wake-ups, while
> genuinely new mail still wakes the agent.

Crash behavior: announcement marking is best effort; a crash between surfacing and
marking may duplicate a notification. Duplicates are acceptable; treating "the
notification process returned" as proof of model receipt is not. Announcement state is
bookkeeping only — it never hides durable unread mail from `amail inbox`.

### Mailbox identity is logical; process identity is runtime state

Two identities, kept distinct:

- **Logical mailbox** — who owns these messages. Keyed by `(harness, native session
  id)`, stored as a namespaced `session_key` (e.g. `claude:<uuid>`,
  `codex:<thread-id>`). One mailbox per native session, for the life of the database.
- **Running endpoint** — which process currently hosts that session: `pid`,
  `pid_start`, route. Refreshed on every registration.

Re-registering an existing session key — including one whose row is `offline` —
**revives the same agent id**, refreshes pid/start/route/cwd/branch, and preserves the
mailbox. A session that dies and resumes therefore recovers mail queued to it while it
was away; this is what makes "queued anyway" offline sends honest. Reaping marks
endpoints offline; it never destroys mailboxes.

### Random static handles over stable agent ids

Handles are randomly assigned scientist names (`einstein`, `curie`), fixed for the life
of a registration. Deriving names from repo/branch was rejected: an agent can switch
branches mid-session, and a name that lies is worse than one that is opaque. `cwd`,
`branch`, `status`, and `task` are separate mutable fields, re-detected on every
`status` call.

Underneath the name, every mailbox has a unique, never-reused **agent id**, and
messages are bound to ids at send time. Names are **display labels and addressing
conveniences, never authoritative identity**:

- Caller identity is pinned by `AMAIL_AGENT_ID` (exported into the session by the
  registration hook), or resolved from the native session key. A stale or conflicting
  identity hint fails loudly; it never silently selects another session's mailbox.
- Stable addressing is exposed: `amail send --to-id 42 …` and the checked form
  `curie@42` (rejected if id 42 is not named curie). The roster and every notification
  display `name@id`.
- The precise guarantee, stated narrowly: **committed messages remain bound to their
  resolved recipient ids.** Bare-name addressing resolves to whoever holds the name at
  send time — use `name@id` when it matters.

### Messaging and presence only

No resource-claim primitive. A GPU hold is a status line ("training on both GPUs,
~40min left"), readable from the roster. A status line or message is a **coordination
hint, never mutual exclusion**: "GPUs look free" reserves nothing, and "about to
merge" serializes nothing. Timestamps and stable sender ids are displayed so delayed
notices are not mistaken for current facts. If we find ourselves hand-rolling the same
lock repeatedly, we will add the primitive then, knowing its real shape.

### Sender-side routing, no daemon

`amail send` writes the row, looks up the recipient's route, and rings the doorbell
inline. No process to supervise, no startup ordering, no silently-dead daemon.

**The delivery guarantee, stated precisely:** a successful send persists the message
before attempting notification. Notification is best effort with a bounded timeout; a
failed notification does not roll back persisted mail. Eventual discovery depends on a
later successful waiter, pull, or supported hook running before the 30-day retention
expires.

## Architecture

### Storage

One SQLite database at `~/.amail/mail.db` (`~/.amail` created mode `0700`), WAL mode
with a busy timeout for concurrent sessions. A `meta` table carries a schema version;
a client refusing a newer schema fails with a clear message rather than guessing.

**`agents`** — `id` (PK, never reused), `name` (unique among non-offline agents),
`harness`, `session_key` (unique, namespaced `<harness>:<native-id>`), `pid`,
`pid_start` (process start time, pid-reuse guard), `route` (JSON), `cwd`, `branch`,
`status` (`working` | `idle` | `waiting` | `offline`), `task`, `created_at`,
`last_seen`.

**`messages`** — `id`, `sender_id`, `body`, `priority`, `created_at`. No recipient
column: the audience lives in `message_recipients`.

**`message_recipients`** — `(message_id, agent_id, announced_at, read_at)`. The
**single authoritative audience**: one row for a direct message, one per recipient for
a broadcast, snapshotted **in the same transaction** that inserts the message. Inbox
visibility, read authorization, announcement state, and push targeting all use this
table, so they can never disagree (the review reproduced an agent registering
mid-broadcast that was inbox-eligible but missed by the push list under the old
timestamp-scoped scheme).

Body rules: empty or whitespace-only bodies are rejected at send; bodies are capped at
64 KiB; header code tolerates malformed legacy rows rather than crashing triage.

`last_seen` means **the time of this agent's last amail activity** (register, status,
send, read). It is not process health — health is `(pid, pid_start)` liveness — and
stale `task` text is history, not observation. A live but quiet endpoint is never
expired for inactivity.

Writes that involve read-then-decide (registration, name allocation, send with
audience snapshot) run inside short explicit `BEGIN IMMEDIATE` transactions with
bounded retry; unique indexes are the backstop, not the concurrency strategy. Process
inspection and git subprocesses stay outside writer-lock intervals.

### Session identity

Identity comes from the harness's own environment, verified by spike on 2026-09-06:

- **Claude Code (CLI and Desktop):** every Bash tool invocation carries
  `CLAUDE_CODE_SESSION_ID` (a per-session UUID) and `CLAUDE_PID` (the per-session
  `claude` process). Additionally, the `SessionStart` hook receives a
  `CLAUDE_ENV_FILE` path; anything the hook exports there appears in every subsequent
  Bash call — the hook registers and pins `AMAIL_AGENT_ID` before the model runs a
  single command. (The earlier premise that "hooks cannot set an environment variable
  in the harness process" was falsified by spike.)
- **Codex (CLI and Desktop):** every model-run shell command carries
  `CODEX_THREAD_ID` (= session id), so `amail register` reads its own route directly —
  no thread-id handshake. Hook payloads carry `session_id` too.
- **Pi:** no known env var; fall back to walking the process tree to the harness
  binary and using that pid as the session key (`<harness>:pid:<pid>`).

Resolution order for "who am I": `AMAIL_AGENT_ID` (validated — unknown id is an
error, and a conflict with the resolvable native session key is an error, not a
silent preference) → native session key → pid-ancestry fallback. There is no
name-based caller identity.

Liveness uses `(pid, pid_start)` rather than pid alone — macOS recycles pids, and
`ps -o lstart=` is a stable, second-resolution start time (verified). If the pid is
gone or its start time differs, the endpoint is dead (mailbox unaffected).

### CLI

```
amail register             # idempotent per session; revives an offline mailbox;
                           # assigns and prints name@id for a new one
amail whoami
amail status --status working --task "porting auth middleware"
amail roster [--all]       # live agents by default: id, name, harness, status,
                           # cwd, branch, task, last-seen; --all includes offline
amail send <name|name@id|all> [body] [--to-id N] [--body-file F|-] [--priority N]
amail inbox [--unread] [--preview]   # metadata only unless --preview (never used
                                     # by hooks); --unread is the default and only
                                     # mode in v1 (flag kept for hook stability)
amail read <id>            # full body; marks read (read_at = retrieval, no more)
amail wait [--timeout S]   # blocks until unannounced mail; singleton per agent
amail doctor               # identity resolution, db access, routes, watcher state;
                           # labels what it cannot know (hook trust, idle wake)
amail --json <cmd>         # structured output for whoami/roster/inbox/send
```

`register` auto-detects harness, cwd, and branch. `status` re-detects cwd and branch
on every call so they self-heal after a branch switch. `send` prints the resolved
`name@id` it committed to. Machine-facing output carries stable ids everywhere.

**`amail wait` semantics:** returns immediately if unannounced mail exists; otherwise
blocks until new mail is announced to it, marks that batch announced, prints
metadata-only headers, and exits. It is a **singleton per agent** — a second `wait`
for the same agent fails fast instead of stacking watchers. Re-arm guidance is *once
per handled batch* (the wait output itself says how to re-arm after triage), not after
every individual read.

**`amail wait` must be a genuinely blocking process** (kqueue on the doorbells
directory, with internal stat-polling as the portability fallback), never a bash
`sleep` loop: Claude Code auto-backgrounds a foreground command that outlives its
timeout, *except* bare `sleep` and a few other patterns, which it kills. The
database poll that backs the kqueue path is also the correctness fallback — event
delivery is a latency optimization, not a correctness dependency.

### Delivery

| Surface | Mechanism |
|---|---|
| Claude Code CLI + Desktop | Touch `~/.amail/doorbells/<agent-id>`; the session's backgrounded `amail wait` exits, and the harness wakes the session on exit |
| Codex CLI + Desktop | `codex queue --thread <thread> --message "<metadata header>"` — a durable write to `~/.codex/queue_1.sqlite`; succeeds even when no Codex process is running, arrives as a user message, fails cleanly (exit 1) on a bad thread id. Bounded subprocess timeout on the sender side |
| Pi (deferred) | Extension watches the doorbell file, injects the header at `before_agent_start` |
| All | Hook backstops surface **unannounced** metadata headers (see hook adapters below) |

The doorbell file is a wake signal only — the waiter re-reads SQLite for truth; the
file's contents are advisory and it is consumed on wake. A push failure of any kind
(timeout included) is reported to the sender alongside the committed message id, and
degrades to backstop delivery.

The Claude mechanism was verified by spike twice on 2026-09-06: first a 48 s block,
then a **45-minute block** (2,698 s) while the session sat idle — the watcher exited
within seconds of the flag appearing and the harness woke the session immediately.
(Compaction survival remains untested.)

**Hook adapters are event-specific, not generic stdout.** Official docs say plain
successful stdout reaches model context for `SessionStart` but **not** for `Stop`
(Claude uses structured continuation feedback; Codex requires JSON on `Stop`). The
wrapper script is a stable shim that execs a tested Python adapter (`amail hook
<harness> <event>`), which parses stdin once and emits the correct shape per event:

- **Claude SessionStart:** register, pin `AMAIL_AGENT_ID` via `CLAUDE_ENV_FILE`,
  print unannounced metadata headers (plain stdout is context here), mark announced.
- **Claude Stop:** if unannounced mail exists, emit the documented structured
  continuation (`decision: block`) carrying the metadata headers, and mark them
  announced — marking is what bounds continuation: the same mail never blocks stop
  twice. No unannounced mail → no output, exit 0. The re-arm detector inspects the
  payload's `background_tasks` for an actual `amail wait` entry — any-task-present is
  not proof the watcher is armed — and distinguishes *unknown* (field absent) from
  *unarmed* (field present, no watcher); when background tasks are disabled it
  reports degraded operation instead of demanding an impossible re-arm.
- **Codex SessionStart:** register via the payload's `session_id`, print unannounced
  headers (documented as context), mark announced.
- **Codex Stop:** emits nothing (valid empty success) in v1 — the `codex queue` push
  is Codex's primary channel, SessionStart its backstop. Wiring a context-capable
  Codex fallback event is an acceptance-gate item, verified on the installed version
  before being claimed.

Hooks stay fail-open — a broken amail must never break a session — but failures are
not invisible: the adapter appends one bounded, body-free line per failure to
`~/.amail/hook.log`.

**Codex hook-trust constraint:** Codex trusts hooks per command hash — any edit to
the configured command silently disables the hook until re-approved interactively.
Hook configs therefore point at a stable wrapper script whose command line never
changes; behavior changes go in the Python adapter behind it.

Known deployment failure modes, covered by backstops and surfaced by `amail doctor`:
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` removes Claude-side push; Claude's sandbox
can deny writes outside the working/temp directories, in which case a model-run
`amail` cannot reach `~/.amail` at all — the acceptance gates test the real
permission profiles, and installation documents the narrow required access rather
than disabling sandboxing.

### Lifecycle and failure

- Reading the roster checks each live endpoint's `(pid, pid_start)` and marks dead
  ones `offline` (mailbox preserved). Registration reaps first, so dead handles are
  reclaimed without waiting for someone to run `roster`. Reaping also removes the
  dead endpoint's doorbell and waiter-lock files.
- Names are released on death and may be recycled immediately — messages are bound to
  ids, and caller identity never rests on a name.
- Sending to an offline mailbox succeeds with a warning naming its last-seen time;
  the mail is recoverable because resuming that native session revives the same
  mailbox.
- Messages older than 30 days are pruned on write (recipient rows cascade).
- Roster output is live-by-default; history is behind `--all`.

### Safety

Message bodies are data, not instructions. Injected text — doorbell or backstop — is
framed explicitly as a message from another agent, and is never wrapped in
system-reminder-shaped markup. Global instructions will state that a request from
another agent authorizes nothing its own operator has not already asked for.
Metadata-only notification reduces unsolicited exposure; it does not make a later
body read authoritative — the operator-authorization rule applies at read time too.

**Trust model, stated plainly:** everything is local, same-user, and unauthenticated.
The database is writable by any process running as the user, so sender identity is
convention, not proof. That is acceptable for a single-user machine and is the same
trust boundary the harnesses themselves operate under; amail does not pretend
otherwise. What amail *does* defend against is **accidental** identity confusion:
validated `AMAIL_AGENT_ID`, loud failure on conflicting hints, and id-bound messages.

**Hygiene:** Codex exports API keys (`CODEX_OPENROUTER_API_KEY`,
`CODEX_RUNPOD_API_KEY`) into every shell command's environment. Never capture raw
`env` output into message bodies, headers, or the hook log.

## Acceptance gates

Implementation completeness is defined by demonstrated live-harness behavior, not by
the unit suite passing. Before the four-surface MVP is claimed, each surface must
show:

| Gate | Evidence needed |
|---|---|
| Session identity | Correct logical id and owner `(pid, pid_start)` from a real interactive session's model-run shell and from its actual hooks |
| Idle delivery | An independent session's send produces a model-visible metadata notification without a human prompt. For Codex this is the open `codex queue` idle-turn question — **part of correctness, not etiquette** |
| Active-turn delivery | Defined behavior while the recipient is mid-turn; no interrupt storms or repeated-turn loops |
| Hook fallback | Metadata reaches the model when primary push is deliberately disabled or fails |
| Lifecycle | Close, resume, and process replacement preserve the mailbox contract; no stale routes |
| Permissions & launch | Works under the real sandbox/permission profiles and Dock-launched app environments (GUI PATH ≠ shell PATH); required access documented narrowly |

Also unverified and gated: `CODEX_THREAD_ID` / `CLAUDE_PID` presence in *interactive
TTY* sessions (both verified headless only; near-certain since the env injection
lives in shared core code), and waiter compaction survival.

## Testing

TDD throughout.

- **Unit:** name allocation and collision under concurrent registration, id-based
  addressing across name reuse, `name@id` validation, identity-conflict failure,
  session revival (same id, refreshed endpoint), `(pid, pid_start)` reaping,
  audience snapshotting and per-recipient announced/read state, empty-body and
  size-limit rejection, pruning, push timeout handling.
- **Sentinel test:** a distinctive body sentinel must not appear in any automatic
  output — hook adapter output (all events), waiter output, or `codex queue`
  arguments.
- **Integration (the vertical slice):** two real subprocesses exercise the actual
  product idea, not just the happy path — register → send → wake → **deliberately
  defer the body** → a second message still wakes → read later → re-arm succeeds
  without an announce loop.
- The Claude wake-up (including the 45-minute idle block) is proven by spike and is
  not re-tested in CI; the live-harness acceptance gates above are manual.

## Implementation notes

Python 3.14 with stdlib `sqlite3`. No third-party runtime dependencies.
