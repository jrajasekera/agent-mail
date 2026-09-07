import subprocess

from amail import mail, routing
from amail.registry import Agent


def agent(agent_id, route):
    return Agent(id=agent_id, name="curie", harness="x", session_key="x:s",
                 pid=1, pid_start="t", route=route, cwd=None, branch=None,
                 status="working", task=None, created_at="c", last_seen="l")


def _codex_agent(agent_id, thread):
    return agent(agent_id, {"kind": "codex_queue", "thread": thread})


HEADER = routing.header_line(mail.Header(7, 3, "bohr", 2, "2026-09-06T00:00:00+00:00"))


def test_header_line_is_metadata_only():
    assert HEADER == ("[amail] msg 7 from bohr@3 prio=2"
                      " at 2026-09-06T00:00:00+00:00 — read with: amail read 7")


def test_doorbell_ring_appends(home):
    a = agent(5, {"kind": "doorbell"})
    assert routing.ring(home, a, HEADER)
    assert routing.ring(home, a, HEADER)
    assert len(routing.doorbell_path(home, 5).read_text().splitlines()) == 2


def test_codex_ring_uses_timeout(home):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0)

    a = agent(6, {"kind": "codex_queue", "thread": "th-1"})
    assert routing.ring(home, a, HEADER, runner=fake_run)
    cmd, kw = calls[0]
    assert cmd[:4] == ["codex", "queue", "--thread", "th-1"]
    assert cmd[5] == HEADER
    assert kw["timeout"] == 10


def test_ring_never_raises(home):
    a = agent(6, {"kind": "codex_queue", "thread": "t"})

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    def missing(cmd, **kw):
        raise FileNotFoundError("codex not installed")

    def fails(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stderr="no rollout")

    for runner in (hang, missing, fails):
        assert routing.ring(home, a, HEADER, runner=runner) == routing.FAILED


def test_ring_all_counts(home):
    ok = agent(1, {"kind": "doorbell"})
    bad = agent(2, {"kind": "codex_queue", "thread": "t"})

    def missing(cmd, **kw):
        raise FileNotFoundError

    assert routing.ring_all(home, [ok, bad], HEADER,
                            runner=missing) == (1, [])


def test_an_unknown_codex_thread_is_reported_as_unreachable(home):
    """amail-nxt: a thread with no rollout can never be resumed, so there is
    no backstop behind a failed push -- unlike a merely stopped one."""
    a = _codex_agent(1, "t-gone")

    def unknown(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 1, "", "Error: failed to queue session message: ... no"
            " rollout found for thread id t-gone (code -32603)")

    assert routing.ring(home, a, HEADER, runner=unknown) is routing.UNREACHABLE

    def busy(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "transient failure")

    assert routing.ring(home, a, HEADER, runner=busy) is routing.FAILED


def test_ring_all_separates_unreachable_from_merely_unpushed(home):
    ok = _codex_agent(1, "t-ok")
    gone = _codex_agent(2, "t-gone")

    def runner(args, **kwargs):
        if "t-gone" in args:
            return subprocess.CompletedProcess(
                args, 1, "", "no rollout found for thread id t-gone")
        return subprocess.CompletedProcess(args, 0, "", "")

    pushed, unreachable = routing.ring_all(home, [ok, gone], HEADER,
                                           runner=runner)
    assert pushed == 1
    assert [a.id for a in unreachable] == [gone.id]
