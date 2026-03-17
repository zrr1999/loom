"""Scheduling engine — ready condition checking and priority sorting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .frontmatter import read_model
from .models import AgentRecord, InboxItem, InboxStatus, Task, TaskStatus, Thread
from .repository import agents_dir, root_config_path, task_file_path

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


def get_ready_tasks(loom_dir: Path, thread_filter: str | None = None) -> list[Task]:
    """Return all ready tasks in dispatch order."""
    threads = load_all_threads(loom_dir)
    all_tasks = load_all_tasks(loom_dir)
    ready = [task for task in all_tasks if is_ready(task, all_tasks, threads)]
    if thread_filter:
        ready = [task for task in ready if task.thread == thread_filter]

    ready.sort(key=lambda task: sort_key(task, threads))
    return ready


def get_next_tasks(loom_dir: Path, *, limit: int, thread_filter: str | None = None) -> list[Task]:
    """Return up to `limit` ready tasks in dispatch order."""
    if limit <= 0:
        return []
    return get_ready_tasks(loom_dir, thread_filter=thread_filter)[:limit]


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


def get_status_summary(loom_dir: Path) -> dict[str, Any]:
    """Build a machine-readable status summary."""
    threads = load_all_threads(loom_dir)
    all_tasks = load_all_tasks(loom_dir)
    ready_tasks = [task for task in all_tasks if is_ready(task, all_tasks, threads)]
    inbox_items = load_all_inbox_items(loom_dir)
    agents: list[dict[str, Any]] = []

    agent_root = agents_dir(loom_dir)
    if agent_root.exists():
        for entry in sorted(agent_root.iterdir()):
            if not entry.is_dir():
                continue
            record_path = entry / "_agent.md"
            if not record_path.exists():
                continue
            record = read_model(record_path, AgentRecord)
            agents.append(
                {
                    "id": record.id,
                    "status": record.status.value,
                    "last_seen": record.last_seen,
                    "threads": record.threads,
                    "checkpoint_summary": record.checkpoint_summary,
                }
            )

    by_status: dict[str, int] = {}
    for task in all_tasks:
        by_status[task.status.value] = by_status.get(task.status.value, 0) + 1

    inbox_by_status: dict[str, int] = {}
    for item in inbox_items:
        inbox_by_status[item.status.value] = inbox_by_status.get(item.status.value, 0) + 1

    return {
        "config_present": root_config_path(loom_dir).exists(),
        "threads": len(threads),
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
    }
