# loom

`loom` is a filesystem-first CLI where humans drop requirements into `.loom/inbox/`, agents pull the next useful action from `.loom/threads/`, and both sides advance the same markdown state machine.

Thread directories stay human-readable (`.loom/threads/backend/`), `_thread.md` stores that readable name directly, and task files inside a thread stay sequence-only (`001.md`, `002.md`). The task id you see everywhere is now name-based (`backend-001`), so planning, dependencies, and the paused/reviewing approval flow all talk about the same readable identifier. Existing workspaces auto-migrate legacy short task ids in place on the next command, rewriting references like `planned_to` and `depends_on` without changing the current review queue semantics.

## Current scope

- Phase 1 foundation: `.loom/` initialization, frontmatter persistence, thread/task/inbox ID generation
- Phase 2 scheduling: ready-task detection, dependency checks, cross-thread priority sorting, machine-readable status
- Phase 3 lifecycle: agent `done` / `pause` / `plan`, human `accept` / `reject` / `decide`
- Phase 4 starter UX: `loom --plain` walks paused and reviewing items in queue order
- Phase 4.1 TUI-first UX: `loom` now opens the approval-queue TUI by default, while `loom tui` remains as an explicit entry
- Agent-first planning: `loom agent next` points agents to pending inbox items first, then returns the next executable task

`loom.toml` lives at the repo root and is the only config file. `loom init` will create it if missing and otherwise reuse it.

Pass `-g` to use the home-level loom workspace at `~/.loom` with `~/loom.toml`.

## Quick start

The command examples below prefer `uvx --from git+https://github.com/zrr1999/loom loom ...` so the published CLI can be used
without a local editable checkout. If you are developing inside this repository, run `uv sync --all-groups` first and then swap
those examples to `uv run loom ...`.

```bash
uv sync --all-groups
uvx prek install --hook-type pre-commit --hook-type commit-msg
uvx --from git+https://github.com/zrr1999/loom loom init --project my-app
uvx --from git+https://github.com/zrr1999/loom loom agent new-thread --name backend --priority 90 --role manager
uvx --from git+https://github.com/zrr1999/loom loom agent new-task --thread backend --title "实现 token 刷新接口" --acceptance "- [ ] POST /auth/refresh 返回新 access token" --role manager
uvx --from git+https://github.com/zrr1999/loom loom manage
uvx --from git+https://github.com/zrr1999/loom loom agent next --role manager
uvx --from git+https://github.com/zrr1999/loom loom spawn
LOOM_WORKER_ID=aaaa uvx --from git+https://github.com/zrr1999/loom loom agent next
uvx --from git+https://github.com/zrr1999/loom loom status
uvx --from git+https://github.com/zrr1999/loom loom
```

## Roles and loops

- Director: orchestrates which role should act next, keeps long-horizon planning outside the manager runtime loop, and should not silently collapse into manager behavior. The director should launch manager / reviewer / worker sub-agents, monitor them, and report back to the human instead of executing Loom runtime work directly.
- Manager: run `uvx --from git+https://github.com/zrr1999/loom loom manage` to get the canonical bootstrap loop, then keep repeating `uvx --from git+https://github.com/zrr1999/loom loom agent next --role manager`. When it returns `ACTION  plan`, create or reuse a thread and add concrete tasks. When it returns `ACTION  task`, execute the claimed task or coordinate a worker, then finish with `uvx --from git+https://github.com/zrr1999/loom loom agent done <task-id> --output <path-or-url> --role manager` or `uvx --from git+https://github.com/zrr1999/loom loom agent pause <task-id> --question '...' --role manager`.
- Reviewer: inspect tasks already in `reviewing`, compare outputs against acceptance criteria, and then help a human choose between `uvx --from git+https://github.com/zrr1999/loom loom accept <task-id>` and `uvx --from git+https://github.com/zrr1999/loom loom reject <task-id> '...'`. Reviewer work stays on top of the same task files and review notes.
- Worker: run with `LOOM_WORKER_ID` set, loop on `uvx --from git+https://github.com/zrr1999/loom loom agent next`, and use `uvx --from git+https://github.com/zrr1999/loom loom agent done`, `uvx --from git+https://github.com/zrr1999/loom loom agent pause`, `uvx --from git+https://github.com/zrr1999/loom loom agent checkpoint`, `uvx --from git+https://github.com/zrr1999/loom loom agent resume`, `uvx --from git+https://github.com/zrr1999/loom loom agent inbox`, and `uvx --from git+https://github.com/zrr1999/loom loom agent reply` to move work forward.
- Human: add requirements with `uvx --from git+https://github.com/zrr1999/loom loom inbox add "..."` or from inside the TUI, inspect status with `uvx --from git+https://github.com/zrr1999/loom loom status`, and resolve paused/reviewing work with `uvx --from git+https://github.com/zrr1999/loom loom`, `uvx --from git+https://github.com/zrr1999/loom loom --plain`, `uvx --from git+https://github.com/zrr1999/loom loom accept`, `uvx --from git+https://github.com/zrr1999/loom loom reject`, and `uvx --from git+https://github.com/zrr1999/loom loom decide`.

Default collaboration flow:

1. Director decides which role should act next, launches the right sub-agent, and keeps orchestration outside the manager loop.
2. Manager runs the canonical filesystem-backed loop and plans or claims the next concrete task.
3. Worker executes delegated task work through `LOOM_WORKER_ID`-scoped commands when manager chooses not to do the task directly.
4. Reviewer helps humans close the loop on `reviewing` work without introducing a second source of truth.

### Manager command contract

The block below is generated from Loom's canonical manager command catalog.

<!-- BEGIN: manager-command-contract -->
- Bootstrap the manager loop: `uvx --from git+https://github.com/zrr1999/loom loom manage`
- Fetch the next action: `uvx --from git+https://github.com/zrr1999/loom loom agent next --role manager`
- Create a planning thread: `uvx --from git+https://github.com/zrr1999/loom loom agent new-thread --name <name> [--priority <n>] --role manager`
- Create a planned task: `uvx --from git+https://github.com/zrr1999/loom loom agent new-task --thread <id> --title '<title>' --acceptance '<criteria>' --role manager`
- Finish completed manager-owned work: `uvx --from git+https://github.com/zrr1999/loom loom agent done <task-id> --output <path-or-url> --role manager`
- Pause for a human decision: `uvx --from git+https://github.com/zrr1999/loom loom agent pause <task-id> --question '<question>' --role manager`
- Spawn or wake a worker when configured: `uvx --from git+https://github.com/zrr1999/loom loom spawn [--threads <backend,frontend>]`
- Delegate the initial handoff: `uvx --from git+https://github.com/zrr1999/loom loom agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager`
- Send follow-up context: `uvx --from git+https://github.com/zrr1999/loom loom agent send <agent-id> '<extra context>' --ref <task-id> --role manager`
<!-- END: manager-command-contract -->

### Manager-facing access split

<!-- BEGIN: manager-command-access -->
- Shared `loom agent` commands: workers use default role semantics with `LOOM_WORKER_ID`; singleton roles may opt in with `--role manager`, `--role director`, or `--role reviewer`.
  - `uvx --from git+https://github.com/zrr1999/loom loom agent next [--role <manager|director|reviewer>]`
  - `uvx --from git+https://github.com/zrr1999/loom loom agent new-thread [--role <manager|director|reviewer>]`
  - `uvx --from git+https://github.com/zrr1999/loom loom agent new-task --thread backend [--role <manager|director|reviewer>]`
  - `uvx --from git+https://github.com/zrr1999/loom loom agent done <id> --output path [--role <manager|director|reviewer>]`
  - `uvx --from git+https://github.com/zrr1999/loom loom agent pause <id> --question ... --options ... [--role <manager|director|reviewer>]`
  - `uvx --from git+https://github.com/zrr1999/loom loom agent propose <to> "..." [--role <manager|director|reviewer>]`
  - `uvx --from git+https://github.com/zrr1999/loom loom agent send <to> "..." [--role <manager|director|reviewer>]`
- Manager entrypoints outside `loom agent`: require a clean manager process without `LOOM_WORKER_ID`.
  - `uvx --from git+https://github.com/zrr1999/loom loom manage`
  - `uvx --from git+https://github.com/zrr1999/loom loom spawn [--threads <backend,frontend>]`
<!-- END: manager-command-access -->

## Task lifecycle

- Humans write requirements into `.loom/inbox/RQ-*.md`.
- Managers convert those requirements into thread-scoped tasks under `.loom/threads/<thread>/001.md`.
- `loom agent next` prioritizes pending inbox planning work before ready tasks.
- Ready tasks are `scheduled` tasks whose dependencies are already `done`.
- `loom agent done` normally moves completed work into `reviewing`, but it pauses the task instead when the result still looks incomplete and needs a human decision.

## Tooling

- package management: `uv`
- CLI: `typer`
- frontmatter parsing: stdlib I/O + `PyYAML`
- validation: `pydantic`
- checks: `ruff`, `ty`, `prek`, `rumdl`
- docs: `zensical`
- prompts: Typer native prompts

`prek` now installs both the standard `pre-commit` hook and a `commit-msg` hook. Commit summaries should follow
`<emoji> <type>(<scope>)?: <subject>`, for example `✨ feat: add more functionality`. Auto-generated merge, revert,
`fixup!`, and `squash!` messages remain allowed. The validator remains repo-local tooling under `tools/hooks/`
rather than part of the main `src/loom/` runtime package.

Markdown linting/formatting is handled by `rumdl` with project config in `.rumdl.toml`. Use:

- `just md-fmt` → `uvx rumdl fmt --config .rumdl.toml .`
- `just md-check` → `uvx rumdl check --config .rumdl.toml .`
- `just md-check-fix` → `uvx rumdl check --fix --config .rumdl.toml .`

The configured scope includes repository docs plus `.loom/**/*.md`, while excluding ephemeral agent mailbox/checkpoint
files under `.loom/agents/**`. Because `.loom/` is gitignored, the rumdl config disables gitignore filtering and the
`prek` hook runs against the configured root scope instead of only staged filenames. If you want to refresh the base
config from upstream defaults, start with `uvx rumdl init --output .rumdl.toml` and then re-apply this repo-specific
scope/exclude policy.

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
Default remains immediate (`0.0` / `0`), preserving current behavior. In an interactive TTY, the retry loop also writes wait feedback to stderr with the current attempt, retry budget, wait seconds, and remaining retries; plain stdout stays unchanged for non-interactive or machine-consumed use.

`loom review` is a non-interactive listing of tasks waiting for human acceptance. `loom` now opens the Textual approval queue by default, while `loom --plain` keeps the original prompt-based approval loop for compatibility or environments without the TUI extra.

`loom tui` remains as an explicit Textual entry point for the same paused / reviewing work. Install it with
`uv sync --all-groups --extra tui`. Inside the TUI, use `n` to create a new inbox requirement with multi-line input, keep using `a` / `r` / `d` / `l` for review actions, press `R` to reload from disk on demand, press `w` to toggle a lightweight watch loop that polls `.loom/` every second, or press `?` for the in-app shortcut overlay. The UX intentionally borrows a few portable terminal-tool patterns (split panes, visible shortcut hints, transient status feedback) without changing Loom's workflow model. The TUI is still only a presentation layer: `.loom/` files remain the sole source of truth, and actions route through the existing scheduler/service logic without introducing a second cache.

The repo now also includes split GitHub Actions workflows under `.github/workflows/` for static checks, tests, and docs, plus a `vercel.json` that builds the Zensical site into `site/`.

`loom inbox` (without subcommand) runs an interactive planning loop for pending inbox items and plans each selected item into an initial task.

Shared `loom agent` commands default to the worker role and read the worker identity from `LOOM_WORKER_ID`. If that variable is missing, those commands fail with guidance to use `--role manager`, `--role director`, or `--role reviewer`, or to configure `LOOM_WORKER_ID`. Manager entrypoints outside `loom agent` such as `loom manage` and `loom spawn` instead require a clean manager process without `LOOM_WORKER_ID`.

Agents are stored under `.loom/agents/`. `loom init` ensures the manager record exists, and `loom spawn` creates worker records plus env files.

Managers should prefer mailbox-first delegation once a worker exists: use `loom agent propose <agent-id> ... --ref <task-id> --role manager` and `loom agent send <agent-id> ... --ref <task-id> --role manager` for handoff/context, let the worker inspect `loom agent inbox`, and keep review closure in the reviewer/human flow.

`loom agent done` normally moves a claimed task into `reviewing`, but it now gates obviously incomplete work. If the task body or output still contains TODO markers, proposal-only output, or explicit follow-up-improvement notes, the command pauses the task instead and writes a generated decision request so a human can decide how to proceed.

`loom manage` prints the manager bootstrap guide for the canonical loop around `loom agent next --role manager`, including the required `done` / `pause` task-id contract. `loom agent start` remains as the lower-level alias behind that guide.

## Agent roles

- Director/orchestrator role: `roles/loom-director.md`
- Canonical manager loop role: `roles/loom-manager.md`
- Canonical reviewer role: `roles/loom-reviewer.md`
- Canonical worker role: `roles/loom-worker.md`
- Role generation/discovery config lives in `roles.toml`

`loom manage` is the preferred manager entrypoint, while `loom agent start` remains the lower-level runtime source of truth for the manager loop behavior. The role file is the stable role definition that points contributors to that loop and command contract.


`loom spawn` replaces the old `loom agent spawn` entrypoint. Running the legacy command now prints a migration hint that points to `loom spawn`.
