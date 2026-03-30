# Dual-entry CLI workflow

## Goal

Keep Loom as a two-surface CLI with a shared filesystem state model:

- `loom` is the human-facing surface for attention, review, and decisions.
- `loom agent` is the manager / executor surface for planning, claiming, progress, and handoff.

The two entry points should stay separate in defaults, help text, and output shape even when they operate on the same `.loom/` files.

## Design status

This document is a concrete product/design spec, not a promise that every command below is already implemented.

Rules for interpreting it:

- document the current behavior precisely where it already exists
- define the intended shape for near-term additions
- call out explicit deferrals instead of leaving gaps ambiguous

## Principles

1. **Separate users, separate defaults**

   Humans should not need agent-oriented flags or machine-friendly output to do normal review work.

2. **One state model, two entry points**

   `loom` and `loom agent` both operate on the same inbox, thread, task, agent, and event files.

3. **Readable by default, structured when needed**

   Human flows should optimize for comprehension and next actions.
   Agent flows should optimize for deterministic parsing and explicit side effects.

4. **Prefer stable text contracts before adding JSON**

   The text surfaces should become predictable and compact first.
   `--json` should be added only where a stable machine contract is clearly valuable.

5. **Prefer additive migration**

   Existing `loom agent` workflows should keep working while `loom` becomes the clearer human default.

## Surface ownership

| Surface | Primary user | Default mode | Responsibilities | Output style |
|---|---|---|---|---|
| `loom` | Human | interactive attention loop | review work, answer decisions, inspect status, submit requests | readable, interaction-first |
| `loom agent` | Manager / executor | command-oriented | plan tasks, claim tasks, pause, finish, communicate, inspect worker state | labeled blocks, deterministic text |

## Command-surface split

### Human surface: `loom`

The human surface owns approval and attention management.

| Command | Purpose | Current status |
|---|---|---|
| `loom` | interactive queue for items needing human action | implemented |
| `loom inbox add "<text>"` | create a new requirement/request item | implemented |
| `loom inbox` | interactive planning loop for pending inbox items | implemented |
| `loom status` | project-level summary | implemented |
| `loom review` | non-interactive reviewing list | implemented |
| `loom accept <task-id>` | accept reviewing work | implemented |
| `loom reject <task-id> "<note>"` | reject reviewing work | implemented |
| `loom decide <task-id> <option>` | answer a paused decision with a predefined option | implemented |
| `loom decide <task-id> --text "<body>"` | answer a paused decision with free text | partial / design target |
| `loom release <id> "<reason>"` | release stale thread ownership so work can be reassigned | implemented |

### Agent surface: `loom agent`

The agent surface owns planning, execution, and coordination.

| Command | Purpose | Current status |
|---|---|---|
| `loom agent next` | return the next plan/task action; claims executor tasks immediately | implemented |
| `loom agent start` | print the manager bootstrap loop | implemented |
| `loom agent new-thread --name <name>` | create a thread | implemented |
| `loom agent new-task --thread <id> --title "<title>"` | create a task | implemented |
| `loom agent done <task-id>` | mark completed work review-ready | implemented |
| `loom agent pause <task-id> --question "<q>"` | pause work and request a decision | implemented |
| `loom agent checkpoint --phase <phase> "<summary>"` | persist runtime progress context | implemented for workers and manager; director/reviewer remain read-only |
| `loom agent resume` | print stored executor checkpoint body | implemented |
| `loom agent status` | manager-facing project and worker status | implemented |
| `loom agent spawn [--threads <ids>]` | register / prepare an executor | implemented |
| `loom agent whoami` | print current actor identity | implemented |
| `loom agent inbox` | list executor messages | implemented |
| `loom agent inbox-read <msg-id>` | read executor message content | implemented |
| `loom agent reply <msg-id> "<body>"` | reply to executor message | implemented |
| `loom agent send <to> "<body>"` | send a message | implemented |
| `loom agent ask <to> "<question>"` | send a question | implemented |
| `loom agent propose <to> "<proposal>"` | send a task proposal | implemented |

## Canonical command signatures

These are the signatures that the CLI surface should converge on.

### `loom`

```bash
loom
loom inbox add "<requirement text>"
loom inbox
loom status
loom review
loom accept <task-id>
loom reject <task-id> "<rejection note>"
loom decide <task-id> <option-id>
loom decide <task-id> --text "<freeform answer>"
loom release <id> "<reason>"
```

### `loom agent`

```bash
loom agent next [--thread <id>] [--wait-seconds <seconds>] [--retries <n>] [--manager]
loom agent start
loom agent new-thread --name <name> [--priority <n>] --manager
loom agent new-task --thread <id> --title "<title>" [--priority <n>] [--acceptance "<text>"] [--depends-on "<id1,id2>"] [--after <task-id>] --manager
loom agent done <task-id> [--output <.loom/products/...|url>]
loom agent pause <task-id> --question "<question>" [--options "<json>"]
loom agent checkpoint --phase <planning|implementing|reviewing|blocked|idle> "<summary>"
loom agent resume
loom agent status
loom agent spawn [--threads <backend,frontend>]
loom agent whoami [--manager]
loom agent inbox
loom agent inbox-read <msg-id>
loom agent reply <msg-id> "<body>"
loom agent send <to> "<body>" [--type <type>] [--ref <id>]
loom agent ask <to> "<question>" [--ref <task-id>]
loom agent propose <to> "<proposal>" [--thread <id>] [--ref <id>]
```

## Interactive flow design

### `loom` with no subcommand

`loom` should remain the human attention queue.

#### Current implemented queue

1. paused task decisions
2. reviewing tasks

#### Recommended expanded queue

If the queue broadens beyond the current implementation, the preferred human attention order is:

1. `paused` tasks that block agents
2. human-directed agent questions
3. task proposal approvals
4. `reviewing` tasks
5. pending inbox/request items

This ordering keeps the human focused on unblocking execution first, then on reviewing completed work, then on planning new work.

### Human interactive mockup

The CLI should use compact action picks with a visible default, not long verb phrases.

```text
$ loom

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ 2 / 5 ]  review  backend-003  ·  x7k2  ·  14:32
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  output
  ✓ src/pages/Login.tsx
  ✓ tests/e2e/test_login.py · 8 passed
  → http://localhost:3000/login

  acceptance
  · form errors are clear
  · responsive on mobile and desktop
  · remembers login state

  A accept   R reject   O open   S skip   ? detail

  choice ›
```

### Role bootstrap flow

The chosen bootstrap surface is now split by role:

- director/orchestrator: repo-local `just start`
- manager: `loom manage`
- reviewer: `loom review` plus top-level `loom accept` / `loom reject`
- lower-level role briefs: `loom agent start --role <manager|director|reviewer|worker>`

This intentionally rejects three tempting additions for now:

- no top-level `loom start`
- no `loom manage start`
- no `loom review start`

Why:

1. `loom manage` already reads naturally as a bootstrap entrypoint for the manager loop.
2. `loom review` already names the reviewer queue surface, while `accept` / `reject` stay short and scriptable as adjacent top-level actions.
3. Director work in this repo is orchestration glue around local prompts and existing commands, so `just start` is a better fit than expanding the product CLI with a repo-specific alias.

Compatibility / migration path:

1. keep `loom agent start --role ...` as the shared lower-level bootstrap contract for all roles
2. document `just start`, `loom manage`, and `loom review` as the preferred human-facing entrypoints
3. if a future release still needs `loom start` or `... start` aliases, add them as thin wrappers over the same help text rather than moving runtime authority away from the existing commands

## Output style contracts

### `loom`

The human surface should prefer:

- plain-language section labels
- visible next actions
- short action menus
- minimal hidden machine markers

### `loom agent`

The agent surface should prefer labeled blocks and stable headings:

```text
<!-- BEGIN: worker-agent-next-text-example -->
ACTION  pickup
COUNT   1
ACTOR   x7k2
THREAD  backend

ASSIGNED TASKS
  TASK  backend-003
    title      : Build login page
    kind       : implementation
    thread     : backend
    status     : scheduled
    priority   : 50
    file       : .loom/threads/backend/003.md
    acceptance :
      - [ ] Render the login form

When finished with each task:
  loom agent done <task-id> [--output <.loom/products/...|url>]

If blocked and need a decision:
  loom agent pause <task-id> --question '<question>'
<!-- END: worker-agent-next-text-example -->
```

Contract decisions:

- headings such as `ACTION`, `COUNT`, `ACTOR`, `READY TASKS`, and `ASSIGNED TASKS` are part of the textual contract
- outputs should avoid trailing sentinel noise like `none: true` / `none: false`
- mutating commands should always make side effects explicit
- task ids should always be printed in full

## `--json` decision

The RQ-008 proposal suggested a broad `--json` option.
This design keeps that idea, but defers broad rollout.

### Adopt later

`--json` is a good future fit for:

- `loom agent next`
- `loom agent status`
- `loom agent whoami`

### Do not add yet

Do not add `--json` to every human or agent command immediately.
Reasons:

- the current text contracts are still evolving
- most current friction is around workflow clarity, not parsing failures
- broad JSON support would multiply compatibility promises too early

### Recommended future JSON shape

When `loom agent next --json` eventually lands, its payload should mirror the text action kind instead of inventing a second model:

```json
<!-- BEGIN: worker-agent-next-json-example -->
{
  "action": "pickup",
  "count": 1,
  "actor": "x7k2",
  "threads": ["backend"],
  "tasks": [
    {
      "id": "backend-003",
      "thread": "backend",
      "title": "Build login page",
      "kind": "implementation",
      "status": "scheduled",
      "priority": 50,
      "depends_on": [],
      "acceptance": "- [ ] Render the login form",
      "file": ".loom/threads/backend/003.md"
    }
  ]
}
<!-- END: worker-agent-next-json-example -->
```

The idle payload should carry a structured waiting summary rather than a boolean `none` flag.

## Role command boundary decision

RQ-059 asked whether role bootstrap should become:

- `loom start` for director
- `loom manage start` for manager
- `loom review start` for reviewer

For this repo, the recommended decision is:

- keep `just start` as the explicit repo-local director bootstrap
- keep `loom manage` as the canonical manager bootstrap entrypoint
- keep `loom review` as the reviewing-list surface, with `loom accept` / `loom reject` staying adjacent top-level review actions
- keep `loom agent start --role ...` as the lower-level cross-role bootstrap surface

Why:

- current workflows, docs, and tests already center `loom manage`, `loom review`, and role-aware `loom agent start --role ...`
- adding `... start` subcommands would duplicate meaning without changing runtime behavior
- the current problem is role clarity, not a lack of command nouns
- director guidance in this repo is intentionally local to the repo bootstrap prompt, so `just start` is clearer than turning a repo convention into a global product command

Possible future path:

- add `loom start` or `loom manage start` / `loom review start` later as aliases if the ecosystem clearly benefits
- keep the existing help text and runtime behavior as the implementation underneath those aliases

## Migration notes

### Stage 0: preserve current semantics

- keep `loom agent next` planning-first and task-second
- keep immediate executor claiming
- keep `loom` focused on paused/reviewing work by default

### Stage 1: tighten text UX

- shorten long static prompts such as `loom agent start`
- standardize compact action picks in interactive human flows
- remove noisy output markers that are not meaningful to users or agents

### Stage 2: expand human attention surface carefully

- optionally add question / proposal handling to the default `loom` queue
- keep `loom review` and `loom inbox` as focused surfaces even if `loom` broadens

### Stage 3: add targeted machine output

- add `--json` only to agent commands with clear automation value
- keep the JSON model aligned with the text action model

### Stage 4: reconsider namespace split

- revisit a dedicated `loom manager` group only after the human and agent surfaces feel stable

## Explicit deferrals

To avoid design ambiguity, these items are intentionally deferred:

- broad `--json` support across the entire CLI
- a new top-level `loom manager` namespace
- collapsing inbox planning into the default human queue immediately
- changing actor-resolution rules away from `LOOM_AGENT_ID` plus explicit `--manager`

## Recommended follow-ups

This design implies the following implementation follow-ups:

1. keep `loom agent start` concise and manager-specific
2. standardize single-letter interactive actions on the human surface
3. extend the human queue only after question/proposal UX is specified concretely
4. add JSON only after the text contracts are stable enough to snapshot confidently
