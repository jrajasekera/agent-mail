import os

from amail import registry

LIVE_ENV = {"CLAUDE_CODE_SESSION_ID": "s-live", "CLAUDE_PID": str(os.getpid())}
DEAD_ENV = {"CLAUDE_CODE_SESSION_ID": "s-dead", "CLAUDE_PID": "11",
            "AMAIL_PID_START": "bogus start"}


def test_update_status_redetects_and_bumps(conn):
    a = registry.register(conn, LIVE_ENV)
    u = registry.update_status(conn, a, status="waiting", task="gpu run")
    assert (u.status, u.task) == ("waiting", "gpu run")
    assert u.last_seen >= a.last_seen
    assert u.branch == registry.detect_branch(os.getcwd())


def test_reap_marks_dead_offline_and_cleans_files(home, conn):
    live = registry.register(conn, LIVE_ENV)
    dead = registry.register(conn, DEAD_ENV)
    bell = home / "doorbells" / str(dead.id)
    lock = home / "waiters" / f"{dead.id}.pid"
    bell.write_text("x")
    lock.write_text("11")
    assert registry.reap(conn, home) == 1
    assert registry.get(conn, dead.id).status == "offline"
    assert registry.get(conn, live.id).status != "offline"
    assert not bell.exists() and not lock.exists()


def test_roster_live_by_default(home, conn):
    registry.register(conn, DEAD_ENV)
    live = registry.register(conn, LIVE_ENV)
    assert [a.id for a in registry.roster(conn, home)] == [live.id]
    both = registry.roster(conn, home, include_offline=True)
    assert len(both) == 2 and both[-1].status == "offline"
