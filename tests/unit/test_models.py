from __future__ import annotations

import pytest

from loom.models import Decision, Task, TaskStatus


def test_task_coerces_created_from_and_depends_on_strings():
    task = Task.model_validate(
        {
            "id": "AA-001-demo",
            "thread": "AA",
            "seq": 1,
            "title": "Demo",
            "status": TaskStatus.SCHEDULED,
            "acceptance": "- [ ] ready",
            "created_from": "RQ-001",
            "depends_on": "AA-000-prev",
        }
    )

    assert task.created_from == ["RQ-001"]
    assert task.depends_on == ["AA-000-prev"]


def test_scheduled_task_requires_acceptance():
    with pytest.raises(ValueError, match="acceptance"):
        Task(
            id="AA-001-demo",
            thread="AA",
            seq=1,
            title="Demo",
            status=TaskStatus.SCHEDULED,
        )


def test_paused_task_requires_decision():
    with pytest.raises(ValueError, match="decision"):
        Task(
            id="AA-001-demo",
            thread="AA",
            seq=1,
            title="Demo",
            status=TaskStatus.PAUSED,
            acceptance="- [ ] ready",
        )

    task = Task(
        id="AA-001-demo",
        thread="AA",
        seq=1,
        title="Demo",
        status=TaskStatus.PAUSED,
        acceptance="- [ ] ready",
        decision=Decision(question="Pick one"),
    )
    assert task.decision is not None
