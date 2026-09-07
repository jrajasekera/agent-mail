"""Vertical slice through real subprocesses: two sessions, one mailbox."""
import os
import subprocess
import time
from pathlib import Path

from tests.conftest import clean_env

AMAIL = str(Path(".venv/bin/amail").resolve())


def sess_env(home, key):
    return {**clean_env(), "AMAIL_HOME": str(home),
            "CLAUDE_CODE_SESSION_ID": key, "CLAUDE_PID": str(os.getpid())}


def amail(env, *args, **kw):
    return subprocess.run([AMAIL, *args], env=env, capture_output=True,
                          text=True, **kw)


def arm_wait(env, timeout="15"):
    p = subprocess.Popen([AMAIL, "wait", "--timeout", timeout], env=env,
                         stdout=subprocess.PIPE, text=True)
    time.sleep(1.0)      # let the waiter arm before the send
    return p


def test_vertical_slice(home):
    env_a, env_b = sess_env(home, "int-a"), sess_env(home, "int-b")
    handle_a = amail(env_a, "register").stdout.strip()
    handle_b = amail(env_b, "register").stdout.strip()
    name_b = handle_b.split("@")[0]

    # 1. B arms; A sends; B wakes with metadata only.
    w = arm_wait(env_b)
    r = amail(env_a, "send", name_b, "SLICE_BODY merge coming", "--priority", "2")
    assert r.returncode == 0, r.stderr
    out, _ = w.communicate(timeout=15)
    assert w.returncode == 0
    assert handle_a in out and "prio=2" in out
    assert "SLICE_BODY" not in out                     # metadata only

    # 2. B deliberately defers reading and re-arms: must NOT re-fire.
    r = amail(env_b, "wait", "--timeout", "2")
    assert r.returncode == 2                           # timed out, no loop

    # 3. A second message still wakes B while the first stays unread.
    w = arm_wait(env_b)
    amail(env_a, "send", name_b, "second message")
    out, _ = w.communicate(timeout=15)
    assert w.returncode == 0 and "re-arm" in out

    # 4. B reads both later; inbox drains; roster shows both live.
    inbox = amail(env_b, "inbox", "--unread").stdout
    ids = [line.split()[1] for line in inbox.splitlines()]
    assert len(ids) == 2
    for mid in ids:
        assert amail(env_b, "read", mid).returncode == 0
    assert amail(env_b, "inbox", "--unread").stdout.strip() == ""
    roster = amail(env_a, "roster").stdout
    assert handle_a in roster and handle_b in roster
