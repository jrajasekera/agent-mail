import json
import os

from amail import db, hooks, mail, registry

PID = str(os.getpid())


def _register_pair(home):
    conn = db.connect(home)
    alice = registry.register(conn, {"CLAUDE_CODE_SESSION_ID": "s-a",
                                     "CLAUDE_PID": PID})
    bob = registry.register(conn, {"CLAUDE_CODE_SESSION_ID": "s-b",
                                   "CLAUDE_PID": PID})
    return conn, alice, bob


def test_claude_sessionstart_registers_and_pins(home, tmp_path):
    env_file = tmp_path / "env.sh"
    env_file.touch()
    code, out = hooks.run_hook("claude", "sessionstart", {
        "AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-h",
        "CLAUDE_PID": PID, "CLAUDE_ENV_FILE": str(env_file)}, "{}")
    assert code == 0
    assert "export AMAIL_AGENT_ID=" in env_file.read_text()


def test_claude_stop_emits_bounded_block_json(home):
    conn, alice, bob = _register_pair(home)
    mail.send(conn, alice, bob.name, "BODY_SENTINEL secret plan")
    env = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-b",
           "CLAUDE_PID": PID}
    code, out = hooks.run_hook("claude", "stop", env,
                               json.dumps({"background_tasks": []}))
    assert code == 0
    payload = json.loads(out)
    assert payload["decision"] == "block"
    assert "BODY_SENTINEL" not in out                  # metadata only
    assert f"{alice.name}@{alice.id}" in payload["reason"]
    assert "re-arm" in payload["reason"]               # unarmed and told so
    code, out = hooks.run_hook("claude", "stop", env,
                               json.dumps({"background_tasks": []}))
    assert (code, out) == (0, "")                      # announced: no loop


def test_stop_rearm_note_distinguishes_unknown_from_armed(home):
    conn, alice, bob = _register_pair(home)
    env = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-b",
           "CLAUDE_PID": PID}
    mail.send(conn, alice, bob.name, "m1")
    _, out = hooks.run_hook("claude", "stop", env, "{}")  # no field: unknown
    assert "re-arm" not in out
    mail.send(conn, alice, bob.name, "m2")
    _, out = hooks.run_hook("claude", "stop", env, json.dumps(
        {"background_tasks": [{"command": "amail wait"}]}))
    assert "re-arm" not in out                          # armed: no nag


def test_codex_sessionstart_uses_payload_session_id(home):
    code, out = hooks.run_hook("codex", "sessionstart", {
        "AMAIL_HOME": str(home), "AMAIL_PID": PID},
        json.dumps({"session_id": "th-hook"}))
    assert code == 0
    conn = db.connect(home)
    row = conn.execute("SELECT harness FROM agents"
                       " WHERE session_key='codex:th-hook'").fetchone()
    assert row["harness"] == "codex"


def test_codex_stop_is_silent_and_failures_are_logged(home):
    assert hooks.run_hook("codex", "stop", {"AMAIL_HOME": str(home)},
                          "{}") == (0, "")
    hooks.log_failure({"AMAIL_HOME": str(home)}, RuntimeError("boom"))
    assert "boom" in (home / "hook.log").read_text()


def test_hook_cli_is_fail_open(home, capsys):
    from amail import cli
    rc = cli.main(["hook", "claude", "stop"],
                  {"AMAIL_HOME": "/nonexistent/forbidden/path"})
    assert rc == 0                                     # never breaks a session
