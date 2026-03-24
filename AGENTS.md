# AGENTS

## Purpose

`loom` is a filesystem-first workflow CLI. Humans add requirements to `.loom/inbox/`, managers convert them into executable tasks under `.loom/threads/`, and executors move those tasks through the markdown state machine until a human accepts or rejects the result.

## Repo layout

- `src/loom/` - CLI, state models, scheduler, persistence, and agent flows
- `tests/e2e/` - command-level behavior tests; many assertions depend on exact CLI text
- `tests/unit/` - model and validation coverage
- `docs/reference/` - CLI and data model notes
- `loom.toml` - single config file
- `.loom/` - runtime state, agent records, inbox items, threads, and event log

## Roles

### Human

- Add requirements with `uv run loom inbox add "..."`
- Review state with `uv run loom status`, `uv run loom review`, and `uv run loom log`
- Resolve queue items with `uv run loom`, `uv run loom review accept`, `uv run loom review reject`, `uv run loom review decide`, and `uv run loom release`

### Manager

- Run `uv run loom agent next --role manager`
- If the result is `ACTION  plan`, create threads and tasks from pending `RQ-*` inbox items
- If the result is `ACTION  task`, execute or coordinate the ready task inside the assigned thread
- Use `uv run loom manage new-thread`, `uv run loom manage new-task`, `uv run loom manage plan`, `uv run loom manage assign`, `uv run loom agent done <task-id> --role manager`, and `uv run loom agent pause <task-id> --role manager`

### Worker

- Must run with `LOOM_WORKER_ID` set
- Loop on `uv run loom agent next`
- Finish work with `uv run loom agent done <task-id> [--output ...]`
- Ask for decisions with `uv run loom agent pause <task-id> --question ... [--options ...]`
- Maintain context with `uv run loom agent checkpoint`, `uv run loom agent resume`, `uv run loom agent mailbox`, and `uv run loom agent reply`

## Operating rules

- Keep workflow state in files; do not add hidden runtime state for task progress
- `scheduled` tasks must have non-empty `acceptance`
- `paused` tasks must carry a `decision` block
- A task is ready only when it is `scheduled` and all `depends_on` tasks are `done`
- `loom agent next` claims executor tasks immediately
- The default human queue only handles `paused` and `reviewing` items; it does not plan inbox work

## Contributor conventions

- Update docs when behavior changes: at minimum check `README.md`, `docs/reference/cli.md`, and `docs/reference/data-model.md`
- Keep `docs/` and `docs/reference/` for stable product/user documentation; put design notes, proposals, and other evolving planning material under `design/`
- Update `tests/e2e/test_cli.py` when command output or flow changes
- Prefer preserving existing plain-text CLI output shape unless the task explicitly changes it
- Use `just format`, `just check`, and `just test` before wrapping up code changes

## Current caveats

- Inbox-to-task planning is still manual; `loom agent next` reports planning work but does not perform it
- Claimed tasks have no automatic timeout
- Some manager-facing agent surfaces are not implemented yet, including manager `checkpoint`, `resume`, and `inbox`
- Docs currently describe a few flows that are still evolving, so check source behavior in `src/loom/agent.py` and `src/loom/cli.py` when in doubt
