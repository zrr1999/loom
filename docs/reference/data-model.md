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
- merge resolution only targets existing `scheduled` tasks in the chosen thread; reviewing/paused work stays separate
- request planning should only auto-attach to a thread when inference is obvious; otherwise the manager must choose the thread explicitly
- `.loom/inbox/` remains a compatibility alias during migration

## `.loom/routines/`

- storage root for recurring manager-owned work
- one markdown file per routine, named `<routine-id>.md`
- routines are ongoing records, not finite tasks; they do not enter `done`

## `.loom/routines/<routine-id>.md`

- `id`: stable routine id used by `loom routine ...`
- `title`: human-facing routine summary
- `status`: `active | paused | disabled`
- `interval`: normalized duration string such as `30m`, `6h`, or `1d`
- `assigned_to`: optional worker id that receives `routine_trigger` messages
- `created_from`: source request ids when the routine came from request planning
- `last_run`: ISO timestamp of the latest completed run; `null` means immediately due
- `last_result`: compact latest outcome such as `ok`, `question`, `no_change`, `task_proposed`, or `escalated`
- body must include `## Responsibilities` and `## Run Log`
- `## Run Log` is append-only; per-run narrative lives there while `last_result` stays a compact status field for `loom status` / `loom routine ls`
- due scheduling only considers `active` routines, using `last_run + interval <= now`

## `.loom/threads/<thread-name>/_thread.md`

- `name`: stable human-facing thread directory name
- `owner`: agent currently owning this thread (at most one)
- `owned_at`: ISO timestamp when ownership was claimed
- `owner_heartbeat_at`: latest checkpoint-driven heartbeat from the current owner
- `owner_lease_expires_at`: when the current ownership lease becomes reclaimable if not refreshed
- cross-thread priority

## `.loom/threads/<thread-name>/<seq>.md`

<!-- BEGIN: task-file-model -->
- filename is only the per-thread sequence (`001.md`, `002.md`, ...)
- `id`: global task id composed as `<thread-name>-<seq>` (for example `backend-001`)
- `status`: `draft | scheduled | reviewing | paused | done`
  - legacy `claimed` task statuses are read only for backward-compat migration
  - migration moves old task-level claims to thread ownership in `_thread.md` (`owner`, `owned_at`, `owner_lease_expires_at`) and rewrites the task to `scheduled`
- `persistent`: optional `true` flag for long-running tasks that should stay scheduled after each completion
- `depends_on`: cross-thread task dependencies
- `created_from`: source request IDs (`RQ-xxx`)
- `claim`: *(deprecated)* legacy task-level claim; migration strips it from task files after upgrading old workspaces because ownership is now thread-level
- `decision`: required while `paused`
- `rejection_note`: legacy compatibility mirror for the latest rejection note recorded in `review_history`
- `review_history`: append-only accept/reject event history
- `acceptance`: required before entering `scheduled`
- `delivery`: optional explicit review handoff contract (`ready`, `artifacts`, `pr_urls`)
- `output`: task-level delivery reference; relative local paths are normalized under `.loom/products/`, while URLs / freeform review notes stay as entered
- `reviewing`: allowed when either the explicit `delivery.ready` contract is true or the legacy body/output heuristics find no TODO / proposal-only / known-follow-up markers
- task markdown keeps `## 背景` / `## 实现方向` sections, but leaves them empty unless real context is provided
<!-- END: task-file-model -->

## `.loom/agents/`

- `manager/_agent.md`: manager singleton checkpoint record; it preserves manager coordination state without introducing a director status record
- `workers/<agent-id>/_agent.md`: worker metadata + checkpoint body, including the worker-owned `AgentStatus` heartbeat updated by `loom agent checkpoint`
- `workers/<agent-id>/inbox/pending/`: incoming worker messages
- `workers/<agent-id>/inbox/replied/`: processed worker messages
- Director and reviewer roles do not have equivalent lifecycle records under `.loom/agents/`; they orchestrate by reading shared state rather than mutating worker status

## `.loom/agents/workers/<agent-id>/worktrees/`

- worker-local worktree storage root
- contains both worker-local checkout directories and adjacent `<name>.md` metadata records
- only the owning worker reads or mutates this subtree through `loom agent worktree ...`

## `.loom/agents/workers/<agent-id>/worktrees/<name>.md`

- optional registry record for one worker-local Git worktree checkout
- `name`: stable worker-local id used by `loom agent worktree ...`
- `path`: absolute path to the registered checkout directory, always under the owning worker subtree
- `branch`: advisory branch label shown in `loom agent worktree list`
- `status`: advisory status such as `registered`, `active`, or `idle`
- `worker`: owning worker id; worker-local commands do not let another worker rewrite it
- `thread`: optional thread currently associated with that checkout
- `created_at` / `updated_at`: registry bookkeeping timestamps
- non-authoritative by design: removing or editing this file does not change actual task/thread state

## `.loom/products/`

- shared output tree for reviewable local artifacts
- `reports/` is created by default as a conventional subfolder
- prefer `.loom/products/reports/<task-id>.md` for human-reviewable completion notes
- `loom agent done --output <relative-path>` records the output as `.loom/products/<relative-path>`
- legacy `.loom/agents/workers/<id>/outputs/...` paths are normalized into `.loom/products/reports/...`

## Agent polling settings (`loom.toml`)

Under `[agent]`, `loom agent next` uses:

- `inbox_plan_batch`: max pending requests scanned per planning chunk while `loom agent next` clears request backlog
- `task_batch`: max ready tasks workers claim per `loom agent next` (manager/director/reviewer stay orchestration-only)
- `next_wait_seconds`: sleep duration between idle retries (default `60.0`)
- `next_retries`: retry count when no actionable role-specific result is ready (default `5`)
- `offline_after_minutes`: offline warning threshold and default ownership lease window refreshed by `loom agent checkpoint`
