"""amail CLI: thin argparse dispatch over the library modules."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping

from amail import db, registry


def _require_agent(conn, env):
    try:
        agent = registry.current_agent(conn, env)
    except LookupError as e:
        print(str(e), file=sys.stderr)
        return None
    if agent is None:
        print("not registered in this session — run: amail register",
              file=sys.stderr)
    return agent


def _agent_dict(agent):
    return {"id": agent.id, "name": agent.name, "handle":
            registry.handle(agent), "harness": agent.harness,
            "status": agent.status, "cwd": agent.cwd, "branch": agent.branch,
            "task": agent.task, "last_seen": agent.last_seen}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amail")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register")
    sub.add_parser("whoami")
    return parser


def main(argv: list[str] | None = None,
         env: Mapping[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    args = build_parser().parse_args(argv)
    home = db.amail_home(env)
    conn = db.connect(home)
    try:
        return dispatch(args, conn, env, home)
    finally:
        conn.close()


def dispatch(args, conn, env, home) -> int:
    if args.command == "register":
        agent = registry.register(conn, env, home)
        print(json.dumps(_agent_dict(agent)) if args.json
              else registry.handle(agent))
        return 0
    if args.command == "whoami":
        agent = _require_agent(conn, env)
        if agent is None:
            return 1
        print(json.dumps(_agent_dict(agent)) if args.json
              else f"{registry.handle(agent)}  {agent.harness}"
                   f"  {agent.status}  {agent.cwd or ''}")
        return 0
    return 1
