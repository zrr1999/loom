"""Tests for review workflow: append-only history and outcome-first presentation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from loom.models import (
    DeliveryContract,
    ReviewEntry,
    Task,
    TaskStatus,
    Thread,
    ThreadPR,
    ThreadWorktree,
    WorktreeStatus,
)

if TYPE_CHECKING:
    from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# Unit tests: ReviewEntry model
# ---------------------------------------------------------------------------


def test_review_entry_defaults():
    entry = ReviewEntry(kind="reject", note="Missing tests")
    assert entry.kind == "reject"
    assert entry.actor == "human"
    assert entry.source == "cli"
    assert entry.note == "Missing tests"


def test_task_with_review_history_round_trips():
    """A Task with review_history entries survives model_dump → model_validate."""
    entries = [
        ReviewEntry(kind="reject", actor="human", created="2026-03-18T08:00:00Z", note="First issue"),
        ReviewEntry(kind="reject", actor="human", created="2026-03-18T09:00:00Z", note="Second issue"),
    ]
    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Demo",
        status=TaskStatus.SCHEDULED,
        acceptance="- [ ] ready",
        review_history=entries,
    )
    data = task.model_dump(mode="python")
    restored = Task.model_validate(data)
    assert len(restored.review_history) == 2
    assert restored.review_history[0].note == "First issue"
    assert restored.review_history[1].note == "Second issue"


def test_task_review_history_defaults_to_empty():
    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Demo",
        status=TaskStatus.DRAFT,
    )
    assert task.review_history == []


# ---------------------------------------------------------------------------
# Unit tests: format_review_summary outcome-first ordering
# ---------------------------------------------------------------------------


def test_format_review_summary_outcome_first():
    """Acceptance and output appear before depends_on/created_from."""
    from loom.services import format_review_summary

    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Token refresh",
        status=TaskStatus.REVIEWING,
        acceptance="- [ ] POST /auth/refresh works",
        output="src/auth.py",
        depends_on=["backend-000"],
        created_from=["RQ-001"],
    )
    lines = format_review_summary(task)
    text = "\n".join(lines)

    # Acceptance criteria and output should come before depends_on/created_from
    acceptance_idx = text.index("acceptance:")
    output_idx = text.index("output:")
    depends_idx = text.index("depends_on:")
    created_idx = text.index("created_from:")

    assert acceptance_idx < depends_idx, "acceptance should appear before depends_on"
    assert acceptance_idx < created_idx, "acceptance should appear before created_from"
    assert output_idx < depends_idx, "output should appear before depends_on"


def test_format_review_summary_shows_review_history():
    """When review_history is present, it appears instead of rejection_note."""
    from loom.services import format_review_summary

    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Token refresh",
        status=TaskStatus.REVIEWING,
        acceptance="- [ ] ok",
        rejection_note="legacy note",
        review_history=[
            ReviewEntry(kind="reject", created="2026-03-18T08:00:00Z", note="First rejection"),
            ReviewEntry(kind="reject", created="2026-03-18T09:00:00Z", note="Second rejection"),
        ],
    )
    lines = format_review_summary(task)
    text = "\n".join(lines)

    assert "review_history:" in text
    assert "First rejection" in text
    assert "Second rejection" in text
    # Legacy rejection_note should NOT appear when review_history is present
    assert "rejection_note:" not in text


def test_format_review_summary_falls_back_to_rejection_note():
    """When review_history is empty, show legacy rejection_note."""
    from loom.services import format_review_summary

    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Token refresh",
        status=TaskStatus.SCHEDULED,
        acceptance="- [ ] ok",
        rejection_note="legacy note",
    )
    lines = format_review_summary(task)
    text = "\n".join(lines)

    assert "rejection_note: legacy note" in text
    assert "review_history:" not in text


def test_format_review_summary_includes_thread_prs_and_worktrees():
    from loom.services import format_review_summary

    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Token refresh",
        status=TaskStatus.REVIEWING,
        acceptance="- [ ] ok",
    )
    thread = Thread(
        name="backend",
        worktrees=[
            ThreadWorktree(
                name="feature-a",
                worker="aaap",
                path="/workspace/feature-a",
                branch="feat/worktree-a",
                status=WorktreeStatus.ACTIVE,
            )
        ],
        pr_artifacts=[
            ThreadPR(
                url="https://github.com/acme/loom/pull/42",
                branch="feat/worktree-a",
                task_ids=["backend-001"],
            )
        ],
    )

    text = "\n".join(format_review_summary(task, thread=thread))
    assert "thread_prs:" in text
    assert "https://github.com/acme/loom/pull/42" in text
    assert "thread_worktrees:" in text
    assert "feature-a [active]" in text


def test_format_review_summary_includes_delivery_contract():
    from loom.services import format_review_summary

    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Token refresh",
        status=TaskStatus.REVIEWING,
        acceptance="- [ ] ok",
        output="handoff note",
        delivery=DeliveryContract(
            ready=True,
            artifacts=[".loom/products/reports/backend-001.md"],
            pr_urls=["https://github.com/acme/loom/pull/42"],
        ),
    )

    text = "\n".join(format_review_summary(task))
    assert "delivery:" in text
    assert "ready: yes" in text
    assert ".loom/products/reports/backend-001.md" in text
    assert "https://github.com/acme/loom/pull/42" in text


# ---------------------------------------------------------------------------
# Integration tests: repeated rejection flow (services layer)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _loom_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal loom project structure for service-level tests."""
    monkeypatch.delenv("LOOM_WORKER_ID", raising=False)
    monkeypatch.delenv("LOOM_AGENT_ID", raising=False)
    monkeypatch.delenv("LOOM_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    from typer.testing import CliRunner

    from loom.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["init", "--project", "test"])
    assert result.exit_code == 0, result.output
    return tmp_path / ".loom"


def _make_reviewing_task(loom: Path) -> str:
    """Create a task in reviewing status via services."""
    from loom.services import create_task, create_thread, transition_task

    create_thread(loom, name="backend", priority=50)
    task, _ = create_task(
        loom,
        thread_name="backend",
        title="Test task",
        acceptance="- [ ] tests pass",
    )
    # Thread-level ownership: SCHEDULED → REVIEWING directly
    transition_task(loom, task.id, TaskStatus.REVIEWING, output="src/main.py")
    return task.id


def test_reject_appends_to_review_history(_loom_project: Path):
    """A single rejection creates a review_history entry."""
    from loom.repository import load_task
    from loom.scheduler import load_all_threads
    from loom.services import claim_thread, reject_task

    loom = _loom_project
    task_id = _make_reviewing_task(loom)
    claim_thread(loom, "backend", agent_id="worker-1")

    reject_task(loom, task_id, "Missing validation tests")

    _, task = load_task(loom, task_id)
    assert task.status == TaskStatus.SCHEDULED
    assert len(task.review_history) == 1
    assert task.review_history[0].kind == "reject"
    assert task.review_history[0].note == "Missing validation tests"
    # Backward compat: rejection_note is also set
    assert task.rejection_note == "Missing validation tests"
    assert load_all_threads(loom)["backend"].owner == "worker-1"


def test_accept_appends_to_review_history(_loom_project: Path):
    """Accepting a task records an accept entry in review_history."""
    from loom.repository import load_task
    from loom.services import accept_task

    loom = _loom_project
    task_id = _make_reviewing_task(loom)

    accept_task(loom, task_id, note="Looks good")

    _, task = load_task(loom, task_id)
    assert task.status == TaskStatus.DONE
    assert len(task.review_history) == 1
    assert task.review_history[0].kind == "accept"
    assert task.review_history[0].note == "Looks good"


def test_complete_task_accepts_explicit_delivery_contract(_loom_project: Path):
    from loom.services import complete_task, reject_task

    loom = _loom_project
    task_id = _make_reviewing_task(loom)
    reject_task(loom, task_id, "Back to implementation")

    _, updated, blockers = complete_task(
        loom,
        task_id,
        output="proposal-only summary\nTODO: finish tests",
        delivery=DeliveryContract(
            ready=True,
            artifacts=["reports/review.txt"],
            pr_urls=["https://github.com/acme/loom/pull/42"],
        ),
    )

    assert blockers == []
    assert updated.status == TaskStatus.REVIEWING
    assert updated.delivery is not None
    assert updated.delivery.ready is True
    assert updated.delivery.artifacts == [".loom/products/reports/review.txt"]
    assert updated.delivery.pr_urls == ["https://github.com/acme/loom/pull/42"]


def test_repeated_rejection_preserves_full_history(_loom_project: Path):
    """Multiple reject-revise-review cycles build up append-only history."""
    from loom.repository import load_task
    from loom.services import accept_task, reject_task, transition_task

    loom = _loom_project
    task_id = _make_reviewing_task(loom)

    # Round 1: reject
    reject_task(loom, task_id, "Validation copy is still English")

    _, task = load_task(loom, task_id)
    assert task.status == TaskStatus.SCHEDULED
    assert len(task.review_history) == 1

    # Worker revises and re-submits
    # Worker revises: SCHEDULED → REVIEWING
    transition_task(loom, task_id, TaskStatus.REVIEWING, output="src/main.py v2")

    # Round 2: reject again
    reject_task(loom, task_id, "Error messages still not localised")

    _, task = load_task(loom, task_id)
    assert task.status == TaskStatus.SCHEDULED
    assert len(task.review_history) == 2
    assert task.review_history[0].note == "Validation copy is still English"
    assert task.review_history[1].note == "Error messages still not localised"
    # rejection_note reflects the latest rejection
    assert task.rejection_note == "Error messages still not localised"

    # Worker revises and re-submits one more time
    # Worker revises: SCHEDULED → REVIEWING
    transition_task(loom, task_id, TaskStatus.REVIEWING, output="src/main.py v3")

    # Round 3: accept
    accept_task(loom, task_id, note="All issues addressed")

    _, task = load_task(loom, task_id)
    assert task.status == TaskStatus.DONE
    assert len(task.review_history) == 3
    assert task.review_history[0].kind == "reject"
    assert task.review_history[1].kind == "reject"
    assert task.review_history[2].kind == "accept"
    assert task.review_history[2].note == "All issues addressed"


def test_review_history_persists_through_frontmatter_round_trip(_loom_project: Path):
    """review_history survives write_model → read_model cycle."""
    from loom.frontmatter import read_model, write_model
    from loom.repository import load_task
    from loom.services import reject_task

    loom = _loom_project
    task_id = _make_reviewing_task(loom)
    reject_task(loom, task_id, "First issue")

    # Load from disk (verifies YAML round-trip)
    path, task = load_task(loom, task_id)
    assert len(task.review_history) == 1
    assert task.review_history[0].kind == "reject"

    # Write back and read again
    write_model(path, task)
    task2 = read_model(path, Task)
    assert len(task2.review_history) == 1
    assert task2.review_history[0].note == "First issue"


# ---------------------------------------------------------------------------
# E2E tests: CLI review command output
# ---------------------------------------------------------------------------


def test_cli_review_shows_outcome_first(runner: CliRunner, isolated_project: Path):
    """'loom review' output shows acceptance before metadata."""
    from loom.cli import app

    # Setup
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0

    env = {"LOOM_WORKER_ID": "x7k2"}
    runner.invoke(
        app,
        ["agent", "new-thread", "--name", "backend", "--role", "manager"],
        env=env,
    )
    runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Token refresh",
            "--acceptance",
            "- [ ] POST /auth/refresh works",
            "--created-from",
            "RQ-001",
            "--role",
            "manager",
        ],
        env=env,
    )
    task_id = "backend-001"

    # Claim and complete
    runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    runner.invoke(app, ["agent", "done", task_id, "--output", "src/auth.py"], env=env)

    # Review output should show acceptance before created_from
    review_result = runner.invoke(app, ["review"])
    assert review_result.exit_code == 0
    output = review_result.output
    if "acceptance:" in output and "created_from:" in output:
        assert output.index("acceptance:") < output.index("created_from:")


def test_cli_reject_then_review_shows_history(runner: CliRunner, isolated_project: Path):
    """After rejection, 'loom review' shows review_history instead of just rejection_note."""
    from loom.cli import app

    # Setup
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0

    env = {"LOOM_WORKER_ID": "x7k2"}
    runner.invoke(
        app,
        ["agent", "new-thread", "--name", "backend", "--role", "manager"],
        env=env,
    )
    runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Auth fix",
            "--acceptance",
            "- [ ] Tests pass",
            "--role",
            "manager",
        ],
        env=env,
    )
    task_id = "backend-001"

    # Round 1: claim, complete, reject
    runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    runner.invoke(app, ["agent", "done", task_id, "--output", "src/auth.py"], env=env)
    reject_result = runner.invoke(app, ["reject", task_id, "Missing edge cases"])
    assert reject_result.exit_code == 0

    # Round 2: re-claim, re-complete
    runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    runner.invoke(app, ["agent", "done", task_id, "--output", "src/auth.py v2"], env=env)

    # Review should show review_history
    review_result = runner.invoke(app, ["review"])
    assert review_result.exit_code == 0
    assert "review_history:" in review_result.output
    assert "Missing edge cases" in review_result.output


def test_cli_accept_records_history(runner: CliRunner, isolated_project: Path):
    """'loom accept' records an accept entry in review_history."""
    from loom.cli import app
    from loom.repository import load_task, require_loom

    # Setup
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0

    env = {"LOOM_WORKER_ID": "x7k2"}
    runner.invoke(
        app,
        ["agent", "new-thread", "--name", "backend", "--role", "manager"],
        env=env,
    )
    runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Auth fix",
            "--acceptance",
            "- [ ] Tests pass",
            "--role",
            "manager",
        ],
        env=env,
    )
    task_id = "backend-001"

    # Claim, complete, accept
    runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    runner.invoke(app, ["agent", "done", task_id, "--output", "src/auth.py"], env=env)
    accept_result = runner.invoke(app, ["accept", task_id])
    assert accept_result.exit_code == 0

    # Verify history was recorded
    loom = require_loom()
    _, task = load_task(loom, task_id)
    assert len(task.review_history) == 1
    assert task.review_history[0].kind == "accept"
