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
    from loom.services import claim_task, create_task, create_thread, transition_task

    thread, _, _ = create_thread(loom, name="backend")
    task, _ = create_task(
        loom,
        thread_id=thread.name,
        title="Fix auth bug",
        acceptance="- [ ] tests pass",
    )
    # scheduled -> claimed -> reviewing
    claim_task(loom, task.id, agent_id="agent1")
    transition_task(loom, task.id, TaskStatus.REVIEWING)
    return task.id


def _make_paused_task(loom: Path) -> str:
    """Create a paused task with a decision block and return its ID."""
    from loom.services import claim_task, create_task, create_thread, pause_task

    thread, _, _ = create_thread(loom, name="frontend")
    task, _ = create_task(
        loom,
        thread_id=thread.name,
        title="Choose framework",
        acceptance="- [ ] framework chosen",
    )
    claim_task(loom, task.id, agent_id="agent1")
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


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


def test_queue_label_reviewing():
    from loom.tui import _queue_label

    item = {"kind": "reviewing", "id": "thaa-001", "title": "Fix auth", "file": "x"}
    label = _queue_label(item)
    assert "thaa-001" in label
    assert "Fix auth" in label
    assert "👁" in label


def test_queue_label_paused():
    from loom.tui import _queue_label

    item = {"kind": "paused", "id": "thab-001", "title": "Choose X", "file": "x"}
    label = _queue_label(item)
    assert "thab-001" in label
    assert "Choose X" in label
    assert "⏸" in label


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


def test_tui_release_reviewing_task(tmp_path, monkeypatch):
    """Pressing 'l' releases the selected claimed queue item back to scheduled."""
    monkeypatch.chdir(tmp_path)
    loom = _setup_loom(tmp_path)
    task_id = _make_reviewing_task(loom)
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
    assert task.claim is None
    assert task.rejection_note == "Release for more work"
