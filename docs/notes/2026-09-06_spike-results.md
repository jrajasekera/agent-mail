# Spike results — design review and verification session

**Date:** 2026-09-06 (evening session, after the design was approved)
**Companion to:** [../plans/2026-09-06_agent-mail-design.md](../plans/2026-09-06_agent-mail-design.md)
and [session-notes.md](session-notes.md)

The design doc was reviewed, three spikes were run (one live in a Claude Code Desktop
session, two by Opus subagents), and the doc was revised. This file records what the
review flagged, what the spikes found, and which parts of the original design changed.
Everything below marked "verified" was tested on this machine during the session.

---

## 1. Review findings that prompted the spikes

1. **Session identity looked broken for the desktop apps.** The design keyed identity
   on "first harness-binary ancestor pid", and the concern was that all sessions inside
   one Electron app would collapse to one pid. (Turned out wrong in detail — see §2 —
   but the investigation surfaced a much better mechanism.)
2. **The 24h name cooldown did not match 30-day message retention.** Mail addressed to
   a departed `curie` on day 1 could land in a freshly named `curie` on day 2. Fixed in
   the design by stable agent ids resolved at send time; the cooldown is gone.
3. Smaller gaps: broadcast scope for late-registering agents, undefined priority
   semantics, implicit trust model, pid-reuse guard, `codex queue` turn semantics, and
   whether the background wait survives realistic idle periods.

---

## 2. Claude identity spike — verified

Run partly live in this Desktop session, partly by a subagent driving headless
`claude -p` with a clean environment and throwaway per-project hook settings (nothing
in `~/.claude` was modified).

- **Every Bash tool invocation carries `CLAUDE_CODE_SESSION_ID` (session UUID) and
  `CLAUDE_PID`.** Verified on Desktop (entrypoint `claude-desktop`, v2.1.260) and CLI
  headless (entrypoint `sdk-cli`, v2.1.263). Interactive TTY mode not driven, but the
  vars come from the same process bootstrap — near-certain.
- **Each Desktop session runs its own `claude` binary process** under
  `Claude.app → disclaimer helper → claude`. The feared Electron pid collision does not
  exist; the ancestry walk just needed to target the `claude` binary, not Electron.
  Moot anyway given the env vars.
- **The design premise "hooks cannot set an environment variable in the harness
  process" is false.** `SessionStart` hooks receive `CLAUDE_ENV_FILE`
  (`~/.claude/session-env/<session-id>/…`); appending `export AMAIL_NAME=einstein`
  there was verified to appear in every subsequent Bash call in that session. Hooks
  also get `session_id` in stdin JSON and `CLAUDE_CODE_SESSION_ID` / `CLAUDE_PID` /
  `CLAUDE_PROJECT_DIR` in env, and run as direct children of the `claude` process.
- **`Stop` hook payload includes `background_tasks: []`** — the backstop can detect an
  un-armed `amail wait` and prompt a re-arm.
- **Background-task behavior (docs-level):** foreground Bash defaults to 2 min timeout
  (max 10); on timeout the command is *auto-backgrounded*, **except** bare `sleep`,
  commands containing `git`, and unparseable compounds, which are killed. Hence the
  design rule: `amail wait` must be a real blocking process, not a `sleep` loop. In
  `-p` mode background commands die shortly after the final result; in interactive
  sessions, main-conversation background commands persist across turns.
  `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` disables the mechanism entirely.
- **Pid-reuse guard:** `ps -o lstart= -p <pid>` is stable across calls on macOS 25.5,
  1-second resolution. `(pid, lstart)` is a sound liveness key.

---

## 3. Codex spike — verified (codex-cli 0.153.4)

Run by a subagent against a throwaway `CODEX_HOME`; nothing in `~/.codex` was modified
and no message was sent to any existing session.

- **Thread id is free.** Every model-run shell command carries `CODEX_THREAD_ID` (and
  an identical `CODEX_SESSION_ID`). Hook payloads (`SessionStart`, `UserPromptSubmit`,
  …) carry `session_id` — the same id — which the existing herdr hook already consumes.
  The design's biggest open question is resolved: no thread-id handshake, no
  degradation to backstop-only. (Verified in `codex exec`; TUI/desktop near-certain —
  the env injection sits in shared core code next to the env-redaction list.)
- **`codex queue` needs no daemon.** It is a durable write to `~/.codex/queue_1.sqlite`
  (`queued_items`, per-thread ordering, revision-bump trigger). It succeeds with no
  Codex process running, delivers as a user message (`{"UserInput": …}` payload), and
  fails cleanly (exit 1, "no rollout found") on an invalid thread id. `--thread`
  accepts a UUID or exact session name.
- **The design doc's "Codex CLI shares a daemon with the desktop app" was wrong.**
  ChatGPT.app holds `~/.codex/ipc/ipc.sock` exclusively; a live CLI TUI does not
  connect to it (verified with lsof). What CLI and desktop share is the SQLite stores
  (`thread_history_1.sqlite`, `queue_1.sqlite`). Better for us: enqueue is a pure
  store write.
- **Unverified:** whether a queued message triggers an immediate turn on an idle
  interactive session or waits for a turn/message boundary. Binary strings ("could not
  start queued user input", sub-agent tool docs distinguishing "does not trigger a new
  turn" vs "trigger a turn if idle") suggest turn-on-idle with message-boundary
  delivery mid-turn. One manual test against a live TUI settles it.
- **Hooks: full coverage, stdout injection verified.** Events include `SessionStart`,
  `Stop`, `SessionEnd`, `UserPromptSubmit`, `PreCompact`/`PostCompact`, and more. Both
  plain stdout and structured `additionalContext` from a hook verifiably land in model
  context (token round-trip test).
- **Hook trust constraint.** Codex records trust per hook-file / event / command hash
  in `config.toml` (`trusted_hash = "sha256:…"`). An untrusted hook *silently does not
  run*, and any edit to the command invalidates trust. The amail hook must call a
  stable wrapper script so upgrades never touch the hook line.
- **Hygiene:** Codex exports `CODEX_OPENROUTER_API_KEY` and `CODEX_RUNPOD_API_KEY`
  into every shell command's environment. Raw `env` dumps must never end up in mail
  bodies or transcripts.

---

## 4. Long-idle doorbell spike — verified live

The original spike blocked 48 s. This session re-ran it at a realistic timescale, in a
Claude Code Desktop session:

```
watcher started  18:28   (background Bash, polling for a flag file)
writer armed     18:28   (detached nohup, sleep 2700 then touch flag)
flag written     19:13
watcher exited   19:13   — blocked 2,698 s (~45 min) while the session sat idle
session woken    19:13   — task notification arrived immediately, output readable
```

The mechanism holds for realistic idle periods, not just seconds. Compaction survival
was not exercised (no compaction occurred in the window) and remains an inference from
the tasks living in the bash layer rather than the context window.

---

## 5. What changed in the design doc

1. **Session identity rewritten.** Env vars (`CLAUDE_CODE_SESSION_ID` / `CLAUDE_PID` /
   `CODEX_THREAD_ID`) are primary; `SessionStart` hook registration via
   `CLAUDE_ENV_FILE` pins `AMAIL_NAME`; process-tree walk survives only as the Pi
   fallback; liveness keys on `(pid, pid_start)`.
2. **Name cooldown replaced by stable agent ids.** Messages address ids, resolved from
   names at send time; names recycle freely; misrouting impossible by construction.
3. **Broadcasts scoped by registration time** (`created_at` comparison), so new
   sessions do not inherit old room-wide mail.
4. **Priority defined:** 0–2, default 1, purely advisory.
5. **Codex surface corrected:** shared SQLite stores, not a shared daemon; `codex
   queue` documented as durable, daemon-free, cleanly-failing.
6. **`amail wait` implementation constraint:** real blocking process, never a `sleep`
   loop (the auto-background exemption kills bare `sleep`).
7. **Codex hook-trust wrapper rule** and the `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`
   failure mode recorded.
8. **Trust model stated explicitly:** local, same-user, unauthenticated; sender
   identity is convention.
9. **`amail read` prints a re-arm reminder**, softening the re-arm-discipline risk
   alongside the `Stop`-hook `background_tasks` check.
10. **Open questions replaced:** the Codex thread-id question is resolved; what
    remains are two one-off manual checks (queue turn semantics; env vars in
    interactive TTYs), neither blocking implementation.
