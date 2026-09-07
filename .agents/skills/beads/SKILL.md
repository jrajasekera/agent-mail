---
name: beads
description: Use when working in this repository's Beads Rust tracker for durable tasks, dependencies, blocker management, multi-session handoff, or shared work state.
---

# Beads Rust

Use Beads as the shared project task system. Local plans, scratch files, and personal memories are useful, but they are not the durable source of truth for project work.

## First Step

Run:

```bash
br info --json
```

Confirm that the resolved workspace and database belong to this checkout before
mutating tracker state.

## Preferred Route

Use the `br` CLI when shell access is available.

## Core CLI Workflow

1. Find work:

```bash
br ready --json
br list --status open --json
br list --status in_progress --json
```

2. Inspect before editing:

```bash
br show <id> --json
```

3. Claim work atomically:

```bash
br update <id> --claim --json
```

4. Create durable follow-up work when implementation reveals new tasks:

```bash
br create "Short title" --description "Why this exists and what needs to be done" --type task --priority 2 --json
```

5. Close completed work:

```bash
br close <id> --reason "Completed" --json
```

## What Belongs In Beads

Use Beads for:

- shared project tasks
- blockers and dependencies
- discovered follow-up work
- work that must survive thread reset, compaction, or handoff
- status that another person or agent should be able to resume

Use agent-local planning tools only for the current turn's execution checklist. Do not treat them as shared project state.

## Rules

- Do not create markdown TODO files as the source of truth when Beads is available.
- Do not use an interactive editor for tracker mutations; use `br update` flags.
- Prefer `--json` for programmatic output.
- Use `parent-child` for hierarchy and `blocks` only for real execution prerequisites.
- Open children may make their parent epic appear blocked as a hierarchy rollup; prefer ready leaf tasks.
- Successful mutations auto-flush JSONL, but `br` never stages, commits, pulls, or pushes Git changes.
- Run `br sync --status --json` before handoff and `br sync --flush-only` as an explicit final export check.
- Do not auto-close or mutate tasks unless the work is actually complete.
