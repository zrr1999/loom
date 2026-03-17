# Data Model

## `loom.toml`

- root-level configuration file
- stores project name, agent scheduling/polling defaults, and thread defaults
- auto-created by `loom init` when missing

## `.loom/inbox/RQ-xxx.md`

- raw requirement text
- `status`: `pending | planned | merged`
- `planned_to`: generated task ID string

## `.loom/threads/<ID>/_thread.md`

- thread strategy: `sequential | parallel`
- cross-thread priority

## `.loom/threads/<ID>/<task>.md`

- `status`: `draft | scheduled | claimed | reviewing | paused | done`
- `depends_on`: cross-thread task dependencies
- `created_from`: source inbox IDs
- `claim`: agent id + claim timestamp while claimed
- `acceptance`: required before entering `scheduled`
- `decision`: required while `paused`

## `.loom/agents/`

- `_manager.md`: manager checkpoint record
- `<agent-id>/_agent.md`: executor metadata + checkpoint body
- `<agent-id>/inbox/pending/`: incoming messages
- `<agent-id>/inbox/replied/`: processed messages

## Agent polling settings (`loom.toml`)

Under `[agent]`, `loom agent next` uses:

- `inbox_plan_batch`: max pending inbox items returned in `ACTION  plan`
- `task_batch`: max ready tasks returned in `ACTION  task`
- `next_wait_seconds`: sleep duration between idle retries (default `0.0`)
- `next_retries`: retry count when no plan/task action is ready (default `0`)
