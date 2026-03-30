# CLI

## Human commands

- `loom init`
- `loom request add "..."`
- `loom request ls`
- `loom inbox add "..."`
- `loom inbox`
- `loom routine ls`
- `loom routine pause <id>`
- `loom routine resume <id>`
- `loom routine run <id>`
- `loom routine log <id>`
- `loom manage`
- `loom manage new-thread --name backend`
- `loom manage new-task --thread backend`
- `loom manage plan <rq-id> [--thread <name>]`
- `loom manage assign --thread backend --worker worker-123`
- `loom status`
- `loom manage priority [--task <id> | --thread <name>] [--set <n>]`
- `loom spawn [--threads <backend,frontend>] [--force]`
- `loom review`
- `loom review accept <id>`
- `loom review reject <id> "reason"`
- `loom review decide <id> <option>`
- `loom release <id> "reason"`
- `loom tui`

Running `loom` with no subcommand now processes the current paused / reviewing approval queue.

Difference between `loom` and `loom review`:

- `loom`: interactive approval loop for `paused` and `reviewing`
- `loom review`: non-interactive list of reviewing items only
- `loom review accept` / `reject` / `decide`: explicit review and approval mutations from the plain CLI
- `loom request ls`: list requests and their resolution state
- `loom inbox`: interactive planning loop for pending requests via the compatibility alias
- `loom tui`: full-screen Textual TUI for the same approval queue

### Review history and repeated rejection

`loom review` is designed for multi-round review, not just a single final gate.

- every `loom review reject <id> "reason"` appends a new `review_history` entry
- a worker can revise the same task and send it back to `reviewing`
- the next `loom review` run shows the full append-only review trail instead of overwriting the prior rejection
- `rejection_note` is kept only as a compatibility mirror for the latest rejection

In practice, repeated rejection looks like:

1. worker moves a task to `reviewing`
2. reviewer runs `loom review reject <id> "reason"`
3. worker addresses the feedback and re-submits the same task
4. reviewer sees both the new state and the earlier `review_history` entries on the next review pass

### Outcome-first review detail

Review detail output is intentionally ordered around outcomes first, metadata second.

When `loom review` shows a task, it emphasizes:

1. acceptance criteria / acceptance coverage
2. output paths and readable previews when available
3. append-only `review_history`
4. secondary metadata such as task kind, dependencies, and provenance

This keeps the human review flow focused on what was delivered and how prior review rounds resolved before dropping into changed-file-style metadata.

- `paused`: use compact keyed actions like `d`, `s`, `o`, or `?`
- `reviewing`: use compact keyed actions like `a`, `r`, `s`, `o`, or `?`
- `inbox`: use compact keyed actions like `p`, `s`, `o`, or `?`

### `loom tui`

`loom tui` opens an optional full-screen Textual TUI that covers the same approval queue as plain `loom`.

- Requires the `tui` optional dependency: `uv sync --extra tui`
- Phase 1 scope: browse `paused` / `reviewing` queue items, then reuse the existing accept / reject / decide / release operations
- Aligns with `design/tui-plan.md`: this is an optional second presentation layer over the same filesystem-backed workflow
- Keyboard shortcuts inside the TUI:
  - `a` — accept the selected reviewing task → done
  - `r` — reject the selected reviewing task → scheduled (prompts for reason)
  - `d` — decide on the selected paused task → scheduled (prompts for choice)
  - `l` — release the selected thread-owned queue item → scheduled (prompts for reason)
  - `R` — refresh the queue from disk
  - `q` — quit
- `.loom/` files remain the source of truth; the TUI reuses the same scheduler/service layer as plain CLI commands

`loom init` ensures both `.loom/` and root `loom.toml` exist. It is idempotent. Pass `-g` to use the home-level loom workspace.

### `loom routine`

`loom routine` is the stable manager/human surface for recurring work stored under
`.loom/routines/`.

- `loom routine ls`: list each routine's lifecycle (`active`, `paused`, `disabled`), interval, due state, latest run, latest result, and backing file
- `loom routine pause <id>`: move an active routine to `paused`
- `loom routine resume <id>`: move a paused or disabled routine back to `active`
- `loom routine run <id>`: force-trigger the routine by sending a `routine_trigger` message to its assigned worker; Loom does not run the routine inline from the manager CLI
- `loom routine log <id>`: show the routine's append-only `## Run Log` section

`loom status` summarizes routine counts plus the next due routine. Manager
`loom agent next --role manager` checks pending request planning work first, then ready
tasks, and only then surfaces due routines with `ACTION  trigger`.

### `loom agent worktree`

`loom agent worktree` is a worker-only helper around worker-local worktree records plus
thread-owned worktree linkage/history. It fails unless `LOOM_WORKER_ID` is set, and it
only reads records from the current worker subtree.

- `loom agent worktree add`: register a worktree directory under `.loom/agents/workers/<id>/worktrees/`
- `loom agent worktree attach`: link or unlink that checkout from thread-owned metadata
- `loom agent worktree list`: show only the current worker's worktrees plus path, branch, status, worker, and thread
- `loom agent worktree remove`: delete the worker-local Loom record, remove the checkout directory, and preserve thread-visible history

Current guardrails:

- every `loom agent worktree ...` command requires `LOOM_WORKER_ID`
- a worker can only see and manage worktrees inside its own `.loom/agents/workers/<id>/worktrees/` subtree
- nested or overlapping worktree paths are rejected
- removing an attached worktree record requires `loom agent worktree attach <name> --clear` first, unless `--force` is passed
- thread metadata is authoritative for worktree linkage/history and PR artifacts; worker-local records remain convenience indexes

Secondary-checkout runtime notes:

- keep `LOOM_DIR` pointed at the primary `.loom/` directory even when the worker shell `cd`s into a registered worktree checkout
- `loom agent whoami`, `loom agent start --role worker`, and `loom agent status` report the current worker/worktree mapping for the active shell
- worker-only config lookup for commands such as `loom agent next` follows the matched checkout root, so repo-local `loom.toml` hooks remain unambiguous when the worker runs from a secondary checkout

Example:

```bash
export LOOM_WORKER_ID=aaap
export LOOM_DIR=/path/to/main-checkout/.loom
cd /path/to/main-checkout/.loom/agents/workers/aaap/worktrees/worktree-flow
loom agent whoami
loom agent start --role worker
loom agent next
```

## Agent commands

- `loom agent next`
- `loom agent start`
- `loom agent whoami`
- `loom agent worktree list` / `add` / `attach` / `remove`
- `loom agent checkpoint "..."`
- `loom agent resume`
- `loom agent mailbox`
- `loom agent mailbox-read <msg-id>`
- `loom agent send <to> "..."`
- `loom agent reply <msg-id> "..."`
- `loom agent done <id> --output <.loom/products/...|url>`
- `loom agent pause <id> --question ... --options ...`
- `loom agent status`

`loom agent new-task` remains available for autonomous worker/agent task creation inside an active runtime. Human-facing planning should still prefer `loom manage new-task`.

Managers can also run `loom agent checkpoint ... --role manager` and `loom agent resume --role manager`; those commands update/read the singleton record at `.loom/agents/manager/_agent.md`.

Legacy compatibility aliases still exist for `loom agent new-thread` and `loom agent plan`, but the canonical manager-facing locations are now under `loom manage`.

Terminology note: `loom inbox` / `.loom/requests/` is the project request inbox, while `loom agent mailbox` is the per-agent message queue. Legacy `loom agent inbox` and `loom agent inbox-read` aliases still work for compatibility.

If `loom agent pause` is called without `--question`, it falls back to a small terminal wizard.

`loom agent done` now supports an explicit delivery contract:

- `--ready`: declare that this handoff is intentionally review-ready even if the freeform output contains words like `TODO`
- `--artifact <path>`: attach structured delivery artifacts; repeatable, and local relative paths normalize under `.loom/products/...`
- `--pr-url <url>`: attach structured PR URLs without relying on URL scraping from `--output`

Without an explicit delivery contract, `loom agent done` still uses the legacy safety heuristics: if the task body or output contains TODO markers, proposal-only output, or explicit follow-up-improvement notes, the command pauses the task instead and generates a decision block for the human queue.

If the task has `persistent: true`, `loom agent done` records the latest output/delivery metadata but keeps the task in `scheduled` so it can be picked up again in a future session without human review.

When `--output` is a relative local path, Loom stores it under `.loom/products/...` so
review handoffs reference a shared products tree. The default human-review report
convention is `.loom/products/reports/<task-id>.md`, and legacy
`.loom/agents/workers/<id>/outputs/...` paths are rewritten into that shared
`reports/` subtree. URLs and freeform multiline summaries are preserved as-is.

`loom agent next` checks pending requests before ready tasks. For manager role, it now auto-plans pending requests when routing is clear, then continues to the next manager action. Worker/director flows still surface planning as an upstream dependency.

Auto-planning also de-duplicates obvious overlap: when a new request matches an existing scheduled task in the target thread, Loom resolves the request as `merged`, links it to the existing task, and raises that task's priority instead of creating a duplicate.

- thread arguments still use human-facing thread names like `backend`
- task ids shown in CLI output use the readable name-based form like `backend-001`

- planning batch size comes from `agent.inbox_plan_batch` and defaults to `10`
- ready-task batch size comes from `agent.task_batch` and defaults to `1`
- idle wait/retry defaults come from `agent.next_wait_seconds` (`60.0`) and `agent.next_retries` (`5`)
- planning output may imply both `new-thread` and `new-task`

`loom agent next` supports idle polling controls:

- `--wait-seconds <n>`: seconds to sleep between idle retries
- `--retries <n>`: retry count when the action is idle

By default, Loom waits `60.0` seconds between idle checks and retries `5` times before returning the final wait/idle action.
Each retry re-checks both pending request planning work and ready tasks before the command finally returns the role-specific wait/idle action.

Important: `loom agent next` is no longer read-only for task execution. Worker calls claim the returned thread(s) for the current worker, and manager calls may also plan pending requests as part of the same loop. `ACTION  plan` now means Loom needs an explicit manager routing choice rather than a generic reminder to do manual planning.

Claimed thread ownership now carries a visible lease in `_thread.md`. `loom agent checkpoint` refreshes that lease for every thread currently owned by the worker, and stale ownership becomes reclaimable by another worker once the stored lease expires. The default lease window matches `agent.offline_after_minutes`.

`loom agent next` can also append role-specific **soft hooks**. These are advisory reminders only: they never block the command, mutate task state, or gate completion. `loom.toml` selects ordered hooks with `[[hooks]]`; each entry declares its own `points`; repo-local definitions live in `loom-hooks.toml`; built-ins use `builtin = "..."`. Loom renders every selected hook `before` phase in definition order and every `after` phase in reverse order.

```toml
[[hooks]]
id = "reminders"
points = ["next"]

[[hooks]]
builtin = "commit-message-policy"
points = ["next"]
```

```toml
[hooks.reminders]
points = ["next"]

[hooks.reminders.before]
all = "Shared reminder shown before the next output."
worker = """
Run the focused tests before `loom agent done`.
Double-check any generated output before handing off.
"""

[hooks.reminders.after]
manager = "Keep task handoffs mailbox-first."
```

- supported per-role keys: `all`, `manager`, `worker`, `director`, `reviewer`
- supported built-in hook ids today: `commit-message-policy`
- worker output with `builtin = "commit-message-policy"` includes a ready-made reminder about the repo's commit message format
- `loom agent next` can render both `SOFT HOOKS  next/before` and `SOFT HOOKS  next/after` sections around role-specific actions such as `pickup`, `assign`, `wake`, `escalate`, and `wait`

`loom agent done` uses the same lifecycle model. Each named hook entry can contribute a `before` phase, an `after` phase, or both. These reminders stay advisory even when `loom agent done` routes the task to `paused` instead of `reviewing`.

```toml
[[hooks]]
builtin = "worker-done-review"
points = ["done"]

[[hooks]]
id = "review-pass"
points = ["done"]
```

```toml
[hooks.review-pass]
points = ["done"]

[hooks.review-pass.before]
worker = "Optional repo-specific reminder layered on top of the built-in review checklist."

[hooks.review-pass.after]
worker = """
If this paused, summarize the blocker and ask for a decision quickly.
If this is reviewing, make sure the handoff names the tests you ran and the output path.
"""
```

- supported per-role keys: `all`, `manager`, `worker`, `director`, `reviewer`
- supported built-in done hook ids today: `worker-done-review`
- `worker-done-review` asks the worker to inspect the diff, question whether any code growth earns its keep, look for simplifications, and confirm checkpoint/tests before finishing
- legacy `hooks.done.before` / `hooks.done.after` config is no longer accepted
- `before` phases print before Loom validates/submits `loom agent done`
- `after` phases print after the result block so workers can sanity-check the handoff they just produced
- when no done hooks are configured, `loom agent done` output stays unchanged apart from the normal result block

Current behavior note: worker-safe `loom agent` commands infer the acting worker from `LOOM_WORKER_ID`. If that environment variable is missing, the command fails with guidance to use `--role manager`, `--role director`, or `--role reviewer`, or to set `LOOM_WORKER_ID`. `loom agent start` now also requires an explicit `--role` outside a worker shell; only shells that already set `LOOM_WORKER_ID` may omit `--role` and fall back to the worker bootstrap.

Role-scoped `loom agent next` output is intentionally narrow for singleton roles: reviewer `next` only reports review-ready queue state and does not dump pending request or execution-task details, director `next` stays orchestration-only, and manager `next` no longer tells the manager to execute implementation work directly.

`loom spawn` always allocates a fresh worker id; it does not auto-reuse existing workers. To avoid silent worker-count growth, Loom refuses to spawn once the configured active/idle worker caps are reached unless `--force` is passed. The default caps are `[agent].spawn_limit_active_workers = 8` and `[agent].spawn_limit_idle_workers = 2`; set either value to `0` to disable that cap.

`loom agent start` is static bootstrap guidance: role contract, loop, guardrails, and action vocabulary. Outside a worker shell, pass `--role <manager|director|reviewer|worker>` explicitly. Inside a worker shell, plain `loom agent start` still resolves to the worker bootstrap. `loom agent next` is the dynamic single-step chooser to re-run after each state change.

## Repo hook policy

- run `uvx prek install --hook-type pre-commit --hook-type commit-msg` after syncing dependencies
- the repo's `commit-msg` hook expects `<emoji> <type>(<scope>)?: <subject>`
- example: `✨ feat: add more functionality`
- auto-generated `Merge ...`, `Revert ...`, `fixup! ...`, and `squash! ...` messages are allowed

Global workspace guidance (`-g`) is only shown in `loom agent start` when global mode is currently active.

Canonical role files live at:

- `roles/loom-director.md`
- `roles/loom-manager.md`
- `roles/loom-reviewer.md`
- `roles/loom-worker.md`

Use `loom agent start --role <director|manager|reviewer|worker>` as the runtime source of truth for the next action for that role.

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

Role generation/discovery settings for this repo live in `roles.toml`.

Human commands keep two interactive surfaces separate:

- `loom` handles approval-only work (`paused` / `reviewing`), and does not plan requests
- `loom inbox` handles pending request planning work
