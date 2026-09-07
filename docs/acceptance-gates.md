# agent-mail acceptance gates

Manual live-harness verification. The four-surface MVP is complete only when
every row records a pass for every claimed surface. Record date, harness
version, and outcome inline. Unit tests passing is not a substitute.

| # | Gate | Procedure | Pass condition |
|---|---|---|---|
| 1 | Interactive identity | In a real interactive session (not headless), run `env \| grep -E 'CLAUDE_CODE_SESSION_ID\|CLAUDE_PID\|CODEX_THREAD_ID'` then `amail register && amail whoami` | Vars present; whoami shows the right harness and a live `(pid, pid_start)` |
| 2 | Hook registration | Wire the wrapper per docs/install.md; open a fresh session; run `amail whoami` with no prior register | Registered via SessionStart; `AMAIL_AGENT_ID` pinned (Claude) |
| 3 | Idle delivery (Claude) | Session B idle with `amail wait` armed as a background task; send from session A; wait ≥5 min idle first | B wakes without human input; output is metadata only |
| 4 | Idle delivery (Codex) | Session B idle; send from A (codex queue path) | **Open question resolved here:** does an idle Codex session start a turn? Record observed behavior — this is correctness, not etiquette |
| 5 | Active-turn delivery | Send while the recipient is mid-task | No interrupt storm; message surfaces at a boundary or via backstop |
| 6 | Stop backstop (Claude) | With unannounced mail and no watcher, end a turn | Structured block fires once with metadata; second stop does not re-fire |
| 7 | Fallback on disabled push | `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`; send to that session | Mail surfaces via hooks; `amail doctor` explains the degradation |
| 8 | Resume/lifecycle | Kill a session's process; send to it (offline warning); resume the same native session | Same mailbox id revived; queued mail in `amail inbox` |
| 9 | Sandbox/permissions | From each harness's real permission profile, run `amail register/send/read` | `~/.amail` reachable; if denied, document the narrow allowance needed — do not disable sandboxing |
| 10 | Dock-launch PATH | Trigger hooks in Dock-launched Claude Desktop / ChatGPT.app | Wrapper finds `amail` (absolute paths, not shell PATH assumptions) |
| 11 | Codex hook trust | Install the codex hook; approve trust; edit the wrapper SCRIPT (not the config line); trigger again | Hook still runs (trust hash intact) |

Results log:

### 2026-09-06 — Claude Code CLI (interactive, this machine)

Harness: Claude Code CLI, real interactive session. amail 0.1.0, installed with
`uv tool install`. Hooks wired into `~/.claude/settings.json` (SessionStart +
Stop) and `~/.codex/hooks.json` (SessionStart + Stop) on this date; the previous
configs are backed up as `settings.json.bak-amail` / `hooks.json.bak-amail`.

| # | Gate | Surface | Result |
|---|---|---|---|
| 1 | Interactive identity | Claude CLI | **PASS.** `CLAUDE_CODE_SESSION_ID` and `CLAUDE_PID` both present in an interactive TTY session; `amail whoami` → `dalton@1 claude working`; `ps -o lstart=` on `CLAUDE_PID` matched the stored `pid_start` of a live `claude` process. |
| 3 | Idle delivery | Claude CLI | **PASS (short-idle).** `amail wait --timeout 300` armed as a background task; an independent identity sent a priority-2 message; the watcher exited within a second, the harness notified the session with no human input, and the output was metadata only. The ≥5-minute-idle and 45-minute variants remain covered only by the 2026-09-06 spike. |
| 6 | Stop backstop | Claude CLI | **PASS (live).** With unannounced mail and no watcher armed, ending a turn fired the wired hook: the session was blocked from stopping and the metadata header plus the unarmed-watcher note reached the model's context — no body content. The mail was marked announced by the same call, so the following stop did not re-fire, and the message stayed unread until `amail read` was run deliberately. Rehearsed separately against the adapter with a synthetic payload, same shape. |
| 8 | Resume/lifecycle | shell identity | **PASS.** A session was registered, its process died, `amail roster` reaped it to `offline`; a send reported the offline warning with its last-seen time and still committed; re-registering the same `AMAIL_SESSION_KEY` revived the same id (`volta@2`) with the queued mail in `amail inbox`. |
| 10 | Dock-launch PATH | wrapper only | **PASS (simulated).** The wrapper was invoked with `env -u PATH`; it resolved `~/.local/bin/amail` and registered the session. A real Dock-launched Claude Desktop / ChatGPT.app run still needs checking. |

### 2026-09-07 — Claude Code CLI (fresh interactive session, this machine)

Harness: Claude Code CLI 2.1.263, real interactive session started *after* the
2026-09-06 hook wiring. amail 0.1.0 (`uv tool install`).

| # | Gate | Surface | Result |
|---|---|---|---|
| 2 | Hook registration | Claude CLI | **PASS.** A fresh session ran no `amail register`; `amail whoami` immediately returned `avogadro@6 claude working`, and `env \| grep AMAIL_AGENT_ID` showed `AMAIL_AGENT_ID=6`. The `agents` row confirms the hook did it: `session_key = claude:dbcd7e5c-…` (this session's id) with `created_at == last_seen == 2026-09-07T03:19:53Z` — created at session start, never touched by a manual register. Pinning works via `hooks.py` appending `export AMAIL_AGENT_ID=<id>` to `$CLAUDE_ENV_FILE`; that variable is set only for the hook process, so the pin is observable in the session but not re-derivable from the shell. `~/.amail/hook.log` does not exist, i.e. the hook logged no failures. The SessionStart matcher is `''` (all sources), so this fired on a `clear`-sourced start; `startup`- and `resume`-sourced starts use the same hook entry but were not separately observed. |

Not yet run, and why:

- **Gate 4 (Codex idle delivery)** — needs a live interactive Codex session, and
  queueing into one would inject a message into the operator's own session.
  Partial finding, worth noting: `codex queue --thread <unknown> --message …`
  (codex-cli 0.153.4) fails cleanly with exit 1 and `Error: No active session
  found matching '…'`. That is the clean-failure behavior amail relies on, but it
  also suggests `codex queue` may require an **active** session, not merely a
  known thread id — the design's "succeeds even when no Codex process is running"
  needs re-verification before Codex delivery is claimed for offline sessions.
- **Gates 5, 7, 9, 11** — need a second live session mid-turn, a session started
  with `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, each harness's real sandbox
  profile, and an interactive Codex trust approval, respectively.
- **Claude Desktop, Codex CLI, Codex Desktop** — no gate has been run on those
  three surfaces yet. The MVP claim covers Claude Code CLI only until they are.
