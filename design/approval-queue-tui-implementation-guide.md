# Approval queue TUI implementation guide

This note captures the task-focused implementation reference that previously lived in `THAE003_IMPLEMENTATION_GUIDE.txt`.
It is evolving implementation guidance for `thae-003`, so it belongs under `design/` rather than the repository root.

**Objective: Implement optional Textual-based TUI for approval queue without**

changing filesystem model, CLI interface, or existing commands.

## 1. CLI ENTRYPOINTS & REGISTRATION

FILE: /workspace/loom/src/loom/cli.py (13KB)

Current CLI structure (lines 39-45):
  app = typer.Typer(name="loom", help="...")
  app.add_typer(agent_app, name="agent", help="Agent commands")
  inbox_app = typer.Typer(help="Inbox commands")
  app.add_typer(inbox_app, name="inbox")

Existing queue commands:
  loom accept <task-id> [line 153] → calls transition_task(DONE)
  loom reject <task-id> "<note>" [line 165] → calls reject_task()
  loom decide <task-id> <option> [line 180] → calls decide_task()
  loom release <task-id> "<note>"    [line 195] → calls release_claim()
  loom review                        [line 119] → non-interactive listing
  loom [line 380] → calls _run_queue()

How _run_queue() works (lines 326-349):

## 1. Get queue: get_interaction_queue(loom) → list[dict with kind/id/title/file]

## 2. Display: _render_item_detail(loom, item)

## 3. Prompt: _prompt_action(item) → "accept"/"reject"/"decide"/"skip"/"open"/"detail"

## 4. Execute: calls decide_task(), reject_task(), transition_task()

## 5. Track: visited set to avoid re-processing

Pattern to follow for TUI:

- Reuse existing service functions (don't duplicate logic)
- Keep filesystem as source of truth
- Don't change CLI contracts

HOW TO ADD "loom tui":
  After line 45 in cli.py, add:
    tui_app = typer.Typer(help="Terminal UI for approval queue")
    app.add_typer(tui_app, name="tui")

  Create /workspace/loom/src/loom/tui.py with:
    import typer
    from textual.app import ComposeResult
    from textual.screen import Screen

    tui_app = typer.Typer()

    @tui_app.callback(invoke_without_command=True)
    def tui_main(ctx: typer.Context) -> None:
        """Launch approval queue TUI."""
        if ctx.invoked_subcommand is not None:
            return
        # Initialize Textual app here
        from loom.runtime import global_root
        from pathlib import Path
        loom_path = ...  # resolve like _resolve_loom() does
        app = ApprovalQueueApp(loom_path)
        app.run()

## 2. SCHEDULER & SERVICE FUNCTIONS TO REUSE

### Load Data

  File: /workspace/loom/src/loom/scheduler.py

  get_interaction_queue(loom_dir: Path) → list[dict] [line 122-138]
    Returns tasks sorted by priority with:
      - kind: "paused" or "reviewing"
      - id: task ID
      - title: task title
      - file: filesystem path
    PAUSED items come first in list (line 129)
    REVIEWING items come after (line 134)

  load_all_tasks(loom_dir: Path) → list[Task] [line 28-41]
    Loads all task files from .loom/threads/*/

  load_all_threads(loom_dir: Path) → dict[str, Thread] [line 15-25]
    Loads all thread definitions

  get_status_summary(loom_dir: Path) → dict [line 141-193]
    Full project status; includes queue as 'queue' key

### Execute Actions

  File: /workspace/loom/src/loom/services.py

  decide_task(loom: Path, task_id: str, option: str) → tuple[Path, Task] [line 434-451]
    Effect: PAUSED → SCHEDULED
    Updates: task.decision.decided = option
    Validation: validate_task_transition(), validate_task_scheduled()
    Event: appends task.decided event
    Usage pattern:
      path, updated_task = decide_task(loom, task_id, "option_id")

  reject_task(loom: Path, task_id: str, note: str) → tuple[Path, Task] [line 453-454]
    Effect: REVIEWING → SCHEDULED
    Updates: task.rejection_note = note
    Wraps: transition_task(loom, task_id, TaskStatus.SCHEDULED, rejection_note=note)
    Usage pattern:
      path, updated_task = reject_task(loom, task_id, "Needs work")

  transition_task(loom: Path, task_id: str, target_status: TaskStatus, **kwargs) [line 326-357]
    Effect: Changes task.status, validates transition
    Kwargs: output=str, rejection_note=str, decision=Decision
    Validation: validate_task_transition() raises InvalidTransitionError
    Writes: updates file via write_model()
    Events: appends to history via append_event()
    Usage for accept:
      path, updated_task = transition_task(loom, task_id, TaskStatus.DONE)

  release_claim(loom: Path, task_id: str, *, note: str) → tuple[Path, Task] [line 263-264]
    Wrapper for: transition_task(loom, task_id, TaskStatus.SCHEDULED, rejection_note=note)

  format_review_summary(task: Task) → list[str] [line 456-468]
    Returns lines for display:
      - task ID: title
      - status: value
      - output: if present
      - depends_on: if present
      - rejection_note: if present
      - created_from: if present
      - acceptance: multi-line if present
    Usage: for line in format_review_summary(task): print(line)

### Validation & State

  File: /workspace/loom/src/loom/state.py

  InvalidTransitionError(kind: str, current: str, target: str) [line 18-22]
    Custom exception: raised when transition is invalid
    Attributes: current, target (status values)

  validate_task_transition(current: TaskStatus, target: TaskStatus) → None [line 25-29]
    Raises InvalidTransitionError if not in TASK_TRANSITIONS map
    Safe to call before transition_task()

### Load Specific Items

  File: /workspace/loom/src/loom/repository.py

  load_task(loom: Path, task_id: str) → tuple[Path, Task]
    Loads single task file by ID
    Returns: (path_to_file, parsed_Task_model)

  task_file_path(loom_dir: Path, task: Task) → str
    Returns filesystem path string for display

  load_inbox_item(loom: Path, item_id: str) → tuple[Path, InboxItem]
    Similar pattern for inbox items (not needed for Phase 1)

## 3. DATA MODELS & STATE MACHINE

FILE: /workspace/loom/src/loom/models.py

<!-- BEGIN: task-status-guide -->
TaskStatus enum:
  DRAFT = "draft" ← not in interactive queue
  SCHEDULED = "scheduled" ← next for an agent; active ownership lives on the thread
  CLAIMED = "claimed" ← deprecated legacy task status; read only for backward-compat migration
  PAUSED = "paused" ← QUEUE: awaiting human decision
  REVIEWING = "reviewing" ← QUEUE: awaiting human approval
  DONE = "done" ← terminal
<!-- END: task-status-guide -->

Task model [current implementation]:
  id: str                    ← e.g., "backend-003"
  thread: str                ← thread name
  seq: int                   ← sequence in thread
  title: str
  kind: TaskKind = implementation
  status: TaskStatus
  priority: int = 50         ← higher = earlier in queue
  persistent: bool | None    ← optional long-running rescheduling flag
  depends_on: list[str] = [] ← task ID dependencies
  created_from: list[str] = []
  created: date
  output: str | None         ← agent's deliverable path/URL
  delivery: DeliveryContract | None ← explicit review handoff contract
  claim: Claim | dict | None ← legacy task-level claim data; backward-compat reads only
  decision: Decision | dict | None  ← FOR PAUSED: human needs to choose
  rejection_note: str | None ← latest rejection-note compatibility mirror
  review_history: list[ReviewEntry] = [] ← append-only accept/reject events
  acceptance: str | None     ← acceptance criteria (required for scheduled)
  body: str = ""

Decision model [line 96-100]:
  question: str ← what to ask human
  options: list[DecisionOption] ← predefined choices
    DecisionOption: id, label, note (optional)
  decided: str | None        ← filled AFTER human chooses

<!-- BEGIN: task-transition-guide -->
TASK STATE MACHINE (TASK_TRANSITIONS):
  DRAFT → SCHEDULED
  SCHEDULED → {REVIEWING, PAUSED}
  CLAIMED → {REVIEWING, PAUSED, SCHEDULED} ← backward-compat reads only; new tasks use thread ownership instead
  REVIEWING → {DONE, SCHEDULED}
  PAUSED → SCHEDULED
  DONE → SCHEDULED
<!-- END: task-transition-guide -->

Invariants:

- PAUSED task MUST have decision field set (line 172-174)
- SCHEDULED task MUST have non-empty acceptance (line 167-170)
- REVIEWING task MUST NOT contain incomplete markers (line 176-180)

## 4. DEPENDENCIES & TERMINAL HELPERS

Current pyproject.toml dependencies [/workspace/loom/pyproject.toml]:
  typer >= 0.15
  pydantic >= 2.0
  loguru >= 0.7
  PyYAML >= 6.0

Rich status:
  NOT explicitly listed, but available via typer
  Modules found: rich.{layout, panel, table, text, style, console, prompt}

Textual status:
  NOT in pyproject.toml
  ACTION: Add "textual >= 0.50" to dependencies list

Prompting helpers [/workspace/loom/src/loom/prompting.py]:
  def select(message: str, choices: Sequence[str], default: str) → str
    Uses Typer's native prompt, renders as [A / B / C]
    Can reuse or replace with Textual equivalent

  def text(message: str, default: str = "") → str
    Simple text input via Typer
    Can reuse or replace

For TUI, use Textual's:

- textual.app.App (main app container)
- textual.screen.Screen (full-screen views)
- textual.containers.Container, Vertical, Horizontal (layout)
- textual.widgets.Static, Input, Button, Select (UI elements)
- textual.binding.Binding (keyboard shortcuts)

## 5. TEST PATTERNS & FIXTURES

E2E TEST FILE: /workspace/loom/tests/e2e/test_cli.py (37KB, 270+ tests)

Fixtures [/workspace/loom/tests/conftest.py]:
  @pytest.fixture
  def runner() → CliRunner:
    """Create Typer CLI test runner."""
    return CliRunner()

  @pytest.fixture
  def isolated_project(tmp_path: Path, monkeypatch) → Path:
    """Create isolated temp directory and chdir to it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path

Common test patterns:

## 1. Initialize loom

     result = runner.invoke(app, ["init", "--project", "demo"])
     assert result.exit_code == 0
     assert (isolated_project / ".loom").is_dir()

## 2. Create thread

     result = runner.invoke(app, ["agent", "new-thread", "--name", "backend"], env={"LOOM_AGENT_ID": "x7k2"})
     assert "CREATED thread backend" in result.output

## 3. Create paused task

     result = runner.invoke(app, [
       "agent", "new-task",
       "--thread", "backend",
       "--title", "...",
       "--acceptance", "..."
     ], env={"LOOM_AGENT_ID": "x7k2"})
     task_id = result.output.split()[-1]  # Extract from output

     # Pause it:
     runner.invoke(app, ["agent", "pause", task_id, "--question", "..."], env={"LOOM_AGENT_ID": "x7k2"})

## 4. Test interactive queue

     result = runner.invoke(app, [])  # No args = default _run_queue()
     assert "[paused] task_id" in result.output
     assert "Queue:" in result.output

## 5. Call queue operations

     result = runner.invoke(app, ["decide", task_id, "option_A"])
     assert result.exit_code == 0

     # Verify file mutation:
     task_file = isolated_project / ".loom" / "threads" / "backend" / "001.md"
     content = task_file.read_text()
     assert "status: scheduled" in content
     assert "decided: option_A" in content

Unit test pattern [/workspace/loom/tests/unit/test_models.py]:
  task = Task.model_validate({...})
  assert task.status == TaskStatus.REVIEWING

  updated_data = task.model_dump(mode="python")
  updated_data["status"] = TaskStatus.DONE
  updated_task = Task.model_validate(updated_data)

FOR TUI TESTING (create /workspace/loom/tests/e2e/test_tui.py):
  Use Textual's Pilot for async/await testing:

  import pytest
  from textual.pilot import Pilot

  @pytest.mark.asyncio
  async def test_tui_accepts_reviewing_task(isolated_project):

## Setup: init loom, create reviewing task

      ...

      # Launch TUI:
      from loom.tui import ApprovalQueueApp
      from pathlib import Path
      app = ApprovalQueueApp(isolated_project / ".loom")

      async with app.run_test(size=(80, 24)) as pilot:
          # Simulate keypress:
          await pilot.press("down")  # Move to first item
          await pilot.press("enter")  # Enter detail view
          await pilot.press("a")      # Accept (if reviewing)

          # Check app state or file:
          task_file = isolated_project / ".loom" / "threads" / "backend" / "001.md"
          content = task_file.read_text()
          assert "status: done" in content

### 6. DESIGN DOCS & STYLING GUIDANCE

DESIGN/TUI-PLAN.MD (core vision):

- Phase 1: Approval queue TUI (paused + reviewing only)
- Phase 2: Inbox planning TUI
- Phase 3: Read-only status views
- Recommendation: Use Textual (not just Rich)
- Risks to monitor: mode drift, test fragility, complexity creep
- Keep plain CLI as stable interface; TUI is optional layer

DESIGN/CLI-DESIGN.MD (human surface styling):
  Lines 160-184: Interactive mockup shows desired UX:

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    [ 2 / 5 ]  review  backend-003  ·  x7k2  ·  14:32
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

      output
      ✓ src/pages/Login.tsx
      ✓ tests/e2e/test_login.py · 8 passed

      acceptance
      · form errors are clear
      · responsive on mobile and desktop

      A accept   R reject   O open   S skip   ? detail
      choice ›

  Human surface principles (lines 22-44):

- Separate users, separate defaults
- One state model, two entry points
- Readable by default, structured when needed
- Prefer stable text contracts before adding JSON
- Prefer additive migration

  Output style rules (lines 199-206):
- Plain-language section labels (NOT "LABELED_BLOCKS")
- Visible next actions
- Short action menus
- Minimal hidden machine markers
- Single-letter shortcuts: A, R, O, S, ?

  IMPORTANT: Keep TUI output style aligned with these principles
- Don't make TUI feel like "agent surface" (which uses LABELED_BLOCKS)
- Prioritize readability and comprehension over compactness
- Show acceptance criteria and prior notes (not just task ID)

DESIGN/REVIEW-WORKFLOW.MD (future context):
  Lines 96-102: Review operations table (current state):
    accept: reviewing → done
    reject: reviewing → scheduled (with note)
    decide: paused → scheduled

  Lines 108-126: Future batch operations (DEFER to Phase 2)
    Current Phase 1: single-task operations only
    Batch operations: accept, reject with same note across multiple tasks

  Lines 59-75: Current review_history append-only model
    review_history already stores append-only accept/reject events
    rejection_note remains the latest-note compatibility mirror

  IMPORTANT: Phase 1 TUI should use CURRENT models, not future ones

- review_history append-only field is available
- rejection_note remains only as the latest-note compatibility mirror
- No follow-up/continue flows yet

### 7. ACTIONABLE IMPLEMENTATION CHECKLIST

#### Setup

  [ ] Add "textual >= 0.50" to pyproject.toml dependencies
  [ ] Run: uv sync

#### Cli Plumbing

  [ ] Edit /workspace/loom/src/loom/cli.py
      - After line 45, before any command definitions:
        tui_app = typer.Typer(help="Terminal UI for approval queue.")
        app.add_typer(tui_app, name="tui")
      - Import at top: from .tui import tui_app

#### Tui Module

  [ ] Create /workspace/loom/src/loom/tui.py with:
      - tui_app: typer.Typer
      - ApprovalQueueApp(ComposeResult-based Textual App)
      - Screens: QueueListScreen, DetailScreen, ActionScreen
      - Event handlers for keyboard bindings

#### Tui Screens - Phase 1

  QueueListScreen:
    [ ] Load queue: get_interaction_queue(loom_path)
    [ ] Render list: paused items first, then reviewing
    [ ] Show format: "[ N / TOTAL ] KIND task_id · details"
    [ ] Selection: Arrow keys or Tab/Shift+Tab to navigate
    [ ] Enter: Move to DetailScreen with selected item
    [ ] ESC/q: Exit TUI

  DetailScreen:
    [ ] Load task: load_task(loom_path, task_id)
    [ ] Display sections:
        - Task title, ID, thread, status
        - Acceptance criteria (if present)
        - Output/artifacts (if present)
        - Prior rejection notes (if present)
    [ ] For PAUSED tasks: show Decision.options
    [ ] Keyboard bindings:
        A = accept (only for REVIEWING)
        R = reject (only for REVIEWING)
        D = decide (only for PAUSED)
        O = open in $EDITOR
        ? = show full detail
        S / ESC = skip / back to list

  ActionScreen (for inputs):
    [ ] For decide: show Decision.options as numbered/lettered list
    [ ] For reject: text input for rejection reason
    [ ] Submit: Call service function (decide_task, reject_task, transition_task)
    [ ] Error handling: Catch InvalidTransitionError, FileNotFoundError, ValueError
    [ ] On success: Return to DetailScreen or reload QueueListScreen

#### Service Integration

  [ ] In ActionScreen, after user input:
      try:
        if action == "decide":
          decide_task(loom_path, task_id, selected_option)
        elif action == "reject":
          reject_task(loom_path, task_id, rejection_text)
        elif action == "accept":
          transition_task(loom_path, task_id, TaskStatus.DONE)
      except InvalidTransitionError as e:
        show_error_modal(f"Cannot {action}: {e}")
      except FileNotFoundError as e:
        show_error_modal(f"Task not found: {e}")
  [ ] After mutation, reload from disk (files are source of truth)
  [ ] Return to appropriate screen

#### Testing

  [ ] Create /workspace/loom/tests/e2e/test_tui.py
  [ ] Setup fixtures: use isolated_project from conftest.py
  [ ] Test scenarios:
      - Launch TUI with no queue → show "No pending items"
      - Load queue with paused + reviewing tasks
      - Navigate to task, show detail
      - Execute decide on paused task → check frontmatter changed
      - Execute accept on reviewing task → check status = done
      - Execute reject → check rejection_note set, status = scheduled
      - Error case: reject non-reviewing task → error modal
      - Reload queue after action → next item appears
  [ ] Use Textual Pilot for async testing

### 8. PRIORITY READING ORDER (BEFORE CODING)

MUST READ (in order):

### 1. design/tui-plan.md

     → Understand Phase 1 scope, Textual recommendation, risks
     Time: 5 min

### 2. src/loom/cli.py lines 326-349 (_run_queue function)

     → See how current loop works; replicate pattern in TUI
     Time: 10 min

### 3. src/loom/scheduler.py lines 122-138 (get_interaction_queue)

     → Understand queue data structure and sorting
     Time: 5 min

### 4. src/loom/services.py lines 263-456

     → All queue operations: decide_task, reject_task, transition_task
     Time: 20 min

### 5. design/cli-design.md lines 160-184

     → Human surface mockup, styling principles
     Time: 10 min

REFERENCE (keep handy):

- src/loom/models.py (Task, Decision, TaskStatus enums)
- src/loom/state.py (InvalidTransitionError, validation)
- src/loom/repository.py (load_task, task_file_path)
- tests/e2e/test_cli.py lines 264-350 (test patterns)

### 9. KEY CODE PATTERNS TO COPY

Pattern: Load and display queue
  from loom.scheduler import get_interaction_queue
  from loom.repository import load_task
  from loom.services import format_review_summary

  queue = get_interaction_queue(loom_path)
  for item in queue:
    _, task = load_task(loom_path, item["id"])
    summary_lines = format_review_summary(task)

## Display lines to user

Pattern: Execute decision
  from loom.services import decide_task
  from loom.state import InvalidTransitionError

  try:
    path, updated_task = decide_task(loom_path, task_id, option_chosen)
  except InvalidTransitionError as e:
    show_error(f"Cannot decide: {e}")

Pattern: Execute rejection
  from loom.services import reject_task

  try:
    path, updated_task = reject_task(loom_path, task_id, rejection_note)
  except InvalidTransitionError as e:
    show_error(f"Cannot reject: {e}")

Pattern: Execute acceptance
  from loom.services import transition_task
  from loom.models import TaskStatus

  try:
    path, updated_task = transition_task(loom_path, task_id, TaskStatus.DONE)
  except InvalidTransitionError as e:
    show_error(f"Cannot accept: {e}")

Pattern: Show Decision options for paused task
  from loom.models import Decision

  _, task = load_task(loom_path, task_id)
  if task.status == TaskStatus.PAUSED:
    decision = task.decision
    if isinstance(decision, dict):
      decision = Decision.model_validate(decision)
    if isinstance(decision, Decision):
      for i, option in enumerate(decision.options):
        print(f"{i+1}. {option.label} ({option.id})")
        if option.note:
          print(f" {option.note}")

Pattern: Reload after mutation

## After calling decide_task(), reject_task(), etc

## Files are already updated on disk by service functions

## Reload queue to see changes

  queue = get_interaction_queue(loom_path)

## Display next item or return to list

END OF REFERENCE DOCUMENT
