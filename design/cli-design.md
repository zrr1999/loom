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
| `loom release <task-id> "<reason>"` | release a stuck claimed task | implemented |

### Agent surface: `loom agent`

The agent surface owns planning, execution, and coordination.

| Command | Purpose | Current status |
|---|---|---|
| `loom agent next` | return the next plan/task action; claims executor tasks immediately | implemented |
| `loom agent start` | print the manager bootstrap loop | implemented |
| `loom agent new-thread --name <name>` | create a thread | implemented |
| `loom agent new-task --thread <id> --title "<title>"` | create a task | implemented |
| `loom agent done <task-id>` | move claimed work to reviewing | implemented |
| `loom agent pause <task-id> --question "<q>"` | pause claimed work and request a decision | implemented |
| `loom agent checkpoint --phase <phase> "<summary>"` | persist executor progress context | implemented for executors; manager support currently deferred |
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
loom release <task-id> "<reason>"
```

### `loom agent`

```bash
loom agent next [--thread <id>] [--wait-seconds <seconds>] [--retries <n>] [--manager]
loom agent start
loom agent new-thread --name <name> [--priority <n>] --manager
loom agent new-task --thread <id> --title "<title>" [--priority <n>] [--acceptance "<text>"] [--depends-on "<id1,id2>"] [--after <task-id>] --manager
loom agent done <task-id> [--output <path-or-url>]
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
[ 2 / 5 ]  review  backend-003-login-page  ·  x7k2  ·  14:32
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

### Manager bootstrap flow

`loom agent start` should stay manager-oriented and intentionally brief:

1. identify the active loom workspace
2. summarize current pending/ready/review counts
3. show the `next -> done/pause -> next` loop
4. list only the essential manager commands

It should not attempt to act as full inline documentation for every `loom agent` subcommand.

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
ACTION  task
COUNT   1
ACTOR   x7k2

CLAIMED TASKS
  TASK  backend-003-login-page
    title      : Build login page
    thread     : backend
    status     : claimed
    priority   : 50
    file       : .loom/threads/backend/backend-003-login-page.md
```

Contract decisions:

- headings such as `ACTION`, `COUNT`, `ACTOR`, `READY TASKS`, and `CLAIMED TASKS` are part of the textual contract
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
{
  "action": "task",
  "count": 1,
  "actor": "x7k2",
  "tasks": [
    {
      "id": "backend-003-login-page",
      "thread": "backend",
      "title": "Build login page",
      "status": "claimed",
      "priority": 50,
      "depends_on": [],
      "acceptance": "- [ ] ...",
      "file": ".loom/threads/backend/backend-003-login-page.md"
    }
  ]
}
```

The idle payload should carry a structured waiting summary rather than a boolean `none` flag.

## `loom manager` subgroup decision

RQ-008 also explored a dedicated `loom manager` group.
For this repo, the recommended decision is:

- keep manager actions under `loom agent` for now
- do not introduce a parallel `loom manager` command group yet

Why:

- current workflows, docs, and tests already center `loom agent --manager`
- splitting manager commands into a third surface would create migration churn before the human `loom` surface is fully settled
- the current problem is role clarity, not top-level namespace count

Possible future path:

- add `loom manager ...` later as an alias or curated manager entry point
- keep `loom agent ...` as the underlying canonical implementation during migration

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
