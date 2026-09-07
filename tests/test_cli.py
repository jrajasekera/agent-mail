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
