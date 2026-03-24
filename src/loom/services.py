"""High-level operations for creating and mutating loom entities."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .config import load_settings
from .frontmatter import write_model
from .history import append_event
from .ids import (
    canonical_thread_name,
    next_agent_id,
    next_inbox_seq,
    next_message_seq,
    next_task_seq,
    task_filename,
    task_id,
)
from .lease import is_thread_stale, refresh_thread_lease, utc_now
from .models import (
    AgentRecord,
    AgentRole,
    AgentStatus,
    Decision,
    DecisionOption,
    ManagerRecord,
    Message,
    MessageType,
    RequestItem,
    RequestResolution,
    RequestStatus,
    ReviewEntry,
    Task,
    TaskKind,
    TaskStatus,
    Thread,
    WorktreeRecord,
    WorktreeStatus,
    find_review_blockers,
)
from .repository import (
    agent_dir,
    agent_pending_dir,
    agent_record_path,
    agent_replied_dir,
    agent_worktrees_dir,
    agents_dir,
    load_agent,
    load_message,
    load_request_item,
    load_task,
    load_worktree,
    manager_path,
    requests_dir,
    worker_agents_dir,
    workspace_root,
    worktree_record_path,
)
from .scheduler import load_all_tasks, load_all_threads
from .state import (
    validate_decision_payload,
    validate_request_transition,
    validate_task_scheduled,
    validate_task_transition,
)
from .templates import agent_body, task_body, thread_body


def parse_csv_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item.strip() for item in value if item.strip()]
    return [item.strip() for item in value.split(",") if item.strip()]


def create_thread(
    loom: Path,
    *,
    name: str = "",
    priority: int = 50,
) -> tuple[Thread, Path, list[str]]:
    """Create a thread keyed only by its canonical human-readable name."""
    threads_dir = loom / "threads"
    resolved_name = canonical_thread_name(name)
    existing = load_all_threads(loom)
    duplicate_ids = [thread.name for thread in existing.values() if thread.name == resolved_name]
    if duplicate_ids:
        raise ValueError(f"thread '{resolved_name}' already exists")

    thread_dir = threads_dir / resolved_name
    thread_dir.mkdir(parents=True, exist_ok=False)

    thread = Thread(
        name=resolved_name,
        priority=priority,
        body=thread_body(),
    )
    path = thread_dir / "_thread.md"
    write_model(path, thread)
    append_event(
        loom,
        "thread.created",
        "thread",
        thread.name,
        {"priority": thread.priority},
    )
    return thread, path, duplicate_ids


def ensure_agent_layout(loom: Path) -> None:
    agents_root = agents_dir(loom)
    agents_root.mkdir(parents=True, exist_ok=True)
    worker_agents_dir(loom).mkdir(parents=True, exist_ok=True)
    manager_file = manager_path(loom)
    if not manager_file.exists():
        manager = ManagerRecord(last_seen=datetime.now(UTC).isoformat(timespec="seconds"), checkpoint_summary="ready")
        write_model(manager_file, manager)


def ensure_worktree_storage(loom: Path, agent_id: str) -> Path:
    path = agent_worktrees_dir(loom, agent_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_worktree_name(name: str) -> str:
    resolved = canonical_thread_name(name)
    if not resolved:
        raise ValueError("worktree name must not be empty")
    return resolved


def _resolve_worktree_path(loom: Path, agent_id: str, *, name: str, value: str = "") -> Path:
    root = ensure_worktree_storage(loom, agent_id).resolve()
    candidate = Path(value).expanduser() if value.strip() else root / name
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"worktree path '{resolved}' must stay under '{root}' for worker '{agent_id}'") from exc
    return resolved


def _detect_git_branch(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def load_all_worktrees(loom: Path, agent_id: str) -> list[WorktreeRecord]:
    root = agent_worktrees_dir(loom, agent_id)
    if not root.exists():
        return []
    return [load_worktree(loom, agent_id, path.stem)[1] for path in sorted(root.glob("*.md"))]


def resolve_current_worktree(
    loom: Path,
    agent_id: str,
    *,
    cwd: Path | None = None,
) -> tuple[Path, WorktreeRecord] | None:
    current_dir = (cwd or Path.cwd()).resolve()
    matches: list[tuple[int, Path, WorktreeRecord]] = []
    for record in load_all_worktrees(loom, agent_id):
        checkout_root = Path(record.path).resolve()
        try:
            current_dir.relative_to(checkout_root)
        except ValueError:
            continue
        matches.append((len(checkout_root.parts), checkout_root, record))
    if not matches:
        return None
    _depth, checkout_root, record = max(matches, key=lambda item: item[0])
    return checkout_root, record


def resolve_actor_workspace_root(loom: Path, agent_id: str = "", *, cwd: Path | None = None) -> Path:
    if agent_id:
        current = resolve_current_worktree(loom, agent_id, cwd=cwd)
        if current is not None:
            checkout_root, _record = current
            return checkout_root
    return workspace_root(loom)


def add_worktree(
    loom: Path,
    agent_id: str,
    *,
    name: str,
    path: str = "",
    branch: str = "",
    status: WorktreeStatus = WorktreeStatus.REGISTERED,
) -> tuple[WorktreeRecord, Path]:
    resolved_name = _resolve_worktree_name(name.strip())
    resolved_path = _resolve_worktree_path(loom, agent_id, name=resolved_name, value=path)
    if resolved_path.exists() and not resolved_path.is_dir():
        raise ValueError(f"worktree path '{resolved_path}' must be a directory")

    existing = load_all_worktrees(loom, agent_id)
    if any(record.name == resolved_name for record in existing):
        raise ValueError(f"worktree '{resolved_name}' already exists")
    if any(Path(record.path) == resolved_path for record in existing):
        duplicate = next(record.name for record in existing if Path(record.path) == resolved_path)
        raise ValueError(f"path '{resolved_path}' is already registered as worktree '{duplicate}'")
    resolved_path.mkdir(parents=True, exist_ok=True)

    resolved_branch = branch.strip() or _detect_git_branch(resolved_path)
    if not resolved_branch:
        raise ValueError(
            f"could not determine git branch for '{resolved_path}'; create the git worktree there "
            "first or pass --branch explicitly"
        )

    now = datetime.now(UTC).isoformat(timespec="seconds")
    record = WorktreeRecord(
        name=resolved_name,
        path=str(resolved_path),
        branch=resolved_branch,
        status=status,
        worker=agent_id,
        created_at=now,
        updated_at=now,
        body=(
            "Worker-local metadata only. Task state still lives under .loom/threads/, "
            "and other workers only see worktrees inside their own agent subtree."
        ),
    )
    record_path = worktree_record_path(loom, agent_id, resolved_name)
    write_model(record_path, record)
    append_event(
        loom,
        "worktree.registered",
        "worktree",
        record.name,
        {
            "path": record.path,
            "branch": record.branch,
            "status": record.status.value,
            "worker": agent_id,
        },
    )
    return record, record_path


def attach_worktree(
    loom: Path,
    agent_id: str,
    name: str,
    *,
    thread: str | None = None,
    status: WorktreeStatus | None = None,
    clear: bool = False,
) -> tuple[Path, WorktreeRecord]:
    path, record = load_worktree(loom, agent_id, name)
    if record.worker != agent_id:
        raise ValueError(f"worktree '{record.name}' belongs to worker '{record.worker}', not '{agent_id}'")

    updates: dict[str, object] = {
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if clear:
        updates["thread"] = None
        updates["status"] = status or WorktreeStatus.REGISTERED
    else:
        normalized_thread = canonical_thread_name(thread) if thread else None
        if thread is not None:
            updates["thread"] = normalized_thread
        updates["status"] = status or (WorktreeStatus.ACTIVE if normalized_thread else record.status)

    updated = record.model_copy(update=updates)
    write_model(path, updated)
    append_event(
        loom,
        "worktree.attached",
        "worktree",
        updated.name,
        {
            "worker": updated.worker,
            "thread": updated.thread,
            "status": updated.status.value,
            "clear": clear,
        },
    )
    return path, updated


def remove_worktree(loom: Path, agent_id: str, name: str, *, force: bool = False) -> tuple[Path, WorktreeRecord]:
    path, record = load_worktree(loom, agent_id, name)
    if record.worker != agent_id:
        raise ValueError(f"worktree '{record.name}' belongs to worker '{record.worker}', not '{agent_id}'")
    if record.thread and not force:
        raise ValueError(
            f"worktree '{record.name}' is still attached to thread metadata; "
            "clear it first with `loom agent worktree attach <name> --clear` or pass --force"
        )

    append_event(
        loom,
        "worktree.removed",
        "worktree",
        record.name,
        {"path": record.path, "worker": record.worker, "thread": record.thread, "forced": force},
    )
    path.unlink()
    return path, record


def create_request_item(loom: Path, description: str) -> tuple[RequestItem, Path]:
    """Create a new pending request item."""
    body = description.strip()
    if not body:
        raise ValueError("description must not be empty")

    request_root = requests_dir(loom)
    seq = next_inbox_seq(request_root)
    rq_id = f"RQ-{seq:03d}"
    item = RequestItem(id=rq_id, body=body)
    path = request_root / f"{rq_id}.md"
    write_model(path, item)
    append_event(loom, "request.created", "request", item.id, {"status": item.status.value})
    return item, path


def create_inbox_item(loom: Path, description: str) -> tuple[RequestItem, Path]:
    """Compatibility alias for request creation."""
    return create_request_item(loom, description)


def spawn_agent(loom: Path, *, threads: list[str] | None = None) -> dict[str, object]:
    ensure_agent_layout(loom)
    agents_root = agents_dir(loom)
    agent_id = next_agent_id(agents_root)
    agent_root = agent_dir(loom, agent_id)
    pending_dir = agent_pending_dir(loom, agent_id)
    replied_dir = agent_replied_dir(loom, agent_id)
    pending_dir.mkdir(parents=True, exist_ok=False)
    replied_dir.mkdir(parents=True, exist_ok=False)

    now = datetime.now(UTC).isoformat(timespec="seconds")
    record = AgentRecord(
        id=agent_id,
        role=AgentRole.WORKER,
        registered=now,
        last_seen=now,
        status=AgentStatus.IDLE,
        threads=threads or [],
        checkpoint_summary="idle",
        body=agent_body(),
    )
    write_model(agent_record_path(loom, agent_id), record)

    env_path = agent_root / f"{agent_id}.env"
    env_lines = [
        f"LOOM_WORKER_ID={agent_id}",
        f"LOOM_DIR={loom}",
    ]
    if threads:
        env_lines.append(f"LOOM_THREADS={','.join(threads)}")
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    append_event(loom, "agent.spawned", "agent", agent_id, {"threads": threads or []})
    return {"id": agent_id, "env": str(env_path), "threads": threads or []}


def touch_agent(
    loom: Path, agent_id: str, *, status: AgentStatus | None = None, summary: str | None = None
) -> AgentRecord:
    path = agent_record_path(loom, agent_id)
    if not path.exists():
        now = datetime.now(UTC).isoformat(timespec="seconds")
        initial = AgentRecord(
            id=agent_id,
            registered=now,
            last_seen=now,
            status=status or AgentStatus.IDLE,
            checkpoint_summary=summary or "",
            body=agent_body(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / "inbox" / "pending").mkdir(parents=True, exist_ok=True)
        (path.parent / "inbox" / "replied").mkdir(parents=True, exist_ok=True)
        write_model(path, initial)
        append_event(loom, "agent.auto-registered", "agent", agent_id, {})
        return initial

    _, agent = load_agent(loom, agent_id)
    updated = agent.model_copy(
        update={
            "last_seen": datetime.now(UTC).isoformat(timespec="seconds"),
            "status": status or agent.status,
            "checkpoint_summary": summary if summary is not None else agent.checkpoint_summary,
        }
    )
    write_model(path, updated)
    return updated


def update_checkpoint(loom: Path, agent_id: str, *, phase: str, summary: str) -> AgentRecord:
    path, agent = load_agent(loom, agent_id)
    now = utc_now()
    updated_at = now.isoformat(timespec="seconds")
    updated_body = f"## Checkpoint\n\n**phase** {phase}\n**updated** {updated_at}\n\n{summary}\n\n## Notes\n\n"
    updated = agent.model_copy(
        update={
            "last_seen": updated_at,
            "status": AgentStatus.ACTIVE,
            "checkpoint_summary": summary[:120],
            "body": updated_body,
        }
    )
    write_model(path, updated)

    refreshed_threads: list[str] = []
    for thread_name, thread in load_all_threads(loom).items():
        if thread.owner != agent_id:
            continue
        refreshed = refresh_thread_lease(thread, loom, now=now)
        write_model(loom / "threads" / thread_name / "_thread.md", refreshed)
        refreshed_threads.append(thread_name)

    append_event(
        loom,
        "agent.checkpointed",
        "agent",
        agent_id,
        {"phase": phase, "refreshed_threads": refreshed_threads},
    )
    return updated


def resume_agent(loom: Path, agent_id: str) -> AgentRecord:
    _, agent = load_agent(loom, agent_id)
    return agent


def create_message(
    loom: Path,
    *,
    sender: str,
    recipient: str,
    message_type: MessageType,
    body: str,
    ref: str | None = None,
    reply_ref: str | None = None,
) -> dict[str, object]:
    ensure_agent_layout(loom)
    recipient_dir = agent_pending_dir(loom, recipient)
    recipient_dir.mkdir(parents=True, exist_ok=True)
    msg_id = f"MSG-{next_message_seq(recipient_dir):03d}"
    sent = datetime.now(UTC).isoformat(timespec="seconds")
    message = Message(
        id=msg_id,
        **{"from": sender},
        to=recipient,
        type=message_type,
        ref=ref,
        sent=sent,
        reply_ref=reply_ref,
        body=body,
    )
    path = recipient_dir / f"{msg_id}.md"
    write_model(path, message)
    append_event(loom, "message.sent", "message", msg_id, {"from": sender, "to": recipient, "type": message_type.value})
    return {"id": msg_id, "file": str(path), "type": message_type.value}


def list_pending_messages(loom: Path, agent_id: str) -> list[Message]:
    pending_dir = agent_pending_dir(loom, agent_id)
    if not pending_dir.exists():
        return []
    return [load_message(pending_dir, path.stem)[1] for path in sorted(pending_dir.glob("MSG-*.md"))]


def reply_to_message(loom: Path, agent_id: str, msg_id: str, body: str) -> dict[str, object]:
    pending_dir = agent_pending_dir(loom, agent_id)
    path, message = load_message(pending_dir, msg_id)
    response = create_message(
        loom,
        sender=agent_id,
        recipient=message.from_,
        message_type=MessageType.ANSWER,
        body=body,
        ref=message.ref,
        reply_ref=message.id,
    )
    replied_dir = agent_replied_dir(loom, agent_id)
    replied_dir.mkdir(parents=True, exist_ok=True)
    path.rename(replied_dir / path.name)
    append_event(loom, "message.replied", "message", msg_id, {"agent": agent_id})
    return response


def adjust_thread_priority(loom: Path, thread_name: str, *, priority: int) -> tuple[Path, Thread]:
    """Persist a thread priority change and return the updated record."""
    canonical = canonical_thread_name(thread_name)
    threads = load_all_threads(loom)
    if canonical not in threads:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")

    thread = threads[canonical]
    path = loom / "threads" / canonical / "_thread.md"
    updated = thread.model_copy(update={"priority": priority})
    write_model(path, updated)
    append_event(
        loom,
        "thread.priority_updated",
        "thread",
        canonical,
        {"from": thread.priority, "to": updated.priority},
    )
    return path, updated


def adjust_task_priority(loom: Path, task_id: str, *, priority: int) -> tuple[Path, Task]:
    """Persist a task priority change and return the updated record."""
    path, task = load_task(loom, task_id)
    updated = task.model_copy(update={"priority": priority})
    write_model(path, updated)
    append_event(
        loom,
        "task.priority_updated",
        "task",
        task.id,
        {"from": task.priority, "to": updated.priority, "thread": task.thread},
    )
    return path, updated


def release_claim(loom: Path, task_id: str, *, note: str) -> tuple[Path, Task]:
    """Release thread ownership and revert the task toward SCHEDULED.

    Works for both legacy CLAIMED tasks and current REVIEWING tasks.
    """
    path, task = load_task(loom, task_id)
    threads = load_all_threads(loom)
    thread = threads.get(task.thread)
    if thread and thread.owner:
        release_thread(loom, task.thread, note=note)
    if task.status in {TaskStatus.CLAIMED, TaskStatus.REVIEWING}:
        return transition_task(loom, task_id, TaskStatus.SCHEDULED, rejection_note=note)
    return path, task


def create_task(
    loom: Path,
    *,
    thread_name: str,
    title: str,
    kind: TaskKind = TaskKind.IMPLEMENTATION,
    priority: int = 50,
    acceptance: str = "",
    depends_on: str | list[str] | None = None,
    created_from: str | list[str] | None = None,
    background: str = "",
    implementation_direction: str = "",
) -> tuple[Task, Path]:
    threads = load_all_threads(loom)
    canonical_thread = canonical_thread_name(thread_name)
    if canonical_thread not in threads:
        raise FileNotFoundError(f"thread '{canonical_thread}' does not exist")

    thread_dir = loom / "threads" / canonical_thread
    seq = next_task_seq(thread_dir)
    normalized_title = title.strip() or f"task-{seq}"
    readable_task_id = task_id(canonical_thread, seq)

    normalized_acceptance = acceptance.strip()
    status = TaskStatus.SCHEDULED if normalized_acceptance else TaskStatus.DRAFT
    if status == TaskStatus.SCHEDULED:
        validate_task_scheduled(normalized_acceptance)

    parsed_depends_on = parse_csv_list(depends_on)
    if parsed_depends_on:
        existing_task_ids = {t.id for t in load_all_tasks(loom)}
        missing = [dep for dep in parsed_depends_on if dep not in existing_task_ids]
        if missing:
            raise ValueError(f"depends_on references unknown task(s): {', '.join(missing)}")

    task = Task(
        id=readable_task_id,
        thread=canonical_thread,
        seq=seq,
        title=normalized_title,
        kind=kind,
        status=status,
        priority=priority,
        depends_on=parsed_depends_on,
        created_from=parse_csv_list(created_from),
        acceptance=normalized_acceptance or None,
        body=task_body(background=background, implementation_direction=implementation_direction),
    )
    path = thread_dir / task_filename(seq)
    write_model(path, task)
    append_event(
        loom,
        "task.created",
        "task",
        task.id,
        {"thread": task.thread, "status": task.status.value, "created_from": task.created_from},
    )
    return task, path


def transition_task(
    loom: Path,
    task_id: str,
    target_status: TaskStatus,
    *,
    output: str | None = None,
    rejection_note: str | None = None,
    decision: Decision | None = None,
    review_entry: ReviewEntry | None = None,
) -> tuple[Path, Task]:
    path, task = load_task(loom, task_id)
    validate_task_transition(task.status, target_status)

    updates: dict[str, object] = {"status": target_status}
    if target_status == TaskStatus.SCHEDULED:
        validate_task_scheduled(task.acceptance)
        updates["claim"] = None
    if output is not None:
        updates["output"] = output
    if rejection_note is not None:
        updates["rejection_note"] = rejection_note
    if decision is not None:
        updates["decision"] = decision
    if review_entry is not None:
        updates["review_history"] = [*task.review_history, review_entry]

    updated = Task.model_validate(task.model_dump(mode="python") | updates)
    write_model(path, updated)
    append_event(
        loom,
        "task.transitioned",
        "task",
        task.id,
        {"from": task.status.value, "to": updated.status.value, "output": output, "rejection_note": rejection_note},
    )
    return path, updated


def complete_task(loom: Path, task_id: str, *, output: str | None = None) -> tuple[Path, Task, list[str]]:
    path, task = load_task(loom, task_id)
    blockers = find_review_blockers(task, output=output)
    if not blockers:
        path, updated = transition_task(loom, task_id, TaskStatus.REVIEWING, output=output)
        return path, updated, []

    decision = Decision(
        question=(
            "This task still looks incomplete "
            f"({', '.join(blockers)}). Should it return to scheduled for more work before review?"
        ),
        options=[
            DecisionOption(
                id="resume",
                label="Resume implementation",
                note="Return to scheduled and finish the remaining work before asking for review again.",
            ),
            DecisionOption(
                id="split",
                label="Split follow-up first",
                note="Create or confirm follow-up work before this task can be reviewed.",
            ),
        ],
    )
    path, updated = transition_task(loom, task_id, TaskStatus.PAUSED, output=output, decision=decision)
    return path, updated, blockers


def claim_thread(loom: Path, thread_name: str, *, agent_id: str) -> tuple[Path, Thread]:
    """Claim a thread for an agent.  One active owner per thread maximum."""
    threads = load_all_threads(loom)
    canonical = canonical_thread_name(thread_name)
    if canonical not in threads:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")

    thread = threads[canonical]
    if thread.owner and thread.owner != agent_id and not is_thread_stale(thread):
        raise ValueError(f"thread '{canonical}' is already owned by '{thread.owner}'")

    path = loom / "threads" / canonical / "_thread.md"
    now = utc_now()
    if thread.owner == agent_id:
        refreshed = refresh_thread_lease(thread, loom, now=now)
        if refreshed.model_dump(mode="python") != thread.model_dump(mode="python"):
            write_model(path, refreshed)
            append_event(
                loom,
                "thread.lease_refreshed",
                "thread",
                canonical,
                {"agent": agent_id, "lease_expires_at": refreshed.owner_lease_expires_at},
            )
        return path, refreshed

    claimed_at = now.isoformat(timespec="seconds")
    updated = refresh_thread_lease(thread.model_copy(update={"owner": agent_id, "owned_at": claimed_at}), loom, now=now)
    write_model(path, updated)
    event_name = "thread.reclaimed" if thread.owner and is_thread_stale(thread, now=now) else "thread.claimed"
    append_event(
        loom,
        event_name,
        "thread",
        canonical,
        {
            "agent": agent_id,
            "owned_at": claimed_at,
            "previous_owner": thread.owner,
            "lease_expires_at": updated.owner_lease_expires_at,
        },
    )
    return path, updated


def release_thread(loom: Path, thread_name: str, *, note: str = "") -> tuple[Path, Thread]:
    """Release thread ownership back to the pool."""
    threads = load_all_threads(loom)
    canonical = canonical_thread_name(thread_name)
    if canonical not in threads:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")

    thread = threads[canonical]
    if not thread.owner:
        raise ValueError(f"thread '{canonical}' has no active owner")

    updated = thread.model_copy(
        update={
            "owner": None,
            "owned_at": None,
            "owner_heartbeat_at": None,
            "owner_lease_expires_at": None,
        }
    )
    path = loom / "threads" / canonical / "_thread.md"
    write_model(path, updated)
    append_event(
        loom,
        "thread.released",
        "thread",
        canonical,
        {"previous_owner": thread.owner, "note": note},
    )
    return path, updated


def assign_thread(loom: Path, thread_name: str, *, agent_id: str, note: str = "") -> tuple[Path, Thread]:
    """Explicitly assign a thread to an agent, including bounded reassignment."""
    threads = load_all_threads(loom)
    canonical = canonical_thread_name(thread_name)
    if canonical not in threads:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")

    thread = threads[canonical]
    if thread.owner == agent_id:
        return claim_thread(loom, canonical, agent_id=agent_id)
    if not thread.owner or is_thread_stale(thread):
        return claim_thread(loom, canonical, agent_id=agent_id)

    path = loom / "threads" / canonical / "_thread.md"
    now = utc_now()
    claimed_at = now.isoformat(timespec="seconds")
    updated = refresh_thread_lease(thread.model_copy(update={"owner": agent_id, "owned_at": claimed_at}), loom, now=now)
    write_model(path, updated)
    append_event(
        loom,
        "thread.reassigned",
        "thread",
        canonical,
        {
            "agent": agent_id,
            "owned_at": claimed_at,
            "previous_owner": thread.owner,
            "lease_expires_at": updated.owner_lease_expires_at,
            "note": note,
        },
    )
    return path, updated


def pause_task(
    loom: Path,
    task_id: str,
    *,
    question: str,
    options: list[dict[str, str]] | list[DecisionOption] | None = None,
) -> tuple[Path, Task]:
    raw_options = options or []
    choice_options = [
        option if isinstance(option, DecisionOption) else DecisionOption(**option) for option in raw_options
    ]
    validate_decision_payload(question, choice_options)
    decision = Decision(question=question.strip(), options=choice_options)
    return transition_task(loom, task_id, TaskStatus.PAUSED, decision=decision)


def decide_task(loom: Path, task_id: str, option: str) -> tuple[Path, Task]:
    path, task = load_task(loom, task_id)
    validate_task_transition(task.status, TaskStatus.SCHEDULED)

    current_decision = task.decision
    if isinstance(current_decision, dict):
        current_decision = Decision.model_validate(current_decision)
    if isinstance(current_decision, Decision):
        decision = current_decision.model_copy(update={"decided": option})
    else:
        decision = Decision(question="", decided=option)

    updated = task.model_copy(update={"status": TaskStatus.SCHEDULED, "decision": decision})
    validate_task_scheduled(updated.acceptance)
    write_model(path, updated)
    append_event(
        loom,
        "task.decided",
        "task",
        task.id,
        {"option": option, "status": updated.status.value},
    )
    return path, updated


def plan_request_item(loom: Path, rq_id: str) -> dict[str, object]:
    request_path, item = load_request_item(loom, rq_id)
    validate_request_transition(item.status, RequestStatus.PROCESSING)

    processing_item = item.model_copy(update={"status": RequestStatus.PROCESSING})
    write_model(request_path, processing_item)
    append_event(loom, "request.processing", "request", item.id, {"status": processing_item.status.value})

    settings = load_settings(workspace_root(loom))
    created_thread_name: str | None = None
    try:
        threads = load_all_threads(loom)
        if not threads:
            thread, _, _duplicates = create_thread(
                loom,
                name="general",
                priority=settings.threads.default_priority,
            )
            threads = {thread.name: thread}
            created_thread_name = thread.name

        target_thread = max(threads.values(), key=lambda thread: (thread.priority, thread.name))
        title = derive_task_title(item)
        acceptance = "- [ ] 覆盖需求描述中的核心行为\n- [ ] 产出可供人工验收的结果"
        task, path = create_task(
            loom,
            thread_name=target_thread.name,
            title=title,
            priority=target_thread.priority,
            acceptance=acceptance,
            created_from=[item.id],
            background=item.body,
            implementation_direction=f"围绕 {item.id} 先拆出第一条可执行任务, 后续再继续细化。",
        )
    except Exception:
        rollback_item = item.model_copy(update={"status": RequestStatus.PENDING})
        write_model(request_path, rollback_item)
        append_event(loom, "request.reverted", "request", item.id, {"status": rollback_item.status.value})
        raise

    resolved_to = [task.id]
    updated_item = item.model_copy(
        update={
            "status": RequestStatus.DONE,
            "resolved_as": RequestResolution.TASK,
            "resolved_to": resolved_to,
            "resolution_note": None,
            "planned_to": None,
        }
    )
    write_model(request_path, updated_item)
    append_event(
        loom,
        "request.resolved",
        "request",
        item.id,
        {
            "resolved_as": updated_item.resolved_as.value if updated_item.resolved_as else None,
            "resolved_to": resolved_to,
            "created_thread": created_thread_name,
        },
    )

    return {
        "rq_id": item.id,
        "status": updated_item.status.value,
        "resolved_as": updated_item.resolved_as.value if updated_item.resolved_as else None,
        "resolved_to": resolved_to,
        "created_thread": created_thread_name,
        "tasks": [{"id": task.id, "file": str(path)}],
    }


def plan_inbox_item(loom: Path, rq_id: str) -> dict[str, object]:
    """Compatibility alias for request-to-task triage."""
    return plan_request_item(loom, rq_id)


def derive_task_title(item: RequestItem) -> str:
    first_line = next((line.strip() for line in item.body.splitlines() if line.strip()), item.id)
    title = first_line.rstrip("。.!? ")
    return title[:40] or item.id


def reject_task(loom: Path, task_id: str, note: str) -> tuple[Path, Task]:
    entry = ReviewEntry(
        kind="reject",
        actor="human",
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        note=note,
        source="cli",
    )
    path, updated = transition_task(loom, task_id, TaskStatus.SCHEDULED, rejection_note=note, review_entry=entry)
    return path, updated


def accept_task(loom: Path, task_id: str, *, note: str = "") -> tuple[Path, Task]:
    entry = ReviewEntry(
        kind="accept",
        actor="human",
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        note=note,
        source="cli",
    )
    return transition_task(loom, task_id, TaskStatus.DONE, review_entry=entry)


def format_review_summary(task: Task) -> list[str]:
    """Format a task for review display, emphasizing outcomes first.

    Order: title/status → acceptance criteria → output/results →
    review history → metadata (kind, depends_on, created_from).
    """
    lines = [f"{task.id}: {task.title}"]
    lines.append(f"  status: {task.status.value}")

    # -- Outcome-first: acceptance criteria --
    if task.acceptance:
        lines.append("  acceptance:")
        lines.extend(f"    {line}" for line in task.acceptance.splitlines())

    # -- Output / results --
    if task.output:
        lines.append(f"  output: {task.output}")

    # -- Review history (append-only) --
    if task.review_history:
        lines.append("  review_history:")
        for entry in task.review_history:
            ts = entry.created[:16] if entry.created else "unknown"
            note_suffix = f"  {entry.note}" if entry.note else ""
            lines.append(f"    {ts} {entry.kind}{note_suffix}")
    elif task.rejection_note:
        # Backward compat: show legacy single rejection_note if no history
        lines.append(f"  rejection_note: {task.rejection_note}")

    # -- Secondary metadata --
    lines.append(f"  kind: {task.kind.value}")
    if task.depends_on:
        lines.append(f"  depends_on: {', '.join(task.depends_on)}")
    if task.created_from:
        lines.append(f"  created_from: {', '.join(task.created_from)}")
    return lines
