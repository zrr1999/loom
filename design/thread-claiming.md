# Design note: thread-level claiming and shared outputs

## Motivation

Current Loom claims work at the task level. RQ-011 asks to move ownership up one level:

- claim the whole thread, not individual tasks
- allow only one executor per thread at a time
- keep the manager out of claiming/execution
- put outputs on the thread, not on isolated tasks

## Proposed lifecycle

1. Manager plans and never claims work.
2. Executor claims a thread.
3. Executor works tasks inside the claimed thread.
4. Canonical artifacts live under `.loom/agents/<agent-id>/assets/`.
5. Selected outputs are linked into `.loom/threads/<THREAD>/outputs/`.
6. Human review remains task-based.
7. Manager curates global outputs under `.loom/outputs/`.

## Filesystem layout changes

```text
.loom/
  threads/
    AK/
      _thread.md
      outputs/
  agents/
    aaaa/
      assets/
  outputs/
    curated/
    daily/
```

## Model changes

- move `claim` from `Task` to `Thread`
- remove task status `claimed`
- remove task field `output` as the primary output surface
- extend thread metadata to describe current owner and shared outputs

## Risks

- less intra-thread parallelism
- bigger lock impact for stale ownership
- link portability across filesystems
- review traceability if task outputs become indirect
