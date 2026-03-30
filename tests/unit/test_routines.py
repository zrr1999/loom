from __future__ import annotations

from pathlib import Path

import pytest

from loom.models import RoutineResult, RoutineStatus


@pytest.fixture()
def loom(tmp_path: Path) -> Path:
    from loom.migration import ensure_request_storage, ensure_routine_storage
    from loom.services import ensure_agent_layout

    loom_dir = tmp_path / ".loom"
    loom_dir.mkdir()
    (loom_dir / "threads").mkdir()
    ensure_request_storage(loom_dir)
    ensure_routine_storage(loom_dir)
    ensure_agent_layout(loom_dir)
    return loom_dir


def test_is_routine_due_respects_lifecycle_and_last_run(loom: Path) -> None:
    from loom.scheduler import get_due_routines, is_routine_due
    from loom.services import create_routine

    routine, _path = create_routine(
        loom,
        routine_id="scan-github-issues",
        title="Scan GitHub issues",
        interval="6h",
        assigned_to="worker-123",
        responsibilities="- inspect issues",
    )

    assert is_routine_due(routine) is True
    assert [item.id for item in get_due_routines(loom)] == ["scan-github-issues"]


def test_record_routine_run_updates_last_result_and_appends_log(loom: Path) -> None:
    from loom.repository import load_routine
    from loom.services import create_routine, record_routine_run

    create_routine(
        loom,
        routine_id="scan-github-issues",
        title="Scan GitHub issues",
        interval="6h",
        assigned_to="worker-123",
        responsibilities="- inspect issues",
    )

    record_routine_run(
        loom,
        "scan-github-issues",
        result=RoutineResult.NO_CHANGE,
        note="Nothing new found.",
        ran_at="2026-03-20T08:00:00+00:00",
    )
    _path, updated = load_routine(loom, "scan-github-issues")

    assert updated.last_run == "2026-03-20T08:00:00+00:00"
    assert updated.last_result == RoutineResult.NO_CHANGE
    assert "- 2026-03-20T08:00:00+00:00 [no_change] Nothing new found." in updated.body
    assert "<!-- append-only notes -->" not in updated.body


def test_status_summary_reports_next_due_routine(loom: Path) -> None:
    from loom.frontmatter import write_model
    from loom.repository import load_routine
    from loom.scheduler import get_status_summary
    from loom.services import create_routine

    create_routine(
        loom,
        routine_id="scan-github-issues",
        title="Scan GitHub issues",
        interval="6h",
        assigned_to="worker-123",
        responsibilities="- inspect issues",
    )
    path, routine = load_routine(loom, "scan-github-issues")
    write_model(
        path,
        routine.model_copy(
            update={
                "status": RoutineStatus.ACTIVE,
                "last_run": "2099-03-20T08:00:00+00:00",
            }
        ),
    )

    summary = get_status_summary(loom)

    assert summary["routines"]["by_status"]["active"] == 1
    assert summary["routines"]["next_due"]["id"] == "scan-github-issues"
