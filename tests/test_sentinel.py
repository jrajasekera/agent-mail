"""The spec's sentinel test: no body content on any automatic path."""
import json
import os

from amail import hooks, mail, registry, routing, waiter

SENTINEL = "ZZBODYSENTINELZZ"
PID = str(os.getpid())


def _pair(conn):
    alice = registry.register(conn, {"CLAUDE_CODE_SESSION_ID": "sent-a",
                                     "CLAUDE_PID": PID})
    bob = registry.register(conn, {"CLAUDE_CODE_SESSION_ID": "sent-b",
                                   "CLAUDE_PID": PID})
    return alice, bob


def test_sentinel_never_reaches_an_automatic_path(home, conn):
    alice, bob = _pair(conn)
    body = f"{SENTINEL} do not leak me"

    # codex queue arguments
    codex = registry.register(conn, {"CODEX_THREAD_ID": "sent-th",
                                     "AMAIL_PID": PID})
    msg_id, targets, _ = mail.send(conn, alice, "all", body)
    (h,) = [x for x in mail.unread(conn, bob) if x.id == msg_id]
    header = routing.header_line(h)
    assert SENTINEL not in header
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        import subprocess
        return subprocess.CompletedProcess(cmd, 0)

    routing.ring_all(home, targets, header, runner=fake_run)
    assert calls and all(SENTINEL not in " ".join(c) for c in calls)

    # doorbell file contents
    assert SENTINEL not in routing.doorbell_path(home, bob.id).read_text()

    # waiter output
    headers = waiter.wait(conn, bob, home, timeout=5)
    assert headers and all(SENTINEL not in routing.header_line(x)
                           for x in headers)

    # hook adapter output, every event
    env_b = {"AMAIL_HOME": str(home), "CLAUDE_CODE_SESSION_ID": "sent-b",
             "CLAUDE_PID": PID}
    env_c = {"AMAIL_HOME": str(home), "CODEX_THREAD_ID": "sent-th",
             "AMAIL_PID": PID}
    for harness, event, env in (("claude", "sessionstart", env_b),
                                ("claude", "stop", env_b),
                                ("codex", "sessionstart", env_c),
                                ("codex", "stop", env_c)):
        mail.send(conn, alice, f"{bob.name}@{bob.id}", body)
        mail.send(conn, alice, f"{codex.name}@{codex.id}", body)
        _, out = hooks.run_hook(harness, event, env, json.dumps({}))
        assert SENTINEL not in out, (harness, event)
