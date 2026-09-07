from amail import cli

ENV = {"CLAUDE_CODE_SESSION_ID": "s-cli", "CLAUDE_PID": "33",
       "AMAIL_PID_START": "t"}


def env_for(home):
    return {"AMAIL_HOME": str(home), **ENV}


def test_register_prints_handle_and_is_idempotent(home, capsys):
    assert cli.main(["register"], env_for(home)) == 0
    handle = capsys.readouterr().out.strip()
    name, _, agent_id = handle.partition("@")
    assert name and agent_id.isdigit()
    cli.main(["register"], env_for(home))
    assert capsys.readouterr().out.strip() == handle


def test_whoami_json(home, capsys):
    cli.main(["register"], env_for(home))
    capsys.readouterr()
    assert cli.main(["--json", "whoami"], env_for(home)) == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert set(data) >= {"id", "name", "harness", "status"}


def test_whoami_unregistered_fails(home, capsys):
    assert cli.main(["whoami"], env_for(home)) == 1
    assert "register" in capsys.readouterr().err


def test_send_with_to_id_takes_a_single_positional_body(home, capsys):
    env_a = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-x",
             "CLAUDE_PID": "33", "AMAIL_PID_START": "t"}
    env_b = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-y",
             "CLAUDE_PID": "33", "AMAIL_PID_START": "t"}
    cli.main(["register"], env_a)
    id_a = capsys.readouterr().out.strip().split("@")[1]
    cli.main(["register"], env_b)
    capsys.readouterr()
    assert cli.main(["send", "--to-id", id_a, "hello by id"], env_b) == 0
    capsys.readouterr()
    cli.main(["inbox", "--preview"], env_a)
    assert "hello by id" in capsys.readouterr().out


def test_unusable_home_is_one_line_not_a_traceback(home, capsys, monkeypatch):
    from amail import db

    def refuse(env):
        raise db.HomeUnusable("cannot create /nope/.amail: Operation not permitted")

    monkeypatch.setattr(db, "amail_home", refuse)
    assert cli.main(["whoami"], env_for(home)) == 1
    captured = capsys.readouterr()
    assert captured.err.strip().count("\n") == 0
    assert "/nope/.amail" in captured.err
    assert "Traceback" not in captured.err


def test_unregisterable_session_is_one_line_not_a_traceback(home, capsys,
                                                            monkeypatch):
    from amail import identity

    def refuse(*a, **k):
        raise PermissionError(1, "Operation not permitted", "ps")

    monkeypatch.setattr(identity.subprocess, "run", refuse)
    env = {k: v for k, v in env_for(home).items() if k != "AMAIL_PID_START"}
    assert cli.main(["register"], env) == 1
    err = capsys.readouterr().err
    assert "start time" in err and "Traceback" not in err
    assert err.strip().count("\n") == 0


def test_unopenable_mailbox_is_one_line_not_a_traceback(home, capsys,
                                                        monkeypatch):
    """Observed live under Codex's workspace-write sandbox: ~/.amail is
    readable but not writable, so the WAL pragma fails."""
    from amail import db

    def refuse(home_):
        raise db.MailboxUnavailable(
            f"cannot open {home_ / 'mail.db'}: attempt to write a readonly database")

    monkeypatch.setattr(db, "connect", refuse)
    assert cli.main(["read", "3"], env_for(home)) == 1
    err = capsys.readouterr().err
    assert "mail.db" in err and "Traceback" not in err
    assert err.strip().count("\n") == 0
