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
- `loom agent new-task --thread backend [--role <manager|director|reviewer>]`
- `loom agent next [--role <manager|director|reviewer>]`
- `loom agent start`
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

Current behavior note: shared `loom agent` commands default to the worker role and infer the acting worker from `LOOM_WORKER_ID`. If that environment variable is missing, the command fails with guidance to use `--role manager`, `--role director`, or `--role reviewer`, or to configure `LOOM_WORKER_ID`. Manager entrypoints outside `loom agent` like `loom manage` and `loom spawn` instead require a clean process without `LOOM_WORKER_ID`.

`loom manage` returns the same concise manager bootstrap prompt describing the expected loop around `loom agent next --role manager`, `done --role manager`, and `pause --role manager`. `loom agent start` remains available as the lower-level alias.

That prompt includes a current-state summary, the practical command set for managers, and explicitly states that `loom agent done` and `loom agent pause` always require a specific task id.

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
- Shared `loom agent` commands: workers use default role semantics with `LOOM_WORKER_ID`; singleton roles may opt in with `--role manager`, `--role director`, or `--role reviewer`.
  - `loom agent next [--role <manager|director|reviewer>]`
  - `loom agent new-thread [--role <manager|director|reviewer>]`
  - `loom agent new-task --thread backend [--role <manager|director|reviewer>]`
  - `loom agent done <id> --output path [--role <manager|director|reviewer>]`
  - `loom agent pause <id> --question ... --options ... [--role <manager|director|reviewer>]`
  - `loom agent propose <to> "..." [--role <manager|director|reviewer>]`
  - `loom agent send <to> "..." [--role <manager|director|reviewer>]`
- Manager entrypoints outside `loom agent`: require a clean manager process without `LOOM_WORKER_ID`.
  - `loom manage`
  - `loom spawn [--threads <backend,frontend>]`
<!-- END: manager-command-access -->

The preferred worker-registration entrypoint is `loom spawn`. Running `loom agent spawn` now prints a migration hint that points to `loom spawn`.

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

Global workspace guidance (`-g`) is only shown in `loom agent start` when global mode is currently active.

Director/orchestrator role definition lives at `roles/loom-director.md`.

Dedicated manager loop role definition lives at `roles/loom-manager.md`.

The reviewer role definition lives at `roles/loom-reviewer.md`.

The worker role definition lives at `roles/loom-worker.md`.

Role generation/discovery settings for this repo live in `roles.toml`.

Human commands keep two interactive surfaces separate:

- `loom` handles approval-only work (`paused` / `reviewing`), and does not plan inbox items
- `loom inbox` handles pending inbox planning work
