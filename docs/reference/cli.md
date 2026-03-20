# CLI

## Human commands

- `loom init`
- `loom inbox add "..."`
- `loom inbox`
- `loom status`
- `loom manage`
- `loom spawn [--threads <backend,frontend>]`
- `loom review`
- `loom accept <id>`
- `loom reject <id> "reason"`
- `loom decide <id> <option>`
- `loom release <id> "reason"`
- `loom tui`

Running `loom` with no subcommand now opens the current paused / reviewing approval queue in the Textual TUI. Use `loom --plain` to keep the original prompt-based approval loop when the TUI extra is unavailable or undesired.

Difference between `loom` and `loom review`:

- `loom`: default full-screen TUI for `paused` and `reviewing`
- `loom --plain`: prompt-based approval loop for `paused` and `reviewing`
- `loom review`: non-interactive list of reviewing items only
- `loom inbox`: interactive planning loop for pending inbox requirements
- `loom tui`: explicit alias for the same full-screen Textual approval queue

- `paused`: use compact keyed actions like `d`, `s`, `o`, or `?`
- `reviewing`: use compact keyed actions like `a`, `r`, `s`, `o`, or `?`
- `inbox`: use compact keyed actions like `p`, `s`, `o`, or `?`

### `loom tui`

`loom tui` opens the same full-screen Textual approval queue that `loom` now launches by default.

- Requires the `tui` optional dependency: `uv sync --extra tui`
- Phase 1 scope: browse `paused` / `reviewing` queue items, add new inbox requirements, then reuse the existing accept / reject / decide / release operations
- Aligns with `design/tui-plan.md`: this is a presentation layer over the same filesystem-backed workflow, not a second source of truth
- Keyboard shortcuts inside the TUI:
  - `a` — accept the selected reviewing task → done
  - `r` — reject the selected reviewing task → scheduled (prompts for reason)
  - `d` — decide on the selected paused task → scheduled (prompts for choice)
  - `n` — add a new inbox requirement with multi-line input
  - `l` — release the selected claimed queue item → scheduled (prompts for reason)
  - `R` — reload the queue from `.loom/` on demand
  - `w` — toggle watch mode, which polls `.loom/` every second and redraws when queue membership changes
  - `?` — open the in-app shortcut/help overlay
  - `q` — quit
- New requirements are written to `.loom/inbox/RQ-*.md`
- `.loom/` files remain the source of truth; the TUI reuses the same scheduler/service layer as plain CLI commands and does not keep a separate cache of task state
- Recent UX polish borrows portable patterns from terminal coding tools (Copilot / Claude Code / Codex / OpenCode): split list/detail panes, visible shortcut hints, a transient status line, and a lightweight help overlay instead of adding hidden modes

`loom init` ensures both `.loom/` and root `loom.toml` exist. It is idempotent. Pass `-g` to use the home-level loom workspace.

## Agent commands

- `loom agent new-thread [--role <manager|director|reviewer>]`
- `loom agent new-task --thread backend [--kind <implementation|design>] [--role <manager|director|reviewer>]`
- `loom agent next [--role <manager|director|reviewer>]`
- `loom agent start [--role <manager|director|reviewer|worker>]`
- `loom agent whoami [--role <manager|director|reviewer>]`
- `loom agent checkpoint "..."`
- `loom agent resume`
- `loom agent inbox`
- `loom agent send <to> "..." [--role <manager|director|reviewer>]`
- `loom agent ask <to> "..." [--role <manager|director|reviewer>]`
- `loom agent propose <to> "..." [--role <manager|director|reviewer>]`
- `loom agent reply <msg-id> "..."`
- `loom agent done <id> --output path [--role <manager|director|reviewer>]`
- `loom agent pause <id> --question ... --options ... [--role <manager|director|reviewer>]`
- `loom agent plan <rq-id>`
- `loom agent status`

If `loom agent pause` is called without `--question`, it falls back to a small terminal wizard.

`loom agent done` only sends work to `reviewing` when the task looks review-ready. If the task body or output still contains TODO markers, proposal-only output, or explicit follow-up-improvement notes, the command pauses the task instead and generates a decision block for the human queue.

`loom agent next` first returns pending inbox items that should be planned into tasks, then returns ready tasks.

`loom status` and `loom agent status` now separate lifecycle from deliverable type: a task can be `done` while still being `kind: design`. When that happens and no implementation task in the same thread is done yet, the capability summary reports the thread as `design-only` and shows the current implementation follow-up task/status when available. Review/detail output also prints the task kind so accepted design work does not read like shipped implementation.

- thread arguments still use human-facing thread names like `backend`
- task ids shown in CLI output use the readable name-based form like `backend-001`
- the same readable task ids are used consistently in `loom review`, plain `loom`, `loom accept/reject/decide`, `planned_to`, and `depends_on`

- planning batch size comes from `agent.inbox_plan_batch` and defaults to `10`
- ready-task batch size comes from `agent.task_batch` and defaults to `1`
- idle wait/retry defaults come from `agent.next_wait_seconds` (`0.0`) and `agent.next_retries` (`0`)
- planning output may imply both `new-thread` and `new-task`

`loom agent next` supports idle polling controls:

- `--wait-seconds <n>`: seconds to sleep between idle retries
- `--retries <n>`: retry count when the action is idle

By default (`0.0` seconds, `0` retries), behavior is unchanged: a single immediate check.
Each retry re-checks both pending inbox planning work and ready tasks before the command finally returns `ACTION  idle`.
When stdout/stderr are both attached to an interactive TTY, the wait loop also emits stderr-only feedback with the current attempt, retry budget, wait seconds, and remaining retries. Plain stdout keeps the existing machine-readable contract.

Important: `loom agent next` is no longer read-only for task execution. It claims returned tasks for the current agent, but it still does not perform inbox-to-task planning for you.

Current behavior note: worker-safe `loom agent` commands default to the worker role and infer the acting worker from `LOOM_WORKER_ID`. If that environment variable is missing, the command fails with guidance to use `--role manager`, `--role director`, or `--role reviewer`, or to configure `LOOM_WORKER_ID`. Singleton-only `loom agent` commands such as `new-thread`, `new-task`, and raw `send` require one of those explicit role overrides. Manager entrypoints outside `loom agent` like `loom manage` and `loom spawn` instead require a clean process without `LOOM_WORKER_ID`, and workers are explicitly blocked from top-level `loom review`; switch to a clean reviewer/human process before using the review queue.

`loom manage` returns the same concise manager bootstrap prompt describing the expected loop around `loom agent next --role manager`, `done --role manager`, and `pause --role manager`. `loom agent start --role <manager|director|reviewer|worker>` remains available as the role-specific bootstrap surface.

That prompt includes a current-state summary, the practical command set for managers, and explicitly states that `loom agent done` and `loom agent pause` always require a specific task id.

Command-boundary note: we deliberately keep `just start` as the repo-local director/orchestrator bootstrap instead of adding a top-level `loom start`, and we keep `loom manage` / `loom review` as focused entrypoints instead of adding `loom manage start` or `loom review start`. Human review decisions stay adjacent at the top level with `loom accept`, `loom reject`, and `loom decide`, while `loom review` itself remains the non-interactive listing surface for items already in `reviewing`.

### Manager command contract

The block below is generated from Loom's canonical manager command catalog.

<!-- BEGIN: manager-command-contract -->
- Bootstrap the manager loop: `loom manage`
- Fetch the next action: `loom agent next --role manager`
- Create a planning thread: `loom agent new-thread --name <name> [--priority <n>] --role manager`
- Create a planned task: `loom agent new-task --thread <id> --title '<title>' --acceptance '<criteria>' --role manager`
- Finish completed manager-owned work: `loom agent done <task-id> --output <path-or-url> --role manager`
- Pause for a human decision: `loom agent pause <task-id> --question '<question>' --role manager`
- Spawn or wake a worker when configured: `loom spawn [--threads <backend,frontend>]`
- Delegate the initial handoff: `loom agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager`
- Send follow-up context: `loom agent send <agent-id> '<extra context>' --ref <task-id> --role manager`
<!-- END: manager-command-contract -->

### Manager-facing access split

<!-- BEGIN: manager-command-access -->
- Worker-safe `loom agent` commands default to the worker role and require `LOOM_WORKER_ID`.
  - `loom agent next`
  - `loom agent done <id> --output path`
  - `loom agent pause <id> --question ... --options ...`
  - `loom agent checkpoint "..."`
  - `loom agent resume`
  - `loom agent inbox`
  - `loom agent inbox-read <msg-id>`
  - `loom agent whoami`
  - `loom agent ask <to> "..."`
  - `loom agent propose <to> "..."`
  - `loom agent reply <msg-id> "..."`
- Singleton-only `loom agent` commands require `--role manager`, `--role director`, or `--role reviewer`.
  - `loom agent new-thread [--role <manager|director|reviewer>]`
  - `loom agent new-task --thread backend [--role <manager|director|reviewer>]`
  - `loom agent send <to> "..." [--role <manager|director|reviewer>]`
- Read-only status remains available without a worker id: `loom agent status`
- Director/orchestrator bootstrap in this repo: `just start`.
- Director and human share the full top-level `loom` command surface.
- Manager entrypoints outside `loom agent`: require a clean manager process without `LOOM_WORKER_ID`.
  - `loom manage`
  - `loom spawn [--threads <backend,frontend>]`
- Reviewer entrypoint outside `loom agent`: `loom review`
<!-- END: manager-command-access -->

The preferred worker-registration entrypoint is `loom spawn`. Running `loom agent spawn` now prints a migration hint that points to `loom spawn`.

Worker runtime state now lives under `.loom/agents/workers/<agent-id>/`, including `_agent.md`, inbox folders, and the generated `<agent-id>.env` file. The manager singleton record remains at `.loom/agents/_manager.md`. Existing workspaces with legacy worker directories at `.loom/agents/<agent-id>/` are upgraded in place the next time a Loom command resolves that workspace.

## Shared-state worktree workflow

Loom's current worktree story is a **manual, documentation-first contract**: use Git to create and remove secondary checkouts, but keep one shared `.loom/` runtime across all of them.

### Create a secondary checkout

From the primary repository root:

```bash
git worktree add ../loom-worktrees/worktree-flow worktree-flow
```

The new checkout gets its own repository files, but it should not get its own independent Loom runtime.

### Expose the shared `.loom` state

Inside the secondary checkout, point the local `.loom` path back to the primary checkout:

```bash
cd ../loom-worktrees/worktree-flow
ln -s ../../loom/.loom .loom
```

This keeps task claims, inbox items, checkpoints, mailbox messages, and review state in one shared filesystem tree. The worktree is only an alternate repository root for edits and commands.

### Start a worker inside that checkout

Run worker-safe commands from the secondary checkout with a concrete worker id:

```bash
LOOM_WORKER_ID=aaah uv run loom agent start --role worker
LOOM_WORKER_ID=aaah uv run loom agent resume
LOOM_WORKER_ID=aaah uv run loom agent next --thread worktree-flow
```

Use the same `LOOM_WORKER_ID` for the rest of the worker session. Shared `.loom/` state means a claim made in one checkout is immediately visible from the main checkout and from any other linked worktree.

### Inspect active mappings

There is not yet a dedicated `loom worktree list` command, so inspect both Git checkout paths and Loom worker/task state:

```bash
git worktree list
uv run loom agent status
LOOM_WORKER_ID=aaah uv run loom agent whoami
```

- `git worktree list` shows the currently attached checkout paths
- `loom agent status` shows claimed tasks and active worker identities from the shared `.loom/` truth
- `loom agent whoami` confirms which worker identity the current shell is using

Together, those commands provide the current worker/worktree visibility contract for this slice.

### Remove worktrees safely

Before removing a secondary checkout:

1. confirm the worker has finished or paused its claimed Loom task
2. confirm shared status no longer shows active work that depends on that checkout
3. confirm the checkout itself is clean with `git status --short`

Then remove it with Git:

```bash
git worktree remove ../loom-worktrees/worktree-flow
```

If you need to publish task output, prefer repository-relative values such as `README.md`, `docs/reference/cli.md`, or `src/loom/cli.py` when running `loom agent done --output ...`. Reviewers should not need a worker-local absolute path to locate the result.

## Default role split

- Director: stays outside the manager runtime loop, decides which role should act next, and should launch manager / reviewer / worker sub-agents instead of executing runtime work directly.
- Manager: runs `loom agent next --role manager`, handles planning, and either executes the claimed task directly or delegates it.
- Reviewer: inspects `reviewing` tasks and helps a human decide whether to accept or reject them.
- Worker: runs with `LOOM_WORKER_ID` set and performs claimed task work via `loom agent next`, `done`, `pause`, `checkpoint`, `resume`, `inbox`, and `reply`.

All four roles operate on the same filesystem-backed state. Director orchestration may live in human conversation or docs, but runtime truth stays in `.loom/`; the director should supervise and report rather than run Loom commands directly.

Managers should prefer mailbox-first delegation once a worker exists:

- `loom agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager`
- `loom agent send <agent-id> '<extra context>' --ref <task-id> --role manager`
- worker reads with `loom agent inbox` / `loom agent inbox-read`
- worker answers with `loom agent reply <msg-id> '...'`

## Repo hook policy

- run `uvx prek install --hook-type pre-commit --hook-type commit-msg` after syncing dependencies
- the repo's `commit-msg` hook expects `<emoji> <type>(<scope>)?: <subject>`
- example: `✨ feat: add more functionality`
- auto-generated `Merge ...`, `Revert ...`, `fixup! ...`, and `squash! ...` messages are allowed
- the validator is kept as repo-local tooling under `tools/hooks/`, not mixed into the main `src/loom/` runtime package
- if another repo wants the same rule today, copy the validator, its hook config, and `tests/unit/hooks/test_commit_message_validator.py`; only split it into a separate repo once several repos need shared releases or configurable rules

### Timely commit workflow

The commit-msg hook enforces message format, but well-shaped history also depends on **when** and **how often** you commit. Implementers should commit meaningful completed changes promptly during their work, not accumulate a single monolithic diff at the end of a task.

**When to commit:**

- After completing a logical step (new function, passing test, config change, doc update)
- When switching focus from one concern to another (e.g., implementation → tests → docs)
- Whenever you could describe the change as a single coherent sentence matching `<emoji> <type>(<scope>)?: <subject>`

**How to keep commits focused:**

- One commit per concern — don't mix a feature change with an unrelated refactor or doc fix
- Use `git add -p` to stage related hunks separately if a working session touches multiple concerns
- Prefer several small, well-named commits over one large catch-all commit

**Why this matters for Loom workflows:**

- Workers finish tasks with `loom agent done`, which sends work to human review; incremental commits let reviewers follow the reasoning step by step
- `loom agent checkpoint` captures implementation context, but Git history is the durable record of what changed and why
- Smaller commits reduce merge conflicts when multiple workers operate on the same thread

Global workspace guidance (`-g`) is only shown in `loom agent start` when global mode is currently active.

Director/orchestrator bootstrap in this repo lives at `just start`, which prints the explicit prompt/instructions that any suitable agent or human can use.

Dedicated manager loop role definition lives at `roles/loom-manager.md`.

The reviewer role definition lives at `roles/loom-reviewer.md`.

The worker role definition lives at `roles/loom-worker.md`.

Role generation/discovery settings for this repo live in `roles.toml`.

Human commands keep two interactive surfaces separate:

- `loom` handles approval-only work (`paused` / `reviewing`), and does not plan inbox items
- `loom inbox` handles pending inbox planning work
