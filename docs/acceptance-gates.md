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

- (append dated entries here)
