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


def test_doctor_reports_cannot_verify_when_ps_is_denied(conn, home,
                                                        monkeypatch):
    from amail import identity

    def refuse(*a, **k):
        raise PermissionError(1, "Operation not permitted", "ps")

    monkeypatch.setattr(identity.subprocess, "run", refuse)
    checks = dict(doctor.report(conn, {"AMAIL_HOME": str(home)}, home))
    assert checks                       # a traceback would never get here


def test_doctor_still_reports_when_the_mailbox_cannot_be_opened(home, capsys,
                                                                monkeypatch):
    """amail-g31: doctor is the tool that explains a degraded mailbox, so it
    must survive one it cannot open."""
    from amail import cli, db

    def refuse(home_):
        raise db.MailboxUnavailable(
            f"cannot open {home_ / 'mail.db'}: unable to open database file")

    monkeypatch.setattr(db, "connect", refuse)
    rc = cli.main(["doctor"], {"AMAIL_HOME": str(home),
                               "CODEX_THREAD_ID": "t-1", "AMAIL_PID": "1",
                               "AMAIL_PID_START": "t",
                               "CODEX_SANDBOX": "seatbelt"})
    out = capsys.readouterr().out
    assert rc == 1
    assert "unable to open database file" in out
    assert "writable_roots" in out          # names the actual fix
    assert "Traceback" not in out


def test_disabled_background_tasks_is_explained(home, conn):
    """amail-87p gate 7: with background tasks off the Bash tool loses its
    run_in_background parameter, so `amail wait` can only run in the
    foreground and blocks the turn. Doctor is where that is explained."""
    env = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-bg",
           "CLAUDE_PID": str(os.getpid()),
           "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
    registry.register(conn, env)
    checks = dict(doctor.report(conn, env, home))
    note = checks["background tasks"]
    assert "amail wait" in note and "Stop hook" in note


def test_background_tasks_note_is_absent_when_they_are_enabled(home, conn):
    env = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-bg2",
           "CLAUDE_PID": str(os.getpid())}
    registry.register(conn, env)
    assert "background tasks" not in dict(doctor.report(conn, env, home))
    env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "0"
    assert "background tasks" not in dict(doctor.report(conn, env, home))
