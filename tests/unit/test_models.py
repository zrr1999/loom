from __future__ import annotations

import pytest

from loom.models import Decision, Task, TaskKind, TaskStatus, find_review_blockers


def test_task_coerces_created_from_and_depends_on_strings():
    task = Task.model_validate(
        {
            "id": "backend-001",
            "thread": "backend",
            "seq": 1,
            "title": "Demo",
            "status": TaskStatus.SCHEDULED,
            "acceptance": "- [ ] ready",
            "created_from": "RQ-001",
            "depends_on": "backend-000",
        }
    )

    assert task.created_from == ["RQ-001"]
    assert task.depends_on == ["backend-000"]
    assert task.kind == TaskKind.IMPLEMENTATION


def test_task_allows_explicit_design_kind():
    task = Task.model_validate(
        {
            "id": "backend-001",
            "thread": "backend",
            "seq": 1,
            "title": "Design auth flow",
            "kind": "design",
            "status": TaskStatus.SCHEDULED,
            "acceptance": "- [ ] ready",
        }
    )

    assert task.kind == TaskKind.DESIGN


def test_scheduled_task_requires_acceptance():
    with pytest.raises(ValueError, match="acceptance"):
        Task(
            id="backend-001",
            thread="backend",
            seq=1,
            title="Demo",
            status=TaskStatus.SCHEDULED,
        )


def test_paused_task_requires_decision():
    with pytest.raises(ValueError, match="decision"):
        Task(
            id="backend-001",
            thread="backend",
            seq=1,
            title="Demo",
            status=TaskStatus.PAUSED,
            acceptance="- [ ] ready",
        )

    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Demo",
        status=TaskStatus.PAUSED,
        acceptance="- [ ] ready",
        decision=Decision(question="Pick one"),
    )
    assert task.decision is not None


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("TODO: finish tests", ["TODOs"]),
        ("proposal-only summary", ["proposal-only output"]),
        ("Known follow-up cleanup remains.", ["known follow-up improvements"]),
    ],
)
def test_find_review_blockers_detects_incomplete_markers(output, expected):
    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Demo",
        status=TaskStatus.CLAIMED,
        acceptance="- [ ] ready",
        output=output,
    )

    assert find_review_blockers(task) == expected


def test_reviewing_task_rejects_incomplete_markers():
    with pytest.raises(ValueError, match="incomplete work markers"):
        Task(
            id="backend-001",
            thread="backend",
            seq=1,
            title="Demo",
            status=TaskStatus.REVIEWING,
            acceptance="- [ ] ready",
            output="TODO: finish tests",
        )
