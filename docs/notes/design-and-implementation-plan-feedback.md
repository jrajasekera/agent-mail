**I think this is worth building, and the overall architecture fits your needs. But I would revise the implementation plan before executing it unchanged.** The biggest risks are not your choice of Python, SQLite, or a daemon-free design—they are session lifecycle, notification behavior, and the harness integrations.

I reviewed all four files and treated the revised design and spike results as current, with the session notes providing the decision history. I also extracted the proposed Python code and ran its supplied module/CLI tests: **all 43 passed, but targeted checks reproduced nine cases those tests miss.** Those checks ran on Python 3.13/Linux, not your target Python 3.14/macOS environment; I did not independently run your desktop apps or live hooks.

I put the detailed findings, affected implementation tasks, suggested changes, and acceptance tests into a handoff document:

[**agent-mail pre-implementation review**](agent-mail-preimplementation-review.md)

## What I would keep

The scope is well chosen: presence and messaging for independent agents, without expanding into orchestration, teams, channels, or resource locking. The reasoning for owning a small tool—particularly needing harness-specific routing metadata—is coherent. I would keep the CLI interface, shared SQLite mailbox, stable internal IDs, commit-before-notification, and sender-side routing. None of the issues below requires replacing those choices.   

The strongest product decision is **“notify immediately, fetch the body deliberately.”** It preserves agent independence and limits unsolicited content entering another session. The implementation needs to follow that decision more consistently. 

## Changes I would make before implementing the full plan

### 1. Separate “unread” from “not yet announced”

This is the most consequential behavioral issue.

`amail wait` returns immediately whenever any unread mail exists. It does not distinguish newly arrived mail from mail it already announced. But the design explicitly allows an agent to receive a notification and choose to read the body later. Those two behaviors conflict.  

I reproduced this by sending one message and calling `wait` twice without reading it. **Both waits immediately returned the same message ID.**

That creates this possible cycle:

```text
Message arrives → agent wakes → agent defers reading
→ agent re-arms wait → wait immediately completes for the same message
→ another notification
```

The instruction to re-arm after each individual `read` also becomes awkward when a batch contains several unread messages. 

**Recommended change:** Add notification acknowledgment state separate from read state. A per-agent notification cursor or per-recipient announcement record is enough; this does not need to become a workflow engine.

The invariant should be:

> Previously announced mail can remain unread without continuously generating new wake-ups, while genuinely new mail can still wake the agent.

Also make the Claude waiter a singleton per active endpoint and re-arm once after handling or deliberately deferring a notification batch—not after every individual read.

Define crash behavior explicitly. Duplicate notifications after an interrupted acknowledgment are acceptable; silently treating “notification process returned” as proof the model received it is not.

### 2. The hook backstop currently exposes message bodies

The design says notifications contain metadata only, and that inbox triage does not pull another agent’s prose into context. However, the proposed `Header.preview` contains the first line of the body, the CLI prints it, and the hook wrapper calls that CLI command.    

I put a distinctive sentinel at the beginning of a body and ran the exact inbox command used by the hooks. The sentinel appeared in the output.

So the ordinary doorbell is metadata-only, but the fallback path is not. At events that inject that output, the first line of the body enters context automatically.

**Recommended change:** Make automatic headers strictly metadata:

```text
message ID, sender ID, sender handle, priority, creation time
```

Keep body previews behind an explicit opt-in command or flag that hooks never use. Use one formatter for every automatic notification path.

This needs an end-to-end sentinel test covering hook output, waiter output, and Codex queue arguments—not just a unit test for `routing.header_line()`.

### 3. The Stop-hook integration does not match the documented contracts

The wrapper handles both `SessionStart` and `Stop` by printing plain text and exiting successfully. Its tests prove that the script prints something; they do not prove that the harness delivers it to the model.  

**External verification:** Current official documentation does not support that generic treatment. Claude Code does not add ordinary successful plain-text `Stop` stdout to model context; it documents structured continuation feedback. Codex explicitly requires JSON for successful `Stop` output and calls plain text invalid. ([Claude][1])

Your spike says hook-output injection was verified generally, but it does not document a Stop-specific round trip that resolves this discrepancy. I would preserve the spike as evidence and add an event-specific test on your installed versions rather than assume the same output works everywhere. 

**Recommended change:** Keep the stable wrapper, but implement separate event adapters—ideally in a tested Python handler that parses stdin once and emits the correct response shape.

Continuation must also be bounded. Otherwise, fixing the output format can turn a previously ineffective reminder into a loop that repeatedly prevents the agent from stopping.

There is another flaw in the re-arm detector: it only checks whether `background_tasks` is empty. **A background build is not proof that `amail wait` is armed.** Identify the actual mail watcher, and distinguish “unknown” from “healthy.” 

### 4. Decide what survives a session restart or resume

The plan currently mixes up two identities:

**Logical mailbox:** Which conversation or agent owns these messages?

**Running endpoint:** Which process currently hosts that conversation?

The existing registration code has problems in both directions.

**When a matching live session row exists, registration updates only `last_seen`.** It leaves the old PID, process start time, and route in place. I reproduced re-registering a session with a replacement process identity and then having the reaper incorrectly mark it offline because its stored identity still described the old process. 

**When the old row is already offline, registration creates a new agent ID.** Messages accepted for the old ID stay there. I reproduced an offline send reporting “queued anyway,” followed by registration of the same native session: the new mailbox had no unread message, while the old mailbox retained it. The normal current-agent lookup does not recover that offline mailbox.  

**My preference:** Preserve the logical mailbox across a supported native-session resume, keyed by something like:

```text
(harness, native_session_id)
```

Refresh its runtime PID/start identity and route when the session returns. Explicitly decide what happens when the same conversation is open in two clients.

A simpler alternative is to make every activation a new mailbox and reject offline sends by default. That is defensible for a first version. What does not work is accepting offline mail as queued without defining how the recipient can ever retrieve it.

### 5. Stable message IDs do not make recyclable names safe everywhere

You correctly fixed the original problem of old messages moving to a newly recycled name by storing recipient IDs. However, the plan still uses a recyclable name as authoritative **caller identity**: `current_agent()` gives `AMAIL_NAME` precedence over the actual native session identity.  

I exercised name reuse by registering `curie`, marking that record offline, assigning `curie` to another session, and resolving the original environment with its pinned `AMAIL_NAME`. **It resolved to the replacement session’s ID.**

That is an accidental identity problem, not an argument for adding authentication to your same-user tool.

**Recommended change:** Pin a stable `AMAIL_AGENT_ID` or canonical native-session binding. Treat names as labels. Conflicting identity hints should fail clearly rather than silently select another session.

There is also a separate addressing issue: an agent can remember the name `curie`, then send a new message after that name has been reused. Resolving names at send time behaves exactly as specified, but it does not guarantee the sender reaches the agent it remembers.

Expose stable addressing, such as:

```bash
amail send --to-id 42 "Are you still using the GPUs?"
```

Or a checked handle-plus-ID form such as `curie@42`. Display that stable identity in the roster and notifications.

I would narrow “misrouting is impossible” to the actual guarantee: **committed messages remain bound to their resolved recipient IDs.**

### 6. Registration and broadcasts need stronger transaction boundaries

The registration sequence checks for an existing session, selects an available name, and inserts a record without a transaction covering that entire decision. 

I reproduced two controlled interleavings:

| Concurrent operation                               | Result                                                                                       |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Two sessions select the same currently free name   | One succeeds; the other raises a uniqueness error                                            |
| Two calls register the same session simultaneously | One succeeds; the other raises a uniqueness error instead of returning the existing identity |

The unique indexes protect database integrity, which is good. But the operation is not reliably idempotent under concurrency.

Also, the test named `test_two_connections_can_write_concurrently` commits the first connection before writing with the second. It does not actually exercise overlap. 

**Recommended change:** Use a short explicit transaction around lookup and allocation, with bounded contention handling. `BEGIN IMMEDIATE` is one suitable approach, although SQLite documents that it can itself encounter a competing writer. Keep process inspection and Git subprocesses outside the writer-lock interval. ([SQLite][2])

Broadcasts have a related issue: push targets are selected first, while inbox eligibility is later determined by registration timestamps. I reproduced an agent registering between those steps: it received the broadcast in its inbox but was absent from the push target list.  

I would snapshot broadcast recipients in the same transaction as the message. A small recipient table could replace the reads-only table:

```text
message_recipients(message_id, agent_id, read_at)
```

Direct messages get one recipient row; broadcasts get one per intended recipient. Inbox visibility, read authorization, and notification routing then use the same audience.

## Make real harness behavior an acceptance gate

I disagree with classifying the remaining Codex idle-turn question as affecting only “chattiness etiquette.” **For your tool, whether an idle agent wakes is part of correctness.** Successful enqueue is not evidence that the receiving session starts a turn. Your spike appropriately leaves that unverified.  

The 45-minute Claude Desktop result is valuable. I would keep it, not reflexively ask for another long soak. The more useful next tests concern the actual adapters, restart behavior, fallback delivery, and permissions. Compaction survival remains untested in the supplied evidence. 

Before claiming the four-surface MVP is complete, I would require:

| Area                 | What needs demonstrating                                                               |
| -------------------- | -------------------------------------------------------------------------------------- |
| Identity             | Correct session ID and owning process from real interactive CLI and desktop sessions   |
| Idle delivery        | A send wakes the destination without another human prompt                              |
| Active-turn delivery | Defined behavior while the recipient is already working                                |
| Fallback             | Metadata reaches the model when primary notification is deliberately disabled or fails |
| Lifecycle            | Close, resume, and process replacement preserve the chosen mailbox semantics           |
| Installation         | Commands work under normal permissions and Dock-launched app environments              |

Permissions deserve particular attention. Claude’s documented sandbox can restrict writes outside the working, added, and temporary directories, including writes by subprocesses. Terminal success therefore does not establish that a model-run `amail` can modify `~/.amail` or invoke a queue-writing child process under your intended configuration. ([Claude][3])

Test the actual permission profiles and document narrowly scoped requirements. Do not broadly expose configuration directories or disable sandboxing merely to make the integration work.

Deferring Pi is reasonable, but label the initial release accordingly: **four supported surfaces, Pi pending**, rather than a completed five-surface implementation. The implementation plan already explicitly defers its extension. 

## Smaller fixes that should go into the plan

These do not require architectural changes, but several would otherwise produce confusing failures.

| Issue                                                                                                                                      | Recommended change                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| **An empty message breaks inbox processing.** `splitlines()[0]` raises `IndexError`; I reproduced this after a successful empty-body send. | Validate bodies and make header handling tolerate existing empty records. Add a size limit.                      |
| **A push can hang the sender.** `codex queue` has no subprocess timeout, and the implementation catches only `OSError`.                    | Add a deadline and structured failure handling; always preserve/report the committed message ID.                 |
| **Hook failures are almost invisible.** Errors are discarded while the script returns success.                                             | Keep hooks fail-open, but add bounded execution and rate-limited local diagnostics without bodies or secrets.    |
| **The roster omits `cwd`.** Branch names alone cannot distinguish projects reliably.                                                       | Display a compact project/worktree location, as the design intended.                                             |
| **Presence freshness is ambiguous.** Registration/status update `last_seen`; sends and reads do not.                                       | Define whether it means activity, status age, or process health. Do not imply these are interchangeable.         |
| **The waiter test can pass through periodic database checks even if event notification is ineffective.**                                   | Keep polling as a correctness fallback; separately test the macOS event path before relying on its performance.  |

For agent usability, I would also add structured `--json` output, stable IDs in responses, and `--stdin` or `--body-file` for sending bodies. A small `amail doctor` would be worthwhile for diagnosing identity resolution, executable paths, mailbox access, and known degraded modes.

## Tighten the promises, rather than adding more infrastructure

The phrase **“failure is always latency, never loss”** is stronger than the implementation supports. A failed push leaves durable mail, but an idle recipient might never run another working fallback before retention expires. The offline recovery issue makes that distinction especially important. 

I would state the guarantee as:

> A successful send persists the message before attempting notification. Notification is best effort. A failed notification does not roll back the message; eventual discovery depends on a later successful waiter, pull, or supported hook before retention expires.

Similarly, `read_at` should mean body retrieval—not proof that the agent understood or acted on it.

Keep resource locking out of scope, but retain the distinction between **coordination hints and mutual exclusion**. A status line saying the GPUs are free does not reserve them, and a merge announcement does not serialize merges. That is acceptable for the tool you chose to build; it just should not become an implicit guarantee later. 

## My recommended next step

I would amend the design’s lifecycle and notification contracts first, then turn the reproduced cases into regression tests. Move the small real-harness checks ahead of the broad implementation work, and build one vertical slice that includes:

**send → wake → deliberately defer the body → receive another message → read later → re-arm successfully.**

That exercises your actual product idea much better than a happy-path send/read loop.

**Bottom line: keep the product and the architecture. Revise the semantics and tests before handing the plan to an implementation agent.** The needed changes are focused reliability work—not a reason to turn this into a larger framework.

[1]: https://code.claude.com/docs/en/hooks "Hooks reference - Claude Code Docs"
[2]: https://www.sqlite.org/lang_transaction.html "Transaction"
[3]: https://code.claude.com/docs/en/sandboxing "Configure the sandboxed Bash tool - Claude Code Docs"
