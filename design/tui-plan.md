## TUI-based human interaction

### Goal

Add an optional TUI for human-driven Loom workflows without changing Loom's filesystem-first model, task state machine, or existing plain CLI commands.

### Current behavior to preserve

- `loom` with no subcommand runs the interactive approval loop for `paused` and `reviewing` tasks only.
- `loom inbox` runs a separate interactive planning loop for pending inbox items.
- State and queue logic already live below the CLI layer.
- E2E tests depend on exact CLI output shape, so current text commands should remain stable.

### Scope

In scope:

- a new optional human TUI for approval and inbox planning queues
- reuse existing repository/service/scheduler logic
- keep `.loom/` files as the source of truth

Out of scope for the first pass:

- replacing existing text commands
- agent-facing machine-friendly flows

### Likely library choice

**Textual** is the most likely fit.

### Coexistence with the plain CLI

The TUI should be a second presentation layer over the same operations, not a second workflow model.

Possible command shape:

- `loom tui`

### Phased rollout

#### Phase 1 — Approval queue TUI

- show queue list, detail, and actions for `paused` / `reviewing`

#### Phase 2 — Inbox planning TUI

- add pending inbox browsing and planning

#### Phase 3 — Read-only status views

- add TUI panels for status summary and reviewing overview

### Risks

- mode drift
- test fragility
- complexity creep
- editor handoff from full-screen mode

### Recommendation

Proceed with an optional Textual-based `loom tui` that initially covers the existing human approval loop only. Keep the plain CLI as the primary stable interface.

### UX polish notes

When polishing the TUI, prefer portable patterns that map cleanly onto Loom's review queue instead of copying a coding agent UI wholesale:

- keep queue + detail visible together, similar to split-pane terminal tools
- keep shortcuts discoverable in the footer/status line, rather than relying on memory
- provide a lightweight help overlay for `?`, so the interface stays learnable without adding a new mode
- keep feedback transient and state-backed: reload/watch should always re-read `.loom/` instead of introducing an in-memory source of truth
