"""State machine validation for tasks and inbox items."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    REQUEST_TRANSITIONS,
    ROUTINE_TRANSITIONS,
    TASK_TRANSITIONS,
    RequestStatus,
    RoutineStatus,
    TaskStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class InvalidTransitionError(Exception):
    def __init__(self, kind: str, current: str, target: str) -> None:
        super().__init__(f"Invalid {kind} transition: {current} → {target}")
        self.current = current
        self.target = target


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Raise if the transition is not allowed."""
    allowed = TASK_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransitionError("task", current.value, target.value)


def validate_request_transition(current: RequestStatus, target: RequestStatus) -> None:
    """Raise if the transition is not allowed."""
    allowed = REQUEST_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransitionError("request", current.value, target.value)


def validate_inbox_transition(current: RequestStatus, target: RequestStatus) -> None:
    """Backward-compatible alias for request transition validation."""
    validate_request_transition(current, target)


def validate_routine_transition(current: RoutineStatus, target: RoutineStatus) -> None:
    """Raise if the routine transition is not allowed."""
    allowed = ROUTINE_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransitionError("routine", current.value, target.value)


def validate_task_scheduled(acceptance: str | None) -> None:
    """A task entering scheduled must have an acceptance field."""
    if not acceptance or not acceptance.strip():
        msg = "Task must have 'acceptance' field to enter scheduled status"
        raise ValueError(msg)


def validate_decision_payload(question: str, options: Sequence[object]) -> None:
    """A paused task must ask a concrete question."""
    if not question.strip():
        msg = "Paused task must include a non-empty question"
        raise ValueError(msg)

    if any(not option for option in options):
        msg = "Decision options must not contain empty entries"
        raise ValueError(msg)
