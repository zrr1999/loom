# loom

`loom` is a filesystem-first CLI where humans drop requirements into `.loom/inbox/`, agents pull the next useful action from `.loom/threads/`, and both sides advance the same markdown state machine.

Thread directories stay human-readable (`.loom/threads/backend/`), while `_thread.md` now carries a short internal thread id like `thaa`. Task files inside a thread are sequence-only (`001.md`, `002.md`), and task ids combine the internal thread id plus that per-thread sequence (for example `thaa-001`).

## Current scope

- Phase 1 foundation: `.loom/` initialization, frontmatter persistence, thread/task/inbox ID generation
- Phase 2 scheduling: ready-task detection, dependency checks, cross-thread priority sorting, machine-readable status
- Phase 3 lifecycle: agent `done` / `pause` / `plan`, human `accept` / `reject` / `decide`
- Phase 4 starter UX: `loom` with no args walks paused and reviewing items in queue order
- Agent-first planning: `loom agent next` points agents to pending inbox items first, then returns the next executable task

`loom.toml` lives at the repo root and is the only config file. `loom init` will create it if missing and otherwise reuse it.

Pass `-g` to use the home-level loom workspace at `~/.loom` with `~/loom.toml`.

## Quick start

```bash
uv sync --all-groups
uv run loom init --project my-app
uv run loom agent new-thread --name backend --priority 90
uv run loom agent new-task --thread backend --title "实现 token 刷新接口" --acceptance "- [ ] POST /auth/refresh 返回新 access token"
uv run loom agent next
uv run loom agent start
uv run loom status
uv run loom
```

## Tooling

- package management: `uv`
- CLI: `typer`
- frontmatter parsing: stdlib I/O + `PyYAML`
- validation: `pydantic`
- checks: `ruff`, `ty`, `prek`
- docs: `zensical`
- prompts: Typer native prompts

## Config

`loom.toml` example:

```toml
[project]
name = "my-app"

[agent]
inbox_plan_batch = 10
task_batch = 1
next_wait_seconds = 0.0
next_retries = 0

[threads]
default_strategy = "sequential"
default_priority = 50
```

When pending inbox items exist, `loom agent next` returns a `kind: "plan"` payload describing which `RQ-*` files the agent should convert into tasks. That work may involve both `new-thread` and `new-task`. The conversion logic is not built into `next`; the agent performs the actual restructuring.

When there is no inbox planning work, `loom agent next` claims up to `task_batch` ready tasks for the current agent and returns them as `kind: "task"`.

If neither planning nor ready tasks exist, `loom agent next` can optionally poll before returning idle: configure `[agent].next_wait_seconds` + `[agent].next_retries`, or override with `--wait-seconds` / `--retries`. Each retry re-checks both pending inbox planning work and ready tasks before the command finally prints `ACTION  idle`.
Default remains immediate (`0.0` / `0`), preserving current behavior.

`loom review` is a non-interactive listing of tasks waiting for human acceptance. Plain `loom` is the interactive approval loop for paused/reviewing items.

`loom inbox` (without subcommand) runs an interactive planning loop for pending inbox items and plans each selected item into an initial task.

Mutating `loom agent` commands infer the actor from `LOOM_AGENT_ID`. If that variable is missing, those commands fail unless you pass `--manager`, which explicitly acts as manager.

Agents are stored under `.loom/agents/`. `loom init` ensures the manager record exists, and `loom agent spawn --manager` creates executor records plus env files.

`loom agent done` normally moves a claimed task into `reviewing`, but it now gates obviously incomplete work. If the task body or output still contains TODO markers, proposal-only output, or explicit follow-up-improvement notes, the command pauses the task instead and writes a generated decision request so a human can decide how to proceed.

`loom agent start` prints a worker-oriented prompt that explains the loop around `loom agent next` and makes explicit that `loom agent done` / `loom agent pause` must always be called with a concrete task id.

## Agent roles

- Canonical manager loop role: `roles/loom-manager.md`
- Canonical reviewer role: `roles/loom-reviewer.md`
- Role generation/discovery config lives in `roles.toml`

`loom agent start` remains the runtime source of truth for the manager loop behavior. The role file is the stable role definition that points contributors to that loop and command contract.
