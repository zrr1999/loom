# Design Note: Thread Merging and Reuse Policy

## Policy goals

- prefer continuity over fragmentation
- keep active work discoverable in as few threads as practical
- make "reuse an existing thread" the default planning posture
- support thread consolidation without rewriting task history

## Policy

### Prefer existing threads for new work

When a new requirement extends an existing workstream, planning should add work to that thread instead of creating a new one.

### Treat thread merge as consolidation, not renaming history

Use a soft merge:

- existing task files and task IDs stay unchanged
- new tasks are planned into the surviving thread
- the merged thread becomes closed to new planning

Applied example in this repo:

- keep `interactive-inbox` as the surviving thread for interactive human workflows
- mark `interactive-tui` as merged into `interactive-inbox`
- preserve the existing `interactive-tui-*` task files as historical records instead of renaming them

### Use inbox `merged` only when no new task is created

If an inbox item is absorbed into already-existing planned work, mark it `merged`.

## UX / command surface

- planning guidance should prefer an existing thread first
- add an explicit manager merge command later, e.g. `loom agent merge-thread --from <id> --into <id> --manager`

## Data model impacts

- add thread metadata such as `status: active | merged`
- add `merged_into`
- keep task IDs and scheduler semantics unchanged

## Future planning rule

When a requirement overlaps an existing thread by default user journey, operator surface, or owning UX area, prefer broadening the surviving thread's scope note over creating a sibling thread with near-identical ownership.
