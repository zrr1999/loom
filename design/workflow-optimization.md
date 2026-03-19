# Workflow Optimization Suggestions

## Focus

These suggestions target the current workflow bottlenecks visible in the repo and docs: manual inbox planning, immediate claim side effects, docs/runtime drift, underpowered messaging handoffs, and uneven CLI output UX.

## Suggestions

| Priority | Suggestion | Rationale |
|---|---|---|
| P0 | **Add a guided manager planning flow for `ACTION  plan`.** Start with a minimal helper that walks each pending `RQ-*` item and scaffolds the required `new-thread` / `new-task` commands or writes them directly. | `loom agent next --manager` identifies planning work but does not perform it, so the highest-friction step is still manual restructuring. This is the main break in the “next useful action” loop. |
| P0 | **Make claim recovery more explicit and easier to use.** Surface claimed-task age, claimer, and the exact `loom release <id> "reason"` recovery path in `status` and `agent next` output. | Tasks are claimed immediately by `loom agent next`, and claims are only released by `done`, `pause`, or `release`. There is also no automatic timeout, so stuck claims are a real operational risk. |
| P0 | **Align docs with actual command behavior and make one source of truth authoritative.** Update README and reference docs anywhere they imply inbox handling in the human queue or a pause wizard fallback that the CLI no longer provides. | The repo already notes evolving flows, and there are visible mismatches between docs and current behavior. This creates avoidable operator error, especially around planning vs approval responsibilities. |
| P1 | **Improve the default human-mode empty state.** When `loom` has no paused/reviewing items, also mention pending inbox work if any exists, instead of only telling the user to add a new requirement. | The current human queue only handles approvals, but the empty-state message can mislead users when inbox items already exist and are simply waiting on manager planning. |
| P1 | **Add a compact "what to do next" summary to key outputs.** Standardize short next-step guidance across `loom status`, `loom agent status`, `loom agent next`, and `loom agent start`. | The CLI already prints helpful blocks, but guidance is spread across commands and roles. A consistent next-action summary would reduce context switching and make the workflow easier to follow. |
| P1 | **Strengthen manager-executor handoff through messaging.** Add lightweight conventions or helpers for task assignment, blocker escalation, and review-ready notifications using the existing send/reply surfaces. | Messaging exists in the data model and agent CLI, but manager inbox surfaces are still incomplete and the workflow leans heavily on manual coordination. Better handoff patterns would reduce ambiguity without changing the file-first model. |
| P2 | **Make planning and execution state more visible in `status`.** Show separate counts for pending inbox items, ready tasks, claimed tasks, paused tasks, and reviewing tasks, with brief role ownership hints. | The workflow spans humans, managers, and executors, but queue ownership is easy to miss. A clearer state summary would help users understand whether the project is blocked on planning, execution, decision, or review. |

## Recommended ordering

1. Guided planning flow
2. Claim recovery visibility
3. Docs/runtime alignment
4. Human empty-state fix
5. Unified next-step summaries
6. Messaging handoff improvements
7. Richer status breakdown
