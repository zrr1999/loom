"""High-level operations for creating and mutating loom entities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
from .models import (
    AgentRecord,
    AgentRole,
    AgentStatus,
    Decision,
    DecisionOption,
    InboxItem,
    InboxStatus,
    ManagerRecord,
    Message,
    MessageType,
    ReviewEntry,
    Task,
    TaskKind,
    TaskStatus,
    Thread,
    find_review_blockers,
)
from .repository import (
    agent_dir,
    agent_pending_dir,
    agent_record_path,
    agent_replied_dir,
    agents_dir,
    load_agent,
    load_inbox_item,
    load_message,
    load_task,
    manager_path,
    worker_agents_dir,
    workspace_root,
)
from .scheduler import load_all_tasks, load_all_threads
from .state import (
    validate_decision_payload,
    validate_inbox_transition,
    validate_task_scheduled,
    validate_task_transition,
)
from .templates import agent_body, task_body, thread_body

if TYPE_CHECKING:
    from pathlib import Path


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


def create_inbox_item(loom: Path, description: str) -> tuple[InboxItem, Path]:
    """Create a new pending inbox item."""
    body = description.strip()
    if not body:
        raise ValueError("description must not be empty")

    seq = next_inbox_seq(loom / "inbox")
    rq_id = f"RQ-{seq:03d}"
    item = InboxItem(id=rq_id, body=body)
    path = loom / "inbox" / f"{rq_id}.md"
    write_model(path, item)
    append_event(loom, "inbox.created", "inbox", item.id, {"status": item.status.value})
    return item, path


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
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    updated_body = f"## Checkpoint\n\n**phase** {phase}\n**updated** {updated_at}\n\n{summary}\n\n## Notes\n\n"
    updated = agent.model_copy(
        update={
            "last_seen": datetime.now(UTC).isoformat(timespec="seconds"),
            "status": AgentStatus.ACTIVE,
            "checkpoint_summary": summary[:120],
            "body": updated_body,
        }
    )
    write_model(path, updated)
    append_event(loom, "agent.checkpointed", "agent", agent_id, {"phase": phase})
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
    if thread.owner and thread.owner != agent_id:
        raise ValueError(f"thread '{canonical}' is already owned by '{thread.owner}'")

    path = loom / "threads" / canonical / "_thread.md"
    if thread.owner == agent_id:
        return path, thread

    now = datetime.now(UTC).isoformat(timespec="seconds")
    updated = thread.model_copy(update={"owner": agent_id, "owned_at": now})
    write_model(path, updated)
    append_event(
        loom,
        "thread.claimed",
        "thread",
        canonical,
        {"agent": agent_id, "owned_at": now},
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

    updated = thread.model_copy(update={"owner": None, "owned_at": None})
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


def plan_inbox_item(loom: Path, rq_id: str) -> dict[str, object]:
    inbox_path, item = load_inbox_item(loom, rq_id)
    validate_inbox_transition(item.status, InboxStatus.PLANNED)

    settings = load_settings(workspace_root(loom))
    created_thread_name: str | None = None
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

    planned_link = task.id
    planned_to = [*item.planned_to, planned_link]
    updated_item = item.model_copy(update={"status": InboxStatus.PLANNED, "planned_to": planned_to})
    write_model(inbox_path, updated_item)
    append_event(
        loom,
        "inbox.planned",
        "inbox",
        item.id,
        {"planned_to": planned_to, "created_thread": created_thread_name},
    )

    return {
        "rq_id": item.id,
        "status": updated_item.status.value,
        "planned_to": planned_link,
        "created_thread": created_thread_name,
        "tasks": [{"id": task.id, "file": str(path)}],
    }


def derive_task_title(item: InboxItem) -> str:
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
    return transition_task(loom, task_id, TaskStatus.SCHEDULED, rejection_note=note, review_entry=entry)


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
