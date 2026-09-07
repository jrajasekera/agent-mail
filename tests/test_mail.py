import os

import pytest

from amail import cli, mail, registry


def make_agent(conn, key):
    return registry.register(conn, {
        "CLAUDE_CODE_SESSION_ID": key, "CLAUDE_PID": str(os.getpid())})


def test_direct_send_read_cycle(conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    msg_id, targets, warning = mail.send(conn, alice, bob.name,
                                         "merging main in 10m", priority=2)
    assert [t.id for t in targets] == [bob.id] and warning is None
    assert mail.unread(conn, alice) == []
    (h,) = mail.unread(conn, bob)
    assert (h.id, h.sender, h.priority) == (msg_id, alice.name, 2)
    sender, body, priority = mail.read(conn, bob, msg_id)
    assert body == "merging main in 10m"
    assert sender == registry.handle(alice)
    assert mail.unread(conn, bob) == []


def test_announced_is_separate_from_read(conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    m1, _, _ = mail.send(conn, alice, bob.name, "one")
    assert [h.id for h in mail.unannounced(conn, bob)] == [m1]
    mail.mark_announced(conn, bob, [m1])
    assert mail.unannounced(conn, bob) == []        # no announce loop
    assert [h.id for h in mail.unread(conn, bob)] == [m1]  # still unread
    m2, _, _ = mail.send(conn, alice, bob.name, "two")
    assert [h.id for h in mail.unannounced(conn, bob)] == [m2]  # new mail fires


def test_checked_handle_and_to_id_addressing(conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    _, targets, _ = mail.send(conn, alice, f"{bob.name}@{bob.id}", "hi")
    assert targets[0].id == bob.id
    _, targets, _ = mail.send(conn, alice, to_id=bob.id, body="hi again")
    assert targets[0].id == bob.id
    with pytest.raises(ValueError, match="not named"):
        mail.send(conn, alice, f"wrongname@{bob.id}", "hi")


def test_broadcast_audience_snapshot(conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    msg_id, targets, _ = mail.send(conn, alice, "all", "gpus free")
    late = make_agent(conn, "s-late")
    assert {t.id for t in targets} == {bob.id}
    assert len(mail.unread(conn, bob)) == 1
    assert mail.unread(conn, late) == []       # not in the snapshot
    assert mail.unread(conn, alice) == []      # sender excluded
    mail.read(conn, bob, msg_id)
    with pytest.raises(KeyError):
        mail.read(conn, late, msg_id)          # audience == authorization


def test_body_validation(conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    for bad in ("", "   \n"):
        with pytest.raises(ValueError, match="empty"):
            mail.send(conn, alice, bob.name, bad)
    with pytest.raises(ValueError, match="64"):
        mail.send(conn, alice, bob.name, "x" * (mail.MAX_BODY_BYTES + 1))
    assert mail.preview("") == ""              # legacy tolerance


def test_offline_send_queues_with_warning_and_is_recoverable(conn):
    alice = make_agent(conn, "s-a")
    dead_env = {"CLAUDE_CODE_SESSION_ID": "s-dead", "CLAUDE_PID": "11",
                "AMAIL_PID_START": "bogus"}
    dead = registry.register(conn, dead_env)
    registry.reap(conn)
    _, targets, warning = mail.send(conn, alice, dead.name, "you there?")
    assert targets == [] and dead.last_seen in warning
    revived = registry.register(conn, dead_env)   # resume the same session
    assert revived.id == dead.id
    assert len(mail.unread(conn, revived)) == 1   # mail was recoverable


def test_old_messages_pruned_on_send(conn):
    alice, bob = make_agent(conn, "s-a"), make_agent(conn, "s-b")
    conn.execute("INSERT INTO messages (sender_id, body, priority, created_at)"
                 " VALUES (?, 'ancient', 1, '2020-01-01T00:00:00+00:00')",
                 (alice.id,))
    mail.send(conn, alice, bob.name, "fresh")
    assert [r[0] for r in conn.execute("SELECT body FROM messages")] == ["fresh"]


def test_cli_send_inbox_read(home, capsys, tmp_path):
    env_a = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-a",
             "CLAUDE_PID": str(os.getpid())}
    env_b = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "s-b",
             "CLAUDE_PID": str(os.getpid())}
    cli.main(["register"], env_a)
    handle_a = capsys.readouterr().out.strip()
    cli.main(["register"], env_b)
    handle_b = capsys.readouterr().out.strip()
    body_file = tmp_path / "b.txt"
    body_file.write_text("BODY_SENTINEL ping")
    assert cli.main(["send", handle_a.split("@")[0],
                     "--body-file", str(body_file)], env_b) == 0
    capsys.readouterr()
    cli.main(["inbox", "--unread"], env_a)
    inbox_out = capsys.readouterr().out
    assert "BODY_SENTINEL" not in inbox_out          # metadata only
    assert handle_b in inbox_out
    msg_id = inbox_out.split()[1]                    # header format: Task 7
    cli.main(["inbox", "--preview"], env_a)
    assert "BODY_SENTINEL" in capsys.readouterr().out  # explicit opt-in
    assert cli.main(["read", msg_id], env_a) == 0
    out = capsys.readouterr().out
    assert "message from agent" in out and "ping" in out
    assert "re-arm" not in out                       # batch-level, not per-read


def test_offline_codex_agent_is_still_a_push_target(conn):
    """amail-3rp: `codex queue` is durable across a stopped process, so an
    offline codex_queue route is exactly the case push exists for. A doorbell
    write to an offline claude session, by contrast, has no reader."""
    alice = make_agent(conn, "s-a")
    codex_env = {"CODEX_THREAD_ID": "t-dead", "AMAIL_PID": "11",
                 "AMAIL_PID_START": "bogus"}
    dead_codex = registry.register(conn, codex_env, expect_harness="codex")
    dead_claude = registry.register(conn, {
        "CLAUDE_CODE_SESSION_ID": "s-dead", "CLAUDE_PID": "11",
        "AMAIL_PID_START": "bogus"})
    registry.reap(conn)

    _, targets, warning = mail.send(conn, alice, dead_codex.name, "ping")
    assert [t.id for t in targets] == [dead_codex.id]
    assert "Codex thread" in warning and "resumes" in warning

    _, targets, warning = mail.send(conn, alice, dead_claude.name, "ping")
    assert targets == []
    assert "resume" in warning
