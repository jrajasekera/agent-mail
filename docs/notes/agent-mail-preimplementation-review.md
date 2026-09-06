# agent-mail: pre-implementation review

**Review date:** September 6, 2026  
**Verdict:** Keep the architecture and scope, but revise the behavioral contracts and implementation plan before executing it unchanged.

## Scope and evidence

I reviewed all four supplied documents:

- **D:** `2026-09-06_agent-mail-design.md` — the revised design.
- **I:** `2026-09-06_agent-mail-implementation.md` — the implementation plan, including its proposed code and tests.
- **S:** `2026-09-06_spike-results.md` — subsequent verification and corrections.
- **N:** `session-notes.md` — decision history, not the current specification where S and D supersede it.

The review preserves the intended product: local presence and messaging for independent coding agents, not a swarm framework, orchestration service, or resource-lock manager.

For additional verification, I extracted the plan's Python modules, composed its prescribed CLI additions, and ran the supplied Python module/CLI tests in an isolated environment. **All 43 extracted tests passed.** Nine targeted counterexamples then reproduced behaviors missing from those tests; their results are summarized below. These checks used **Python 3.13.5 on Linux**, not the target Python 3.14/macOS installation. I did not run the macOS harnesses, the zsh hook tests, the plan's separate installed-console-script integration test, or any live queue/hook acceptance test. No user harness settings or real mailboxes were modified.

I also checked current official Claude Code and Codex documentation for the hook contracts and Claude's sandbox behavior. Those external findings are identified separately from the supplied spike evidence. The supplied spikes remain evidence about the versions and configurations actually tested on your machine; this review does not independently reproduce them.

## Overall assessment

The core design is appropriate for the stated use case. I would retain the CLI-first interface, a single local SQLite mailbox, stable internal agent IDs, commit-before-notification, best-effort sender-side routing, and the separation between metadata notifications and explicitly fetched bodies. I would also retain the explicit non-goals. Nothing found here justifies adding a cloud service, generic broker, distributed lock manager, or large MCP surface. [D: Purpose; Decisions and rationale]

The implementation plan is less ready than the architecture. Its important weaknesses are notification semantics, identity/lifecycle behavior, and harness adapter contracts. Several tests validate what the sample code does rather than the stronger behavior promised by the design.

The most important distinction to make explicit is:

> Persisted mail, notification delivery, body retrieval, and acting on a request are four different events.

A successful `send` should not imply that the destination has awakened, read the body, or agreed to do anything.

## 1. P1 — Separate unread mail from unannounced mail

**Affected:** I Task 8, `waiter.wait`; D metadata-only push and CLI sections.

The waiter returns immediately whenever `mail.unread()` returns anything. It does not remember that a message has already been announced. Yet the design expressly allows the recipient to defer reading a body.

A deterministic reproduction sent one message, called `wait`, left the body unread, and called `wait` again. Both calls returned message 1 immediately; together they took approximately 0.3 milliseconds in the review environment. No second message arrived.

That creates a problematic interaction: the notification wakes Claude, Claude chooses to defer reading, Claude re-arms, and the existing unread message immediately completes the new waiter. It can cause repeated notifications or turns. The instruction to re-arm after each individual `read` also encourages extra waiters when several messages arrive together.

**Recommended change:** Track announcement/notification acknowledgment separately from `reads`. A per-agent notification cursor or per-delivery announcement state is sufficient; this does not require a workflow engine. Define exactly when an announcement is considered acknowledged, permit duplicate notices after a crash, and retain the unread row independently. Do not claim exactly-once model receipt.

Use one waiter per active Claude endpoint. Re-arm once after handling or explicitly deferring a notification batch, not after every body read. An already announced, deliberately unread message must not cause a busy re-arm loop. A genuinely new message must still wake the recipient while older messages remain unread.

Treat notification state as bookkeeping, not permission to discard the underlying message. The fallback path must remain able to expose durable unread mail after notification failures or interrupted turns.

**Acceptance tests:** Deferral and re-arm, two-message batches, a new message arriving while old mail is unread, duplicate waiter starts, and a crash at each notification/acknowledgment boundary.

## 2. P1 — Remove body previews from every automatic backstop

**Affected:** I Task 6, `Header`, `mail.unread`, and CLI `inbox`; I Task 9 hook wrapper; D CLI and Safety sections.

The design promises metadata-only notifications and says inbox triage does not pull another agent's prose into context. However, `Header.preview` contains the first 80 characters of the first body line, `inbox` prints it, and both hook adapters invoke `amail inbox --unread`.

The review reproduction put `BODY_SENTINEL_NOT_METADATA` at the start of a message body. Running the exact inbox command used by the hook printed the sentinel.

Thus the normal doorbell formatter is metadata-only, but the fallback formatter is not. At hook events that actually inject stdout, the message's first line is injected automatically. Calling the first line a header does not change its trust level. [D: Push is immediate; CLI; I Tasks 6 and 9]

**Recommended change:** Make the default header strictly structured metadata: message ID, sender ID and handle, priority, and creation time. Keep body previews behind an explicit opt-in command/flag that no automatic hook uses. Use one shared metadata formatter for all notification paths.

Add a distinctive body sentinel test covering SessionStart output, Stop output, waiter output, and Codex queue arguments. Check the entire automatic output, not merely `routing.header_line()` in isolation.

Metadata-only notification reduces unsolicited exposure. It does not make a later body read safe to treat as operator authorization. Keep the design's rule that another agent's request cannot authorize actions the receiving operator has not already authorized.

## 3. P1 — Implement event-specific hook output, not a generic stdout backstop

**Affected:** I Task 9, `hooks/amail-hook.sh`; installation instructions; D Delivery section.

The wrapper prints plain inbox text and exits zero for both SessionStart and Stop. The tests check script stdout and return codes, not whether the harness delivers that output to the model. [I Task 9]

**External documentation check:** Claude Code's current documentation says plain successful stdout reaches context for selected events, including SessionStart, but not Stop. Stop uses structured continuation feedback. Codex documents SessionStart plain stdout as context, but says Stop expects JSON and considers plain output invalid. [E1, E2]

The spike report says hook-output injection was verified generally; it does not record a Stop-specific token round trip that resolves this discrepancy. Treat this as a documented adapter-contract mismatch until tested on the installed versions, rather than silently overriding either the spike or the current docs. [S: Codex spike]

**Recommended change:** Keep the stable wrapper path, but put the event adapter in a tested Python handler. Parse input once and emit the appropriate event-specific response. Do not mix human-readable inbox lines and a JSON response on the same stdout stream.

For Stop, make continuation deliberate and bounded. Use notification state and the hook's continuation guard rather than emitting the same reminder forever. Where breakpoint-only behavior is intended, consider a verified context-capable event such as UserPromptSubmit rather than assuming Stop stdout works. No future hook event means no guaranteed future fallback delivery.

The current re-arm detector also mistakes any background task for an armed mail watcher: it only checks whether the array is empty. A running build with no waiter produces a false healthy result. Identify the actual watcher and endpoint, and distinguish an unknown task registry from a confirmed healthy watcher. When background tasks are disabled, report degraded operation rather than repeatedly asking for an impossible re-arm. [I Task 9; S Claude identity spike]

## 4. P1 — Define mailbox identity separately from a running process

**Affected:** I Tasks 3–5, `identity.resolve`, `registry.register`, `current_agent`, and reaping; I Task 6 offline send.

The code has no consistent resumed-session contract.

When a live row already has the same session key, `register()` refreshes only `last_seen`. It does not update `pid`, `pid_start`, or routing metadata. A reproduction re-registered the same session with a replacement process identity and then reaped it: the record still referred to the old process and was incorrectly marked offline.

Conversely, after a record is already offline, re-registration creates a new agent ID. Mail accepted for the old ID remains attached to that old record. In a reproduction, the send reported “queued anyway,” but resuming the same session produced a new ID with an empty inbox; the old ID still held the unread message. The CLI's current-agent lookup does not provide a recovery path into that offline mailbox. [I Tasks 4–6]

**Recommended contract:** For harnesses with durable native session IDs, prefer a logical mailbox keyed by `(harness, native_session_id)` and a separately refreshed runtime binding. Preserve the logical mailbox across a supported resume; refresh the current PID/start identity and route. Explicitly decide what happens when the same conversation is open through two clients.

This need not require a complex activation service or several new tables. It requires a clear invariant and transactionally correct transitions. A process being alive is also not proof that its thread is loaded or able to receive an immediate turn.

An alternative is to make every activation a new mailbox. That is simpler in some respects, but then offline sends should normally fail rather than promise queued delivery, unless a separate explicit recovery mechanism exists.

Do not expire an otherwise live but idle Claude endpoint merely because it has not called `status`; your own successful idle spike is a reason to avoid an activity timeout masquerading as process death.

## 5. P1 — Do not use a recyclable handle as authoritative identity

**Affected:** D stable IDs and session identity sections; I Task 4 `current_agent`; I Task 9 `AMAIL_NAME` export; CLI addressing.

Using agent IDs in stored messages solves an important problem: previously committed mail does not move when a name is recycled. It does not solve every identity problem.

`current_agent()` gives `AMAIL_NAME` precedence over the native session identity. A deterministic reproduction registered an agent as `curie`, marked it offline, reused `curie` for another session, and resolved the original environment with its pinned name. It resolved to the replacement agent's ID.

The reproduction deliberately exercised name reuse; it does not assert that every ordinary shutdown causes a stale live process. However, incorrect reaping, resume, and inherited environment values make the behavior worth preventing. This is an accidental-identity problem even within the explicitly unauthenticated same-user trust model.

There is a second, distinct case: an agent remembers `curie`, that agent departs, and a later bare-name send resolves to the replacement. That is consistent with the plan's name-at-send-time rule, but weaker than “misrouting is impossible.”

**Recommended change:** Pin a stable `AMAIL_AGENT_ID` or the canonical native-session binding; use a handle only for display. Validate conflicting identity hints instead of silently selecting a different session. Expose stable destination addressing, for example `amail send --to-id 42 ...` or a checked `curie@42` form, and display IDs in the roster and notices.

Namespace native session keys by harness. Add tests for inherited parent variables and define whether native subagents intentionally share their parent's mailbox. Do not let precedence accidents decide that policy.

## 6. P1 — Make registration atomic under actual concurrency

**Affected:** I Tasks 1, 2, and 4, especially `names.allocate` and `registry.register`.

Registration checks for a live session, reads the set of taken names, chooses one, and inserts it without a transaction covering that read/choose/write decision. Unique indexes protect integrity but do not make the operation successful or idempotent under concurrency.

Two controlled interleavings reproduced the missing cases:

| Concurrent case | Observed outcome |
|---|---|
| Two different sessions choose the same currently free name | One registration succeeds; the other raises `IntegrityError` on `agents.name` |
| Two calls register the same session after both observe no existing row | One succeeds; the other raises `IntegrityError` on `agents.session_key` |

The plan's test named `test_two_connections_can_write_concurrently` actually commits connection 1 before writing with connection 2. It does not test overlapping transactions. [I Task 1 tests]

**Recommended change:** Use a short explicit transaction, such as `BEGIN IMMEDIATE`, around session lookup and name allocation/insertion, with bounded busy/conflict handling. SQLite documents that `BEGIN IMMEDIATE` starts the write transaction immediately and can still encounter another writer; a busy timeout alone is not an application-level concurrency strategy. [E3]

Avoid running slow process inspection or Git subprocesses while holding the writer lock. Define transaction ownership at the operation boundary rather than allowing helpers to commit unexpectedly.

Registration also needs a way to reclaim confirmed dead handles without depending on somebody first running `roster` or `send`; otherwise dead records can exhaust the finite name pool.

## 7. P1/P2 — Use one authoritative broadcast recipient set

**Affected:** D broadcast scoping; I Task 6 `mail.send`, `unread`, and `read`.

The broadcast push target list and inbox eligibility are computed differently. The sender selects live targets, then later inserts a message with a timestamp. Inbox membership is based on registration time.

A deterministic scheduling reproduction registered another agent between those steps. The new agent was eligible to read the broadcast but absent from its push target list. The original tests cover an agent registering after the broadcast finishes, not registration during the send.

Registration-time filtering also does not mean “agents online at send time”: an old offline record can satisfy the timestamp condition. Whether that is acceptable needs an explicit definition, especially if resume preserves mailbox IDs.

**Recommended change:** Snapshot intended recipients in the same transaction as the message. A `message_recipients(message_id, agent_id, read_at)` table can replace the current reads-only table, with one row for a direct message and one per recipient for a broadcast. This is a small schema simplification in semantics, not a channels/teams feature.

Use that same membership for inbox queries, read authorization, and push target selection. Keep timestamps for display and retention, not as the sole representation of audience membership. Snapshot membership also avoids needing wall-clock ordering to decide who was “in the room.”

## 8. P1 acceptance gate — Verify the real adapters before claiming immediate delivery

**Affected:** D Open questions and Testing; I Tasks 9–11 and Deferred.

The Codex idle-turn question is not merely notification etiquette. For this product, whether an idle recipient wakes is part of the feature. Successful enqueue does not prove that a turn starts. The spike appropriately calls this unverified; the implementation plan should not relegate it to a nonblocking final footnote. [S: Codex spike; D Open questions]

The 45-minute Claude Desktop result is useful evidence and should be preserved. It does not establish every other environment, compaction survival, restart behavior, or the correctness of the new adapter code. [S: Long-idle doorbell spike]

Before declaring the four-surface MVP complete, verify the following on each claimed surface:

| Gate | Evidence needed |
|---|---|
| Session identity | Correct logical ID and owner PID/start from a real model-run shell and the actual hooks |
| Idle delivery | Another independent session's send causes a model-visible metadata notification without a new human prompt |
| Active-turn delivery | Behavior during ongoing work, with no unexpected immediate interruption or repeated turn loop |
| Hook fallback | Correct context delivery when primary notification is intentionally disabled or fails |
| Resume and endpoint close | Intended mailbox recovery and absence of stale routes |
| Permissions and launch context | Mailbox access, executable paths, hook trust, and queue behavior under normal CLI and Dock-launched app configurations |

**External documentation check:** Claude's sandbox can restrict writes outside the working/added directories and session temp directory, including subprocess writes. Therefore ordinary terminal success does not establish that a model-run command can write `~/.amail` or invoke a queue-writing subprocess under the intended sandbox. [E4]

Test with the actual permission profiles you plan to use. Document narrowly scoped required access and explicit approval where necessary; do not disable sandboxing or broadly expose configuration/credential directories merely to make the demo pass. Test Codex's chosen permission profile directly rather than assuming same Unix user means same access.

The plan explicitly defers Pi. That is reasonable, but call the initial deliverable a four-surface MVP with Pi pending. Also, Pi's proposed `before_agent_start` injection by itself describes a context insertion point, not evidence that an idle turn is started. Leave that guarantee unclaimed until its adapter is tested.

## 9. Concrete code fixes to include in the revised plan

| Finding | Evidence and recommended change |
|---|---|
| An empty body can break the recipient's inbox | `splitlines()[0]` raises `IndexError` for an accepted empty string. Reproduced. Reject empty/whitespace-only bodies according to an explicit rule, and make header code tolerate existing empty records. Add size limits. [I Task 6] |
| Push can hang the sender | `subprocess.run` for `codex queue` has no timeout. `ring()` catches only `OSError`, despite the stated never-raise contract. Add a bounded deadline, narrow explicit exception handling, and a structured push result. Preserve the committed message ID in any post-commit error. [I Task 7] |
| Hook fail-open behavior hides operational failure | `2>/dev/null` and `|| true` prevent session interruption, but also conceal an unusable mailbox or failed registration. Keep fail-open behavior, add a short hook deadline and rate-limited local diagnostics without bodies/secrets. [I Task 9] |
| The roster omits the project location | The design includes `cwd`; the implemented roster prints name, harness, status, branch, task, and last-seen, but no `cwd`. Show a compact project/worktree path so equal branch names across repositories are distinguishable. [D CLI; I Task 5] |
| Presence freshness is ambiguous | Registration/status update `last_seen`; ordinary sends and reads do not. Define whether this means process health, tool activity, or status age. Do not present old self-reported task text as fresh observations. [I Tasks 4–6] |
| Idle polling tests do not prove kqueue delivery | The waiter queries SQLite repeatedly with bounded waits, so the test can pass even when vnode events are ineffective. Keep polling as a correctness fallback; test the macOS event path separately only if relying on its performance. [I Task 8] |
| The doorbell contents are redundant | Routing appends headers, but the waiter uses file existence/events and rereads SQLite rather than consuming those contents. A simple hint or sequence marker is enough unless Pi needs a documented richer contract. Do not make the file a second authoritative mailbox. [I Tasks 7–8] |
| Unbounded output/state accumulation | Add bounded inbox output, an active-only default roster with an explicit history option, and maintenance for obsolete endpoint files/records. The current roster includes all historical agents. [I Tasks 5–6] |
| Machine use needs stable interfaces | Add `--json` for identity/roster/headers/send outcomes and `--body-file` or `--stdin` for message bodies. Either implement the distinction promised by `inbox --unread` or document/remove the no-op flag. These are recommended usability improvements, not currently specified behavior. [I Task 6] |

Before broad installation, add a schema-version/migration policy, private mailbox permissions, appropriate inbox/retention indexes, and a small `amail doctor` command. The diagnostic should report actual identity resolution and local capabilities, and clearly label facts it cannot infer, such as whether a hook is trusted or an idle turn will start without a live probe.

Use a stable installed wrapper and known executable locations for Dock-launched apps. Do not assume the GUI process PATH matches an interactive shell. Keep the routing implementation behind the public `codex queue` command rather than directly editing Codex's internal SQLite tables.

## 10. Tighten the guarantees in the docs

Replace “failure is always latency, never loss” with something narrower:

> A successful send persists the message before attempting notification. Notification is best effort. A failed notification does not roll back persisted mail. Eventual discovery depends on a later successful pull, waiter, or supported hook before retention expires.

This reflects the no-daemon design without promising retries or guaranteed eventual execution that are not implemented. It also distinguishes unread retention from delivery guarantees. The offline recovery defect must be fixed independently.

Replace “misrouting is impossible” with the precise property: committed messages remain bound to their resolved stable recipient IDs. Bare-name addressing and caller identity require their own protections.

Define `read_at` as tool-level body retrieval, not proof that the model understood the message or performed the request. The current read is marked before CLI output completes, so even successful rendering is not an exactly-once acknowledgment protocol. That can be acceptable for this tool when stated honestly.

Keep resource locking out of scope, but state that a roster status or a message is not mutual exclusion. “I am about to merge” and “GPUs look free” remain coordination hints. Display timestamps and stable sender identity so delayed notices are not mistaken for current resource authorization.

Correct remaining document drift: D still says the implementation plan has not been written; N contains superseded daemon and environment assumptions that should be visibly marked historical; the README should not imply all five surfaces are complete when Pi is deferred. The CLI rationale should say it avoids a persistent MCP tool-schema footprint, not literally zero context cost, because instructions, commands, and outputs still enter context.

## 11. Revised implementation order

**First: amend the contracts.** Decide durable mailbox versus activation identity, offline behavior, announcement versus read state, broadcast membership, and supported guarantees. Turn these decisions into failing regression tests before preserving the existing sample code as the implementation template.

**Second: validate the risky adapters.** Perform the small real-harness tests for idle Codex queue consumption, interactive identity, Stop output, and normal sandbox/GUI launch access. Keep the existing 45-minute result; another lengthy soak is not the immediate priority.

**Third: build the core.** Implement transactionally correct registration, stable ID lookup, recipient membership, body validation, reads, and notification bookkeeping. Keep OS process/Git work outside long database transactions.

**Fourth: integrate one vertical slice.** Start with two real independent sessions, including at least one desktop surface, and exercise register → send → metadata wake → intentional deferral → later read → re-arm. Add the other supported adapters after this behavior is reliable.

**Fifth: harden installation.** Add structured outputs, diagnostics, bounded failures, explicit versions/capabilities, safe install/uninstall behavior, and permission documentation. Keep changes to existing user hook configuration manual and explicitly approved, as the current plan intends.

The key release criterion is not “all supplied tests pass.” It is that the core invariants and the claimed live-harness behavior have both been demonstrated.

## Appendix A — Targeted reproduction results

These are observations against the plan snippets in the isolated review environment, not live macOS harness results.

| Counterexample | Observed result |
|---|---|
| Re-arm with unchanged unread mail | Both waits immediately returned the same message ID |
| Send an empty body | Send succeeded; unread lookup raised `IndexError` |
| Body sentinel in hook's inbox command | Sentinel appeared in printed header output |
| Resume same session with new process identity | Old PID/start remained stored and the resumed endpoint was reaped |
| Send offline, then register same native session | New mailbox ID had no unread message; old ID retained it |
| Reuse a pinned name | Original environment resolved to the replacement session's ID |
| Concurrent registration with same available handle | One `IntegrityError` on the live-name constraint |
| Concurrent registration for same session | One `IntegrityError` on the live-session constraint |
| Registration during a broadcast send | New agent had the broadcast in its inbox but was absent from push targets |

The name/registration/broadcast cases use controlled scheduling or lifecycle setup to expose valid interleavings. They are not estimates of how frequently the conditions occur during ordinary usage.

## Appendix B — External sources checked

These are current official documentation checks, distinct from the supplied local spike observations.

**E1 — Claude Code hooks reference.** Sections: Exit code 0; Stop input; Stop decision control.  
`https://code.claude.com/docs/en/hooks`

**E2 — Codex hooks reference.** Sections: SessionStart; Stop; review and trust hooks. The developer-documentation URL redirects to ChatGPT Learn.  
`https://developers.openai.com/codex/hooks`

**E3 — SQLite transactions.** Section 2.2: DEFERRED, IMMEDIATE, and EXCLUSIVE transactions.  
`https://www.sqlite.org/lang_transaction.html`

**E4 — Claude Code sandboxing.** Sections: Configure sandboxing; filesystem isolation.  
`https://code.claude.com/docs/en/sandboxing`
