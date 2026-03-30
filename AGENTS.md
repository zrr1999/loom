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
- `loom agent next --role manager` now auto-plans pending `RQ-*` inbox items when routing is clear
- If the result is `ACTION  plan`, Loom needs an explicit routing choice before it can continue
- If the result is `ACTION  assign`, wake workers and hand off ready thread work with `loom manage assign`
- If the result is `ACTION  unblock`, clear stale ownership or route paused/reviewing blockers toward the human queue
- Use `uv run loom manage new-thread`, `uv run loom manage new-task`, `uv run loom manage plan`, `uv run loom manage assign`, `uv run loom agent done <task-id> --role manager`, and `uv run loom agent pause <task-id> --role manager`

### Worker

- Must run with `LOOM_WORKER_ID` set
- Loop on `uv run loom agent next`
- Finish work with `uv run loom agent done <task-id> [--output ...]`
- Ask for decisions with `uv run loom agent pause <task-id> --question ... [--options ...]`
- Maintain context with `uv run loom agent checkpoint`, `uv run loom agent resume`, `uv run loom agent mailbox`, and `uv run loom agent reply`

### Director

- Run `uv run loom agent start --role director` to bootstrap the round and `uv run loom agent next --role director` for the orchestration loop
- Read `.loom/` state and delegate the next step to the manager, workers, reviewer, or human
- Do not create, own, or update an `AgentStatus` lifecycle for the director role; worker `AgentStatus` remains worker-owned and manager tracking stays on `agents/manager/_agent.md`

### Reviewer

- Run `uv run loom agent next --role reviewer` to inspect the review queue without claiming implementation work
- Treat reviewer `ACTION  idle` as queue-focused review guidance; acceptance and rejection still happen through `uv run loom review ...` or the default human queue

## Operating rules

- Keep workflow state in files; do not add hidden runtime state for task progress
- `scheduled` tasks must have non-empty `acceptance`
- `paused` tasks must carry a `decision` block
- A task is ready only when it is `scheduled` and all `depends_on` tasks are `done`
- `loom agent next` claims executor tasks immediately
- The default human queue only handles `paused` and `reviewing` items; it does not plan inbox work
- Director orchestration is read-only with respect to agent lifecycle state: workers update their own `AgentStatus`, the manager keeps its existing singleton record, and director/reviewer flows must not introduce new runtime status records

## Contributor conventions

- Update docs when behavior changes: at minimum check `README.md`, `docs/reference/cli.md`, and `docs/reference/data-model.md`
- Keep `docs/` and `docs/reference/` for stable product/user documentation; put design notes, proposals, and other evolving planning material under `design/`
- Update `tests/e2e/test_cli.py` when command output or flow changes
- Prefer preserving existing plain-text CLI output shape unless the task explicitly changes it
- Use `just format`, `just check`, and `just test` before wrapping up code changes

## Current caveats

- Claimed tasks do not have a separate automatic timeout; recovery currently relies on thread ownership leases expiring after missed worker checkpoints
- Manager `loom agent checkpoint` / `loom agent resume` now update the singleton record under `.loom/agents/manager/_agent.md`; broader stale-thread recovery still relies on thread ownership leases plus manager coordination under `loom manage ...`
