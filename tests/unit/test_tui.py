"""Unit and integration tests for the optional Textual TUI (Phase 1)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Helpers to build a minimal .loom workspace
# ---------------------------------------------------------------------------


def _setup_loom(tmp_path: Path) -> Path:
    """Create a minimal .loom workspace and return the loom root path."""
    from loom.services import ensure_agent_layout

    loom = tmp_path / ".loom"
    loom.mkdir()
    (loom / "inbox").mkdir()
    (loom / "threads").mkdir()
    ensure_agent_layout(loom)
    # Minimal loom.toml
    (tmp_path / "loom.toml").write_text(
        '[project]\nname = "test"\n\n[agent]\ninbox_plan_batch = 10\ntask_batch = 1\n'
        'executor_command = ""\noffline_after_minutes = 30\nnext_wait_seconds = 0.0\nnext_retries = 0\n\n'
        "[threads]\ndefault_priority = 50\n",
        encoding="utf-8",
    )
    return loom


def _make_reviewing_task(loom: Path) -> str:
    """Create a reviewing task and return its ID."""
    from loom.models import TaskStatus
    from loom.services import create_task, create_thread, transition_task

    thread, _, _ = create_thread(loom, name="backend")
    task, _ = create_task(
        loom,
        thread_name=thread.name,
        title="Fix auth bug",
        acceptance="- [ ] tests pass",
    )
    # scheduled -> reviewing (thread-level ownership, no task claiming)
    transition_task(loom, task.id, TaskStatus.REVIEWING)
    return task.id


def _make_paused_task(loom: Path) -> str:
    """Create a paused task with a decision block and return its ID."""
    from loom.services import create_task, create_thread, pause_task

    thread, _, _ = create_thread(loom, name="frontend")
    task, _ = create_task(
        loom,
        thread_name=thread.name,
        title="Choose framework",
        acceptance="- [ ] framework chosen",
    )
    pause_task(
        loom,
        task.id,
        question="Which framework?",
        options=[{"id": "A", "label": "React"}, {"id": "B", "label": "Vue"}],
    )
    return task.id


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_tui_command_appears_in_help(runner, isolated_project):
    """The `loom tui` subcommand is registered and visible in --help."""
    from loom.cli import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "tui" in result.output


def test_tui_command_help_text(runner, isolated_project):
    """The `loom tui` subcommand has a help string."""
    from loom.cli import app

    result = runner.invoke(app, ["tui", "--help"])
    assert result.exit_code == 0, result.output
    assert "tui" in result.output.lower() or "textual" in result.output.lower()
    assert "watch" in result.output.lower()


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


def test_queue_label_reviewing():
    from loom.tui import _queue_label

    item = {"kind": "reviewing", "id": "backend-001", "title": "Fix auth", "file": "x"}
    label = _queue_label(item)
    assert "backend-001" in label
    assert "Fix auth" in label
    assert "👁" in label


def test_queue_label_paused():
    from loom.tui import _queue_label

    item = {"kind": "paused", "id": "frontend-001", "title": "Choose X", "file": "x"}
    label = _queue_label(item)
    assert "frontend-001" in label
    assert "Choose X" in label
    assert "⏸" in label


def test_queue_signature_is_stable():
    from loom.tui import _queue_signature

    items = [
        {"kind": "paused", "id": "frontend-001", "title": "Choose X", "file": "x"},
        {"kind": "reviewing", "id": "backend-001", "title": "Fix auth", "file": "y"},
    ]
    assert _queue_signature(items) == (("paused", "frontend-001"), ("reviewing", "backend-001"))


def test_detail_text_reviewing(tmp_path):
    loom = _setup_loom(tmp_path)
    task_id = _make_reviewing_task(loom)
    from loom.tui import _detail_text

    item = {
        "kind": "reviewing",
        "id": task_id,
        "title": "Fix auth bug",
        "file": str(tmp_path / ".loom" / "threads" / "backend" / "001.md"),
    }
    text = _detail_text(loom, item)
    assert task_id in text
    assert "REVIEWING" in text


def test_detail_text_paused_shows_question(tmp_path):
    loom = _setup_loom(tmp_path)
    task_id = _make_paused_task(loom)
    from loom.tui import _detail_text

    item = {
        "kind": "paused",
        "id": task_id,
        "title": "Choose framework",
        "file": str(tmp_path / ".loom" / "threads" / "frontend" / "001.md"),
    }
    text = _detail_text(loom, item)
    assert "Which framework?" in text
    assert "React" in text
    assert "Vue" in text


def test_help_modal_mentions_disk_first_limits():
    from loom.tui import _HelpModal

    assert ".loom/" in _HelpModal.HELP_TEXT
    assert "source of truth" in _HelpModal.HELP_TEXT


def test_decision_options_paused(tmp_path):
    loom = _setup_loom(tmp_path)
    task_id = _make_paused_task(loom)
    from loom.tui import _decision_options

    opts = _decision_options(loom, task_id)
    assert "A" in opts
    assert "B" in opts


def test_decision_options_missing_task(tmp_path):
    loom = _setup_loom(tmp_path)
    from loom.tui import _decision_options

    opts = _decision_options(loom, "thzz-999")
    assert opts == []


def test_create_inbox_item_service(tmp_path):
    loom = _setup_loom(tmp_path)
    from loom.services import create_inbox_item

    item, path = create_inbox_item(loom, "Line 1\nLine 2")

    assert item.id == "RQ-001"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Line 1" in content
    assert "Line 2" in content


# ---------------------------------------------------------------------------
# require_textual guard
# ---------------------------------------------------------------------------


def test_require_textual_passes_when_available():
    from loom.tui import require_textual

    # Should not raise because textual is installed
    require_textual()


# ---------------------------------------------------------------------------
# TUI app integration tests using run_test (headless Textual pilot)
# ---------------------------------------------------------------------------


def test_tui_empty_queue_starts_and_quits(tmp_path, monkeypatch):
    """TUI starts with an empty queue, shows no-item message, and quits."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(run())


def test_tui_shows_reviewing_item(tmp_path, monkeypatch):
    """TUI lists a reviewing task in the queue."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    task_id = _make_reviewing_task(loom)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            # The queue list should contain the task id
            lv = app.query_one("#queue-list")
            assert lv is not None
            # Check queue was loaded
            assert len(app._queue) == 1
            assert app._queue[0]["id"] == task_id
            await pilot.press("q")

    asyncio.run(run())


def test_tui_updates_panel_titles_for_selected_item(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    task_id = _make_reviewing_task(loom)
    from textual.widgets import ListView, Static

    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            queue = app.query_one("#queue-list", ListView)
            detail = app.query_one("#detail-panel", Static)
            assert "1 item" in (queue.border_title or "")
            assert task_id in (detail.border_title or "")
            await pilot.press("q")

    asyncio.run(run())


def test_tui_accept_reviewing_task(tmp_path, monkeypatch):
    """Pressing 'a' on a reviewing task accepts it and removes it from queue."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    task_id = _make_reviewing_task(loom)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            assert len(app._queue) == 1
            await pilot.press("a")
            await pilot.pause()
            # Queue should be empty after accept
            assert len(app._queue) == 0
            await pilot.press("q")

    asyncio.run(run())

    # Verify the task is actually done on disk
    from loom.repository import load_task

    _, task = load_task(loom, task_id)
    from loom.models import TaskStatus

    assert task.status == TaskStatus.DONE


def test_tui_reject_reviewing_task(tmp_path, monkeypatch):
    """Pressing 'r' on a reviewing task opens modal; entering note rejects the task."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    task_id = _make_reviewing_task(loom)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            # Modal should be open — type note and submit
            await pilot.press(*"Need rework")
            await pilot.press("enter")
            await pilot.pause()
            assert len(app._queue) == 0
            await pilot.press("q")

    asyncio.run(run())

    from loom.repository import load_task

    _, task = load_task(loom, task_id)
    from loom.models import TaskStatus

    assert task.status == TaskStatus.SCHEDULED
    assert task.rejection_note == "Need rework"


def test_tui_decide_paused_task(tmp_path, monkeypatch):
    """Pressing 'd' on a paused task opens decide modal; picking option schedules task."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    task_id = _make_paused_task(loom)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            assert len(app._queue) == 1
            await pilot.press("d")
            await pilot.pause()
            # Type option A in input and submit
            await pilot.press(*"A")
            await pilot.press("enter")
            await pilot.pause()
            assert len(app._queue) == 0
            await pilot.press("q")

    asyncio.run(run())

    from loom.repository import load_task

    _, task = load_task(loom, task_id)
    from loom.models import Decision, TaskStatus

    assert task.status == TaskStatus.SCHEDULED
    decision = task.decision
    if isinstance(decision, dict):
        from loom.models import Decision

        decision = Decision.model_validate(decision)
    assert isinstance(decision, Decision)
    assert decision.decided == "A"


def test_tui_wrong_action_on_paused_shows_status(tmp_path, monkeypatch):
    """Pressing 'a' (accept) on a paused task shows an error in the status bar."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    _make_paused_task(loom)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            status = str(app.query_one("#status-bar").render())
            assert "not in reviewing" in status.lower() or "paused" in status.lower()
            await pilot.press("q")

    asyncio.run(run())


def test_tui_refresh_reloads_queue(tmp_path, monkeypatch):
    """Pressing 'R' refreshes the queue from disk."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            assert len(app._queue) == 0
            # Add a reviewing task while TUI is open
            _make_reviewing_task(loom)
            await pilot.press("R")
            await pilot.pause()
            assert len(app._queue) == 1
            await pilot.press("q")

    asyncio.run(run())


def test_tui_watch_reloads_queue_after_external_change(tmp_path, monkeypatch):
    """Pressing 'w' enables polling reloads from disk."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            assert len(app._queue) == 0
            await pilot.press("w")
            await pilot.pause()
            _make_reviewing_task(loom)
            await pilot.pause(1.2)
            assert len(app._queue) == 1
            status = str(app.query_one("#status-bar").render())
            assert "watch on" in status.lower() or "reloaded queue" in status.lower()
            await pilot.press("w")
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(run())


def test_tui_help_overlay_opens_and_closes(tmp_path, monkeypatch):
    """Pressing '?' opens the shortcut overlay."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("?")
            await pilot.pause()
            assert len(app.screen_stack) == 2
            assert app.screen_stack[-1].__class__.__name__ == "_HelpModal"
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("q")

    asyncio.run(run())


def test_tui_release_reviewing_task(tmp_path, monkeypatch):
    """Pressing 'l' releases thread ownership for the selected queue item."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    task_id = _make_reviewing_task(loom)
    # Assign thread ownership so the release action has something to release.
    from loom.services import claim_thread

    claim_thread(loom, "backend", agent_id="agent1")
    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            await pilot.press(*"Release for more work")
            await pilot.press("enter")
            await pilot.pause()
            assert len(app._queue) == 0
            await pilot.press("q")

    asyncio.run(run())

    from loom.models import TaskStatus
    from loom.repository import load_task

    _, task = load_task(loom, task_id)
    assert task.status == TaskStatus.SCHEDULED
    assert task.rejection_note == "Release for more work"
    # Thread ownership should also be released.
    from loom.scheduler import load_all_threads

    threads = load_all_threads(loom)
    assert threads["backend"].owner is None


def test_tui_new_requirement_creates_inbox_item(tmp_path, monkeypatch):
    """Creating a new requirement from the TUI writes an inbox item."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)

    from loom.tui import QueueApp

    app = QueueApp(loom)

    async def run():
        async with app.run_test(headless=True, size=(100, 30)) as pilot:
            await pilot.pause()
            app._do_create_inbox_item("First line\nSecond line")
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(run())

    inbox_file = loom / "inbox" / "RQ-001.md"
    assert inbox_file.exists()
    content = inbox_file.read_text(encoding="utf-8")
    assert "First line" in content
    assert "Second line" in content
