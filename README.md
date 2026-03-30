# loom

`loom` is a filesystem-first CLI where humans drop requirements into `.loom/inbox/`, agents pull the next useful action from `.loom/threads/`, and both sides advance the same markdown state machine.

<!-- BEGIN: task-storage-model -->
Thread directories stay human-readable (`.loom/threads/backend/`). `_thread.md` stores the canonical metadata for that directory. Task files inside a thread are sequence-only (`001.md`, `002.md`), and each task's frontmatter `id` is generated as `<thread-name>-<seq>` (for example `backend-001`).
<!-- END: task-storage-model -->

## Current scope

- Phase 1 foundation: `.loom/` initialization, frontmatter persistence, thread/task/inbox ID generation
- Phase 2 scheduling: ready-task detection, dependency checks, cross-thread priority sorting, machine-readable status
- Phase 3 lifecycle: agent `done` / `pause` / `plan`, human `accept` / `reject` / `decide`
- Phase 4 starter UX: `loom` with no args walks paused and reviewing items in queue order
- Agent-first planning: `loom agent next` points agents to pending inbox items first, then returns the next executable task

`loom.toml` lives at the repo root for project settings. `loom init` will create it if missing and otherwise reuse it. Additional repo-local config files include `loom-hooks.toml` for hook definitions and `roles.toml` for role generation/discovery.

Pass `-g` to use the home-level loom workspace at `~/.loom` with `~/loom.toml`.

## Quick start

```bash
uv sync --all-groups
uv run loom init --project my-app
uv run loom manage new-thread --name backend --priority 90
uv run loom manage new-task --thread backend --title "实现 token 刷新接口" --acceptance "- [ ] POST /auth/refresh 返回新 access token"
uv run loom agent next
uv run loom agent start
uv run loom status
uv run loom
```

## Requests and routines

Humans add new requirements to `.loom/requests/` via `loom inbox add ...` / `loom request add ...`.
Managers then resolve each request into finite task work, a long-running routine, a merge into
existing work, or a rejection. The request record keeps that decision in `resolved_as`,
`resolved_to`, and `resolution_note`.

Recurring manager-owned work lives under `.loom/routines/<routine-id>.md`. A routine is not a
task with a delayed `done`; it stays in `active`, `paused`, or `disabled` and tracks scheduling
state with:

- `interval`: normalized duration such as `30m`, `6h`, or `1d`
- `last_run`: latest completed run timestamp
- `last_result`: compact result summary such as `ok`, `no_change`, or `task_proposed`
- `## Run Log`: append-only markdown history for per-run notes

Manager/human routine surfaces are:

- `loom routine ls`: list routines with lifecycle, due state, and latest run/result
- `loom routine pause <id>` / `loom routine resume <id>`: change lifecycle without deleting history
- `loom routine run <id>`: force a `routine_trigger` message to the assigned worker
- `loom routine log <id>`: show the append-only run log

`loom status` includes a compact routines summary, and manager `loom agent next --role manager`
only surfaces due routines after pending request planning work and ready tasks.

## Worker-local worktrees

Loom now keeps optional, non-authoritative worktree records under each worker agent at
`.loom/agents/workers/<worker-id>/worktrees/`.
Only the owning worker sees and manages those records through `loom agent worktree ...`.
Thread/task truth still lives under `.loom/threads/`.

Recommended flow:

1. enter a worker shell with `LOOM_WORKER_ID` set
2. create or register a worker-local checkout path under that agent subtree
3. attach the checkout to a thread so the thread owns the visible linkage/history

```bash
export LOOM_WORKER_ID=aaap
mkdir -p .loom/agents/workers/aaap/worktrees/worktree-flow
uv run loom agent worktree add worktree-flow --branch feat/worktree-flow
uv run loom agent worktree attach worktree-flow --thread worktree-flow
uv run loom agent worktree list
```

Guardrails:

- `loom agent worktree ...` fails without `LOOM_WORKER_ID`
- each worker only sees its own worktrees under `.loom/agents/workers/<worker-id>/worktrees/`
- nested or overlapping worktree paths are rejected
- `loom agent worktree remove <name>` removes both the Loom record and the checkout on disk, while keeping thread-visible history
- if a worktree is still attached to thread metadata, clear it first with
  `loom agent worktree attach <name> --clear` or use `--force`

When a worker shell runs from one of those registered secondary checkouts, keep
`LOOM_DIR` pointed at the primary `.loom/` directory:

```bash
export LOOM_WORKER_ID=aaap
export LOOM_DIR=$PWD/.loom
cd .loom/agents/workers/aaap/worktrees/worktree-flow
uv run loom agent whoami
uv run loom agent next
```

`loom agent whoami`, `loom agent start --role worker`, and `loom agent status`
now report the current worker/worktree mapping, while worker hook/config loading
follows the active checkout root so secondary-checkout settings stay unambiguous.

## Product outputs

Human-reviewable local outputs now live under `.loom/products/`.
Loom creates `.loom/products/reports/` by default, and `loom agent done --output ...`
rewrites relative local paths into `.loom/products/...` so worker handoffs point at one
shared product tree instead of scattered per-worker folders. For human review reports,
the default convention is `.loom/products/reports/<task-id>.md`; legacy
`.loom/agents/workers/<id>/outputs/...` handoffs are also rewritten into that shared
`reports/` tree.

## Tooling

- package management: `uv`
- CLI: `typer`
- frontmatter parsing: stdlib I/O + `PyYAML`
- validation: `pydantic`
- checks: `ruff`, `ty`, `prek`, `lizard`
- docs: `zensical`
- prompts: Typer native prompts

Local validation entry points live in `justfile`: `just format`, `just check`,
`just quality-check`, and `just test` cover the standard developer workflow,
while `just ci` runs the full stack.

## Config

`loom.toml` example:

```toml
[project]
name = "my-app"

[agent]
inbox_plan_batch = 10
task_batch = 1
next_wait_seconds = 60.0
next_retries = 5

[threads]
default_strategy = "sequential"
default_priority = 50
```

`loom agent next` is now role-specific:

- director: `bootstrap`, `wake`, `coordinate`, `review`, `wait`
- manager: `plan`, `assign`, `unblock`, `wait`
- worker: `pickup`, `execute`, `escalate`, `wait`
- reviewer: queue-focused `idle`

When pending inbox items exist, worker `next` escalates that planning blocker, manager `next` auto-plans requests when routing is clear, and director `next` routes orchestration toward manager/bootstrap work instead of claiming tasks directly. Manager `next` only returns `ACTION  plan` when Loom needs an explicit routing choice such as `loom manage plan <rq-id> --thread <name>`.

When ready execution exists, worker `next` claims up to `task_batch` tasks and returns `ACTION  pickup` or `ACTION  execute`, while manager/director `next` stay orchestration-only and return assignment/wake guidance. Thread ownership records now include checkpoint-driven lease timestamps, so stale ownership can be reclaimed after the lease expires.

If no immediate action exists, `loom agent next` can optionally poll before returning a role-specific wait/idle action: configure `[agent].next_wait_seconds` + `[agent].next_retries`, or override with `--wait-seconds` / `--retries`. Each retry re-checks both pending inbox planning work and ready tasks before the command finally prints the wait result.
Default now waits `60.0` seconds between retries and retries `5` times before returning the final wait result.

`loom review` is a non-interactive listing of tasks waiting for human acceptance. Use `loom review accept`, `loom review reject`, and `loom review decide` for explicit approval actions. Plain `loom` is the interactive approval loop for paused/reviewing items.

Review records are append-only. Each `accept` or `reject` adds an entry to `review_history`, so a task can move through repeated reject → revise → review cycles without losing earlier reviewer notes. The legacy `rejection_note` remains a compatibility mirror for the latest rejection, but reviewers should treat `review_history` as the full user-facing audit trail.

Review detail is outcome-first. `loom review` and other review-detail surfaces prioritize:

1. acceptance coverage
2. delivered outputs / previews
3. append-only review history
4. secondary metadata such as task kind, dependencies, and provenance

That ordering keeps reviewer attention on what was delivered and what prior review rounds said before falling back to changed-file-style metadata.

`loom inbox` (without subcommand) runs an interactive planning loop for pending inbox items and plans each selected item into an initial task.

Worker-safe `loom agent` commands infer the actor from `LOOM_WORKER_ID`. Singleton-only mutations such as manager planning/task commands require `--role manager`, `--role director`, or `--role reviewer`.

Agents are stored under `.loom/agents/`. `loom init` ensures the manager record exists, and top-level `loom spawn` creates worker records plus env files for director/human orchestration. It always creates a fresh worker id, so Loom now enforces active/idle worker-count safety limits and requires `--force` once those caps are reached. The defaults come from `[agent].spawn_limit_active_workers = 8` and `[agent].spawn_limit_idle_workers = 2`; set either value to `0` to disable that cap.

`loom agent done` normally moves an assigned task into `reviewing`, but it now gates obviously incomplete work. If the task body or output still contains TODO markers, proposal-only output, or explicit follow-up-improvement notes, the command pauses the task instead and writes a generated decision request so a human can decide how to proceed.

Advisory hooks now use a registry model:

- `loom.toml` enables hooks with ordered `[[hooks]]` entries, and each entry declares its own `points`
- repo-local hook definitions live in `loom-hooks.toml`
- built-in hook ids such as `commit-message-policy` and `worker-done-review` are selected with `builtin = "..."`
- legacy `hooks.done.before` / `hooks.done.after` tables are no longer accepted

For a given command, Loom renders each selected hook's `before` phase in definition order, prints the main command output, then renders each hook's `after` phase in reverse order. These reminders stay soft — they never bypass validation and they never hard-block the command.

`loom agent start` now stays static: it explains the role contract, loop, and action vocabulary. Outside a worker shell you must pass `--role <manager|director|reviewer|worker>` explicitly; inside a worker shell, `LOOM_WORKER_ID` still lets plain `loom agent start` default to the worker bootstrap. `loom agent next` is the dynamic step chooser that should be re-run after each state change.

## Agent roles

- Canonical director role: `roles/loom-director.md`
- Canonical manager role: `roles/loom-manager.md`
- Canonical reviewer role: `roles/loom-reviewer.md`
- Canonical worker role: `roles/loom-worker.md`
- Role generation/discovery config lives in `roles.toml`

`loom agent start --role <director|manager|reviewer|worker>` remains the runtime source of truth for each role. The role files are the stable role definitions that point contributors back to that live bootstrap output.

If you want a reusable CLI outside `uv run ...`, install Loom once and then invoke `loom`
directly:

```bash
uv tool install git+https://github.com/zrr1999/loom
loom --help
```

### Manager command contract

<!-- BEGIN: manager-command-contract -->
- Bootstrap the manager loop: `loom manage`
- Fetch the next action: `loom agent next --role manager`
- Create a planning thread: `loom manage new-thread --name <name> [--priority <n>]`
- Create a planned task: `loom manage new-task --thread <id> --title '<title>' --acceptance '<criteria>' [--persistent]`
- Plan a pending request directly: `loom manage plan <rq-id> [--thread <name>]`
  - If Loom cannot clearly infer the target thread, the command exits non-zero and tells the manager to rerun it with `--thread` or create a new thread first.
- Finish completed manager-owned work: `loom agent done <task-id> --output <.loom/products/...|url> --role manager`
- Pause for a human decision: `loom agent pause <task-id> --question '<question>' --role manager`
- Assign a thread to a worker: `loom manage assign --thread <name> --worker <agent-id>`
- Inspect or adjust task/thread priority: `loom manage priority [--task <id> | --thread <name>] [--set <n>]`
- Delegate the initial handoff: `loom agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager`
- Send follow-up context: `loom agent send <agent-id> '<extra context>' --ref <task-id> --role manager`
<!-- END: manager-command-contract -->

### Manager-facing access split

<!-- BEGIN: manager-command-access -->
- Worker-safe `loom agent` commands default to the worker role and require `LOOM_WORKER_ID`.
  - `loom agent new-task --thread <id> --title '<title>' --acceptance '<criteria>' [--persistent]`
  - `loom agent next`
  - `loom agent done <id> --output <.loom/products/...|url>`
  - `loom agent pause <id> --question ... --options ...`
  - `loom agent checkpoint "..."`
  - `loom agent resume`
  - `loom agent mailbox`
  - `loom agent mailbox-read <msg-id>`
  - `loom agent whoami`
  - `loom agent worktree list|add|attach|remove`
  - `loom agent ask <to> "..."`
  - `loom agent propose <to> "..."`
  - `loom agent reply <msg-id> "..."`
- Mailbox commands can also target singleton mailboxes with `--role manager`, `--role director`, or `--role reviewer`.
  - `loom agent mailbox --role <manager|director|reviewer>`
  - `loom agent mailbox-read <msg-id> --role <manager|director|reviewer>`
  - `loom agent reply <msg-id> "..." --role <manager|director|reviewer>`
- Singleton-only `loom agent` commands require `--role manager`, `--role director`, or `--role reviewer`.
  - `loom agent send <to> "..." [--role <manager|director|reviewer>]`
- Read-only status remains available without a worker id: `loom agent status`
- Director/orchestrator bootstrap in this repo: `just start`.
- Director and human share the full top-level `loom` command surface.
- Manager entrypoints outside `loom agent`: require a clean manager process without `LOOM_WORKER_ID`.
  - `loom manage`
  - `loom manage new-thread --name <name> [--priority <n>]`
  - `loom manage new-task --thread <id> --title '<title>' --acceptance '<criteria>'`
  - `loom manage plan <rq-id> [--thread <name>]`
  - `loom manage assign --thread <name> --worker <agent-id>`
  - `loom manage priority [--task <id> | --thread <name>] [--set <n>]`
- Human/director worker-launch entrypoint: `loom spawn [--threads <backend,frontend>] [--force]`
- Reviewer/human entrypoints outside `loom agent`:
  - `loom review`
  - `loom review accept <id>`
  - `loom review reject <id> "reason"`
  - `loom review decide <id> <option>`
<!-- END: manager-command-access -->

Managers can also use `loom agent checkpoint ... --role manager` and `loom agent resume --role manager` to maintain the singleton checkpoint stored at `.loom/agents/manager/_agent.md`.

Terminology note: `.loom/requests/` / `loom inbox` is the project request inbox, while `loom agent mailbox` is the per-agent message queue for handoffs, questions, and replies. Legacy `loom agent inbox` / `inbox-read` aliases still work for compatibility.
