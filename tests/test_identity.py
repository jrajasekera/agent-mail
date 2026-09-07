import os

import pytest

from amail import identity, registry


def test_claude_env_vars_win_and_namespace():
    ident = identity.resolve({
        "CLAUDE_CODE_SESSION_ID": "uuid-1", "CLAUDE_PID": "4242",
        "AMAIL_PID_START": "t"})
    assert (ident.harness, ident.session_key, ident.pid) == \
        ("claude", "claude:uuid-1", 4242)


def test_codex_env_var():
    ident = identity.resolve({
        "CODEX_THREAD_ID": "th-9", "AMAIL_PID": "77", "AMAIL_PID_START": "t"})
    assert (ident.harness, ident.session_key) == ("codex", "codex:th-9")


def test_amail_overrides_beat_everything():
    ident = identity.resolve({
        "CLAUDE_CODE_SESSION_ID": "uuid-1", "CLAUDE_PID": "4242",
        "AMAIL_HARNESS": "pi", "AMAIL_SESSION_KEY": "pi-7",
        "AMAIL_PID": "99", "AMAIL_PID_START": "t"})
    assert (ident.harness, ident.session_key, ident.pid) == ("pi", "pi:pi-7", 99)


def test_ancestry_fallback_yields_namespaced_pid_key():
    # NOTE: inside a real harness session the walk legitimately finds that
    # harness — assert the shape, not the name.
    ident = identity.resolve({})
    assert ident.harness in ("shell", "claude", "codex", "pi")
    assert ident.session_key == f"{ident.harness}:pid:{ident.pid}"
    assert ident.pid_start


def test_pid_alive_requires_matching_start():
    start = identity.pid_start(os.getpid())
    assert identity.pid_alive(os.getpid(), start)
    assert not identity.pid_alive(os.getpid(), "some other time")
    assert not identity.pid_alive(2**22, start)


def test_expected_harness_beats_inherited_claude_env():
    # amail-3um: a Codex process that inherited a Claude session's env must
    # resolve as Codex, not adopt the parent's identity.
    ident = identity.resolve({
        "CLAUDE_CODE_SESSION_ID": "uuid-1", "CLAUDE_PID": "4242",
        "CODEX_THREAD_ID": "th-9", "AMAIL_PID": "77",
        "AMAIL_PID_START": "t"}, expect_harness="codex")
    assert (ident.harness, ident.session_key) == ("codex", "codex:th-9")


def test_expected_harness_raises_rather_than_picking_a_winner():
    # No thread id to resolve with, and inherited Claude vars present: the
    # contract says a conflict raises, it does not pick a winner.
    with pytest.raises(identity.IdentityConflict):
        identity.resolve({"CLAUDE_CODE_SESSION_ID": "uuid-1",
                          "CLAUDE_PID": "4242", "AMAIL_PID_START": "t"},
                         expect_harness="codex")


def test_expected_harness_matching_env_is_unchanged():
    ident = identity.resolve({
        "CLAUDE_CODE_SESSION_ID": "uuid-1", "CLAUDE_PID": "4242",
        "AMAIL_PID_START": "t"}, expect_harness="claude")
    assert ident.session_key == "claude:uuid-1"


def test_expected_harness_rejects_ancestry_of_a_different_harness():
    # The ancestry fallback must not hand a Codex hook a claude identity.
    with pytest.raises(identity.IdentityConflict):
        identity.resolve({"AMAIL_PID_START": "t"}, expect_harness="pi")


class _NoPs:
    """`ps` cannot be executed at all — a sandbox denying its exec."""

    def __call__(self, *a, **k):
        raise PermissionError(1, "Operation not permitted", "ps")


def _no_inspection(monkeypatch):
    """Neither mechanism can look at a process: sysctl refused and ps cannot
    be executed. This is what a hostile sandbox looks like."""
    def refuse(pid):
        raise identity.InspectionUnavailable("sysctl(kern.proc.pid): denied")

    monkeypatch.setattr(identity, "kinfo", refuse)
    monkeypatch.setattr(identity, "ancestry", refuse)
    monkeypatch.setattr(identity.subprocess, "run", _NoPs())


def test_ps_exec_failure_is_distinguishable_from_a_dead_pid(monkeypatch):
    _no_inspection(monkeypatch)
    with pytest.raises(identity.InspectionUnavailable):
        identity.pid_start(os.getpid())
    with pytest.raises(identity.InspectionUnavailable):
        identity._ps_field(os.getpid(), "comm")


def test_native_identity_resolves_without_ps(monkeypatch):
    """A sandboxed session still knows its own session_key: that comes from
    the harness env vars. Only pid_start needs process inspection."""
    _no_inspection(monkeypatch)
    ident = identity.resolve({"CLAUDE_CODE_SESSION_ID": "s-x",
                              "CLAUDE_PID": "123"}, "claude")
    assert ident.session_key == "claude:s-x"
    assert ident.pid_start is None            # honestly unknown, not invented
    assert ident.native


def test_register_without_pid_start_fails_cleanly(conn, monkeypatch):
    _no_inspection(monkeypatch)
    with pytest.raises(RuntimeError, match="start time"):
        registry.register(conn, {"CLAUDE_CODE_SESSION_ID": "s-x",
                                 "CLAUDE_PID": "123"})


def test_reap_never_marks_agents_offline_it_cannot_verify(conn, monkeypatch):
    live = registry.register(conn, {"CLAUDE_CODE_SESSION_ID": "s-live",
                                    "CLAUDE_PID": str(os.getpid())})
    _no_inspection(monkeypatch)
    assert registry.reap(conn) == 0
    assert registry.get(conn, live.id).status != "offline"


def test_inherited_pin_is_still_rejected_without_ps(conn, monkeypatch):
    """The sandbox is exactly where a Codex process launched from a Claude
    session runs, so the pin-conflict check must survive a missing ps."""
    other = registry.register(conn, {"CLAUDE_CODE_SESSION_ID": "s-parent",
                                     "CLAUDE_PID": str(os.getpid())})
    _no_inspection(monkeypatch)
    with pytest.raises(LookupError, match="identity conflict"):
        registry.current_agent(conn, {"CODEX_THREAD_ID": "t-child",
                                      "AMAIL_PID": "123",
                                      "AMAIL_AGENT_ID": str(other.id)})


AMBIGUOUS = {"CLAUDE_CODE_SESSION_ID": "s-parent", "CLAUDE_PID": "14",
             "CODEX_THREAD_ID": "t-child", "AMAIL_PID": "12",
             "AMAIL_PID_START": "t"}


def test_process_tree_breaks_a_tie_between_two_harness_environments(
        monkeypatch):
    """amail-svb: a Codex session launched from a Claude session's shell has
    BOTH sets of variables. Env can be inherited; the process tree cannot."""
    monkeypatch.setattr(identity, "ancestry",
                        lambda pid: _chain(CODEX_IN_CLAUDE))
    assert identity.resolve(AMBIGUOUS).session_key == "codex:t-child"
    monkeypatch.setattr(identity, "ancestry",
                        lambda pid: _chain(CLAUDE_IN_CODEX))
    assert identity.resolve(AMBIGUOUS).session_key == "claude:s-parent"


def test_an_undecidable_tie_raises_rather_than_picking_a_winner(monkeypatch):
    monkeypatch.setattr(identity, "ancestry",
                        lambda pid: _chain([("me", 10), ("zsh", 11)]))
    with pytest.raises(identity.IdentityConflict, match="claude"):
        identity.resolve(AMBIGUOUS)


def test_an_expected_harness_still_wins_over_the_tree(monkeypatch):
    monkeypatch.setattr(identity, "ancestry",
                        lambda pid: _chain(CLAUDE_IN_CODEX))
    assert identity.resolve(AMBIGUOUS, "codex").session_key == "codex:t-child"


# --- process inspection without exec (amail-svb) ---------------------------

def test_kinfo_matches_ps_for_a_real_process():
    import subprocess as sp
    proc = sp.Popen(["/bin/sleep", "30"])
    try:
        info = identity.kinfo(proc.pid)
        assert info is not None
        assert info.comm == "sleep"
        assert info.ppid == os.getpid()
        expected = sp.run(["/bin/ps", "-o", "lstart=", "-p", str(proc.pid)],
                          capture_output=True, text=True).stdout.strip()
        assert info.start == expected      # stored rows need no migration
    finally:
        proc.kill()
        proc.wait()


def test_kinfo_returns_none_for_a_dead_pid():
    assert identity.kinfo(2 ** 22) is None


def test_ancestry_starts_at_the_given_pid_and_climbs():
    chain = identity.ancestry(os.getpid())
    assert chain[0].pid == os.getpid()
    assert chain[1].pid == os.getppid()


# Claude execs a versioned binary, so its comm is a version string, never
# "claude"; only CLAUDE_PID identifies it. Codex's comm starts with "codex".
CODEX_IN_CLAUDE = [("me", 10), ("zsh", 11), ("codex", 12), ("zsh", 13),
                   ("2.1.263", 14)]
CLAUDE_IN_CODEX = [("me", 10), ("zsh", 11), ("2.1.263", 14), ("zsh", 15),
                   ("codex", 12)]


def _chain(pairs):
    return [identity.Proc(pid, comm, 0, "") for comm, pid in pairs]


def test_nearest_ancestor_wins_in_both_nesting_directions():
    env = {"CLAUDE_PID": "14"}
    both = {"claude", "codex"}
    assert identity._nearest_harness(env, both,
                                     _chain(CODEX_IN_CLAUDE)) == "codex"
    assert identity._nearest_harness(env, both,
                                     _chain(CLAUDE_IN_CODEX)) == "claude"


def test_an_unrecognisable_ancestry_decides_nothing():
    assert identity._nearest_harness({"CLAUDE_PID": "99"}, {"claude", "codex"},
                                     _chain([("me", 10), ("zsh", 11)])) is None
