"""Optional Textual TUI for the Loom approval queue (Phase 1).

Requires the ``tui`` optional dependency group::

    uv sync --extra tui

Launch with::

    loom tui
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

    _TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _TEXTUAL_AVAILABLE = False
if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def require_textual() -> None:
    """Raise ImportError with install hint if Textual is not available."""
    if not _TEXTUAL_AVAILABLE:
        raise ImportError("The 'tui' optional dependency is required. Install it with: uv sync --extra tui")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _queue_label(item: dict[str, Any]) -> str:
    kind_tag = "⏸" if item["kind"] == "paused" else "👁"
    return f"{kind_tag} {item['id']}  {item['title']}"


def _detail_text(loom: Path, item: dict[str, Any]) -> str:
    """Build a plain-text detail view for a queue item."""
    from .models import Decision
    from .repository import load_task
    from .services import format_review_summary

    lines: list[str] = []
    lines.append(f"[{item['kind'].upper()}]  {item['id']}")
    lines.append(f"Title : {item['title']}")
    lines.append(f"File  : {item['file']}")
    lines.append("")

    try:
        _, task = load_task(loom, item["id"])
    except FileNotFoundError:
        lines.append("(task file not found)")
        return "\n".join(lines)

    for line in format_review_summary(task)[1:]:
        lines.append(line.lstrip())

    claim_agent, claimed_at = _claim_summary(task)
    if claim_agent:
        lines.append(f"Claim : {claim_agent}")
    if claimed_at:
        lines.append(f"Claimed at : {claimed_at}")

    if task.decision:
        decision = task.decision
        if isinstance(decision, dict):
            decision = Decision.model_validate(decision)
        if isinstance(decision, Decision):
            lines.append("")
            lines.append(f"Question: {decision.question}")
            if decision.options:
                lines.append("Options:")
                for opt in decision.options:
                    suffix = f" — {opt.note}" if opt.note else ""
                    lines.append(f"  [{opt.id}] {opt.label}{suffix}")

    if task.output:
        lines.append("")
        lines.append("Output:")
        for ol in task.output.splitlines():
            lines.append(f"  {ol}")

    return "\n".join(lines)


def _decision_options(loom: Path, task_id: str) -> list[str]:
    """Return the list of option IDs for a paused task's decision block."""
    from .models import Decision
    from .repository import load_task

    try:
        _, task = load_task(loom, task_id)
    except FileNotFoundError:
        return []
    if not task.decision:
        return []
    decision = task.decision
    if isinstance(decision, dict):
        decision = Decision.model_validate(decision)
    if isinstance(decision, Decision):
        return [opt.id for opt in decision.options]
    return []


def _claim_summary(task: Any) -> tuple[str | None, str | None]:
    """Return `(agent, claimed_at)` for a task claim payload."""
    from .models import Claim

    claim = task.claim
    if isinstance(claim, dict):
        claim = Claim.model_validate(claim)
    if isinstance(claim, Claim):
        return claim.agent, claim.claimed_at
    return None, None


# ---------------------------------------------------------------------------
# Modal screens
# ---------------------------------------------------------------------------

if _TEXTUAL_AVAILABLE:

    class _TextInputModal(ModalScreen[str | None]):
        """Generic single-line text input modal.  Returns the entered text or None."""

        DEFAULT_CSS = """
        _TextInputModal {
            align: center middle;
        }
        _TextInputModal > Vertical {
            width: 60;
            height: auto;
            border: thick $accent;
            background: $surface;
            padding: 1 2;
        }
        _TextInputModal Label {
            margin-bottom: 1;
        }
        _TextInputModal Input {
            margin-bottom: 1;
        }
        _TextInputModal .buttons {
            layout: horizontal;
            align: right middle;
            height: auto;
        }
        _TextInputModal Button {
            margin-left: 1;
        }
        """

        def __init__(self, prompt: str) -> None:
            super().__init__()
            self._prompt = prompt

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Label(self._prompt)
                yield Input(placeholder="Enter text…", id="text-input")
                with Horizontal(classes="buttons"):
                    yield Button("OK", id="ok", variant="primary")
                    yield Button("Cancel", id="cancel")

        def on_mount(self) -> None:
            self.query_one("#text-input", Input).focus()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "ok":
                value = self.query_one("#text-input", Input).value.strip()
                if value:
                    self.dismiss(value)
                    return
            self.dismiss(None)

        def on_input_submitted(self, _event: Input.Submitted) -> None:
            value = self.query_one("#text-input", Input).value.strip()
            if value:
                self.dismiss(value)

    class _DecideModal(ModalScreen[str | None]):
        """Decision modal — shows options and/or accepts free-text input."""

        DEFAULT_CSS = """
        _DecideModal {
            align: center middle;
        }
        _DecideModal > Vertical {
            width: 60;
            height: auto;
            border: thick $accent;
            background: $surface;
            padding: 1 2;
        }
        _DecideModal Label {
            margin-bottom: 1;
        }
        _DecideModal ListView {
            height: auto;
            max-height: 8;
            margin-bottom: 1;
            border: solid $primary-darken-2;
        }
        _DecideModal Input {
            margin-bottom: 1;
        }
        _DecideModal .buttons {
            layout: horizontal;
            align: right middle;
            height: auto;
        }
        _DecideModal Button {
            margin-left: 1;
        }
        """

        def __init__(self, question: str, options: list[str]) -> None:
            super().__init__()
            self._question = question
            self._options = options

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Label(f"Decide: {self._question}" if self._question else "Decide")
                if self._options:
                    yield Label("Options (select or type below):")
                    yield ListView(
                        *[ListItem(Label(opt), id=f"opt-{opt}") for opt in self._options],
                        id="options-list",
                    )
                yield Input(placeholder="Option ID or free text…", id="decide-input")
                with Horizontal(classes="buttons"):
                    yield Button("OK", id="ok", variant="primary")
                    yield Button("Cancel", id="cancel")

        def on_mount(self) -> None:
            self.query_one("#decide-input", Input).focus()

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            item_id = event.item.id or ""
            if item_id.startswith("opt-"):
                opt = item_id[4:]
                inp = self.query_one("#decide-input", Input)
                inp.value = opt

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "ok":
                value = self.query_one("#decide-input", Input).value.strip()
                if value:
                    self.dismiss(value)
                    return
            self.dismiss(None)

        def on_input_submitted(self, _event: Input.Submitted) -> None:
            value = self.query_one("#decide-input", Input).value.strip()
            if value:
                self.dismiss(value)

    # ---------------------------------------------------------------------------
    # Main application
    # ---------------------------------------------------------------------------

    class QueueApp(App[None]):
        """Loom approval-queue TUI (Phase 1)."""

        TITLE = "Loom — Approval Queue"
        SUB_TITLE = "paused / reviewing"

        CSS = """
        Screen {
            layout: vertical;
        }
        #main {
            layout: horizontal;
        }
        #queue-list {
            width: 1fr;
            min-width: 30;
            border: solid $primary-darken-2;
            border-title-color: $accent;
        }
        #detail-panel {
            width: 3fr;
            border: solid $primary-darken-2;
            border-title-color: $accent;
            padding: 1 2;
            overflow-y: scroll;
        }
        #status-bar {
            height: 1;
            background: $primary-darken-2;
            color: $text;
            padding: 0 1;
        }
        """

        BINDINGS: ClassVar[list[Binding]] = [
            Binding("a", "accept", "Accept", show=True),
            Binding("r", "reject", "Reject", show=True),
            Binding("d", "decide", "Decide", show=True),
            Binding("l", "release", "Release", show=True),
            Binding("R", "refresh", "Refresh", show=True),
            Binding("q", "quit", "Quit", show=True),
        ]

        def __init__(self, loom: Path) -> None:
            super().__init__()
            self._loom = loom
            self._queue: list[dict[str, Any]] = []

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="main"):
                yield ListView(id="queue-list")
                yield Static("Select an item from the queue.", id="detail-panel")
            yield Static("", id="status-bar")
            yield Footer()

        def on_mount(self) -> None:
            self._reload_queue()
            lv = self.query_one("#queue-list", ListView)
            lv.border_title = "Queue"
            self.query_one("#detail-panel", Static).border_title = "Detail"

        # ------------------------------------------------------------------
        # Queue loading
        # ------------------------------------------------------------------

        def _reload_queue(self) -> None:
            from .scheduler import get_interaction_queue

            self._queue = get_interaction_queue(self._loom)
            lv = self.query_one("#queue-list", ListView)
            lv.clear()
            for item in self._queue:
                lv.append(ListItem(Label(_queue_label(item))))
            if self._queue:
                lv.index = 0
                self._show_detail(0)
            else:
                self._set_detail("No paused or reviewing tasks.")
            self._set_status(
                f"{len(self._queue)} item(s) in queue · [a] accept · [r] reject · "
                "[d] decide · [l] release · [R] refresh · [q] quit"
            )

        def _current_item(self) -> dict[str, Any] | None:
            lv = self.query_one("#queue-list", ListView)
            idx = lv.index
            if idx is None or idx < 0 or idx >= len(self._queue):
                return None
            return self._queue[idx]

        def _show_detail(self, idx: int) -> None:
            if 0 <= idx < len(self._queue):
                text = _detail_text(self._loom, self._queue[idx])
            else:
                text = "Select an item from the queue."
            self._set_detail(text)

        def _set_detail(self, text: str) -> None:
            self.query_one("#detail-panel", Static).update(text)

        def _set_status(self, msg: str) -> None:
            self.query_one("#status-bar", Static).update(msg)

        # ------------------------------------------------------------------
        # ListView selection
        # ------------------------------------------------------------------

        def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
            lv = self.query_one("#queue-list", ListView)
            idx = lv.index
            if idx is not None:
                self._show_detail(idx)

        # ------------------------------------------------------------------
        # Key actions
        # ------------------------------------------------------------------

        def action_refresh(self) -> None:
            self._reload_queue()
            self._set_status("Refreshed.")

        def action_accept(self) -> None:
            item = self._current_item()
            if item is None:
                self._set_status("No item selected.")
                return
            if item["kind"] != "reviewing":
                self._set_status(f"{item['id']} is not in reviewing status — use [d]ecide for paused tasks.")
                return
            self._do_accept(item)

        def _do_accept(self, item: dict[str, Any]) -> None:
            from .models import TaskStatus
            from .services import transition_task
            from .state import InvalidTransitionError

            try:
                transition_task(self._loom, item["id"], TaskStatus.DONE)
            except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
                self._set_status(f"Error: {exc}")
                return
            self._reload_queue()
            self._set_status(f"Accepted {item['id']} → done.")

        def action_reject(self) -> None:
            item = self._current_item()
            if item is None:
                self._set_status("No item selected.")
                return
            if item["kind"] != "reviewing":
                self._set_status(f"{item['id']} is not in reviewing status.")
                return

            def _on_note(note: str | None) -> None:
                if not note:
                    self._set_status("Rejection cancelled.")
                    return
                self._do_reject(item, note)

            self.push_screen(_TextInputModal("Rejection reason:"), _on_note)

        def _do_reject(self, item: dict[str, Any], note: str) -> None:
            from .services import reject_task
            from .state import InvalidTransitionError

            try:
                reject_task(self._loom, item["id"], note)
            except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
                self._set_status(f"Error: {exc}")
                return
            self._reload_queue()
            self._set_status(f"Rejected {item['id']} → scheduled.")

        def action_release(self) -> None:
            item = self._current_item()
            if item is None:
                self._set_status("No item selected.")
                return

            from .repository import load_task

            try:
                _, task = load_task(self._loom, item["id"])
            except FileNotFoundError:
                self._set_status(f"Task file not found: {item['id']}")
                return

            claim_agent, _claimed_at = _claim_summary(task)
            if not claim_agent:
                self._set_status(f"{item['id']} has no active claim to release.")
                return

            def _on_note(note: str | None) -> None:
                if not note:
                    self._set_status("Release cancelled.")
                    return
                self._do_release(item, note)

            self.push_screen(_TextInputModal("Release reason:"), _on_note)

        def _do_release(self, item: dict[str, Any], note: str) -> None:
            from .services import release_claim
            from .state import InvalidTransitionError

            try:
                release_claim(self._loom, item["id"], note=note)
            except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
                self._set_status(f"Error: {exc}")
                return
            self._reload_queue()
            self._set_status(f"Released {item['id']} → scheduled.")

        def action_decide(self) -> None:
            item = self._current_item()
            if item is None:
                self._set_status("No item selected.")
                return
            if item["kind"] != "paused":
                self._set_status(f"{item['id']} is not paused — use [a]ccept or [r]eject for reviewing tasks.")
                return

            from .models import Decision
            from .repository import load_task

            try:
                _, task = load_task(self._loom, item["id"])
            except FileNotFoundError:
                self._set_status(f"Task file not found: {item['id']}")
                return

            decision = task.decision
            if isinstance(decision, dict):
                decision = Decision.model_validate(decision)

            question = decision.question if isinstance(decision, Decision) else ""
            options = [opt.id for opt in decision.options] if isinstance(decision, Decision) else []

            def _on_choice(choice: str | None) -> None:
                if not choice:
                    self._set_status("Decision cancelled.")
                    return
                self._do_decide(item, choice)

            self.push_screen(_DecideModal(question, options), _on_choice)

        def _do_decide(self, item: dict[str, Any], option: str) -> None:
            from .services import decide_task
            from .state import InvalidTransitionError

            try:
                decide_task(self._loom, item["id"], option)
            except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
                self._set_status(f"Error: {exc}")
                return
            self._reload_queue()
            self._set_status(f"Decided {item['id']} → scheduled.")


# ---------------------------------------------------------------------------
# Entry point called from cli.py
# ---------------------------------------------------------------------------


def run_tui(loom: Path) -> None:
    """Launch the Textual TUI.  Raises ImportError if Textual is not installed."""
    require_textual()
    app = QueueApp(loom)
    app.run()
