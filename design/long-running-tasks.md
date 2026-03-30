# Long-Running Task Support

> Historical design note: this document describes the original lease proposal that kept
> task-level `claimed` status. The implemented model moved claiming to thread ownership in
> `.loom/threads/<thread-name>/_thread.md` via `owner`, `owned_at`, `owner_heartbeat_at`,
> and `owner_lease_expires_at`; legacy `claimed` tasks are migrated to `scheduled`.

## Goal

Support executor work that spans more than one short session without adding hidden runtime state or a separate control plane. Task progress should remain visible in `.loom/` files and recoverable through existing agent records.

## Historical Baseline

Today, Loom already provides the core pieces:

- task states at proposal time: `draft | scheduled | claimed | reviewing | paused | done`
- readiness rule: a task is ready only when it is `scheduled` and all `depends_on` tasks are `done`
- `loom agent next` claimed ready tasks immediately
- `loom agent checkpoint` updated the executor `_agent.md`
- `loom agent resume` prints the stored checkpoint body
- a claim was released only by `done`, `pause`, or `loom release`
- there was **no automatic timeout on claimed tasks**

That made long-running work possible, but not yet safe to recover automatically when an executor disappeared.

## Design: keep `claimed`, add a lease

Do not add a new task status in the first step. A long-running task remains `claimed`; the missing piece is a visible lease on that claim.

### Lifecycle rules

Keep the existing state machine and add these rules for claimed work:

- `scheduled -> claimed` still means exclusive ownership by one executor
- `claimed` remains valid only while its lease is fresh
- `claimed -> reviewing | paused | scheduled` stays unchanged
- `done`, `pause`, and `loom release` still clear the claim
- an expired claim becomes reclaimable by the manager or scheduler and returns to `scheduled`

This keeps the user-facing model small: long-running work is not a different kind of task, just a claimed task with an active lease.

## Checkpoint and heartbeat expectations

Use checkpoints as the primary heartbeat in the minimal design.

### Executor expectations

For any task expected to run across multiple sessions or over a longer duration, the executor should:

- record an initial checkpoint soon after claim
- update checkpoints periodically while work is active
- keep the checkpoint summary short and recovery-oriented
- include enough detail in `_agent.md` for `loom agent resume` to be useful after interruption

Recommended rule: if work is still active, the executor must refresh the checkpoint often enough to keep the lease alive.

## Claim timeout / lease model

Add lease metadata to the existing claim rather than creating a second mechanism.

### Claim fields

Extend claim data from:

- `agent`
- `claimed_at`

to also track lease freshness, such as:

- last heartbeat time
- lease expiry time

### Lease behavior

- a new claim gets an initial lease window
- each checkpoint refreshes that lease
- if the lease expires, the task is treated as abandoned
- abandoned claimed tasks can be moved back to `scheduled` for reassignment

## Manager and executor workflow

### Executor flow

1. `loom agent next` returns and claims a task
2. executor starts work and records a checkpoint
3. executor keeps checkpointing while the task remains active
4. executor finishes with:
   - `loom agent done ...` when ready for review
   - `loom agent pause ...` when blocked on a decision
   - `loom release ...` when voluntarily giving up the task
5. after interruption, executor uses `loom agent resume` to recover context

### Manager flow

The manager should treat claimed tasks in two buckets:

- **fresh claimed**: actively worked, leave alone
- **stale claimed**: lease expired, return to `scheduled` or explicitly release/reassign

This gives the manager a clear recovery path without changing the normal approval flow for `paused` and `reviewing`.

## Minimal migration path

Start with a compatibility-first rollout.

### Phase 1

- keep the current statuses unchanged
- keep `checkpoint`, `resume`, `done`, `pause`, and `release` semantics
- extend claim metadata with lease timestamps
- make checkpoint refresh the lease

### Compatibility

- existing tasks without lease metadata remain readable
- existing claimed tasks can still be recovered manually with `loom release`
- new lease fields are written only when a task is newly claimed or checkpointed

## Summary

The minimal long-running-task design is:

- **no new task status**
- **reuse `claimed` plus a visible lease**
- **treat checkpoint as the first heartbeat mechanism**
- **expire stale claims back to `scheduled`**
- **preserve the current human approval flow and existing commands**
