from __future__ import annotations

import pytest

from loom.models import (
    Decision,
    DeliveryContract,
    RequestItem,
    RequestResolution,
    RequestStatus,
    Routine,
    RoutineResult,
    RoutineStatus,
    Task,
    TaskKind,
    TaskStatus,
    find_review_blockers,
)


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


def test_task_allows_persistent_flag():
    task = Task.model_validate(
        {
            "id": "backend-001",
            "thread": "backend",
            "seq": 1,
            "title": "Monitor CI health",
            "status": TaskStatus.SCHEDULED,
            "acceptance": "- [ ] checks recorded",
            "persistent": True,
        }
    )

    assert task.persistent is True


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


def test_reviewing_task_allows_explicit_delivery_contract():
    task = Task(
        id="backend-001",
        thread="backend",
        seq=1,
        title="Demo",
        status=TaskStatus.REVIEWING,
        acceptance="- [ ] ready",
        output="TODO: follow-up note for humans",
        delivery=DeliveryContract(
            ready=True,
            artifacts=[".loom/products/demo/report.md"],
            pr_urls=["https://github.com/acme/loom/pull/42"],
        ),
    )

    assert task.delivery is not None
    assert task.delivery.ready is True
    assert find_review_blockers(task) == []


def test_request_item_upgrades_legacy_planned_inbox_fields():
    item = RequestItem.model_validate(
        {
            "id": "RQ-001",
            "created": "2026-03-18",
            "status": "planned",
            "planned_to": ["backend-001"],
            "body": "Legacy request",
        }
    )

    assert item.status == RequestStatus.DONE
    assert item.resolved_as == RequestResolution.TASK
    assert item.resolved_to == ["backend-001"]


def test_done_request_requires_resolution_details():
    with pytest.raises(ValueError, match="resolved_as"):
        RequestItem.model_validate(
            {
                "id": "RQ-001",
                "created": "2026-03-18",
                "status": "done",
                "body": "Missing resolution",
            }
        )


def test_routine_normalizes_interval_and_created_from():
    routine = Routine.model_validate(
        {
            "id": "scan-github-issues",
            "title": "Scan GitHub issues",
            "status": "active",
            "interval": "06H",
            "created_from": "RQ-007",
            "body": "## Responsibilities\n\n- inspect issues\n\n## Run Log\n\n<!-- append-only notes -->\n",
        }
    )

    assert routine.status == RoutineStatus.ACTIVE
    assert routine.interval == "6h"
    assert routine.created_from == ["RQ-007"]


def test_routine_last_result_uses_normalized_values():
    routine = Routine.model_validate(
        {
            "id": "scan-github-issues",
            "title": "Scan GitHub issues",
            "status": "active",
            "interval": "30m",
            "last_result": "task_proposed",
            "body": (
                "## Responsibilities\n\n- inspect issues\n\n## Run Log\n\n"
                "- 2026-03-20T08:00:00+00:00 [task_proposed] proposed backend-001\n"
            ),
        }
    )

    assert routine.last_result == RoutineResult.TASK_PROPOSED


def test_routine_requires_responsibilities_and_run_log_sections():
    with pytest.raises(ValueError, match="Responsibilities"):
        Routine.model_validate(
            {
                "id": "scan-github-issues",
                "title": "Scan GitHub issues",
                "status": "active",
                "interval": "6h",
                "body": "## Run Log\n\n<!-- append-only notes -->\n",
            }
        )

    with pytest.raises(ValueError, match="Run Log"):
        Routine.model_validate(
            {
                "id": "scan-github-issues",
                "title": "Scan GitHub issues",
                "status": "active",
                "interval": "6h",
                "body": "## Responsibilities\n\n- inspect issues\n",
            }
        )


# ---------------------------------------------------------------------------
# DeliveryContract — explicit delivery metadata
# ---------------------------------------------------------------------------


def test_delivery_contract_summary_field():
    contract = DeliveryContract(
        ready=True,
        summary="Implemented auth endpoint; all tests green",
        pr_urls=["https://github.com/acme/loom/pull/99"],
    )

    assert contract.summary == "Implemented auth endpoint; all tests green"
    assert contract.ready is True
    assert contract.pr_urls == ["https://github.com/acme/loom/pull/99"]


def test_find_review_blockers_bypassed_by_ready_delivery_contract():
    """review_ready contract should silence all TODO/proposal heuristics."""
    task = Task(
        id="backend-002",
        thread="backend",
        seq=2,
        title="Demo with TODOs",
        status=TaskStatus.CLAIMED,
        acceptance="- [ ] ready",
        output="TODO: finish later\nproposal-only output",
        delivery=DeliveryContract(ready=True),
    )

    assert find_review_blockers(task) == []


def test_find_review_blockers_still_fires_without_delivery_contract():
    task = Task(
        id="backend-003",
        thread="backend",
        seq=3,
        title="Demo with TODOs",
        status=TaskStatus.CLAIMED,
        acceptance="- [ ] ready",
        output="TODO: finish later",
    )

    assert "TODOs" in find_review_blockers(task)


def test_reviewing_task_with_delivery_contract_and_summary():
    task = Task(
        id="backend-004",
        thread="backend",
        seq=4,
        title="Completed task",
        status=TaskStatus.REVIEWING,
        acceptance="- [ ] ready",
        output="TODO: some follow-up note for the reviewer",
        delivery=DeliveryContract(
            ready=True,
            summary="Feature complete, follow-up tracked separately",
            pr_urls=["https://github.com/acme/loom/pull/55"],
        ),
    )

    assert task.delivery is not None
    assert task.delivery.summary == "Feature complete, follow-up tracked separately"
    assert task.delivery.pr_urls == ["https://github.com/acme/loom/pull/55"]
    assert find_review_blockers(task) == []
