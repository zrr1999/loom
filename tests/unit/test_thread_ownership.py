"""Tests for thread-level ownership model."""

from __future__ import annotations

from pathlib import Path

import pytest

from loom.models import TaskStatus, Thread

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def loom(tmp_path: Path) -> Path:
    """Bootstraps a minimal loom workspace for testing."""
    from loom.services import create_thread, ensure_agent_layout

    loom_dir = tmp_path / ".loom"
    loom_dir.mkdir()
    (loom_dir / "threads").mkdir()
    (loom_dir / "inbox").mkdir()
    ensure_agent_layout(loom_dir)
    create_thread(loom_dir, name="backend", priority=80)
    return loom_dir


# ---------------------------------------------------------------------------
# Thread ownership
# ---------------------------------------------------------------------------


def test_claim_thread_sets_owner(loom: Path) -> None:
    from loom.services import claim_thread

    _, thread = claim_thread(loom, "backend", agent_id="worker-1")
    assert thread.owner == "worker-1"
    assert thread.owned_at is not None
    assert thread.owner_heartbeat_at is not None
    assert thread.owner_lease_expires_at is not None


def test_claim_thread_idempotent_for_same_agent(loom: Path) -> None:
    from loom.services import claim_thread

    claim_thread(loom, "backend", agent_id="worker-1")
    _, thread = claim_thread(loom, "backend", agent_id="worker-1")
    assert thread.owner == "worker-1"
    assert thread.owner_lease_expires_at is not None


def test_claim_thread_rejects_different_agent(loom: Path) -> None:
    from loom.services import claim_thread

    claim_thread(loom, "backend", agent_id="worker-1")
    with pytest.raises(ValueError, match="already owned"):
        claim_thread(loom, "backend", agent_id="worker-2")


def test_release_thread_clears_owner(loom: Path) -> None:
    from loom.services import claim_thread, release_thread

    claim_thread(loom, "backend", agent_id="worker-1")
    _, thread = release_thread(loom, "backend", note="done working")
    assert thread.owner is None
    assert thread.owned_at is None
    assert thread.owner_heartbeat_at is None
    assert thread.owner_lease_expires_at is None


def test_release_unclaimed_thread_raises(loom: Path) -> None:
    from loom.services import release_thread

    with pytest.raises(ValueError, match="no active owner"):
        release_thread(loom, "backend", note="oops")


def test_claim_nonexistent_thread_raises(loom: Path) -> None:
    from loom.services import claim_thread

    with pytest.raises(FileNotFoundError, match="does not exist"):
        claim_thread(loom, "nonexistent", agent_id="worker-1")


# ---------------------------------------------------------------------------
# Task transitions without CLAIMED
# ---------------------------------------------------------------------------


def test_scheduled_to_reviewing_direct(loom: Path) -> None:
    """Tasks go SCHEDULED → REVIEWING directly (no CLAIMED step)."""
    from loom.services import create_task, transition_task

    task, _ = create_task(
        loom,
        thread_name="backend",
        title="Impl token refresh",
        acceptance="- [ ] POST /auth/refresh works",
    )
    assert task.status == TaskStatus.SCHEDULED

    _, updated = transition_task(loom, task.id, TaskStatus.REVIEWING)
    assert updated.status == TaskStatus.REVIEWING


def test_scheduled_to_paused_direct(loom: Path) -> None:
    """Tasks go SCHEDULED → PAUSED directly."""
    from loom.models import Decision, DecisionOption
    from loom.services import create_task, transition_task

    task, _ = create_task(
        loom,
        thread_name="backend",
        title="Choose framework",
        acceptance="- [ ] framework chosen",
    )
    decision = Decision(
        question="Which framework?",
        options=[DecisionOption(id="A", label="React"), DecisionOption(id="B", label="Vue")],
    )
    _, updated = transition_task(loom, task.id, TaskStatus.PAUSED, decision=decision)
    assert updated.status == TaskStatus.PAUSED


def test_scheduled_to_claimed_rejected(loom: Path) -> None:
    """SCHEDULED → CLAIMED is no longer a valid transition."""
    from loom.services import create_task, transition_task
    from loom.state import InvalidTransitionError

    task, _ = create_task(
        loom,
        thread_name="backend",
        title="blocked task",
        acceptance="- [ ] ready",
    )
    with pytest.raises(InvalidTransitionError):
        transition_task(loom, task.id, TaskStatus.CLAIMED)


# ---------------------------------------------------------------------------
# Scheduler filtering
# ---------------------------------------------------------------------------


def test_get_ready_tasks_excludes_other_owner(loom: Path) -> None:
    """Tasks in a thread owned by another agent are excluded."""
    from loom.scheduler import get_ready_tasks
    from loom.services import claim_thread, create_task

    create_task(
        loom,
        thread_name="backend",
        title="task1",
        acceptance="- [ ] ok",
    )
    claim_thread(loom, "backend", agent_id="worker-1")

    # worker-2 should not see tasks in worker-1's thread
    ready = get_ready_tasks(loom, for_agent="worker-2")
    assert len(ready) == 0

    # worker-1 should see its own tasks
    ready = get_ready_tasks(loom, for_agent="worker-1")
    assert len(ready) == 1


def test_get_ready_tasks_allows_stale_owner_reassignment(loom: Path) -> None:
    from loom.frontmatter import write_model
    from loom.scheduler import get_ready_tasks, load_all_threads
    from loom.services import claim_thread, create_task

    create_task(
        loom,
        thread_name="backend",
        title="task1",
        acceptance="- [ ] ok",
    )
    path, thread = claim_thread(loom, "backend", agent_id="worker-1")
    stale_thread = thread.model_copy(
        update={
            "owner_heartbeat_at": "2026-03-20T08:00:00+00:00",
            "owner_lease_expires_at": "2026-03-20T08:10:00+00:00",
        }
    )
    write_model(path, stale_thread)

    ready = get_ready_tasks(loom, for_agent="worker-2")
    assert [task.id for task in ready] == ["backend-001"]

    _, reclaimed = claim_thread(loom, "backend", agent_id="worker-2")
    assert reclaimed.owner == "worker-2"
    assert reclaimed.owner_lease_expires_at is not None
    assert load_all_threads(loom)["backend"].owner == "worker-2"


def test_get_ready_tasks_includes_unowned(loom: Path) -> None:
    """Tasks in unowned threads are available to any agent."""
    from loom.scheduler import get_ready_tasks
    from loom.services import create_task

    create_task(
        loom,
        thread_name="backend",
        title="task1",
        acceptance="- [ ] ok",
    )
    ready = get_ready_tasks(loom, for_agent="worker-99")
    assert len(ready) == 1


# ---------------------------------------------------------------------------
# Thread model fields
# ---------------------------------------------------------------------------


def test_thread_owner_fields_in_model() -> None:
    """Thread model has owner and owned_at fields."""
    t = Thread(name="test", priority=50)
    assert t.owner is None
    assert t.owned_at is None
    assert t.owner_heartbeat_at is None
    assert t.owner_lease_expires_at is None

    t2 = t.model_copy(
        update={
            "owner": "w1",
            "owned_at": "2026-01-01T00:00:00+00:00",
            "owner_heartbeat_at": "2026-01-01T00:05:00+00:00",
            "owner_lease_expires_at": "2026-01-01T00:35:00+00:00",
        }
    )
    assert t2.owner == "w1"
    assert t2.owned_at == "2026-01-01T00:00:00+00:00"
    assert t2.owner_heartbeat_at == "2026-01-01T00:05:00+00:00"
    assert t2.owner_lease_expires_at == "2026-01-01T00:35:00+00:00"


def test_update_checkpoint_refreshes_owned_thread_lease(loom: Path) -> None:
    from loom.frontmatter import read_model, write_model
    from loom.repository import load_agent
    from loom.services import claim_thread, touch_agent, update_checkpoint

    path, thread = claim_thread(loom, "backend", agent_id="worker-1")
    stale_thread = thread.model_copy(
        update={
            "owner_heartbeat_at": "2026-03-20T08:00:00+00:00",
            "owner_lease_expires_at": "2026-03-20T08:10:00+00:00",
        }
    )
    write_model(path, stale_thread)
    touch_agent(loom, "worker-1")

    update_checkpoint(loom, "worker-1", phase="implementing", summary="still working")

    _agent_path, agent = load_agent(loom, "worker-1")
    assert agent.checkpoint_summary == "still working"
    updated_thread = read_model(path, Thread)
    assert updated_thread.owner == "worker-1"
    assert updated_thread.owner_heartbeat_at != stale_thread.owner_heartbeat_at
    assert updated_thread.owner_lease_expires_at != stale_thread.owner_lease_expires_at


def test_status_summary_marks_stale_owned_thread(loom: Path) -> None:
    from loom.frontmatter import write_model
    from loom.scheduler import get_status_summary
    from loom.services import claim_thread

    path, thread = claim_thread(loom, "backend", agent_id="worker-1")
    write_model(
        path,
        thread.model_copy(
            update={
                "owner_heartbeat_at": "2026-03-20T08:00:00+00:00",
                "owner_lease_expires_at": "2026-03-20T08:10:00+00:00",
            }
        ),
    )

    summary = get_status_summary(loom)
    owned = summary["owned_threads"]["backend"]
    assert owned["owner"] == "worker-1"
    assert owned["stale"] is True
    assert summary["stale_owned_threads"] == ["backend"]


def test_migration_upgrades_claimed_task_to_thread_owner(loom: Path) -> None:
    from loom.frontmatter import read_raw
    from loom.migration import ensure_thread_ownership_metadata
    from loom.repository import load_task
    from loom.services import create_task

    task, path = create_task(
        loom,
        thread_name="backend",
        title="legacy claimed task",
        acceptance="- [ ] migrated",
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "status: scheduled\n",
            "status: claimed\nclaim:\n  agent: worker-7\n  claimed_at: '2026-03-20T08:00:00+00:00'\n",
            1,
        ),
        encoding="utf-8",
    )

    ensure_thread_ownership_metadata(loom)

    _, migrated = load_task(loom, task.id)
    assert migrated.status == TaskStatus.SCHEDULED
    assert migrated.claim is None

    metadata, _body = read_raw(loom / "threads" / "backend" / "_thread.md")
    assert metadata["owner"] == "worker-7"
    assert metadata["owned_at"] == "2026-03-20T08:00:00+00:00"


def test_migration_removes_legacy_claim_from_completed_task(loom: Path) -> None:
    from loom.frontmatter import read_raw
    from loom.migration import ensure_thread_ownership_metadata
    from loom.repository import load_task
    from loom.services import create_task, transition_task

    task, _path = create_task(
        loom,
        thread_name="backend",
        title="legacy completed task",
        acceptance="- [ ] migrated",
    )
    transition_task(loom, task.id, TaskStatus.REVIEWING)
    transition_task(loom, task.id, TaskStatus.DONE)
    path, completed = load_task(loom, task.id)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "review_history: []\n",
            "claim:\n  agent: worker-2\n  claimed_at: '2026-03-20T07:00:00+00:00'\nreview_history: []\n",
            1,
        ),
        encoding="utf-8",
    )

    ensure_thread_ownership_metadata(loom)

    _, migrated = load_task(loom, completed.id)
    metadata, _body = read_raw(path)
    assert migrated.claim is None
    assert "claim" not in metadata
    thread_metadata, _thread_body = read_raw(loom / "threads" / "backend" / "_thread.md")
    assert "owner" not in thread_metadata
