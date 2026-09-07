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
