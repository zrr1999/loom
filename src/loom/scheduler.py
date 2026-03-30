"""Scheduling engine — ready condition checking and priority sorting."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .duration import format_compact_duration, parse_interval
from .frontmatter import read_model
from .lease import is_thread_stale, parse_timestamp
from .models import AgentRecord, RequestItem, RequestStatus, Routine, RoutineStatus, Task, TaskKind, TaskStatus, Thread
from .repository import (
    agent_pending_dir,
    agent_replied_dir,
    agent_worktrees_dir,
    agents_dir,
    load_worktree,
    requests_dir,
    root_config_path,
    routines_dir,
    task_file_path,
    worker_agents_dir,
)

if TYPE_CHECKING:
    pass


def load_all_threads(loom_dir: Path) -> dict[str, Thread]:
    """Load all thread definitions from .loom/threads/."""
    threads: dict[str, Thread] = {}
    threads_dir = loom_dir / "threads"
    if not threads_dir.exists():
        return threads
    for d in sorted(threads_dir.iterdir()):
        meta_file = d / "_thread.md"
        if d.is_dir() and meta_file.exists():
            threads[d.name] = read_model(meta_file, Thread)
    return threads


def load_all_tasks(loom_dir: Path) -> list[Task]:
    """Load all task files from .loom/threads/*/."""
    tasks: list[Task] = []
    threads_dir = loom_dir / "threads"
    if not threads_dir.exists():
        return tasks
    for d in sorted(threads_dir.iterdir()):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name == "_thread.md":
                continue
            tasks.append(read_model(f, Task))
    return tasks


def is_ready(task: Task, all_tasks: list[Task], threads: dict[str, Thread]) -> bool:
    """Check if a task satisfies the ready conditions.

    1. status == scheduled
    2. All depends_on tasks are done
    """
    if task.status != TaskStatus.SCHEDULED:
        return False

    if task.thread not in threads:
        return False

    # Check dependencies
    done_ids = {t.id for t in all_tasks if t.status == TaskStatus.DONE}
    return all(dep_id in done_ids for dep_id in task.depends_on)


def get_ready_tasks(loom_dir: Path, thread_filter: str | None = None, *, for_agent: str | None = None) -> list[Task]:
    """Return all ready tasks in dispatch order.

    When *for_agent* is given, tasks in threads owned by a **different** agent
    are excluded so workers never compete over the same thread.
    """
    threads = load_all_threads(loom_dir)
    all_tasks = load_all_tasks(loom_dir)
    ready = [task for task in all_tasks if is_ready(task, all_tasks, threads)]
    if thread_filter:
        ready = [task for task in ready if task.thread == thread_filter]

    if for_agent:
        ready = [
            task
            for task in ready
            if not threads[task.thread].owner
            or threads[task.thread].owner == for_agent
            or is_thread_stale(threads[task.thread])
        ]

    ready.sort(key=lambda task: sort_key(task, threads, preferred_agent=for_agent))
    return ready


def get_next_tasks(
    loom_dir: Path, *, limit: int, thread_filter: str | None = None, for_agent: str | None = None
) -> list[Task]:
    """Return up to `limit` ready tasks in dispatch order."""
    if limit <= 0:
        return []
    return get_ready_tasks(loom_dir, thread_filter=thread_filter, for_agent=for_agent)[:limit]


def get_next_task(loom_dir: Path, thread_filter: str | None = None) -> Task | None:
    """Return the highest-priority ready task, or None."""
    ready = get_ready_tasks(loom_dir, thread_filter=thread_filter)
    if not ready:
        return None
    return ready[0]


def sort_key(
    task: Task, threads: dict[str, Thread], *, preferred_agent: str | None = None
) -> tuple[int, int, int, int, str]:
    thread_prio = threads[task.thread].priority if task.thread in threads else 0
    continuity_boost = (
        1 if preferred_agent and threads.get(task.thread) and threads[task.thread].owner == preferred_agent else 0
    )
    return (-continuity_boost, -thread_prio, -task.priority, task.seq, task.id)


def load_all_inbox_items(loom_dir: Path) -> list[RequestItem]:
    """Load all request items from `.loom/requests/` or the inbox compatibility path."""
    items: list[RequestItem] = []
    request_dir = requests_dir(loom_dir)
    if not request_dir.exists():
        return items
    for path in sorted(request_dir.glob("RQ-*.md")):
        items.append(read_model(path, RequestItem))
    return items


def load_all_routines(loom_dir: Path) -> list[Routine]:
    """Load all routine files from `.loom/routines/`."""
    routines_root = routines_dir(loom_dir)
    if not routines_root.exists():
        return []
    return [read_model(path, Routine) for path in sorted(routines_root.glob("*.md"))]


def routine_due_at(routine: Routine) -> datetime | None:
    """Return the next due timestamp for an active routine."""
    if routine.status != RoutineStatus.ACTIVE:
        return None
    last_run = parse_timestamp(routine.last_run)
    if last_run is None:
        return datetime.min.replace(tzinfo=UTC)
    return last_run + parse_interval(routine.interval)


def is_routine_due(routine: Routine, *, now: datetime | None = None) -> bool:
    """Check whether a routine is due to be triggered."""
    due_at = routine_due_at(routine)
    if due_at is None:
        return False
    observed_at = now or datetime.now(UTC)
    return due_at <= observed_at


def get_due_routines(loom_dir: Path, *, limit: int | None = None) -> list[Routine]:
    """Return due routines in trigger order."""
    observed_at = datetime.now(UTC)
    due_routines = [routine for routine in load_all_routines(loom_dir) if is_routine_due(routine, now=observed_at)]
    due_routines.sort(
        key=lambda routine: (
            routine_due_at(routine) or datetime.max.replace(tzinfo=UTC),
            0 if routine.assigned_to else 1,
            routine.id,
        )
    )
    if limit is not None and limit > 0:
        return due_routines[:limit]
    return due_routines


def next_routine_due(routines: list[Routine], *, now: datetime | None = None) -> dict[str, str] | None:
    """Describe the next active routine due time for status surfaces."""
    observed_at = now or datetime.now(UTC)
    next_due_at: datetime | None = None
    next_routine: Routine | None = None
    for routine in routines:
        due_at = routine_due_at(routine)
        if due_at is None:
            continue
        if (
            next_due_at is None
            or due_at < next_due_at
            or (due_at == next_due_at and next_routine is not None and routine.id < next_routine.id)
        ):
            next_due_at = due_at
            next_routine = routine
    if next_due_at is None or next_routine is None:
        return None

    if next_due_at <= observed_at:
        return {"id": next_routine.id, "when": "now"}
    return {"id": next_routine.id, "when": format_compact_duration(next_due_at - observed_at)}


def get_pending_inbox_items(loom_dir: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Return pending inbox items in planning order without mutating files."""
    request_dir = requests_dir(loom_dir)
    pending_items = [item for item in load_all_inbox_items(loom_dir) if item.status == RequestStatus.PENDING]
    if limit is not None and limit > 0:
        pending_items = pending_items[:limit]

    return [
        {
            "id": item.id,
            "status": item.status.value,
            "title": item.body.splitlines()[0] if item.body else item.id,
            "body": item.body,
            "file": str(request_dir / f"{item.id}.md"),
        }
        for item in pending_items
    ]


def get_interaction_queue(loom_dir: Path) -> list[dict[str, Any]]:
    """Return approval items in prompt order."""
    queue: list[dict[str, Any]] = []
    threads = load_all_threads(loom_dir)
    tasks = load_all_tasks(loom_dir)
    ordered_tasks = sorted(tasks, key=lambda task: sort_key(task, threads))
    for task in ordered_tasks:
        if task.status == TaskStatus.PAUSED:
            queue.append(
                {"kind": "paused", "id": task.id, "title": task.title, "file": str(task_file_path(loom_dir, task))}
            )
    for task in ordered_tasks:
        if task.status == TaskStatus.REVIEWING:
            queue.append(
                {"kind": "reviewing", "id": task.id, "title": task.title, "file": str(task_file_path(loom_dir, task))}
            )
    return queue


def _capability_phase(thread_tasks: list[Task]) -> str:
    implementation_tasks = [task for task in thread_tasks if task.kind == TaskKind.IMPLEMENTATION]
    if any(task.status == TaskStatus.DONE for task in implementation_tasks):
        return "implementation-complete"
    if any(task.kind == TaskKind.DESIGN and task.status == TaskStatus.DONE for task in thread_tasks):
        return "design-only"
    if any(
        task.status in {TaskStatus.CLAIMED, TaskStatus.REVIEWING, TaskStatus.PAUSED} for task in implementation_tasks
    ):
        return "implementation-in-progress"
    if implementation_tasks:
        return "implementation-planned"
    return "planned"


def summarize_capabilities(threads: dict[str, Thread], tasks: list[Task]) -> list[dict[str, Any]]:
    tasks_by_thread: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        tasks_by_thread[task.thread].append(task)

    summaries: list[dict[str, Any]] = []
    for thread_name, _thread in sorted(threads.items(), key=lambda item: (-item[1].priority, item[0])):
        thread_tasks = sorted(tasks_by_thread.get(thread_name, []), key=lambda task: (task.seq, task.id))
        if not thread_tasks:
            continue

        completed = [task for task in thread_tasks if task.status == TaskStatus.DONE]
        latest_completed = completed[-1] if completed else None
        implementation_follow_up = next(
            (task for task in thread_tasks if task.kind == TaskKind.IMPLEMENTATION and task.status != TaskStatus.DONE),
            None,
        )
        summaries.append(
            {
                "thread": thread_name,
                "phase": _capability_phase(thread_tasks),
                "latest_completed": (
                    {
                        "id": latest_completed.id,
                        "kind": latest_completed.kind.value,
                        "status": latest_completed.status.value,
                    }
                    if latest_completed is not None
                    else None
                ),
                "implementation_follow_up": (
                    {
                        "id": implementation_follow_up.id,
                        "status": implementation_follow_up.status.value,
                    }
                    if implementation_follow_up is not None
                    else None
                ),
                "latest_pr": (
                    {
                        "url": _thread.pr_artifacts[-1].url,
                        "branch": _thread.pr_artifacts[-1].branch,
                    }
                    if _thread.pr_artifacts
                    else None
                ),
            }
        )
    return summaries


def _path_is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_thread_worktree_references(loom_dir: Path, threads: dict[str, Thread]) -> dict[str, list[str]]:
    """Describe stale or invalid active thread worktree references."""

    issues: dict[str, list[str]] = {}
    for thread_name, thread in threads.items():
        thread_issues: list[str] = []
        for item in thread.worktrees:
            if item.removed_at is not None:
                continue

            worktree_path = Path(item.path).resolve()
            worker_root = agent_worktrees_dir(loom_dir, item.worker).resolve()
            if not _path_is_within_root(worktree_path, worker_root):
                thread_issues.append(
                    f"worktree '{item.name}' is cross-worker-invalid: path '{worktree_path}' is outside "
                    f"worker '{item.worker}' root '{worker_root}'"
                )
                continue

            if not worktree_path.exists():
                thread_issues.append(
                    f"worktree '{item.name}' is missing its checkout path '{worktree_path}' for worker '{item.worker}'"
                )

            try:
                _record_path, record = load_worktree(loom_dir, item.worker, item.name)
            except FileNotFoundError:
                thread_issues.append(
                    f"worktree '{item.name}' is stale: thread '{thread_name}' points to a missing "
                    f"worker-local record for worker '{item.worker}'"
                )
                continue

            record_path = Path(record.path).resolve()
            if record.worker != item.worker:
                thread_issues.append(
                    f"worktree '{item.name}' is cross-worker-invalid: thread metadata says worker "
                    f"'{item.worker}' but local record says '{record.worker}'"
                )
            if record.thread != thread_name:
                if record.thread:
                    thread_issues.append(
                        f"worktree '{item.name}' is stale: thread '{thread_name}' references it, but the "
                        f"worker-local record is attached to thread '{record.thread}'"
                    )
                else:
                    thread_issues.append(
                        f"worktree '{item.name}' is stale: thread '{thread_name}' references it, but the "
                        "worker-local record is no longer attached to any thread"
                    )
            if record_path != worktree_path:
                thread_issues.append(
                    f"worktree '{item.name}' is stale: thread metadata path '{worktree_path}' does not match "
                    f"worker-local record path '{record_path}'"
                )

        if thread_issues:
            issues[thread_name] = thread_issues

    return issues


def get_status_summary(loom_dir: Path) -> dict[str, Any]:
    """Build a machine-readable status summary."""
    threads = load_all_threads(loom_dir)
    all_tasks = load_all_tasks(loom_dir)
    ready_tasks = [task for task in all_tasks if is_ready(task, all_tasks, threads)]
    inbox_items = load_all_inbox_items(loom_dir)
    routines = load_all_routines(loom_dir)
    due_routines = get_due_routines(loom_dir)
    next_due = next_routine_due(routines)
    agents: list[dict[str, Any]] = []

    agent_root = worker_agents_dir(loom_dir)
    legacy_root = agents_dir(loom_dir)
    if agent_root.exists():
        for entry in sorted(agent_root.iterdir()):
            if not entry.is_dir():
                continue
            record_path = entry / "_agent.md"
            if not record_path.exists():
                continue
            record = read_model(record_path, AgentRecord)
            pending_dir = agent_pending_dir(loom_dir, record.id)
            replied_dir = agent_replied_dir(loom_dir, record.id)
            agents.append(
                {
                    "id": record.id,
                    "status": record.status.value,
                    "last_seen": record.last_seen,
                    "threads": record.threads,
                    "checkpoint_summary": record.checkpoint_summary,
                    "pending_messages": len(list(pending_dir.glob("*.md"))) if pending_dir.exists() else 0,
                    "replied_messages": len(list(replied_dir.glob("*.md"))) if replied_dir.exists() else 0,
                }
            )
    if not agents and legacy_root.exists():
        for entry in sorted(legacy_root.iterdir()):
            if not entry.is_dir() or entry.name in {"workers", "manager"}:
                continue
            record_path = entry / "_agent.md"
            if not record_path.exists():
                continue
            record = read_model(record_path, AgentRecord)
            pending_dir = agent_pending_dir(loom_dir, record.id)
            replied_dir = agent_replied_dir(loom_dir, record.id)
            agents.append(
                {
                    "id": record.id,
                    "status": record.status.value,
                    "last_seen": record.last_seen,
                    "threads": record.threads,
                    "checkpoint_summary": record.checkpoint_summary,
                    "pending_messages": len(list(pending_dir.glob("*.md"))) if pending_dir.exists() else 0,
                    "replied_messages": len(list(replied_dir.glob("*.md"))) if replied_dir.exists() else 0,
                }
            )

    by_status: dict[str, int] = {}
    for task in all_tasks:
        by_status[task.status.value] = by_status.get(task.status.value, 0) + 1

    inbox_by_status: dict[str, int] = {}
    for item in inbox_items:
        inbox_by_status[item.status.value] = inbox_by_status.get(item.status.value, 0) + 1

    routines_by_status: dict[str, int] = {}
    for routine in routines:
        routines_by_status[routine.status.value] = routines_by_status.get(routine.status.value, 0) + 1

    owned_threads = {
        name: {
            "owner": thread.owner,
            "owned_at": thread.owned_at,
            "heartbeat_at": thread.owner_heartbeat_at,
            "lease_expires_at": thread.owner_lease_expires_at,
            "stale": is_thread_stale(thread),
        }
        for name, thread in threads.items()
        if thread.owner
    }
    worktree_issues = validate_thread_worktree_references(loom_dir, threads)

    return {
        "config_present": root_config_path(loom_dir).exists(),
        "threads": len(threads),
        "owned_threads": owned_threads,
        "worktree_issues": worktree_issues,
        "tasks": {
            "total": len(all_tasks),
            "by_status": by_status,
            "ready": len(ready_tasks),
            "ready_ids": [task.id for task in sorted(ready_tasks, key=lambda task: sort_key(task, threads))],
        },
        "inbox": {
            "total": len(inbox_items),
            "pending": inbox_by_status.get(RequestStatus.PENDING.value, 0),
            "by_status": inbox_by_status,
        },
        "routines": {
            "total": len(routines),
            "by_status": routines_by_status,
            "due": len(due_routines),
            "due_ids": [routine.id for routine in due_routines],
            "next_due": next_due,
        },
        "inbox_pending": inbox_by_status.get(RequestStatus.PENDING.value, 0),
        "agents": agents,
        "stale_owned_threads": [name for name, details in owned_threads.items() if details.get("stale")],
        "queue": get_interaction_queue(loom_dir),
        "capabilities": summarize_capabilities(threads, all_tasks),
    }
