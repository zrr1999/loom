# AGENTS

## Purpose

`loom` is a filesystem-first workflow CLI. Humans add requirements to `.loom/inbox/`, managers convert them into executable tasks under `.loom/threads/`, and workers move those tasks through the markdown state machine until a human accepts or rejects the result.

## Repo layout

- `src/loom/` - CLI, state models, scheduler, persistence, and agent flows
- `tests/e2e/` - command-level behavior tests; many assertions depend on exact CLI text
- `tests/unit/` - model and validation coverage
- `docs/reference/` - CLI and data model notes
- `loom.toml` - single config file
- `.loom/` - runtime state, agent records, inbox items, threads, and event log

## Roles

### Director / orchestrator

- Bootstrap orchestration in this repo with `just start`
- Any suitable agent or human may perform director duties from that prompt
- Stay above the runtime loop; decide whether manager, worker, reviewer, or a human should act next

### Human

- Add requirements with `uv run loom inbox add "..."`
- Review state with `uv run loom status`, `uv run loom review`, and `uv run loom log`; bootstrap manager work with `uv run loom manage`; create worker records with `uv run loom spawn`
- Resolve queue items with `uv run loom`, `uv run loom accept`, `uv run loom reject`, `uv run loom decide`, and `uv run loom release`

### Manager

- Run `uv run loom manage` to get the loop guide, then `uv run loom agent next --role manager`
- If the result is `ACTION  plan`, create threads and tasks from pending `RQ-*` inbox items
- If the result is `ACTION  task`, execute or coordinate the claimed task
- Use `uv run loom agent new-thread --role manager`, `uv run loom agent new-task --role manager`, `uv run loom agent done <task-id> --role manager`, and `uv run loom agent pause <task-id> --role manager`

### Worker

- Must run with `LOOM_WORKER_ID` set
- Loop on `uv run loom agent next`
- Finish work with `uv run loom agent done <task-id> [--output ...]`
- Ask for decisions with `uv run loom agent pause <task-id> --question ... [--options ...]`
- Maintain context with `uv run loom agent checkpoint`, `uv run loom agent resume`, `uv run loom agent inbox`, and `uv run loom agent reply`

## Operating rules

- Keep workflow state in files; do not add hidden runtime state for task progress
- `scheduled` tasks must have non-empty `acceptance`
- `paused` tasks must carry a `decision` block
- A task is ready only when it is `scheduled` and all `depends_on` tasks are `done`
- `loom agent next` claims worker tasks immediately
- The default human queue only handles `paused` and `reviewing` items; it does not plan inbox work

## Contributor conventions

- Update docs when behavior changes: at minimum check `README.md`, `docs/reference/cli.md`, and `docs/reference/data-model.md`
- Keep `docs/` and `docs/reference/` for stable product/user documentation; put design notes, proposals, and other evolving planning material under `design/`
- Update `tests/e2e/test_cli.py` when command output or flow changes
- Prefer preserving existing plain-text CLI output shape unless the task explicitly changes it
- Use `just format`, `just check`, and `just test` before wrapping up code changes

### Incremental commit expectations

Commit meaningful completed changes promptly during implementation work instead of accumulating a single large diff at the end. Each commit should be a coherent, self-contained unit of progress that follows the repo's `<emoji> <type>(<scope>)?: <subject>` format.

Practical guidance:

- Commit after completing a logical step: a new function, a passing test, a docs update, a config change
- Keep each commit focused on one type of change — avoid mixing unrelated feature work, refactors, and doc updates in the same commit
- A good rhythm is to commit whenever you would naturally describe progress to a colleague: "added the validator", "fixed the edge case", "updated the CLI docs"
- Do not wait until the entire task is finished to make your first commit; reviewers and future readers benefit from incremental history
- If you realize a commit is growing too large, split it: stage related hunks with `git add -p` and commit them separately

This expectation complements the commit-msg hook — the hook enforces format, this convention encourages timely, well-scoped commits throughout a task.

## Current caveats

- Inbox-to-task planning is still manual; `loom agent next` reports planning work but does not perform it
- Claimed tasks have no automatic timeout
- Some manager-facing agent surfaces are not implemented yet, including manager `checkpoint`, `resume`, and `inbox`
- Docs currently describe a few flows that are still evolving, so check source behavior in `src/loom/agent.py` and `src/loom/cli.py` when in doubt
