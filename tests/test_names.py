import pytest

from amail import names


def _insert(conn, name, status="working"):
    conn.execute(
        "INSERT INTO agents (name, harness, session_key, pid, pid_start,"
        " route, status, created_at, last_seen)"
        " VALUES (?,'claude','claude:'||?,1,'t','{}',?,'2026','2026')",
        (name, name, status))


def test_pool_size_and_shape():
    assert len(names.POOL) >= 40
    assert all(n == n.lower() and n.isalpha() for n in names.POOL)


def test_allocate_skips_live_names(conn):
    for n in names.POOL[:-1]:
        _insert(conn, n)
    assert names.allocate(conn) == names.POOL[-1]


def test_offline_names_are_reusable(conn):
    for n in names.POOL:
        _insert(conn, n, status="offline")
    assert names.allocate(conn) in names.POOL


def test_exhausted_pool_raises(conn):
    for n in names.POOL:
        _insert(conn, n)
    with pytest.raises(RuntimeError):
        names.allocate(conn)
