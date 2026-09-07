import os

import pytest

from amail import identity


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
