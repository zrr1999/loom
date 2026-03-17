# CLI

## Human commands

- `loom init`
- `loom inbox add "..."`
- `loom inbox`
- `loom status`
- `loom review`
- `loom accept <id>`
- `loom reject <id> "reason"`
- `loom decide <id> <option>`
- `loom release <id> "reason"`

Running `loom` with no subcommand now processes the current paused / reviewing approval queue.

Difference between `loom` and `loom review`:

- `loom`: interactive approval loop for `paused` and `reviewing`
- `loom review`: non-interactive list of reviewing items only
- `loom inbox`: interactive planning loop for pending inbox requirements

- `paused`: choose `decide`, `skip`, `open`, or `detail`
- `reviewing`: choose `accept`, `reject`, `skip`, `open`, or `detail`
- `inbox`: choose `plan`, `skip`, `open`, or `detail`

`loom init` ensures both `.loom/` and root `loom.toml` exist. It is idempotent. Pass `-g` to use the home-level loom workspace.

## Agent commands

- `loom agent new-thread`
- `loom agent new-task --thread AA`
- `loom agent next`
- `loom agent start`
- `loom agent spawn --manager`
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

`loom agent next` first returns pending inbox items that should be planned into tasks, then returns ready tasks.

- planning batch size comes from `agent.inbox_plan_batch` and defaults to `10`
- ready-task batch size comes from `agent.task_batch` and defaults to `1`
- idle wait/retry defaults come from `agent.next_wait_seconds` (`0.0`) and `agent.next_retries` (`0`)
- planning output may imply both `new-thread` and `new-task`

`loom agent next` supports idle polling controls:

- `--wait-seconds <n>`: seconds to sleep between idle retries
- `--retries <n>`: retry count when the action is idle

By default (`0.0` seconds, `0` retries), behavior is unchanged: a single immediate check.

Important: `loom agent next` is no longer read-only for task execution. It claims returned tasks for the current agent, but it still does not perform inbox-to-task planning for you.

Current behavior note: mutating `loom agent` commands infer the acting agent from `LOOM_AGENT_ID`. If that environment variable is missing, the command fails unless `--manager` is passed explicitly. Read-only commands like `loom agent status` and `loom agent start` do not require it.

`loom agent start` returns a concise manager bootstrap prompt describing the expected loop around `loom agent next`, `done`, and `pause`.

That prompt includes a current-state summary, the practical command set for managers, and explicitly states that `loom agent done` and `loom agent pause` always require a specific task id.

Global workspace guidance (`-g`) is only shown in `loom agent start` when global mode is currently active.

Dedicated manager loop role definition lives at `.agents/roles/loom-loop.md`.

For role-forge-style discovery in this repo, `roles.toml` points `roles_dir` to `.agents/roles`.

Human commands keep two interactive surfaces separate:

- `loom` handles approval-only work (`paused` / `reviewing`), and does not plan inbox items
- `loom inbox` handles pending inbox planning work
