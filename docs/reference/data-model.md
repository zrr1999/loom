# Data Model

## `loom.toml`

- root-level configuration file
- stores project name, agent scheduling/polling defaults, and thread defaults
- auto-created by `loom init` when missing

## `.loom/requests/RQ-xxx.md`

- raw requirement text
- `status`: `pending | processing | done`
- `resolved_as`: `task | routine | merged | rejected`
- `resolved_to`: related task ids or routine ids
- `resolution_note`: manager explanation for merged / rejected outcomes
- `.loom/inbox/` remains a compatibility alias during migration

## `.loom/threads/<thread-name>/_thread.md`

- `name`: stable human-facing thread directory name
- `owner`: agent currently owning this thread (at most one)
- `owned_at`: ISO timestamp when ownership was claimed
- `owner_heartbeat_at`: latest checkpoint-driven heartbeat from the current owner
- `owner_lease_expires_at`: when the current ownership lease becomes reclaimable if not refreshed
- cross-thread priority

## `.loom/threads/<thread-name>/<seq>.md`

- filename is only the per-thread sequence (`001.md`, `002.md`, ...)
- `id`: global task id composed as `<thread-id>-<seq>` (for example `thaa-001`)
- `status`: `draft | scheduled | reviewing | paused | done` (legacy `claimed` values are migrated to thread ownership and then rewritten to `scheduled`)
- `depends_on`: cross-thread task dependencies
- `created_from`: source request IDs (`RQ-xxx`)
- `claim`: *(deprecated)* legacy task-level claim; migration strips it from task files after upgrading old workspaces
- `acceptance`: required before entering `scheduled`
- `decision`: required while `paused`
- `reviewing`: rejected for tasks whose body/output still advertises TODOs, proposal-only output, or known follow-up improvements
- task markdown keeps `## 背景` / `## 实现方向` sections, but leaves them empty unless real context is provided

## `.loom/agents/`

- `_manager.md`: manager checkpoint record
- `<agent-id>/_agent.md`: executor metadata + checkpoint body
- `<agent-id>/inbox/pending/`: incoming messages
- `<agent-id>/inbox/replied/`: processed messages

## Agent polling settings (`loom.toml`)

Under `[agent]`, `loom agent next` uses:

- `inbox_plan_batch`: max pending requests returned in `ACTION  plan`
- `task_batch`: max ready tasks returned in `ACTION  task`
- `next_wait_seconds`: sleep duration between idle retries (default `0.0`)
- `next_retries`: retry count when no plan/task action is ready (default `0`)
- `offline_after_minutes`: offline warning threshold and default ownership lease window refreshed by `loom agent checkpoint`
