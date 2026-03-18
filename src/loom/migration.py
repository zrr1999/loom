"""Workspace migrations for evolving on-disk Loom state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .frontmatter import write_model
from .ids import canonical_thread_name, is_short_thread_id, next_thread_id, task_filename, task_id
from .repository import load_agent, load_inbox_item, load_task
from .scheduler import load_all_inbox_items, load_all_tasks, load_all_threads

if TYPE_CHECKING:
    from pathlib import Path


def ensure_name_based_threads(loom: Path) -> None:
    """Keep human-facing thread directories, but normalize internal ids and task files."""
    threads = load_all_threads(loom)
    if not threads:
        return

    canonical_by_dir: dict[str, str] = {}
    for dir_name, thread in threads.items():
        canonical = canonical_thread_name(thread.name or thread.id or dir_name)
        other = next((key for key, value in canonical_by_dir.items() if value == canonical and key != dir_name), None)
        if other is not None:
            msg = f"cannot migrate duplicate thread names: {dir_name!r} and {other!r} both resolve to {canonical!r}"
            raise ValueError(msg)
        canonical_by_dir[dir_name] = canonical

    thread_id_by_name: dict[str, str] = {}
    assigned_thread_ids: set[str] = set()
    for dir_name in sorted(canonical_by_dir):
        thread = threads[dir_name]
        canonical = canonical_by_dir[dir_name]
        if is_short_thread_id(thread.id) and thread.id not in assigned_thread_ids:
            thread_id_by_name[canonical] = thread.id
            assigned_thread_ids.add(thread.id)
            continue
        new_thread_id = next_thread_id(assigned_thread_ids)
        thread_id_by_name[canonical] = new_thread_id
        assigned_thread_ids.add(new_thread_id)

    task_updates: dict[str, tuple[str, str]] = {}
    task_links: dict[str, str] = {}
    for task in load_all_tasks(loom):
        canonical_thread = canonical_by_dir.get(task.thread, canonical_thread_name(task.thread))
        new_id = task_id(thread_id_by_name[canonical_thread], task.seq)
        task_updates[task.id] = (canonical_thread, new_id)
        task_links[task.id] = f"{canonical_thread}/{new_id}"

    threads_dir = loom / "threads"
    for dir_name, canonical in canonical_by_dir.items():
        if dir_name == canonical:
            continue
        target_dir = threads_dir / canonical
        if target_dir.exists():
            msg = f"cannot migrate thread {dir_name!r}: target directory {canonical!r} already exists"
            raise ValueError(msg)

    for dir_name, canonical in canonical_by_dir.items():
        source_dir = threads_dir / dir_name
        target_dir = threads_dir / canonical
        if dir_name != canonical:
            source_dir.rename(target_dir)
        thread_path = target_dir / "_thread.md"
        thread = threads[dir_name]
        updated_thread = thread.model_copy(update={"id": thread_id_by_name[canonical], "name": canonical})
        write_model(thread_path, updated_thread)

    for old_id, (canonical_thread, new_id) in task_updates.items():
        path, task = load_task(loom, old_id)
        updated_task = task.model_copy(
            update={
                "id": new_id,
                "thread": canonical_thread,
                "depends_on": [task_updates.get(dep_id, (canonical_thread, dep_id))[1] for dep_id in task.depends_on],
            }
        )
        new_path = loom / "threads" / canonical_thread / task_filename(task.seq)
        write_model(new_path, updated_task)
        if new_path != path and path.exists():
            path.unlink()

    for item in load_all_inbox_items(loom):
        updated_refs: list[str] = []
        changed = False
        for ref in item.planned_to:
            task_ref = ref.split("/", 1)[1] if "/" in ref else ref
            updated_ref = task_links.get(task_ref, ref)
            updated_refs.append(updated_ref)
            changed = changed or updated_ref != ref
        if changed:
            path, original = load_inbox_item(loom, item.id)
            updated_item = original.model_copy(update={"planned_to": updated_refs})
            write_model(path, updated_item)

    agents_dir = loom / "agents"
    if agents_dir.exists():
        for entry in agents_dir.iterdir():
            if not entry.is_dir():
                continue
            record_path = entry / "_agent.md"
            if not record_path.exists():
                continue
            _, record = load_agent(loom, entry.name)
            updated_threads = [canonical_by_dir.get(thread_name, thread_name) for thread_name in record.threads]
            if updated_threads != record.threads:
                updated_record = record.model_copy(update={"threads": updated_threads})
                write_model(record_path, updated_record)
