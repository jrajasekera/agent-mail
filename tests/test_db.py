from pathlib import Path

import pytest

from amail import db


def test_amail_home_creates_private_dirs(tmp_path):
    home = db.amail_home({"AMAIL_HOME": str(tmp_path / "h")})
    assert (home / "doorbells").is_dir()
    assert (home / "waiters").is_dir()
    assert (home.stat().st_mode & 0o777) == 0o700


def test_connect_pragmas_and_schema(home):
    conn = db.connect(home)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.isolation_level is None
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "agents", "messages", "message_recipients"} <= tables
    db.connect(home).close()  # idempotent re-connect


def test_newer_schema_version_is_refused(home):
    conn = db.connect(home)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                 (str(db.SCHEMA_VERSION + 1),))
    conn.close()
    with pytest.raises(RuntimeError, match="schema"):
        db.connect(home)


def test_overlapping_write_transactions_conflict_cleanly(home):
    c1, c2 = db.connect(home), db.connect(home)
    c2.execute("PRAGMA busy_timeout=100")
    c1.execute("BEGIN IMMEDIATE")
    c1.execute("INSERT INTO meta VALUES ('x', '1')")
    with pytest.raises(Exception):  # sqlite3.OperationalError: busy
        c2.execute("BEGIN IMMEDIATE")
    c1.execute("COMMIT")
    c2.execute("BEGIN IMMEDIATE")   # now succeeds
    c2.execute("COMMIT")


def test_concurrent_connects_do_not_fail(home):
    import threading

    errors = []

    def worker():
        try:
            db.connect(home).close()
        except Exception as e:            # a locked db must not surface here
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


def test_existing_private_home_is_not_chmodded(tmp_path, monkeypatch):
    """A sandbox can forbid chmod on a directory it did not create; amail must
    not touch the mode of a home that is already private."""
    env = {"AMAIL_HOME": str(tmp_path / "h")}
    db.amail_home(env)

    def refuse(*a, **k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "chmod", refuse)
    assert db.amail_home(env) == (tmp_path / "h")


def test_unusable_home_raises_a_clean_error(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(db.HomeUnusable) as e:
        db.amail_home({"AMAIL_HOME": str(tmp_path / "h")})
    assert str(tmp_path / "h") in str(e.value)
    assert "\n" not in str(e.value)
