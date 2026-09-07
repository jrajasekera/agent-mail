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
