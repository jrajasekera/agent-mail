import os
import threading

import pytest

from amail import db, registry

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
    with pytest.raises(LookupError, match="conflict"):
        registry.current_agent(conn, {"AMAIL_AGENT_ID": str(b.id), **ENV1})


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
