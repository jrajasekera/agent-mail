import os
import threading

import pytest

from amail import db, identity, registry

ENV1 = {"CLAUDE_CODE_SESSION_ID": "s-1", "CLAUDE_PID": "11",
        "AMAIL_PID_START": "t1"}
ENV2 = {"CODEX_THREAD_ID": "th-2", "AMAIL_PID": "22", "AMAIL_PID_START": "t2"}


def test_register_creates_agent_with_route(conn):
    a = registry.register(conn, ENV1)
    assert a.harness == "claude" and a.route == {"kind": "doorbell"}
    assert registry.handle(a) == f"{a.name}@{a.id}"


def test_codex_route_carries_native_thread(conn):
    a = registry.register(conn, ENV2)
    assert a.route == {"kind": "codex_queue", "thread": "th-2"}


def test_register_idempotent_and_revives_offline_mailbox(conn):
    a1 = registry.register(conn, ENV1)
    assert registry.register(conn, ENV1).id == a1.id
    conn.execute("UPDATE agents SET status='offline' WHERE id=?", (a1.id,))
    a2 = registry.register(conn, ENV1)          # resume: same mailbox
    assert (a2.id, a2.name) == (a1.id, a1.name)
    assert a2.status == "working"


def test_register_refreshes_endpoint_identity(conn):
    a1 = registry.register(conn, ENV1)
    a2 = registry.register(conn, {**ENV1, "CLAUDE_PID": "999",
                                  "AMAIL_PID_START": "t-new"})
    assert a2.id == a1.id
    assert (a2.pid, a2.pid_start) == (999, "t-new")  # reaper sees new process


def test_concurrent_registration_converges(home):
    results, errors = [], []

    def worker(env):
        c = db.connect(home)
        try:
            results.append(registry.register(c, env).id)
        except Exception as e:                      # any error fails the test
            errors.append(e)
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(ENV1,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(results)) == 1                   # one mailbox, no raises


def test_current_agent_pinned_and_conflicting(conn):
    a = registry.register(conn, ENV1)
    b = registry.register(conn, ENV2)
    assert registry.current_agent(conn, {"AMAIL_AGENT_ID": str(a.id),
                                         **ENV1}).id == a.id
    with pytest.raises(LookupError, match="does not exist"):
        registry.current_agent(conn, {"AMAIL_AGENT_ID": "9999"})
    # A stale pin alongside a session that DOES have its own mailbox is not a
    # conflict about who we are: AMAIL_AGENT_ID is inherited by every
    # descendant, so native identity wins and the pin is dropped (amail-b8o).
    assert registry.current_agent(conn, {"AMAIL_AGENT_ID": str(b.id),
                                         **ENV1}).id == a.id


def test_pin_is_rejected_when_it_belongs_to_another_session(conn):
    """A resumed Codex session gets a thread id with no mailbox. If it also
    inherited AMAIL_AGENT_ID from the session that launched it, the pin must
    not silently win — that is how a Codex session reads a Claude mailbox."""
    a = registry.register(conn, ENV1)                    # a claude mailbox
    resumed = {"CODEX_THREAD_ID": "th-never-registered", "AMAIL_PID": "22",
               "AMAIL_PID_START": "t2", "AMAIL_AGENT_ID": str(a.id)}
    with pytest.raises(LookupError, match="conflict"):
        registry.current_agent(conn, resumed)


def test_current_agent_finds_offline_mailbox(conn):
    a = registry.register(conn, ENV1)
    conn.execute("UPDATE agents SET status='offline' WHERE id=?", (a.id,))
    assert registry.current_agent(conn, ENV1).id == a.id


def _mk(conn, env, harness=None):
    return registry.register(conn, env, expect_harness=harness)


CLAUDE_PARENT = {"CLAUDE_CODE_SESSION_ID": "s-parent", "CLAUDE_PID": "14",
                 "AMAIL_PID_START": "t"}


def test_a_pin_that_contradicts_native_identity_is_ignored(conn, monkeypatch):
    """amail-b8o: AMAIL_AGENT_ID is inherited by every descendant exactly like
    CLAUDE_*, so in a nested session a foreign pin is the normal case. The
    harness's own statement about who it is wins; the pin is a cross-check."""
    parent = _mk(conn, CLAUDE_PARENT)
    child_env = {"CODEX_THREAD_ID": "t-child", "AMAIL_PID": "12",
                 "AMAIL_PID_START": "t"}
    child = _mk(conn, child_env, "codex")

    nested = {**CLAUDE_PARENT, **child_env,
              "AMAIL_AGENT_ID": str(parent.id)}
    monkeypatch.setattr(identity, "ancestry", lambda pid: [
        identity.Proc(10, "Python", 11, ""),
        identity.Proc(12, "codex", 13, ""),
        identity.Proc(14, "2.1.263", 1, "")])
    assert registry.current_agent(conn, nested).id == child.id


def test_a_pin_is_refused_when_identity_is_undecidable(conn, monkeypatch):
    parent = _mk(conn, CLAUDE_PARENT)
    nested = {**CLAUDE_PARENT, "CODEX_THREAD_ID": "t-child", "AMAIL_PID": "12",
              "AMAIL_AGENT_ID": str(parent.id)}
    monkeypatch.setattr(identity, "ancestry",
                        lambda pid: [identity.Proc(10, "Python", 1, "")])
    with pytest.raises(LookupError, match="at once"):
        registry.current_agent(conn, nested)


def test_a_pin_still_works_when_nothing_contradicts_it(conn):
    agent = _mk(conn, CLAUDE_PARENT)
    assert registry.current_agent(
        conn, {**CLAUDE_PARENT, "AMAIL_AGENT_ID": str(agent.id)}).id == agent.id
    # and with no harness variables at all, the pin is the only claim there is
    assert registry.current_agent(
        conn, {"AMAIL_AGENT_ID": str(agent.id)}).id == agent.id
