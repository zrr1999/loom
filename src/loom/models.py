"""Data models for loom — all backed by Pydantic for frontmatter validation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TaskStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    CLAIMED = "claimed"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    DONE = "done"


class TaskKind(StrEnum):
    IMPLEMENTATION = "implementation"
    DESIGN = "design"


class InboxStatus(StrEnum):
    PENDING = "pending"
    PLANNED = "planned"
    MERGED = "merged"


class AgentRole(StrEnum):
    DIRECTOR = "director"
    MANAGER = "manager"
    REVIEWER = "reviewer"
    WORKER = "worker"


class AgentStatus(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"


class MessageType(StrEnum):
    TASK_ASSIGNMENT = "task_assignment"
    QUESTION = "question"
    ANSWER = "answer"
    INFO = "info"
    DECISION_RESULT = "decision_result"
    REVIEW_REQUEST = "review_request"
    TASK_PROPOSAL = "task_proposal"


# ---------------------------------------------------------------------------
# State machine — allowed transitions
# ---------------------------------------------------------------------------

TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.SCHEDULED},
    TaskStatus.SCHEDULED: {TaskStatus.REVIEWING, TaskStatus.PAUSED},
    # CLAIMED is kept for backward-compat reads of pre-migration task files.
    TaskStatus.CLAIMED: {TaskStatus.REVIEWING, TaskStatus.PAUSED, TaskStatus.SCHEDULED},
    TaskStatus.REVIEWING: {TaskStatus.DONE, TaskStatus.SCHEDULED},
    TaskStatus.PAUSED: {TaskStatus.SCHEDULED},
    TaskStatus.DONE: {TaskStatus.SCHEDULED},
}

REVIEW_INCOMPLETE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TODOs", re.compile(r"(?im)(?:^|\b)TODO\b|^- \[ \]")),
    ("proposal-only output", re.compile(r"(?im)\bproposal(?:-only)?\b|\btask proposal\b")),
    (
        "known follow-up improvements",
        re.compile(
            r"(?im)\b(?:known|needs|remaining|future|later)\s+"
            r"(?:follow[- ]up|improvement|cleanup|work|pass)\b"
        ),
    ),
)

INBOX_TRANSITIONS: dict[InboxStatus, set[InboxStatus]] = {
    InboxStatus.PENDING: {InboxStatus.PLANNED, InboxStatus.MERGED},
    InboxStatus.PLANNED: {InboxStatus.MERGED},
    InboxStatus.MERGED: set(),
}


# ---------------------------------------------------------------------------
# Decision (embedded in task when paused)
# ---------------------------------------------------------------------------


class DecisionOption(BaseModel):
    id: str
    label: str
    note: str = ""


class Decision(BaseModel):
    question: str
    options: list[DecisionOption] = Field(default_factory=list)
    decided: str | None = None


class ReviewEntry(BaseModel):
    """A single append-only record of a review event (accept or reject)."""

    kind: str  # "accept" or "reject"
    actor: str = "human"
    created: str = ""
    note: str = ""
    source: str = "cli"


class Claim(BaseModel):
    """Legacy task-level claim — kept for backward-compat reads only."""

    agent: str | None = None
    claimed_at: str | None = None


# ---------------------------------------------------------------------------
# Thread
# ---------------------------------------------------------------------------


class Thread(BaseModel):
    name: str
    priority: int = 50
    created: date = Field(default_factory=date.today)
    owner: str | None = None
    owned_at: str | None = None
    body: str = ""


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class Task(BaseModel):
    id: str
    thread: str
    seq: int
    title: str
    kind: TaskKind = TaskKind.IMPLEMENTATION
    status: TaskStatus = TaskStatus.DRAFT
    priority: int = 50
    depends_on: list[str] = Field(default_factory=list)
    created_from: list[str] = Field(default_factory=list)
    created: date = Field(default_factory=date.today)
    output: str | None = None
    claim: Claim | dict[str, Any] | None = None  # deprecated: kept for backward-compat reads
    decision: Decision | dict[str, Any] | None = None
    rejection_note: str | None = None
    review_history: list[ReviewEntry] = Field(default_factory=list)
    acceptance: str | None = None
    body: str = ""

    @field_validator("depends_on", mode="before")
    @classmethod
    def _coerce_depends_on(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, Iterable):
            return [str(item) for item in value]
        msg = "depends_on must be a string or iterable of strings"
        raise TypeError(msg)

    @field_validator("created_from", mode="before")
    @classmethod
    def _coerce_created_from(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, Iterable):
            return [str(item) for item in value]
        msg = "created_from must be a string or iterable of strings"
        raise TypeError(msg)

    @model_validator(mode="after")
    def _validate_status_requirements(self) -> Task:
        if self.status == TaskStatus.SCHEDULED and not (self.acceptance and self.acceptance.strip()):
            msg = "Task must have 'acceptance' field to enter scheduled status"
            raise ValueError(msg)

        if self.status == TaskStatus.PAUSED and self.decision is None:
            msg = "Paused task must include a decision block"
            raise ValueError(msg)

        if self.status == TaskStatus.REVIEWING:
            blockers = find_review_blockers(self)
            if blockers:
                msg = f"Reviewing task must not include incomplete work markers: {', '.join(blockers)}"
                raise ValueError(msg)

        return self


def find_review_blockers(task: Task, *, output: str | None = None) -> list[str]:
    combined_text = output if output is not None else (task.output or "")

    blockers: list[str] = []
    for label, pattern in REVIEW_INCOMPLETE_PATTERNS:
        if pattern.search(combined_text):
            blockers.append(label)
    return blockers


# ---------------------------------------------------------------------------
# Inbox item
# ---------------------------------------------------------------------------


class InboxItem(BaseModel):
    id: str
    created: date = Field(default_factory=date.today)
    status: InboxStatus = InboxStatus.PENDING
    planned_to: list[str] = Field(default_factory=list)
    body: str = ""


class AgentRecord(BaseModel):
    id: str
    role: AgentRole = AgentRole.WORKER
    registered: str | None = None
    last_seen: str | None = None
    status: AgentStatus = AgentStatus.IDLE
    threads: list[str] = Field(default_factory=list)
    checkpoint_summary: str = ""
    body: str = "## Checkpoint\n\n未记录。\n\n## Notes\n\n"

    @field_validator("role", mode="before")
    @classmethod
    def _upgrade_executor_role(cls, value: Any) -> Any:
        if value == "executor":
            return AgentRole.WORKER
        return value


class ManagerRecord(BaseModel):
    role: AgentRole = AgentRole.MANAGER
    last_seen: str | None = None
    status: str = "active"
    checkpoint_summary: str = ""
    body: str = "## Checkpoint\n\n未记录。\n\n## Notes\n\n"


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_: str = Field(alias="from")
    to: str
    type: MessageType
    ref: str | None = None
    sent: str | None = None
    reply_ref: str | None = None
    body: str = ""
