# Requests and routines model

## Goal

Evolve Loom from an inbox-only intake model into a clearer split:

- **requests** are the single human entry point for new work
- **routines** are long-running, manager-owned work that fires repeatedly over time

This document turns the raw proposal into a repo-facing design that defines:

- directory and file-model changes
- scheduling semantics
- CLI surface changes
- routine execution / messaging behavior
- migration impact from today's inbox-centric model

## Current model

Today Loom has:

- `.loom/inbox/` for human-submitted requirements
- `.loom/threads/` for task execution
- `.loom/agents/` for manager/executor state

That model works well for one-shot work, but it does not distinguish:

- an ordinary request that should become a task
- a standing responsibility that should recur forever
- a request that is merged into existing work or rejected outright

## Core decision

Adopt the following conceptual split:

| Concept | Meaning | Has an end state? | Owned by |
|---|---|---|---|
| request | human intent submitted to the system | yes | manager triage |
| task | finite executable work | yes | executor / manager |
| routine | repeatable managed work triggered by schedule | no end state, only lifecycle states | manager |

## Directory model

Recommended target layout:

```text
.loom/
├── requests/
├── threads/
├── routines/
└── agents/
```

Migration note:

- `requests/` is a rename of today's `inbox/`
- request ids remain `RQ-xxx`
- task and thread layout stays as-is

## Request model

### Request file shape

Recommended request frontmatter:

```yaml
id: RQ-007
created: 2026-03-18T10:00:00Z
status: pending
resolved_as: null
resolved_to: []
resolution_note: null
```

Body remains free-form natural language.

### Field meanings

| Field | Meaning |
|---|---|
| `status` | `pending | processing | done` |
| `resolved_as` | `task | routine | merged | rejected` |
| `resolved_to` | related task ids or routine ids |
| `resolution_note` | manager explanation when merged or rejected |

### Manager triage rules

When a new request appears, manager decides one of four outcomes:

1. **task**

   Use when the request has a clear completion condition and should become one or more finite tasks.

2. **routine**

   Use when the request implies ongoing monitoring, periodic checks, or continuous maintenance without a natural "done" state.

3. **merged**

   Use when the request substantially overlaps with an existing task or routine.

4. **rejected**

   Use when the request is out of scope or should not be acted on.

Important behavior:

- manager should ask a human question instead of guessing when the request is ambiguous
- manager-facing planning commands should stop instead of falling back to the highest-priority unrelated thread when thread inference is ambiguous
- `resolved_as` and `resolved_to` must make the triage decision inspectable after the fact

### `processing` transition timing

`processing` means the manager has started triage on the request but has not finished recording the resolution yet.

Use it only for the short mutation window where Loom is actively converting a pending request into one of the supported outcomes. Concretely:

1. `pending -> processing` when manager begins an arranging action that will create/update tasks or routines.
2. `processing -> done` once `resolved_as`, `resolved_to`, and any `resolution_note` are fully written.

Rules:

- requests should not sit in `processing` as a long-term work queue state
- if manager aborts before a resolution is recorded, the request should fall back to `pending`
- humans should still treat `processing` as manager-owned triage, not executor-owned implementation

## Request CLI

Recommended human-facing commands:

```bash
loom request add "<description>"
loom request ls
loom request ls --pending
```

Migration note:

- `loom inbox add` and `loom inbox` remain as compatibility aliases during migration
- docs and prompts should gradually shift to `request` terminology first

## Human queue behavior

Pending requests should remain low-priority attention items in the default human queue.

Recommended interaction:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ 5 / 5 ]  request  RQ-007  ·  pending
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Periodically scan GitHub issues and PRs and raise follow-up work.

  manager has not processed this request yet.

  T trigger now   S skip   O open

  choice ›
```

This does **not** mean requests should immediately become part of the primary approval loop.
They stay informational until the broader human queue is ready.

## Routine model

### Routine purpose

A routine represents recurring managed work such as:

- scan GitHub issues every 6 hours
- check CI failures daily
- remind humans about long-waiting reviews

Unlike tasks:

| Property | task | routine |
|---|---|---|
| lifecycle | finite | ongoing |
| completion rule | explicit acceptance | no terminal "done" state |
| scheduling | dependency graph + priority | interval / due-time based |
| output | reviewable work artifact | run log + messages / task proposals |

### Routine file shape

Recommended routine frontmatter:

```yaml
id: scan-github-issues
title: Scan GitHub issues and PRs
status: active
interval: 6h
assigned_to: x7k2
created_from:
  - RQ-007
last_run: null
last_result: null
```

Body sections:

```markdown
## Responsibilities

- inspect issues and PRs
- propose tasks for matched cases
- notify humans about stale review states

## Run Log

<!-- append-only notes -->
```

### Routine lifecycle

Recommended routine statuses:

- `active`
- `paused`
- `disabled`

`done` is intentionally not part of the routine lifecycle.

### `last_result` semantics

`last_result` is a compact manager-facing summary of the most recent completed run outcome, not a full execution log.

Recommended semantics:

- store a short normalized status such as `ok`, `question`, `task_proposed`, `no_change`, or `failed`
- keep detailed notes in the append-only run log instead of expanding `last_result` into a large blob
- update `last_result` only after a run has actually finished or failed in a durable way
- leave `last_result: null` until the first completed run

This gives `loom status` and `loom routine ls` a stable field to summarize without duplicating the full narrative already captured in the run log.

### Interval semantics

Manager determines routine due-ness via:

`last_run + interval <= now`

Rules:

- if `last_run` is `null`, the routine is immediately due
- paused and disabled routines are ignored by the due scheduler
- interval parsing should reuse one normalized duration parser across the codebase

## Scheduling semantics

### Priority relative to tasks

Ready tasks still take precedence over due routines.

Recommended ordering for `loom agent next --manager`:

1. pending request planning work
2. ready tasks
3. due routines
4. idle

This prevents long-running maintenance from starving normal task execution.

### Manager behavior

The manager loop should periodically check active routines.

When a routine is due:

- if `assigned_to` is set, send a `routine_trigger` message to that executor
- if `assigned_to` is empty, manager may execute it directly only if the project explicitly supports manager-run routines

Recommended default for this repo:

- support the data model now
- defer manager-executes-routine behavior until an explicit runtime story exists
- prefer executor-assigned routines first

## Messaging model

Add a new message type:

`routine_trigger`

Recommended message shape:

```yaml
id: MSG-012
from: manager
to: x7k2
type: routine_trigger
ref: scan-github-issues
sent: 2026-03-18T18:00:00Z
reply_ref: null
```

Body example:

```text
Routine is due. Execute the responsibilities in .loom/routines/scan-github-issues.md and reply with the run result summary.
```

Executor contract:

1. read the routine file
2. perform one run
3. append to the routine run log
4. reply to the trigger message
5. manager updates `last_run` (and optionally `last_result`) on successful acknowledgement

## Status and reporting

### `loom status`

Add a compact routines summary line:

```text
routines  2 active · 0 paused · next due in 2h (scan-github-issues)
```

### `loom routine`

Recommended human/manager commands:

```bash
loom routine ls
loom routine pause <id>
loom routine resume <id>
loom routine run <id>
loom routine log <id>
```

Notes:

- `loom routine run <id>` is an explicit forced trigger, not a status mutation
- `loom routine log <id>` is a viewing surface over append-only run history

## Interaction between routines and tasks

Routines should not bypass the normal task system.

If a routine discovers concrete work:

- send `task_proposal` to manager
- or send `info` / `question` to human when the result is informational or decision-oriented

Example flow:

1. routine scans GitHub issues
2. finds a bug report matching project rules
3. executor sends `task_proposal` referencing the routine id
4. manager approves and creates a task in a normal thread

This keeps routines as discovery/maintenance mechanisms, not as a parallel task system.

## CLI migration plan

### Stage 1: terminology shift

- introduce `request` language in docs first
- add `loom request ...` aliases while keeping `loom inbox ...`
- keep storage under `.loom/inbox/` initially if needed for compatibility

### Stage 2: request metadata

- add `resolved_as`, `resolved_to`, and `resolution_note`
- update manager planning flows to write them

### Stage 3: routine storage and listing

- add `.loom/routines/`
- add `loom routine ls / pause / resume / log`
- add due-time summary to `loom status`

### Stage 4: scheduled triggering

- add `routine_trigger` messages
- extend manager scheduling to surface due routines after ready tasks

### Stage 5: rename storage

- rename `.loom/inbox/` to `.loom/requests/`
- keep a compatibility path or migration helper while old workspaces are upgraded

## Compatibility and migration impact

### Stored files

Migration will need to touch:

- `.loom/inbox/` -> `.loom/requests/`
- any code that assumes inbox is the only human intake surface
- status and scheduler readers

### CLI docs and tests

Tests will need updates for:

- renamed `request` commands
- new routine status output
- manager `next` output when a due routine exists
- message-type handling for `routine_trigger`

## Request / inbox file-count growth strategy

The requests transition should solve the older inbox file-count problem without giving up
filesystem-first auditability.

### Core strategy

Use a two-tier layout:

1. **active working set stays shallow**
   - keep mutable requests directly under `.loom/requests/` (or `.loom/inbox/` during compatibility)
   - only `pending` and short-lived `processing` requests stay in that hot path by default
2. **resolved requests move into an immutable archive tree**
   - move `done` requests into `.loom/requests/archive/YYYY/MM/RQ-xxx.md`
   - during the compatibility window, use the same shape under `.loom/inbox/archive/YYYY/MM/`
   - archived files keep the exact markdown body/frontmatter, so review and grep stay simple

This keeps the day-to-day directory small for humans and the manager loop while preserving one
file per request for long-term history.

### Why archive instead of compaction

- **auditability stays obvious**: each request remains a readable markdown record
- **migration risk stays low**: active readers still operate on ordinary files instead of a new
  database or bundle format
- **performance improves where it matters**: manager planning and `loom request ls` primarily need
  the open working set, not every historical request ever created

Compaction or opaque index files can remain future optimizations, but they should not be the first
solution to file-count growth.

### Reader rules

- default request/inbox reads should scan the active root only
- commands that need history (`loom request ls --all`, request lookup by id, audit/report flows)
  should also scan `archive/`
- archive traversal must be recursive, but the hot-path scan for active work stays shallow and
  predictable
- routines keep their existing flat `.loom/routines/` layout for now because they are long-lived,
  manager-owned records rather than high-churn intake items

### Backward compatibility

Support these workspace shapes during migration:

1. legacy flat `.loom/inbox/RQ-xxx.md`
2. mixed inbox workspace with `.loom/inbox/archive/YYYY/MM/RQ-xxx.md`
3. new requests workspace with `.loom/requests/RQ-xxx.md`
4. new requests workspace with `.loom/requests/archive/YYYY/MM/RQ-xxx.md`

Rules:

- if `.loom/requests/` exists, treat it as the canonical intake root
- otherwise keep using `.loom/inbox/` as the compatibility root
- archived requests keep the same `RQ-xxx` ids, so links from tasks/routines do not change
- migration should be path-only; request frontmatter does not need a second archival state

### Agent inbox / mailbox follow-up

The same pattern can later be applied to high-volume mailbox trees:

- keep `pending/` flat for current actionable messages
- move replied or resolved messages into `archive/YYYY/MM/` under the existing mailbox subtree

That follow-up should happen only after request archival lands, because request/inbox intake growth
is the immediate pain point and the manager-owned routine flow depends on keeping request scans fast.

### Smallest safe implementation slices

1. **archive-aware readers**
   - teach repository helpers to read active roots plus optional `archive/` trees
   - keep default list/status commands focused on active requests unless explicitly asked for
2. **manual archival command**
   - add a manager/human command to move old `done` requests from the active root into the archive
   - preserve filenames/ids and print the destination path for auditability
3. **status/reporting polish**
   - surface active vs archived counts where useful
   - add targeted tests for mixed legacy/new layouts and archived lookups
4. **optional policy automation**
   - only after the manual flow is trusted, add threshold- or age-based archival helpers

This sequence preserves compatibility, avoids touching routine scheduling semantics, and keeps the
manager-owned routine/request flow stable while the intake history grows.

## Explicit deferrals

To keep the first implementation slice realistic, defer:

- fully removing `loom inbox ...` on day one
- manager-executes-routines directly by default
- routines creating tasks automatically without manager approval
- complex cron syntax beyond a small duration format such as `30m`, `6h`, `1d`

## Recommended first implementation slice

The smallest coherent rollout is:

1. document requests as the new term
2. add request-resolution metadata
3. introduce routine file models and list/status surfaces
4. defer automatic routine triggering until the message path is ready
