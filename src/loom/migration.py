"""Workspace migrations for evolving on-disk Loom state."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from .frontmatter import read_raw, write_model
from .ids import canonical_thread_name, task_filename, task_id
from .models import Claim, TaskStatus
from .repository import (
    agents_dir,
    load_agent,
    load_inbox_item,
    load_task,
    worker_agents_dir,
)
from .scheduler import load_all_inbox_items, load_all_tasks, load_all_threads

if TYPE_CHECKING:
    from pathlib import Path


def ensure_name_based_threads(loom: Path) -> None:
    """Normalize legacy thread/task storage to name-only thread identities."""
    threads = load_all_threads(loom)
    if not threads:
        return

    canonical_by_dir: dict[str, str] = {}
    canonical_by_alias: dict[str, str] = {}
    for dir_name, thread in threads.items():
        meta_path = loom / "threads" / dir_name / "_thread.md"
        metadata, _body = read_raw(meta_path)
        legacy_thread_id = str(metadata.get("id", "")).strip()
        canonical = canonical_thread_name(thread.name or dir_name)
        other = next((key for key, value in canonical_by_dir.items() if value == canonical and key != dir_name), None)
        if other is not None:
            msg = f"cannot migrate duplicate thread names: {dir_name!r} and {other!r} both resolve to {canonical!r}"
            raise ValueError(msg)
        canonical_by_dir[dir_name] = canonical
        canonical_by_alias[dir_name] = canonical
        canonical_by_alias[thread.name] = canonical
        if legacy_thread_id:
            canonical_by_alias[legacy_thread_id] = canonical

    task_updates: dict[str, tuple[str, str]] = {}
    task_links: dict[str, str] = {}
    for task in load_all_tasks(loom):
        canonical_thread = canonical_by_alias.get(task.thread, canonical_thread_name(task.thread))
        new_id = task_id(canonical_thread, task.seq)
        task_updates[task.id] = (canonical_thread, new_id)
        task_links[task.id] = new_id

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
        updated_thread = thread.model_copy(update={"name": canonical})
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

    agent_dirs: list[Path] = []
    agents_root = agents_dir(loom)
    if agents_root.exists():
        for entry in agents_root.iterdir():
            if not entry.is_dir() or entry.name == "workers":
                continue
            agent_dirs.append(entry)
    workers_root = worker_agents_dir(loom)
    if workers_root.exists():
        agent_dirs.extend(entry for entry in workers_root.iterdir() if entry.is_dir())

    for entry in agent_dirs:
        record_path = entry / "_agent.md"
        if not record_path.exists():
            continue
        _, record = load_agent(loom, entry.name)
        updated_threads = [canonical_by_alias.get(thread_name, thread_name) for thread_name in record.threads]
        if updated_threads != record.threads:
            updated_record = record.model_copy(update={"threads": updated_threads})
            write_model(record_path, updated_record)


def ensure_worker_agent_subtree(loom: Path) -> None:
    """Move legacy worker directories under `.loom/agents/workers/`."""
    agents_root = agents_dir(loom)
    if not agents_root.exists():
        return

    workers_root = worker_agents_dir(loom)
    workers_root.mkdir(parents=True, exist_ok=True)

    for entry in sorted(agents_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == "workers":
            continue
        record_path = entry / "_agent.md"
        if not record_path.exists():
            continue

        target_dir = workers_root / entry.name
        if target_dir.exists():
            for child in sorted(entry.iterdir()):
                destination = target_dir / child.name
                if destination.exists():
                    continue
                shutil.move(str(child), str(destination))
            if not any(entry.iterdir()):
                entry.rmdir()
            continue
        entry.rename(target_dir)


def ensure_thread_ownership_metadata(loom: Path) -> None:
    """Upgrade legacy task-level claims into thread ownership metadata.

    Old workspaces may still store `status: claimed` and `claim:` blocks on task
    files. The thread-ownership runtime expects active ownership on
    `.loom/threads/<thread>/_thread.md` instead, while task files should no
    longer persist claim metadata.
    """
    threads = load_all_threads(loom)
    tasks = load_all_tasks(loom)
    if not threads or not tasks:
        return

    active_claims: dict[str, tuple[str, str | None]] = {}
    for task in tasks:
        if task.claim is None:
            continue

        claim = task.claim if isinstance(task.claim, Claim) else Claim.model_validate(task.claim)
        if task.status != TaskStatus.CLAIMED:
            continue
        if not claim.agent:
            msg = f"cannot migrate claimed task '{task.id}' without claim.agent"
            raise ValueError(msg)

        existing = active_claims.get(task.thread)
        if existing is not None and existing[0] != claim.agent:
            msg = (
                f"cannot migrate thread '{task.thread}': conflicting legacy claims from "
                f"'{existing[0]}' and '{claim.agent}'"
            )
            raise ValueError(msg)

        claimed_at = claim.claimed_at
        if existing is None or (claimed_at or "") >= (existing[1] or ""):
            active_claims[task.thread] = (claim.agent, claimed_at)

    for thread_name, (agent_id, owned_at) in active_claims.items():
        thread = threads.get(thread_name)
        if thread is None:
            msg = f"cannot migrate claimed task metadata: missing thread '{thread_name}'"
            raise ValueError(msg)
        if thread.owner and thread.owner != agent_id:
            msg = (
                f"cannot migrate thread '{thread_name}': existing owner '{thread.owner}' "
                f"conflicts with legacy claim owner '{agent_id}'"
            )
            raise ValueError(msg)

        next_owned_at = owned_at or thread.owned_at
        if thread.owner == agent_id and thread.owned_at == next_owned_at:
            continue

        thread_path = loom / "threads" / thread_name / "_thread.md"
        updated_thread = thread.model_copy(update={"owner": agent_id, "owned_at": next_owned_at})
        write_model(thread_path, updated_thread)
        threads[thread_name] = updated_thread

    for task in tasks:
        updates: dict[str, object] = {}
        if task.claim is not None:
            updates["claim"] = None
        if task.status == TaskStatus.CLAIMED:
            updates["status"] = TaskStatus.SCHEDULED
        if not updates:
            continue

        path, latest = load_task(loom, task.id)
        updated_task = latest.model_copy(update=updates)
        write_model(path, updated_task)
