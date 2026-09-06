# Session notes — how we arrived at the agent-mail design

**Date:** 2026-09-06
**Companion to:** [../plans/2026-09-06_agent-mail-design.md](../plans/2026-09-06_agent-mail-design.md)

The design doc says *what* we are building. This one says *how we got there* — what was
evaluated, what was measured, which assumptions broke, and why each decision changed.
Everything marked "verified" was tested on this machine during the session; everything
else is labelled as unconfirmed.

---

## 1. The starting question

The original goal: install Smoo's **Agent Mail** (`th mcp serve`) globally so agents in
five surfaces could talk to each other — Codex CLI, Codex Desktop, Claude Code CLI,
Claude Code Desktop, and Pi.

Answer, after investigation: partly possible, but it would not have worked well. Three
separate problems, described below. Each one pushed the design further from "install a
product" and closer to "own a small tool".

---

## 2. Evaluating Smoo Agent Mail (`th`)

`th` 0.41.4 was already installed on this machine, so everything here was measured
rather than read.

**What worked.** The bus itself is real and simple. Registering two handles, sending a
typed message, reading an inbox, and listing presence all worked against
`~/.smooth/mail.db` with no account and no network. The test database was deleted
afterwards.

`th mcp install --harness all --dry-run` writes exactly three files: `~/.claude.json`,
`~/.codex/config.toml`, `~/.config/opencode/opencode.json`.

**Problem 1 — identity does not persist across MCP calls.** Verified by driving the
stdio server directly:

```
agent_identity {name: "carol"}  →  "You are now `carol`"
mail_inbox {}                   →  ERROR: No agent handle
mail_inbox {agent_id: "carol"}  →  works
```

The claim does not stick. Every subsequent call must re-pass the name from the model's
memory. The documented alternative, `$SMOOTH_AGENT_HANDLE` in the MCP config's `env`, is
static per config file — so every Claude Code session on the machine would share one
mailbox. With nothing set, the CLI falls back to `user@host`, identical for every
session.

**Problem 2 — no push.** Polling only. An agent sees mail when it decides to call
`mail_inbox`, and a session idling at a prompt never will.

**Problem 3 — tool bloat.** `th mcp serve` exposes **23 tools, ~21.5 KB of JSON
(~5.4k tokens)**, measured from a real `tools/list` response. Only 6 are mail; the rest
are Smoo org tools (`ask_business`, `observability_llm_cost_breakdown`,
`operator_tools_set`, …) that are useless here and would not authenticate. There is no
flag to serve a subset — `th mcp serve --help` accepts only `--profile`.

**Harness coverage.** Claude Code CLI and Desktop share `~/.claude.json` (confirmed: the
desktop session's own MCP server list matched that file). Codex CLI and Desktop share
`~/.codex/config.toml` (confirmed: an existing entry points into
`/Applications/ChatGPT.app`). **Pi has no MCP support at all** — its own docs state it
"intentionally does not include built-in MCP", and the only `mcpServers` strings in its
bundle come from the vendored Google GenAI SDK.

**Upstream.** `SmooAI/smooth` is MIT, Rust, genuinely active (multiple releases per day
in late August 2026), but has 7 stars and 0 forks. Local mode needs no account; whether
the binary phones home in sqlite mode was **not verified** — the binary contains
`api.smoo.ai` and `llm.smoo.ai` strings, but string presence is not a call.

**The finding that mattered most:** `th agent` / `th msg` work fine from the CLI, with
no MCP at all. Three calls timed end to end at **63 ms**. That is harness-agnostic by
construction, costs zero context, and sidesteps the identity bug entirely — because the
CLI does honour `$SMOOTH_AGENT_HANDLE` even though the MCP path forgets it.

This is where the design turned: **MCP was the wrong layer, not `th` specifically.**

---

## 3. Evaluating the alternatives

**agent-bus** — dismissed by the user as excessive for the scope (no swarms or teams
wanted).

**AgentWorkforce/relay** — far more mature than `th`: 812 stars, 64 forks, ~4,867
commits, 15+ contributors, Apache-2.0, releases days old. Rejected anyway, for
structural reasons:

- **32+ MCP tools** (channels, threads, reactions, files, search, spawn/broker
  management) — roughly 7× the context cost, for a feature set explicitly out of scope.
- **Cloud-first.** The MCP server defaults to `RELAY_BASE_URL =
  https://cast.agentrelay.com`; self-hosting the backend is not documented.
- **The broker cannot wrap the desktop apps.** Relay's model is a local Rust broker that
  *spawns* the agent CLIs with injected config (`--mcp-config` for Claude, `--config`
  for Codex), and its best push mechanism is PTY injection into a process it owns. The
  desktop apps are launched from the Dock. Documented harnesses are Claude, Codex,
  Cursor, OpenCode, Gemini/Droid; desktop apps and Pi are not mentioned.

One thing relay does better than `th`: `register_agent` stores identity in the MCP
session, so later calls need not re-pass it. Moot for us once we left MCP.

**One relay detail became a design rule.** Its broker injects incoming messages wrapped
in `<system-reminder>` tags — another agent's prose arriving dressed as harness-level
instruction. Smoo's docs go the other way and warn that message bodies are untrusted
input. We adopted the stricter position: injected text is always framed as a message
from another agent, never as system-shaped markup.

**Why all three struggled**, stated plainly: two of the five surfaces are GUI apps that
no agent framework can spawn. That is not something a better framework fixes.

---

## 4. Finding the push mechanisms

If MCP is out, delivery has to come from each harness's own machinery. What exists,
verified by inspection:

| Surface | Mechanism | Status |
|---|---|---|
| Codex CLI + Desktop | `codex queue --thread <t> --message` over `~/.codex/ipc/ipc.sock` | Verified: ChatGPT.app holds that socket; CLI and Desktop share one app-server daemon |
| Claude Code CLI in herdr | `herdr agent prompt <target> <text>`; `herdr agent list` for presence | Verified in CLI help |
| Claude Code Desktop | in-app `send_message` — but only callable *from* another desktop session | Verified: the tool exists in this session |
| Pi | extension API: `before_agent_start` can inject a message; dynamic tools supported | Verified in pi's docs |
| Claude + Codex | hooks (`SessionStart` / `PostToolUse` / `Stop`), same JSON shape in both | Verified: both `~/.claude/settings.json` and `~/.codex/hooks.json` already use them |

**The asymmetry that shaped the design.** Codex is reachable from outside; **Claude
Desktop is not**. Checked its processes and open files: Electron, no listening TCP
socket, no unix socket, no IPC file. Nothing external can inject into a running session.

Also noted: presence for herdr panes is already solved by the existing
`METADATA_CONTRACT.md`, which explicitly designs `title`/`tokens` as an agent-to-agent
channel. The gap was always the two desktop apps, which never appear in herdr.

---

## 5. The spike that closed the gap

Hypothesis: a Claude session can start a **background command that blocks until mail
arrives**, and the harness re-invokes the agent when a background command exits — making
a blocking `wait` into a real doorbell.

Test: a background watcher polling for a flag file, plus a delayed writer creating that
flag ~45 s later — deliberately after the turn ended, so the session was idle and the
user away, which is the condition that matters.

```
watcher blocked 48s while the session sat idle
flag written   17:31:40
watcher exited 17:31:41   ← 1s poll interval
session woken  17:31:41   ← notification arrived, output readable
```

**It works.** Three consequences:

1. **The wake-up carries no payload.** The notification says only that a background
   command completed; the text lives in an output file the agent reads. The harness
   enforces the metadata-only doorbell rather than us relying on discipline.
2. **It re-arms manually.** Once fired, the watcher is gone. This is the fragile part,
   and the reason the hook backstop stays in the design — a forgotten re-arm degrades to
   breakpoint delivery, never to silence.
3. **It works in Claude Code CLI too**, which removed the need for `herdr agent prompt`
   and collapsed the routing table from four mechanisms to two (plus pi's extension).

---

## 6. Why we build our own rather than wrap `th`

The user raised this, and investigation supported it. The deciding argument is
**routing metadata**: delivery requires knowing *how* to reach each agent — a Codex
thread id, a doorbell path, a session pid. `th agent` has nowhere to store that. We
would shadow its roster in a second file, at which point `th` is only a message table we
did not write.

Supporting reasons: we would use ~5 of its ~20 commands; its identity model already
fights us; it ships a paid cloud tier and unaudited endpoints; and it releases multiple
times a day underneath our hooks.

Accepted costs, stated honestly: we own concurrent SQLite access (WAL +
`busy_timeout`), liveness and reaping, and hook latency discipline. Small, and the
runtime suits it — Python 3.14 with stdlib `sqlite3`, no dependencies.

---

## 7. Design decisions and what changed them

Each of these moved from my initial proposal to something better because of a specific
objection.

**Delivery semantics — user's improvement.** I offered breakpoint-only, urgent-push, or
always-push. The user proposed a fourth: always push immediately, but the push carries
only *notice* of a message and its priority; the agent reads the body when it chooses.
This separates interruption from context injection and closes the injection path relay
left open. Adopted wholesale.

**Naming — user's objection.** I recommended auto-derived `<harness>:<repo>:<branch>`
handles. The user pointed out that an agent can switch branches mid-session, making the
name lie. Replaced with random static scientist names plus separate mutable `cwd` /
`branch` / `status` / `task` fields, re-detected on every `status` call. A name that
lies is worse than one that is opaque.

**Scope — YAGNI, confirmed.** The GPU and merge examples could have justified a
resource-claim primitive with leases and expiry. Rejected in favour of messaging plus a
status line. If we hand-roll the same lock repeatedly, we will add it then, knowing its
real shape.

**Delivery architecture — no daemon.** Sender-side routing: `send` writes the row, looks
up the recipient's route, rings the doorbell inline. A daemon would add retries and
future cross-machine relay, at the cost of a process to supervise and the classic
"why didn't my agent get the message" failure of a silently dead daemon. The row is
committed before any push, so failure is latency, not loss.

**Session identity — pid ancestry.** Hooks cannot set environment variables in the
harness process, so the usual env-var handshake is unavailable. Walking up the process
tree to the first harness-binary ancestor gives a stable per-session key for free, and
liveness with it — which is what `th` needed an explicit `--pid` flag for.

**Name and location.** `~/source/agent-mail`, CLI `amail` (avoiding the existing `mail`
binary on macOS).

---

## 8. Open questions and known risks

- **Codex thread id — unconfirmed.** Whether a Codex session can learn its own thread id
  (most likely from the `SessionStart` hook payload). If it cannot, Codex degrades to
  hook-backstop delivery only. This is the one open item that could change a design
  decision rather than an implementation detail, and it should be resolved first.
- **Watcher re-arm is discipline-dependent.** Mitigated by the hook backstop, not
  eliminated.
- **`th` telemetry in local mode — unverified.** Only relevant if we ever reconsider it.
- **The wake-up costs a turn.** Fine while mail is rare; worth revisiting if it is not.

---

## 9. Rejected alternatives, in one place

| Option | Why not |
|---|---|
| `th mcp serve` (Smoo Agent Mail over MCP) | 23 tools / ~5.4k tokens per session; identity does not persist across calls; poll-only |
| `th` CLI as the store, with our own routing beside it | Its agent record cannot hold routing metadata; we would shadow its roster anyway |
| agent-bus | Excessive for the scope |
| AgentWorkforce/relay | 32+ tools; cloud-first with no documented self-hosting; broker cannot wrap the desktop apps; injects messages as `<system-reminder>` |
| Auto-derived handles from repo/branch | Branch can change mid-session, making the name lie |
| Resource-claim primitive (GPU/main locks) | Third concept plus expiry logic; status line covers the real cases |
| Delivery daemon | A process to supervise; silent death is the worst failure mode here |
| Pull-only (every agent watches its own mailbox) | Codex and Pi have no equivalent of the background-watcher wake |
