"""State machine validation for tasks and inbox items."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    INBOX_TRANSITIONS,
    TASK_TRANSITIONS,
    InboxStatus,
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


def validate_inbox_transition(current: InboxStatus, target: InboxStatus) -> None:
    """Raise if the transition is not allowed."""
    allowed = INBOX_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTransitionError("inbox", current.value, target.value)


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
