# Codex identity fixes — amail-3um and amail-eec

**Date:** 2026-09-07
**Branch:** `codex-identity-fixes`
**Source:** defects found running acceptance gate 4 (see docs/acceptance-gates.md,
2026-09-07 Codex CLI entry).

## The two defects

**amail-3um (P1) — a Codex session writes to its parent Claude session's mailbox.**
`identity.resolve` checks `CLAUDE_CODE_SESSION_ID`/`CLAUDE_PID` before it checks
`CODEX_THREAD_ID`, and it never looks at the harness the hook was *invoked as*. A Codex
process that inherits a Claude session's environment therefore resolves as claude, and
`amail hook codex sessionstart` silently updates the parent's agent row. Observed live:
`avogadro@6` had its `cwd` rewritten to the Codex scratch dir, and no Codex mailbox was
created. Silent — no error, no `hook.log` line.

**amail-eec (P2) — a Codex session is unaddressable until its first turn.** On
codex-cli 0.153.4 the thread id does not exist when the TUI launches; SessionStart
fires when the first turn creates the thread. A freshly opened, never-prompted Codex
session has no mailbox and does not appear in `amail roster`.

## Decisions

**3um: the hook's harness argument is authoritative.** `amail hook codex sessionstart`
already *knows* it is Codex. Identity resolution invoked from a hook must not be free to
disagree with that. Two parts:

1. `identity.resolve(env, expect_harness=None)`. When `expect_harness` is given, the
   probe for that harness is tried *first*, so a Codex hook with both `CLAUDE_*` and
   `CODEX_THREAD_ID` present resolves as Codex rather than Claude.
2. A post-check: if the resolved harness still differs from `expect_harness`, raise
   `IdentityConflict`. This deliberately reuses the existing contract — *"a conflict
   between them raises, it does not pick a winner"* (AGENTS.md) — rather than inventing
   a new precedence rule.

`hooks.run_hook` passes its `harness` argument down. Because hooks are fail-open, the
raise surfaces as one bounded line in `~/.amail/hook.log` and exit 0. Loud beats silent
corruption; the session keeps working either way.

Note this makes the ancestry fallback safe too: `walk_to_harness` finding a `claude`
process while running as a Codex hook now raises instead of adopting that identity.

**eec: document it and make `amail doctor` say it.** Chosen over re-keying a provisional
mailbox. The thread id genuinely does not exist yet — there is nothing to bind to — and
a provisional-then-re-keyed mailbox would need merge logic for two rows, which is the
one thing the "messages bind to agent ids" contract exists to prevent. With the 3um fix
in place, a Codex SessionStart with no thread id already fails loudly rather than
hijacking, so the remaining gap is purely one of explanation.

## Plan

1. **`identity.py`** — add `IdentityConflict`; refactor `resolve` into ordered probes;
   add the `expect_harness` parameter and post-check.
2. **`registry.py`** — `register()` and `current_agent()` take and forward
   `expect_harness`.
3. **`hooks.py`** — `run_hook` passes its `harness` through to both.
4. **`doctor.py`** — when running under Codex with no thread id, say the session has no
   mailbox until its first turn, and why. Keep the honest "cannot verify here" style.
5. **Docs** — design doc Delivery table + `docs/install.md`: state the Codex
   first-turn limitation, and that a Codex session launched from another agent's
   environment must have `CLAUDE_*`/`AMAIL_*` scrubbed.

## Tests (TDD — each written failing first)

- Codex hook with `CLAUDE_CODE_SESSION_ID` + `CLAUDE_PID` + `CODEX_THREAD_ID` in env
  registers a **codex** mailbox and leaves the Claude row untouched.
- Codex hook with `CLAUDE_*` present and **no** thread id raises `IdentityConflict`
  (and, through the hook, exits 0 and writes one `hook.log` line naming neither body
  nor env contents).
- Claude hook with `CLAUDE_*` still resolves as claude — no regression.
- `resolve(env)` with no `expect_harness` keeps its current behavior exactly.
- `doctor` under Codex-with-no-thread-id emits the first-turn explanation.

## Out of scope

`amail-a1w` (does `codex queue` reach a *stopped* thread) is untouched — it needs a
separate live experiment.
