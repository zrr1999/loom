"""Scheduling engine — ready condition checking and priority sorting."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from .frontmatter import read_model
from .models import AgentRecord, InboxItem, InboxStatus, Task, TaskKind, TaskStatus, Thread
from .repository import (
    agent_pending_dir,
    agent_replied_dir,
    agents_dir,
    root_config_path,
    task_file_path,
    worker_agents_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


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


def get_ready_tasks(
    loom_dir: Path, thread_filter: str | None = None, *, for_agent: str | None = None
) -> list[Task]:
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
            if not threads[task.thread].owner or threads[task.thread].owner == for_agent
        ]

    ready.sort(key=lambda task: sort_key(task, threads))
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


def sort_key(task: Task, threads: dict[str, Thread]) -> tuple[int, int, int, str]:
    thread_prio = threads[task.thread].priority if task.thread in threads else 0
    return (-thread_prio, -task.priority, task.seq, task.id)


def load_all_inbox_items(loom_dir: Path) -> list[InboxItem]:
    """Load all inbox items from .loom/inbox/."""
    items: list[InboxItem] = []
    inbox_dir = loom_dir / "inbox"
    if not inbox_dir.exists():
        return items
    for path in sorted(inbox_dir.glob("RQ-*.md")):
        items.append(read_model(path, InboxItem))
    return items


def get_pending_inbox_items(loom_dir: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Return pending inbox items in planning order without mutating files."""
    pending_items = [item for item in load_all_inbox_items(loom_dir) if item.status == InboxStatus.PENDING]
    if limit is not None and limit > 0:
        pending_items = pending_items[:limit]

    return [
        {
            "id": item.id,
            "status": item.status.value,
            "title": item.body.splitlines()[0] if item.body else item.id,
            "body": item.body,
            "file": str(loom_dir / "inbox" / f"{item.id}.md"),
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
    for thread_name, thread in sorted(threads.items(), key=lambda item: (-item[1].priority, item[0])):
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
            }
        )
    return summaries


def get_status_summary(loom_dir: Path) -> dict[str, Any]:
    """Build a machine-readable status summary."""
    threads = load_all_threads(loom_dir)
    all_tasks = load_all_tasks(loom_dir)
    ready_tasks = [task for task in all_tasks if is_ready(task, all_tasks, threads)]
    inbox_items = load_all_inbox_items(loom_dir)
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
            if not entry.is_dir() or entry.name == "workers":
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

    owned_threads = {
        name: {"owner": thread.owner, "owned_at": thread.owned_at}
        for name, thread in threads.items()
        if thread.owner
    }

    return {
        "config_present": root_config_path(loom_dir).exists(),
        "threads": len(threads),
        "owned_threads": owned_threads,
        "tasks": {
            "total": len(all_tasks),
            "by_status": by_status,
            "ready": len(ready_tasks),
            "ready_ids": [task.id for task in sorted(ready_tasks, key=lambda task: sort_key(task, threads))],
        },
        "inbox": {
            "total": len(inbox_items),
            "pending": inbox_by_status.get(InboxStatus.PENDING.value, 0),
            "by_status": inbox_by_status,
        },
        "inbox_pending": inbox_by_status.get(InboxStatus.PENDING.value, 0),
        "agents": agents,
        "queue": get_interaction_queue(loom_dir),
        "capabilities": summarize_capabilities(threads, all_tasks),
    }
