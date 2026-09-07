import os
import threading
import time

import pytest

from amail import db, mail, registry, routing, waiter


def make_agent(conn, key):
    return registry.register(conn, {
        "CLAUDE_CODE_SESSION_ID": key, "CLAUDE_PID": str(os.getpid())})


def test_wait_returns_unannounced_and_marks(home, conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    mail.send(conn, alice, bob.name, "already here")
    headers = waiter.wait(conn, bob, home, timeout=5)
    assert len(headers) == 1
    assert mail.unannounced(conn, bob) == []
    assert len(mail.unread(conn, bob)) == 1        # still unread


def test_announced_unread_mail_does_not_refire(home, conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    mail.send(conn, alice, bob.name, "deferred")
    waiter.wait(conn, bob, home, timeout=5)        # announces
    start = time.monotonic()
    assert waiter.wait(conn, bob, home, timeout=0.5,
                       poll_interval=0.1) == []    # re-arm: no loop
    assert time.monotonic() - start >= 0.4


def test_new_mail_wakes_while_old_stays_unread(home, conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    mail.send(conn, alice, bob.name, "old")
    waiter.wait(conn, bob, home, timeout=5)

    def deliver():
        time.sleep(0.4)
        c2 = db.connect(home)
        a2, b2 = registry.current_agent(c2, {
            "CLAUDE_CODE_SESSION_ID": "s-a",
            "CLAUDE_PID": str(os.getpid())}), registry.get(c2, bob.id)
        msg_id, targets, _ = mail.send(c2, a2, b2.name, "new")
        (h,) = [h for h in mail.unread(c2, b2) if h.id == msg_id]
        routing.ring_all(home, targets, routing.header_line(h))
        c2.close()

    t = threading.Thread(target=deliver)
    t.start()
    headers = waiter.wait(conn, bob, home, timeout=10, poll_interval=0.1)
    t.join()
    assert len(headers) == 1                       # only the new one
    assert len(mail.unread(conn, bob)) == 2
    assert not routing.doorbell_path(home, bob.id).exists()


def test_wait_is_singleton_per_agent(home, conn):
    bob = make_agent(conn, "s-b")
    lock = home / "waiters" / f"{bob.id}.pid"
    lock.write_text(str(os.getpid()))              # a live competing waiter
    with pytest.raises(RuntimeError, match="already armed"):
        waiter.wait(conn, bob, home, timeout=0.2)
    lock.write_text("999999999")                   # stale lock is replaced
    assert waiter.wait(conn, bob, home, timeout=0.2,
                       poll_interval=0.1) == []
    assert not lock.exists()


def test_eperm_means_alive_not_dead(monkeypatch):
    """EPERM from kill(pid, 0) means the process exists and we may not signal
    it — treating that as 'gone' lets a second waiter steal a live lock."""
    def eperm(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(waiter.os, "kill", eperm)
    assert waiter._pid_running(4242) is True
