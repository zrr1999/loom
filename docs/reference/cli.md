# CLI

## Human commands

- `loom init`
- `loom request add "..."`
- `loom request ls`
- `loom inbox add "..."`
- `loom inbox`
- `loom status`
- `loom review`
- `loom accept <id>`
- `loom reject <id> "reason"`
- `loom decide <id> <option>`
- `loom release <id> "reason"`
- `loom tui`

Running `loom` with no subcommand now processes the current paused / reviewing approval queue.

Difference between `loom` and `loom review`:

- `loom`: interactive approval loop for `paused` and `reviewing`
- `loom review`: non-interactive list of reviewing items only
- `loom request ls`: list requests and their resolution state
- `loom inbox`: interactive planning loop for pending requests via the compatibility alias
- `loom tui`: full-screen Textual TUI for the same approval queue

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

## Agent commands

- `loom agent new-thread`
- `loom agent new-task --thread backend`
- `loom agent next`
- `loom agent start`
- `loom spawn`
- `loom agent whoami`
- `loom agent checkpoint "..."`
- `loom agent resume`
- `loom agent inbox`
- `loom agent send <to> "..."`
- `loom agent reply <msg-id> "..."`
- `loom agent done <id> --output path`
- `loom agent pause <id> --question ... --options ...`
- `loom agent plan <rq-id>`
- `loom agent status`

If `loom agent pause` is called without `--question`, it falls back to a small terminal wizard.

`loom agent done` only sends work to `reviewing` when the task looks review-ready. If the task body or output still contains TODO markers, proposal-only output, or explicit follow-up-improvement notes, the command pauses the task instead and generates a decision block for the human queue.

`loom agent next` first returns pending requests that should be planned into tasks, then returns ready tasks.

- thread arguments still use human-facing thread names like `backend`
- task ids shown in CLI output use the readable name-based form like `backend-001`

- planning batch size comes from `agent.inbox_plan_batch` and defaults to `10`
- ready-task batch size comes from `agent.task_batch` and defaults to `1`
- idle wait/retry defaults come from `agent.next_wait_seconds` (`0.0`) and `agent.next_retries` (`0`)
- planning output may imply both `new-thread` and `new-task`

`loom agent next` supports idle polling controls:

- `--wait-seconds <n>`: seconds to sleep between idle retries
- `--retries <n>`: retry count when the action is idle

By default (`0.0` seconds, `0` retries), behavior is unchanged: a single immediate check.
Each retry re-checks both pending request planning work and ready tasks before the command finally returns `ACTION  idle`.

Important: `loom agent next` is no longer read-only for task execution. Worker calls claim the returned thread(s) for the current worker, but the command still does not perform request-to-task planning for you.

Claimed thread ownership now carries a visible lease in `_thread.md`. `loom agent checkpoint` refreshes that lease for every thread currently owned by the worker, and stale ownership becomes reclaimable by another worker once the stored lease expires. The default lease window matches `agent.offline_after_minutes`.

Current behavior note: worker-safe `loom agent` commands infer the acting worker from `LOOM_WORKER_ID`. If that environment variable is missing, the command fails with guidance to use `--role manager`, `--role director`, or `--role reviewer`, or to set `LOOM_WORKER_ID`. Read-only commands like `loom agent status` and bootstrap guidance such as `loom agent start` do not require a worker id.

`loom agent start` returns a concise manager bootstrap prompt describing the expected loop around `loom agent next`, `done`, and `pause`.

That prompt includes a current-state summary, the practical command set for managers, and explicitly states that `loom agent done` and `loom agent pause` always require a specific task id.

## Repo hook policy

- run `uvx prek install --hook-type pre-commit --hook-type commit-msg` after syncing dependencies
- the repo's `commit-msg` hook expects `<emoji> <type>(<scope>)?: <subject>`
- example: `✨ feat: add more functionality`
- auto-generated `Merge ...`, `Revert ...`, `fixup! ...`, and `squash! ...` messages are allowed

Global workspace guidance (`-g`) is only shown in `loom agent start` when global mode is currently active.

Dedicated manager loop role definition lives at `roles/loom-manager.md`.

### Manager command contract

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

The reviewer role definition lives at `roles/loom-reviewer.md`.

Role generation/discovery settings for this repo live in `roles.toml`.

Human commands keep two interactive surfaces separate:

- `loom` handles approval-only work (`paused` / `reviewing`), and does not plan requests
- `loom inbox` handles pending request planning work
