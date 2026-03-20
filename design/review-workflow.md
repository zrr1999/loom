# Review workflow, follow-ups, and history

## Goal

Treat review as a first-class workflow instead of a final yes/no gate.

The design in this document combines these related requests into one model:

- repeated rejection history
- batch accept / reject ergonomics
- long review notes from files
- persistent review notes on both accept and reject
- generated review summaries / prep
- richer detail output during review
- accept-and-create-follow-up flows
- continue-from-here flows after a successful review

## Current gaps

Today the review model is intentionally small:

- a task moves to `reviewing`
- a human runs `loom review`, `loom accept`, or `loom reject`
- a rejected task keeps only a single `rejection_note`
- follow-up work must be created manually after acceptance

That is workable, but it loses history and makes iterative review awkward.

## Design principles

1. **Review history must be append-only**

   A task may be rejected multiple times before it is accepted.
   The model should preserve the full sequence instead of overwriting one note field.

2. **Accepting work should not end the thread by accident**

   If a reviewer sees the obvious next slice immediately, Loom should let them capture it in one step.

3. **Human review should be informed, not scavenger-hunt driven**

   Review detail screens and summaries should bring together acceptance criteria, outputs, and prior notes.

4. **Batch operations should help with simple cases without hiding per-task history**

   Bulk accept/reject is useful, but each task still needs its own recorded review event.

## File-model changes

### Keep

- `status`
- `output`
- `acceptance`
- `rejection_note` during migration

### Add

Add a new append-only `review_notes` field to task frontmatter.

Recommended shape:

```yaml
review_notes:
  - kind: reject
    actor: human
    created: "2026-03-18T08:42:11Z"
    note: "Validation copy is still English."
    source: cli
  - kind: accept
    actor: human
    created: "2026-03-18T09:10:03Z"
    note: "Accepted with known copy cleanup follow-up."
    source: cli
```

Field rules:

- append only; never rewrite prior entries
- both `accept` and `reject` may add notes
- empty notes are allowed, but the event is still recorded
- `rejection_note` remains as a temporary compatibility mirror for the latest rejection until migration is complete

### Optional generated metadata

Do **not** store generated review summaries in frontmatter by default.
Generate them on demand from task state plus output artifacts.

If later caching is needed, prefer a sibling artifact file over bloating task frontmatter:

- `.loom/threads/<thread>/.review/<task-id>.md`

## Review event model

Conceptually, a review produces one event per task:

| Event | Status effect | Note allowed | Follow-up allowed |
|---|---|---|---|
| `accept` | `reviewing -> done` | yes | yes |
| `reject` | `reviewing -> scheduled` | yes | no |
| `accept_with_followup` | `reviewing -> done` + create new task | yes | yes |
| `continue_from_here` | `reviewing -> done` + create next task with inherited context | yes | yes |

The CLI may keep `accept` plus flags rather than introducing new top-level verbs for each variant.

## CLI changes

### Batch accept / reject

Recommended signatures:

```bash
loom accept <task-id> [<task-id> ...]
loom accept <task-id> [<task-id> ...] --note "accepted after smoke check"
loom accept <task-id> [<task-id> ...] --note-file ./review-note.md

loom reject <task-id> [<task-id> ...] "<note>"
loom reject <task-id> [<task-id> ...] --note-file ./review-note.md
```

Batch rules:

- apply the same note body to each selected task unless later extended with per-task notes
- write a distinct review-note event into each task
- stop on the first invalid task id and report which tasks were not processed yet
- print a short summary at the end, for example `accepted: 3`

### Note file support

`--note-file <path>` should:

- read UTF-8 text from the given file
- preserve newlines exactly
- fail loudly if the file does not exist or is unreadable

This is especially useful for long rejection explanations that are too awkward for a shell one-liner.

### Accept with follow-up

Recommended signature:

```bash
loom accept <task-id> --followup "Document copy cleanup"
loom accept <task-id> --followup-file ./followup.md
```

Semantics:

- accept the current task first
- create a new task in the same thread
- set the new task status to `scheduled` only if acceptance text is provided or can be scaffolded explicitly
- add a provenance link back to the accepted task
- inherit thread-level context only; do **not** automatically copy the accepted task's `depends_on`, output artifact list, or unfinished execution notes unless the human writes them into the follow-up content on purpose

Recommended new frontmatter on the created task:

```yaml
created_from_review:
  task: backend-003-login-page
  mode: followup
```

### Continue-after-accept

This is similar to follow-up creation, but with stronger context inheritance.

Recommended signatures:

```bash
loom accept <task-id> --continue "Next step direction"
loom continue <thread-name> "Next step direction"
```

Semantics:

- create the next task in the same thread
- inherit context from the accepted task or most recent done task in the thread
- copy forward the prior task id into `depends_on`
- include the accepted task output path in the new task body or metadata so the next executor can continue from the actual artifact

In short:

- `--followup` = "capture a related next task, but keep it mostly fresh"
- `--continue` = "treat the approved task as the immediate predecessor and carry forward execution context"

This keeps "approved, now keep going" as a single flow.

## Review detail output

### `loom review <task-id>`

Single-task detail mode should compose:

1. task title / status / thread
2. acceptance criteria
3. output paths and inline preview when text is readable
4. review-note history
5. available actions

Recommended detail layout:

```text
$ loom review backend-003-login-page

TASK      backend-003-login-page
THREAD    backend
STATUS    reviewing

ACCEPTANCE
  - form errors are clear
  - responsive on mobile and desktop

OUTPUT
  src/pages/Login.tsx
  tests/e2e/test_login.py

OUTPUT PREVIEW
  --- src/pages/Login.tsx ---
  export function LoginPage() { ... }

REVIEW HISTORY
  2026-03-18 08:42 reject  Validation copy is still English.

NEXT ACTIONS
  loom accept backend-003-login-page
  loom reject backend-003-login-page "reason"
```

### Interactive queue `detail`

The `detail` action inside plain `loom` should show the same review composition, not just task metadata.

That means:

- include acceptance criteria
- include output preview when practical
- include prior review notes
- include follow-up / continue hints if the task is likely part of a longer thread

## Review prep / summarize

Two surfaces can expose the same capability:

```bash
loom review --summarize <task-id> [<task-id> ...]
loom agent review-prep <task-id>
```

`loom review --summarize` should require at least one task id. For multiple ids, print one clearly separated summary block per task plus a compact roll-up at the end.

Purpose:

- read the task acceptance criteria
- inspect referenced output artifacts when they are local files
- produce a structured summary for the human reviewer

Recommended summary sections:

- covered criteria
- missing or uncertain criteria
- artifact preview / changed files
- risks / suggested review questions

Important rule:

This summary is advisory only.
It must not silently auto-accept or auto-reject work.

## Repeated rejection history

Repeated rejection should no longer overwrite the prior note.

Example lifecycle:

1. executor marks task done -> `reviewing`
2. human rejects with note A -> task returns to `scheduled`, note A appended
3. executor revises and marks done again -> `reviewing`
4. human rejects with note B -> task returns to `scheduled`, note B appended
5. executor revises and marks done again
6. human accepts with note C -> `done`, note C appended

The task file therefore becomes the permanent review record for that work item.

## Batch ergonomics

Batch review should optimize for the common "many small tasks, same decision" case.

Recommended rules:

- batch accept / reject only operates on tasks already in `reviewing`
- print one failure per invalid task id, then abort without partial mutation unless an explicit `--best-effort` flag is added later
- write the event log and `review_notes` for each task individually

Example:

```text
$ loom accept backend-003 backend-004 backend-005 --note "Smoke-tested together."

ACCEPTED 3 tasks
  backend-003
  backend-004
  backend-005
```

## Migration plan

### Stage 1: additive data model

- add `review_notes`
- keep writing `rejection_note` as the latest reject note for compatibility
- update detail surfaces to read from `review_notes` first

### Stage 2: richer command ergonomics

- add `loom accept <id1> <id2> ...`
- add `loom reject --note-file`
- add single-task detail mode for `loom review <id>`

### Stage 3: follow-up flows

- add `loom accept --followup`
- add `loom accept --continue`
- add `loom continue <thread>`

### Stage 4: summary helpers

- add `loom review --summarize`
- optionally add `loom agent review-prep <id>`

## Testing impact

Implementation will need updates across:

- e2e CLI tests for new command signatures and output text
- task frontmatter tests for `review_notes`
- review/detail tests for output previews and history rendering
- follow-up creation tests for created task metadata and dependencies

## Explicit deferrals

These ideas should stay out of the first implementation slice:

- per-task custom notes within a single batch command
- automatic LLM-generated acceptance decisions
- storing large generated summaries directly inside task frontmatter
- collapsing all review and planning work into one giant interactive surface at once
