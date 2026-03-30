# loom

`loom` keeps project planning in markdown files so humans and agents can share the same queue without hidden state.

## Core ideas

- `requests/` stores raw requirements from humans (`inbox/` remains a compatibility alias)
- `threads/` stores executable task streams for agents
- `agents/workers/<id>/worktrees/` stores worker-local checkout records and directories
- frontmatter drives scheduling, review, pause, and decision state

See `docs/reference/cli.md` for commands and `docs/reference/data-model.md` for file shapes.

`docs/` is reserved for stable product and user-facing documentation. Design notes, proposals, and other evolving planning material should live under `design/` so the reference docs stay focused on the current contract.

## Developer workflow

The standard local validation flow lives in `justfile`:

- `just format` updates generated docs and formats code
- `just check` runs documentation, lint, and type checks
- `just quality-check` runs `uvx lizard src/` for code complexity visibility
- `just test` runs the test suite
- `just ci` runs the full validation stack

Additional design notes live under `design/`:

- `design/workflow-optimization.md`
- `design/long-running-tasks.md`
- `design/cli-design.md`
- `design/requests-and-routines.md`
- `design/review-workflow.md`
- `design/tui-plan.md`
- `design/thread-claiming.md`
- `design/project-preferences.md`
- `design/thread-merge-policy.md`
- `design/thread-names.md`
- `design/snapshot-testing.md`
