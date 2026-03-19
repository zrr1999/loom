# loom

`loom` keeps project planning in markdown files so humans and agents can share the same queue without hidden state.

## Core ideas

- `inbox/` stores raw requirements from humans
- `threads/` stores executable task streams for agents
- frontmatter drives scheduling, review, pause, and decision state

See `docs/reference/cli.md` for commands and `docs/reference/data-model.md` for file shapes.

`docs/` is reserved for stable product and user-facing documentation. Design notes, proposals, and other evolving planning material should live under `design/` so the reference docs stay focused on the current contract.

Additional design notes live under `design/`:

- `design/workflow-optimization.md`
- `design/long-running-tasks.md`
- `design/cli-design.md`
- `design/requests-and-routines.md`
- `design/review-workflow.md`
- `design/tui-plan.md`
- `design/approval-queue-tui-implementation-guide.md`
- `design/thread-claiming.md`
- `design/project-preferences.md`
- `design/thread-merge-policy.md`
- `design/thread-names.md`
- `design/snapshot-testing.md`
