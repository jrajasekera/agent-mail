import os

from amail import cli, doctor, db, registry


def test_report_covers_key_checks(home, conn):
    env = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-d",
           "CLAUDE_PID": str(os.getpid())}
    registry.register(conn, env)
    checks = dict(doctor.report(conn, env, home))
    assert checks["identity"].startswith("claude:")
    assert "@" in checks["agent"]
    assert "writable" in checks["mailbox"]
    assert checks["watcher"] == "not armed"
    assert checks["hook trust"] == "cannot verify here"
    assert checks["codex idle wake"] == "cannot verify here"


def test_doctor_cli_runs_even_unregistered(home, capsys):
    assert cli.main(["doctor"], {"AMAIL_HOME": str(home)}) == 0
    out = capsys.readouterr().out
    assert "identity" in out and "cannot verify here" in out


def test_codex_before_first_turn_explains_it_has_no_mailbox(home, conn,
                                                            monkeypatch):
    """amail-eec: under Codex with no thread id yet, say why there is no
    mailbox rather than reporting a bare 'not registered'."""
    monkeypatch.setattr(doctor.identity, "walk_to_harness",
                        lambda pid: ("codex", 4242))
    checks = dict(doctor.report(conn, {"AMAIL_HOME": str(home)}, home))
    note = checks["codex mailbox"]
    assert "first turn" in note
    assert "cannot be sent to" in note


def test_codex_with_thread_id_gets_no_first_turn_note(home, conn):
    env = {"AMAIL_HOME": str(home), "CODEX_THREAD_ID": "th-1",
           "AMAIL_PID": str(os.getpid())}
    assert "codex mailbox" not in dict(doctor.report(conn, env, home))
