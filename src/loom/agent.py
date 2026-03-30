"""loom agent — machine-friendly subcommands for agent integration."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from .agent_command_catalog import (
    manager_assign_command,
    manager_new_task_command,
    manager_new_thread_command,
    manager_next_command,
    manager_plan_command,
    manager_propose_command,
    manager_send_command,
    manager_spawn_command,
)
from .config import LoomSettings, load_settings
from .lease import is_thread_stale
from .migration import (
    ensure_manager_agent_subtree,
    ensure_name_based_threads,
    ensure_request_storage,
    ensure_routine_storage,
    ensure_thread_ownership_metadata,
    ensure_thread_worktree_metadata,
    ensure_worker_agent_subtree,
)
from .models import AgentRole, AgentStatus, DeliveryContract, MessageType, Task, TaskKind, TaskStatus, WorktreeStatus
from .repository import agent_pending_dir, load_message, load_task, require_loom, task_file_path, workspace_root
from .runtime import global_root, is_global_mode_active, set_root
from .scheduler import (
    get_due_routines,
    get_next_tasks,
    get_pending_inbox_items,
    get_ready_tasks,
    get_status_summary,
    load_all_threads,
    validate_thread_worktree_references,
)
from .services import (
    AmbiguousRequestRoutingError,
    _worktree_has_dirty_git_state,
    add_worktree,
    attach_worktree,
    claim_thread,
    complete_task,
    create_message,
    create_or_merge_task,
    create_thread,
    list_pending_messages,
    load_all_worktrees,
    pause_task,
    plan_inbox_item,
    remove_worktree,
    reply_to_message,
    resolve_actor_workspace_root,
    resolve_current_worktree,
    resume_agent,
    resume_manager,
    spawn_agent,
    touch_agent,
    update_checkpoint,
    update_manager_checkpoint,
)
from .soft_hooks import render_hook_phase_lines
from .state import InvalidTransitionError

app = typer.Typer(invoke_without_command=True)
worktree_app = typer.Typer(help="Worker worktree commands with thread-owned linkage.")
app.add_typer(worktree_app, name="worktree")
_SINGLETON_ACTORS = {
    AgentRole.MANAGER.value,
    AgentRole.DIRECTOR.value,
    AgentRole.REVIEWER.value,
}
_ROLE_HELP = "Run as worker, manager, director, or reviewer. Defaults to worker."
_START_ROLE_HELP = (
    "Show bootstrap guidance for worker, manager, director, or reviewer. Required outside a worker shell."
)
WorkerRoleOption = Annotated[AgentRole, typer.Option("--role", help=_ROLE_HELP)]
StartRoleOption = Annotated[AgentRole | None, typer.Option("--role", help=_START_ROLE_HELP)]
GlobalModeOption = Annotated[bool, typer.Option("-g", help="Use the home-level loom directory.")]
_WORKER_SAFE_COMMANDS = (
    "new-task",
    "next",
    "done",
    "pause",
    "status",
    "whoami",
    "checkpoint",
    "resume",
    "mailbox",
    "mailbox-read",
    "inbox",
    "inbox-read",
    "ask",
    "propose",
    "reply",
)


@app.callback(invoke_without_command=True)
def agent_root_options(
    ctx: typer.Context,
    global_mode: GlobalModeOption = False,
) -> None:
    set_root(global_root() if global_mode else None)
    if ctx.invoked_subcommand is None:
        start()


def _emit_error(message: str, *, code: str = "error") -> None:
    """Print a plain-text error to stderr and exit 1."""
    typer.echo(f"ERROR [{code}]: {message}", err=True)
    raise typer.Exit(1)


def _resolve_loom() -> Path:
    try:
        loom = require_loom()
        ensure_request_storage(loom)
        ensure_routine_storage(loom)
        ensure_manager_agent_subtree(loom)
        ensure_worker_agent_subtree(loom)
        ensure_name_based_threads(loom)
        ensure_thread_ownership_metadata(loom)
        ensure_thread_worktree_metadata(loom)
        return loom
    except FileNotFoundError as exc:
        _emit_error(str(exc), code="loom_not_found")
        raise  # unreachable; satisfies type checker


def _resolve_actor(
    *,
    role: AgentRole,
    missing_worker_message: str | None = None,
) -> str:
    if role != AgentRole.WORKER:
        return role.value

    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    legacy_agent_id = os.environ.get("LOOM_AGENT_ID", "").strip()
    if not worker_id:
        migration_note = ""
        if legacy_agent_id:
            migration_note = " LOOM_AGENT_ID is no longer used; rename it to LOOM_WORKER_ID."
        message = missing_worker_message or (
            "LOOM_WORKER_ID is required for worker commands. "
            "Use --role manager / --role director / --role reviewer, or set LOOM_WORKER_ID."
        )
        _emit_error(
            f"{message}{migration_note}",
            code="missing_worker_id",
        )
        raise  # unreachable
    return worker_id


def _resolve_start_role(role: AgentRole | None) -> AgentRole:
    if role is not None:
        return role

    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    if worker_id:
        return AgentRole.WORKER

    legacy_agent_id = os.environ.get("LOOM_AGENT_ID", "").strip()
    migration_note = ""
    if legacy_agent_id:
        migration_note = " LOOM_AGENT_ID is no longer used; rename it to LOOM_WORKER_ID."
    _emit_error(
        (
            "loom agent start requires --role outside a worker shell. "
            "Use --role manager / --role director / --role reviewer, or set LOOM_WORKER_ID "
            "to use the worker bootstrap."
            f"{migration_note}"
        ),
        code="start_role_required",
    )
    raise  # unreachable


def _require_manager_context(command_name: str) -> None:
    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    if worker_id:
        _emit_error(
            f"loom agent {command_name} is manager-only. LOOM_WORKER_ID={worker_id!r} is set, "
            "so this process is running as a worker. Start a clean manager process without "
            "LOOM_WORKER_ID in the environment and run the command there.",
            code="worker_not_allowed",
        )
    legacy_agent_id = os.environ.get("LOOM_AGENT_ID", "").strip()
    if legacy_agent_id:
        _emit_error(
            (
                f"loom agent {command_name} no longer reads LOOM_AGENT_ID={legacy_agent_id!r}. "
                "Rename it to LOOM_WORKER_ID when launching a worker, or run this manager-only "
                "command from a clean manager process."
            ),
            code="legacy_worker_env",
        )


def _resolve_actor_for_command(command_name: str, *, role: AgentRole) -> str:
    actor = _resolve_actor(role=role)
    if role != AgentRole.WORKER:
        return actor
    if command_name in _WORKER_SAFE_COMMANDS:
        return actor

    worker_commands = ", ".join(f"`loom agent {name}`" for name in _WORKER_SAFE_COMMANDS)
    guidance = " Re-run with `--role manager`, `--role director`, or `--role reviewer` if this is singleton-role work."
    if command_name == "send":
        guidance += " Workers should use `loom agent ask`, `loom agent propose`, or `loom agent reply` instead."
    _emit_error(
        (
            f"loom agent {command_name} is not available to the worker role. "
            f"Worker-safe commands are: {worker_commands}.{guidance}"
        ),
        code="worker_command_not_allowed",
    )
    raise  # unreachable


def _worktree_problem_labels(loom: Path, record) -> list[str]:
    labels: list[str] = []
    path = Path(record.path)
    if not path.exists():
        labels.append("missing path")
    elif _worktree_has_dirty_git_state(path):
        labels.append("dirty")

    if record.thread:
        thread = load_all_threads(loom).get(record.thread)
        if thread is None:
            labels.append("missing thread")
        else:
            issues = validate_thread_worktree_references(loom, {record.thread: thread}).get(record.thread, [])
            for issue in issues:
                if f"worktree '{record.name}'" in issue:
                    if "cross-worker-invalid" in issue:
                        labels.append("cross-worker-invalid")
                    elif "stale:" in issue:
                        labels.append("stale")
                    elif "missing its checkout path" in issue and "missing path" not in labels:
                        labels.append("missing path")
    return labels


def _format_worktree_line(loom: Path, record) -> list[str]:
    effective_status = record.status.value
    labels = _worktree_problem_labels(loom, record)
    if labels:
        effective_status = f"{effective_status} ({', '.join(dict.fromkeys(labels))})"
    lines = [f"{record.name}  {effective_status}"]
    lines.append(f"  path    : {record.path}")
    lines.append(f"  branch  : {record.branch}")
    lines.append(f"  worker  : {record.worker}")
    lines.append(f"  thread  : {record.thread or '-'}")
    return lines


def _settings_root_for_actor(loom: Path, actor: str) -> Path:
    return resolve_actor_workspace_root(loom, actor if actor not in _SINGLETON_ACTORS else "")


def _load_settings_for_actor(loom: Path, actor: str) -> LoomSettings:
    return load_settings(_settings_root_for_actor(loom, actor))


def _current_worker_context_lines(loom: Path, worker_id: str) -> list[str]:
    checkout_root = resolve_actor_workspace_root(loom, worker_id)
    current = resolve_current_worktree(loom, worker_id)
    lines = [f"  checkout root : {checkout_root}"]
    if current is None:
        lines.append("  worktree      : primary workspace (no registered worker-local checkout matched cwd)")
        return lines

    _root, record = current
    lines.append(f"  worktree      : {record.name}")
    lines.append(f"  branch        : {record.branch}")
    lines.append(f"  status        : {record.status.value}")
    if record.thread:
        lines.append(f"  thread        : {record.thread}")
    labels = _worktree_problem_labels(loom, record)
    if labels:
        lines.append(f"  warnings      : {', '.join(dict.fromkeys(labels))}")
    return lines


@worktree_app.command("ls")
@worktree_app.command("list")
def worktree_list() -> None:
    """List worktrees registered under the current worker only."""
    loom = _resolve_loom()
    worker_id = _resolve_actor(
        role=AgentRole.WORKER,
        missing_worker_message=(
            "LOOM_WORKER_ID is required for `loom agent worktree ...` commands. "
            "Run them from a worker shell with LOOM_WORKER_ID set."
        ),
    )
    records = load_all_worktrees(loom, worker_id)
    if not records:
        typer.echo("No worker-local worktrees.")
        return
    for record in records:
        for line in _format_worktree_line(loom, record):
            typer.echo(line)


@worktree_app.command("add")
def worktree_add(
    name: str = typer.Argument(..., help="Worker-local worktree name."),
    path: str = typer.Option(
        "",
        "--path",
        help=("Optional directory path under .loom/agents/workers/<id>/worktrees/. Defaults to the worktree name."),
    ),
    branch: str = typer.Option("", "--branch", help="Branch name. Auto-detected when omitted."),
    status: Annotated[WorktreeStatus, typer.Option("--status", help="Worker-local advisory status.")] = (
        WorktreeStatus.REGISTERED
    ),
) -> None:
    """Register a worker-local worktree directory."""
    loom = _resolve_loom()
    worker_id = _resolve_actor(
        role=AgentRole.WORKER,
        missing_worker_message=(
            "LOOM_WORKER_ID is required for `loom agent worktree ...` commands. "
            "Run them from a worker shell with LOOM_WORKER_ID set."
        ),
    )
    try:
        record, record_path = add_worktree(loom, worker_id, name=name, path=path, branch=branch, status=status)
    except (FileNotFoundError, ValueError) as exc:
        _emit_error(str(exc), code="worktree_invalid")
        raise  # unreachable

    typer.echo(f"REGISTERED worktree {record.name}")
    typer.echo(f"  file   : {record_path}")
    typer.echo(f"  path   : {record.path}")
    typer.echo(f"  branch : {record.branch}")
    typer.echo(f"  worker : {record.worker}")
    typer.echo(f"  status : {record.status.value}")
    typer.echo(
        "  note   : thread linkage/history is stored on the owning thread; this record is local discovery state."
    )


@worktree_app.command("attach")
def worktree_attach(
    name: str = typer.Argument(..., help="Registered worker-local worktree name."),
    thread: str = typer.Option("", "--thread", help="Thread name currently worked in this checkout."),
    status: Annotated[
        WorktreeStatus | None,
        typer.Option("--status", help="Advisory status after updating thread metadata."),
    ] = None,
    clear: bool = typer.Option(False, "--clear", help="Clear thread metadata for this worktree."),
) -> None:
    """Link or unlink a worker-local worktree to thread-owned metadata."""
    loom = _resolve_loom()
    worker_id = _resolve_actor(
        role=AgentRole.WORKER,
        missing_worker_message=(
            "LOOM_WORKER_ID is required for `loom agent worktree ...` commands. "
            "Run them from a worker shell with LOOM_WORKER_ID set."
        ),
    )
    try:
        path, record = attach_worktree(
            loom,
            worker_id,
            name,
            thread=thread or None,
            status=status,
            clear=clear,
        )
    except (FileNotFoundError, ValueError) as exc:
        _emit_error(str(exc), code="worktree_invalid")
        raise  # unreachable

    action = "CLEARED" if clear else "ATTACHED"
    typer.echo(f"{action} worktree {record.name}")
    typer.echo(f"  file   : {path}")
    typer.echo(f"  worker : {record.worker}")
    typer.echo(f"  thread : {record.thread or '-'}")
    typer.echo(f"  status : {record.status.value}")
    if not clear:
        typer.echo(
            "  note   : thread metadata is now authoritative; this worker-local record mirrors it for local discovery."
        )


@worktree_app.command("remove")
def worktree_remove(
    name: str = typer.Argument(..., help="Registered worker-local worktree name."),
    force: bool = typer.Option(False, "--force", help="Remove even if thread metadata is still attached."),
) -> None:
    """Remove a worktree record and delete the worker-local checkout directory."""
    loom = _resolve_loom()
    worker_id = _resolve_actor(
        role=AgentRole.WORKER,
        missing_worker_message=(
            "LOOM_WORKER_ID is required for `loom agent worktree ...` commands. "
            "Run them from a worker shell with LOOM_WORKER_ID set."
        ),
    )
    try:
        _path, record = remove_worktree(loom, worker_id, name, force=force)
    except (FileNotFoundError, ValueError) as exc:
        _emit_error(str(exc), code="worktree_invalid")
        raise  # unreachable

    typer.echo(f"REMOVED worktree {record.name}")
    typer.echo(f"  path   : {record.path}")
    typer.echo("  note   : worker-local record and checkout directory were removed; thread history is preserved.")


def _format_executor_command(template: str, *, agent_id: str, loom_dir: Path, threads: list[str], env_path: str) -> str:
    return (
        template.replace("{agent_id}", agent_id)
        .replace("{loom_dir}", str(loom_dir))
        .replace("{threads}", ",".join(threads))
        .replace("{env_file}", env_path)
    )


def _has_configured_executor_command(settings: object) -> bool:
    return bool(getattr(getattr(settings, "agent", None), "executor_command", "").strip())


def _manager_mailbox_steps(settings: object) -> list[str]:
    lines = ["Manager next steps:"]
    if _has_configured_executor_command(settings):
        lines.append(
            f"  1. Ask the director or host system to run `{manager_spawn_command()}` if a worker needs waking."
        )
    else:
        lines.append(
            "  1. Ask the director or host system to start or wake a worker runtime with LOOM_WORKER_ID + LOOM_DIR."
        )
    lines.extend(
        [
            (f"  2. Assign the thread explicitly when useful: {manager_assign_command()}"),
            (f"  3. Prefer mailbox-first delegation: {manager_propose_command()}"),
            (f"  4. Follow up with {manager_send_command()} when needed."),
            "  5. Tell the worker to run `loom agent next` in its own executor environment.",
            "  6. Re-run `loom agent next --role manager` after each assignment or queue change.",
            "  7. Keep using `loom agent status` to monitor ready / paused / reviewing work.",
            "",
        ]
    )
    return lines


def _manager_launch_guidance(settings: object) -> list[str]:
    if _has_configured_executor_command(settings):
        return [
            f"  Ask the director or host system to run: {manager_spawn_command()}",
            "    That top-level command creates or wakes a worker assignment and prints the configured launch command.",
            "",
        ]
    return [
        "  Ask the director or host system to create or wake a worker runtime.",
        "    Pass LOOM_WORKER_ID and LOOM_DIR from your own launcher or wrapper process.",
        "    Configure [agent].executor_command later if you want `loom spawn` to print that command for you.",
        "",
    ]


def _format_wait_seconds(seconds: float) -> str:
    text = f"{seconds:.2f}"
    return text.rstrip("0").rstrip(".")


def _interactive_wait_feedback_enabled() -> bool:
    stdout_isatty = getattr(sys.stdout, "isatty", None)
    stderr_isatty = getattr(sys.stderr, "isatty", None)
    return bool(callable(stdout_isatty) and stdout_isatty() and callable(stderr_isatty) and stderr_isatty())


def _emit_with_hook_phases(
    lines: list[str],
    *,
    settings: LoomSettings,
    config_root: Path,
    actor: str,
    point: Literal["next", "done"],
) -> None:
    before = render_hook_phase_lines(
        settings, actor, config_root=config_root, point=point, when="before", leading_blank=False
    )
    after = render_hook_phase_lines(
        settings, actor, config_root=config_root, point=point, when="after", leading_blank=bool(before or lines)
    )
    typer.echo("\n".join([*before, *lines, *after]))


def _emit_wait_feedback(*, attempt: int, retries: int, wait_seconds: float) -> None:
    remaining = retries - attempt
    typer.echo(
        (
            f"WAITING  attempt {attempt + 1}/{retries + 1}"
            f"  retries:{retries}"
            f"  wait_seconds:{_format_wait_seconds(wait_seconds)}"
            f"  remaining:{remaining}"
        ),
        err=True,
    )


def _format_task_block(loom: Path, task: Task) -> list[str]:
    lines = [
        f"  TASK  {task.id}",
        f"    title      : {task.title}",
        f"    kind       : {task.kind.value}",
        f"    thread     : {task.thread}",
        f"    status     : {task.status.value}",
        f"    priority   : {task.priority}",
    ]
    if task.depends_on:
        lines.append(f"    depends_on : {', '.join(task.depends_on)}")
    lines.append(f"    file       : {task_file_path(loom, task)}")
    if task.acceptance:
        lines.append("    acceptance :")
        for line in task.acceptance.strip().splitlines():
            lines.append(f"      {line}")
    lines.append("")
    return lines


def _format_minutes_ago(timestamp: str | None) -> str:
    if not timestamp:
        return "unknown"
    try:
        last_seen = datetime.fromisoformat(timestamp)
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - last_seen.astimezone(UTC)
        minutes = max(int(delta.total_seconds() // 60), 0)
        return f"{minutes}m ago"
    except ValueError:
        return timestamp


def _agent_last_seen(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        last_seen = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    return last_seen.astimezone(UTC)


def _agent_is_offline(timestamp: str | None, *, offline_after_minutes: int) -> bool:
    last_seen = _agent_last_seen(timestamp)
    if last_seen is None:
        return False
    limit_minutes = max(offline_after_minutes, 1)
    age_minutes = max(int((datetime.now(UTC) - last_seen).total_seconds() // 60), 0)
    return age_minutes >= limit_minutes


def _worker_spawn_counts(agents: object, *, offline_after_minutes: int) -> tuple[int, int]:
    if not isinstance(agents, list):
        return 0, 0
    active_count = 0
    idle_count = 0
    for item in agents:
        if not isinstance(item, dict):
            continue
        item_map = cast("dict[str, object]", item)
        last_seen = item_map.get("last_seen")
        last_seen_text = last_seen if isinstance(last_seen, str) else None
        if _agent_is_offline(last_seen_text, offline_after_minutes=offline_after_minutes):
            continue
        status = item_map.get("status")
        if status == AgentStatus.ACTIVE.value:
            active_count += 1
        elif status == AgentStatus.IDLE.value:
            idle_count += 1
    return active_count, idle_count


def _enforce_spawn_limits(*, loom: Path, settings: LoomSettings, force: bool) -> None:
    if force:
        return
    active_limit = settings.agent.spawn_limit_active_workers
    idle_limit = settings.agent.spawn_limit_idle_workers
    if active_limit <= 0 and idle_limit <= 0:
        return

    summary = get_status_summary(loom)
    active_count, idle_count = _worker_spawn_counts(
        summary.get("agents"),
        offline_after_minutes=settings.agent.offline_after_minutes,
    )
    violations: list[str] = []
    if active_limit > 0 and active_count >= active_limit:
        violations.append(f"active workers {active_count}/{active_limit}")
    if idle_limit > 0 and idle_count >= idle_limit:
        violations.append(f"idle workers {idle_count}/{idle_limit}")
    if not violations:
        return

    _emit_error(
        "Refusing to spawn a new worker because "
        + " and ".join(violations)
        + ". Reuse or wake an existing worker when possible, inspect `loom agent status`, "
        + "or pass `loom spawn --force` to override.",
        code="spawn_limit_reached",
    )


def _touch_if_agent(loom: Path, actor: str) -> None:
    if actor in _SINGLETON_ACTORS:
        return
    touch_agent(loom, actor, status=AgentStatus.ACTIVE)


def _pending_manager_handoffs(
    loom: Path,
    actor: str,
    *,
    thread_filter: str | None = None,
) -> list[dict[str, str]]:
    ready_task_ids = {task.id for task in get_ready_tasks(loom, thread_filter=thread_filter)}
    if not ready_task_ids:
        return []

    threads = load_all_threads(loom)
    handoffs: list[dict[str, str]] = []
    seen_task_ids: set[str] = set()
    for message in list_pending_messages(loom, actor):
        if message.from_ != AgentRole.MANAGER.value or not message.ref:
            continue
        try:
            _, task = load_task(loom, message.ref)
        except FileNotFoundError:
            continue
        if task.id not in ready_task_ids or task.id in seen_task_ids:
            continue
        thread = threads.get(task.thread)
        if thread is None:
            continue
        owner = thread.owner or ""
        if not owner or owner == actor or is_thread_stale(thread):
            continue
        handoffs.append(
            {
                "message_id": message.id,
                "task_id": task.id,
                "thread": task.thread,
                "title": task.title,
                "owner": owner,
            }
        )
        seen_task_ids.add(task.id)
    return handoffs


@app.command("new-thread", hidden=True)
def new_thread(
    name: Annotated[str, typer.Option(help="Thread name.")] = "",
    priority: Annotated[int, typer.Option(help="Thread priority.")] = 50,
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Create a new thread using its canonical readable name."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor_for_command("new-thread", role=role))

    try:
        thread, path, duplicates = create_thread(loom, name=name, priority=priority)
    except ValueError as exc:
        _emit_error(str(exc))
        raise  # unreachable

    lines = [
        f"CREATED thread {thread.name}",
        f"  priority : {thread.priority}",
        f"  path     : {path.parent}",
    ]
    if duplicates:
        lines.append(f"  WARNING  : thread name '{thread.name}' already used by {', '.join(duplicates)}")
    typer.echo("\n".join(lines))


@app.command("new-task", hidden=True)
def new_task(
    thread: Annotated[str, typer.Option("--thread", help="Canonical thread name (e.g. backend).")],
    title: Annotated[str, typer.Option(help="Task title.")] = "",
    kind: Annotated[TaskKind, typer.Option("--kind", help="Task kind.")] = TaskKind.IMPLEMENTATION,
    priority: Annotated[int, typer.Option(help="Task priority.")] = 50,
    acceptance: Annotated[str, typer.Option(help="Acceptance criteria.")] = "",
    depends_on: Annotated[str, typer.Option(help="Comma-separated dependency IDs.")] = "",
    after: Annotated[
        str,
        typer.Option("--after", help="Sugar for --depends-on: single task ID this task comes after."),
    ] = "",
    created_from: Annotated[str, typer.Option(help="Comma-separated source inbox RQ IDs.")] = "",
    persistent: Annotated[bool, typer.Option("--persistent", help="Keep the task scheduled after each completion.")] = (
        False
    ),
    background: Annotated[str, typer.Option(help="Task background section content.")] = "",
    implementation_direction: Annotated[str, typer.Option(help="Implementation direction section content.")] = "",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Create a new task file in the given thread."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor_for_command("new-task", role=role))

    # Merge --after into depends_on
    merged_deps = depends_on
    if after:
        merged_deps = ",".join(filter(None, [depends_on, after]))

    try:
        result = create_or_merge_task(
            loom,
            thread_name=thread,
            title=title,
            kind=kind,
            priority=priority,
            acceptance=acceptance,
            depends_on=merged_deps,
            created_from=created_from,
            persistent=persistent,
            background=background,
            implementation_direction=implementation_direction,
        )
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        _emit_error(str(exc))
        raise  # unreachable

    task = result.task
    path = result.path
    heading = "CREATED" if result.created else "MERGED"
    lines = [
        f"{heading} task {task.id}",
        f"  kind   : {task.kind.value}",
        f"  status : {task.status.value}",
        f"  thread : {task.thread}",
        f"  file   : {path}",
    ]
    if task.persistent:
        lines.append("  persistent : true")
    if not result.created and result.merge_reason:
        lines.append(f"  merged  : {result.merge_reason}")
        if result.priority_changed:
            lines.append(f"  priority: elevated to {task.priority}")
    typer.echo("\n".join(lines))


@app.command("next")
def next_task(
    thread: Annotated[str, typer.Option("--thread", help="Limit to a specific thread.")] = "",
    plan_limit: Annotated[
        int,
        typer.Option("--plan-limit", min=0, help="Plan up to this many pending inbox items first."),
    ] = 0,
    task_limit: Annotated[int, typer.Option("--task-limit", min=0, help="Return up to this many ready tasks.")] = 0,
    wait_seconds: Annotated[
        float | None,
        typer.Option("--wait-seconds", help="Seconds to wait between retries when action is idle."),
    ] = None,
    retries: Annotated[
        int | None,
        typer.Option("--retries", min=0, help="Retry count when no plan/task action is ready."),
    ] = None,
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Get the next agent action."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("next", role=role)
    _touch_if_agent(loom, actor)
    config_root = _settings_root_for_actor(loom, actor)
    settings = load_settings(config_root)
    effective_wait_seconds = settings.agent.next_wait_seconds if wait_seconds is None else wait_seconds
    effective_retries = settings.agent.next_retries if retries is None else retries

    if effective_wait_seconds < 0:
        _emit_error("--wait-seconds must be >= 0", code="invalid_wait_seconds")
    if effective_retries < 0:
        _emit_error("--retries must be >= 0", code="invalid_retries")

    if actor == AgentRole.REVIEWER.value:
        reviewing_count = 0
        paused_count = 0
        for attempt in range(effective_retries + 1):
            summary = get_status_summary(loom)
            queue = summary.get("queue", [])
            reviewing_count = sum(1 for item in queue if item.get("kind") == "reviewing")
            paused_count = sum(1 for item in queue if item.get("kind") == "paused")
            if reviewing_count or paused_count:
                break
            if attempt < effective_retries and effective_wait_seconds > 0:
                if _interactive_wait_feedback_enabled():
                    _emit_wait_feedback(
                        attempt=attempt,
                        retries=effective_retries,
                        wait_seconds=effective_wait_seconds,
                    )
                time.sleep(effective_wait_seconds)

        lines = [
            "ACTION  idle",
            "",
            "Reviewer work is queue-driven; `loom agent next --role reviewer` only tracks review-ready state.",
        ]
        if reviewing_count or paused_count:
            lines.append("")
            lines.append("WAITING ON")
            if reviewing_count:
                lines.append(f"  reviewing : {reviewing_count} (human must accept or reject)")
            if paused_count:
                lines.append(f"  paused    : {paused_count} (human must decide)")
        lines.extend(
            _singleton_role_idle_steps(
                actor,
                reviewing_count=reviewing_count,
                paused_count=paused_count,
                inbox_pending=0,
            )
        )
        _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
        return

    summary = get_status_summary(loom)
    effective_plan_limit = plan_limit or settings.agent.inbox_plan_batch
    auto_planned_lines: list[str] = []
    pending_inbox: list[dict[str, object]] = []
    tasks: list[Task] = []
    due_routines = []
    for attempt in range(effective_retries + 1):
        pending_inbox = get_pending_inbox_items(loom, limit=effective_plan_limit)
        if pending_inbox:
            break

        tasks = get_next_tasks(
            loom,
            limit=task_limit or settings.agent.task_batch,
            thread_filter=thread or None,
            for_agent=actor if actor != "manager" else None,
        )
        if tasks:
            break

        if actor == AgentRole.MANAGER.value:
            due_routines = get_due_routines(loom, limit=task_limit or 1)
            if due_routines:
                break

        if attempt < effective_retries and effective_wait_seconds > 0:
            if _interactive_wait_feedback_enabled():
                _emit_wait_feedback(
                    attempt=attempt,
                    retries=effective_retries,
                    wait_seconds=effective_wait_seconds,
                )
            time.sleep(effective_wait_seconds)
        summary = get_status_summary(loom)

    if pending_inbox and actor == AgentRole.MANAGER.value:
        while pending_inbox:
            for item in pending_inbox:
                try:
                    planned = plan_inbox_item(loom, cast("str", item["id"]))
                except (FileNotFoundError, ValueError, InvalidTransitionError, AmbiguousRequestRoutingError) as exc:
                    lines = ["ACTION  plan", f"COUNT   {len(pending_inbox)}", ""]
                    if auto_planned_lines:
                        lines.extend(["AUTO-PLANNED REQUESTS", *auto_planned_lines, ""])
                    lines.extend(
                        [
                            "AUTO-PLANNING STOPPED",
                            f"  failed_on : {item['id']}",
                        ]
                    )
                    reason_lines = str(exc).splitlines() or [str(exc)]
                    lines.append(f"  reason    : {reason_lines[0]}")
                    for extra_line in reason_lines[1:]:
                        lines.append(f"              {extra_line}")
                    lines.extend(
                        [
                            "",
                            "Manager next steps:",
                            "  1. Resolve the routing choice for the failed request.",
                            f"  2. Retry the specific item with {manager_plan_command()}.",
                            "  3. If the request needs a brand-new thread first, use:",
                            f"     {manager_new_thread_command()}",
                            "  4. If you need to shape the first task manually, use:",
                            f"     {manager_new_task_command()}",
                            f"  5. Re-run `{manager_next_command()}` after the routing choice is recorded.",
                            "",
                        ]
                    )
                    _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
                    return

                resolved_to = ", ".join(cast("list[str]", planned.get("resolved_to", [])))
                resolution = cast("str | None", planned.get("resolved_as"))
                suffix = f" ({resolution})" if resolution else ""
                auto_planned_lines.append(f"  {planned['rq_id']} -> {resolved_to}{suffix}")
                created_thread = cast("str | None", planned.get("created_thread"))
                if created_thread:
                    auto_planned_lines.append(f"    created_thread : {created_thread}")

            summary = get_status_summary(loom)
            pending_inbox = get_pending_inbox_items(loom, limit=effective_plan_limit)

        tasks = get_next_tasks(
            loom,
            limit=task_limit or settings.agent.task_batch,
            thread_filter=thread or None,
            for_agent=None,
        )
        if not tasks:
            due_routines = get_due_routines(loom, limit=task_limit or 1)

    if pending_inbox:
        item_lines = []
        for item in pending_inbox:
            item_lines.append(f"  {item['id']}  {item.get('title', item.get('body', ''))}")
            item_lines.append(f"    file   : {item.get('file', '')}")
        action = (
            "plan"
            if actor == AgentRole.MANAGER.value
            else "coordinate"
            if actor == AgentRole.DIRECTOR.value
            else "escalate"
        )
        lines = [f"ACTION  {action}", f"COUNT   {len(pending_inbox)}", "", "UNPLANNED REQUESTS", *item_lines, ""]
        if actor == AgentRole.MANAGER.value:
            lines.extend(
                [
                    "Manager next steps:",
                    "  1. Turn each pending request into threads/tasks from the manager surface.",
                    f"  2. Run: {manager_plan_command()}",
                    "  3. If the request needs a brand-new thread first, use:",
                    f"     {manager_new_thread_command()}",
                    "  4. If you need to shape the first task manually, use:",
                    f"     {manager_new_task_command()}",
                    f"  5. Repeat `{manager_next_command()}` after all requirements above are arranged.",
                    "",
                ]
            )
        elif actor in _SINGLETON_ACTORS:
            lines.extend(_singleton_role_plan_steps(actor))
        else:
            lines.extend(
                [
                    "Worker next steps:",
                    "  1. Planning work is blocking execution; notify the manager or director immediately.",
                    "  2. Ask for planning clearance with",
                    "     `loom agent ask manager 'Please clear pending request planning.'`",
                    "     or propose the handoff with `loom agent propose manager '<planning handoff>' --ref <rq-id>`.",
                    "  3. After planning clears, run `loom agent next` again.",
                    "",
                ]
            )
        _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
        return

    if actor == AgentRole.MANAGER.value and not tasks and due_routines:
        routine_lines: list[str] = []
        for routine in due_routines:
            routine_lines.extend(
                [
                    f"  {routine.id}  {routine.title}",
                    f"    status      : {routine.status.value}",
                    f"    interval    : {routine.interval}",
                    f"    assigned_to : {routine.assigned_to or '-'}",
                    f"    last_run    : {routine.last_run or '-'}",
                    f"    last_result : {routine.last_result.value if routine.last_result else '-'}",
                ]
            )

        lines = [
            "ACTION  trigger",
            f"COUNT   {len(due_routines)}",
            "",
            *(["AUTO-PLANNED REQUESTS", *auto_planned_lines, ""] if auto_planned_lines else []),
            "DUE ROUTINES",
            *routine_lines,
            "",
            "Manager next steps:",
            "  1. Trigger the next due routine with `loom routine run <id>`.",
            "  2. Keep ready-task ordering intact by re-running `loom agent next --role manager` after each trigger.",
            "",
        ]
        _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
        return

    if not tasks:
        queue = summary.get("queue", [])
        reviewing_count = sum(1 for item in queue if item.get("kind") == "reviewing")
        paused_count = sum(1 for item in queue if item.get("kind") == "paused")
        inbox_pending = summary.get("inbox_pending", 0)
        stale_owned_threads = list(summary.get("stale_owned_threads", []))

        if actor == AgentRole.DIRECTOR.value:
            worker_count = len(summary.get("agents", []))
            if reviewing_count or paused_count:
                lines = [
                    "ACTION  review",
                    "",
                    "Human queue work is now the main bottleneck.",
                    "",
                    "WAITING ON",
                ]
                if reviewing_count:
                    lines.append(f"  reviewing : {reviewing_count} (human must accept or reject)")
                if paused_count:
                    lines.append(f"  paused    : {paused_count} (human must decide)")
                lines.extend(
                    [
                        "",
                        "Director next steps:",
                        "  1. Use `loom review` or plain `loom` to inspect the queue.",
                        "  2. Ask the human for accept/reject/decide input immediately.",
                        "  3. Re-run `loom agent next --role director` after each review decision.",
                    ]
                )
                _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
                return
            if (inbox_pending or stale_owned_threads) and worker_count == 0:
                lines = [
                    "ACTION  bootstrap",
                    "",
                    "Work exists, but no worker runtime is visible yet.",
                    "",
                    "Director next steps:",
                    "  1. Launch the manager, reviewer, and the needed worker runtimes now.",
                    "  2. Prefer resuming existing worker checkpoints before creating fresh workers.",
                    "  3. Re-run `loom agent next --role director` once the round is bootstrapped.",
                ]
                _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
                return
            if inbox_pending or stale_owned_threads:
                lines = [
                    "ACTION  coordinate",
                    "",
                    "Coordination is needed before execution can move.",
                ]
                if stale_owned_threads:
                    lines.extend(["", "WAITING ON", f"  stale-owned-threads : {', '.join(stale_owned_threads)}"])
                lines.extend(
                    _singleton_role_idle_steps(actor, reviewing_count=0, paused_count=0, inbox_pending=inbox_pending)
                )
                _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
                return
            lines = ["ACTION  wait", "", "No immediate orchestration action is waiting."]
            lines.extend(_singleton_role_idle_steps(actor, reviewing_count=0, paused_count=0, inbox_pending=0))
            _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
            return

        if actor == AgentRole.MANAGER.value:
            if paused_count or reviewing_count or stale_owned_threads:
                lines = ["ACTION  unblock", ""]
                if auto_planned_lines:
                    lines.extend(["AUTO-PLANNED REQUESTS", *auto_planned_lines, ""])
                if paused_count or reviewing_count or stale_owned_threads:
                    lines.append("WAITING ON")
                    if stale_owned_threads:
                        lines.append(f"  stale-owned-threads : {', '.join(stale_owned_threads)}")
                    if reviewing_count:
                        lines.append(f"  reviewing           : {reviewing_count}")
                    if paused_count:
                        lines.append(f"  paused              : {paused_count}")
                    lines.append("")
                lines.extend(
                    _manager_unblock_steps(
                        paused_count=paused_count,
                        reviewing_count=reviewing_count,
                        stale_threads=stale_owned_threads,
                    )
                )
                _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
                return
            lines = ["ACTION  wait", ""]
            if auto_planned_lines:
                lines.extend(["AUTO-PLANNED REQUESTS", *auto_planned_lines, ""])
            lines.append("No planning, assignment, or unblock action is ready.")
            lines.extend(
                [
                    "Manager next steps:",
                    "  1. Monitor `loom agent status` and mailbox traffic.",
                    "  2. Re-run `loom agent next --role manager` after state changes.",
                    "",
                ]
            )
            _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
            return

        pending_handoffs = _pending_manager_handoffs(loom, actor, thread_filter=thread or None)
        if pending_handoffs:
            handoff_lines: list[str] = []
            for handoff in pending_handoffs:
                handoff_lines.extend(
                    [
                        f"  {handoff['task_id']}  {handoff['title']}",
                        f"    thread : {handoff['thread']}",
                        f"    owner  : {handoff['owner']}",
                        f"    msg    : {handoff['message_id']}",
                    ]
                )
            lines = [
                "ACTION  escalate",
                "",
                "A manager handoff is waiting on explicit thread assignment before this worker can claim it.",
                "",
                "PENDING HANDOFFS",
                *handoff_lines,
                "",
                "Worker next steps:",
                "  1. Read the handoff details with `loom agent mailbox` / `loom agent mailbox-read <msg-id>`.",
                "  2. Ask the manager to assign or reassign the thread explicitly.",
                f"     Manager command: {manager_assign_command()}",
                "  3. After the assignment lands, run `loom agent next` again (optionally with `--thread <name>`).",
            ]
            _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
            return

        worker_action = "escalate" if (reviewing_count or paused_count or inbox_pending) else "wait"
        lines = [f"ACTION  {worker_action}", "", "No ready task is available for this worker right now."]
        if reviewing_count or paused_count or inbox_pending:
            lines.append("")
            lines.append("WAITING ON")
            if reviewing_count:
                lines.append(f"  reviewing : {reviewing_count} (human must accept or reject)")
            if paused_count:
                lines.append(f"  paused    : {paused_count} (human must decide)")
            if inbox_pending:
                lines.append(f"  inbox     : {inbox_pending} pending items")
        lines.extend(
            _worker_wait_or_escalate_steps(
                reviewing_count=reviewing_count,
                paused_count=paused_count,
                inbox_pending=inbox_pending,
            )
        )
        _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
        return

    if actor in _SINGLETON_ACTORS:
        task_lines = []
        for task in tasks:
            task_lines.extend(_format_task_block(loom, task))

        action = "assign" if actor == AgentRole.MANAGER.value else "wake"
        lines = [
            f"ACTION  {action}",
            f"COUNT   {len(tasks)}",
            f"ACTOR   {actor}",
            "",
            *(
                ["AUTO-PLANNED REQUESTS", *auto_planned_lines, ""]
                if actor == AgentRole.MANAGER.value and auto_planned_lines
                else []
            ),
            "READY TASKS",
            *task_lines,
            *(
                _manager_mailbox_steps(settings)
                if actor == AgentRole.MANAGER.value
                else _singleton_role_next_steps(actor)
            ),
        ]
        _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")
        return

    # Claim thread(s) for the tasks about to be dispatched.
    claimed_threads: set[str] = set()
    owned_threads = summary.get("owned_threads", {})
    already_owned_threads = {task.thread for task in tasks if owned_threads.get(task.thread, {}).get("owner") == actor}
    for task in tasks:
        if task.thread not in claimed_threads:
            claim_thread(loom, task.thread, agent_id=actor)
            claimed_threads.add(task.thread)

    task_lines = []
    for task in tasks:
        task_lines.extend(_format_task_block(loom, task))

    thread_text = ", ".join(sorted(claimed_threads))
    action = "execute" if claimed_threads and claimed_threads.issubset(already_owned_threads) else "pickup"
    lines = [
        f"ACTION  {action}",
        f"COUNT   {len(tasks)}",
        f"ACTOR   {actor}",
        f"THREAD  {thread_text}",
        "",
        "ASSIGNED TASKS",
        *task_lines,
        "When finished with each task:",
        "  loom agent done <task-id> [--output <.loom/products/...|url>]",
        "",
        "If blocked and need a decision:",
        "  loom agent pause <task-id> --question '<question>'",
    ]
    _emit_with_hook_phases(lines, settings=settings, config_root=config_root, actor=actor, point="next")


def _render_manager_bootstrap(loom: Path) -> list[str]:
    settings = load_settings(workspace_root(loom))
    loom_dir_env = os.environ.get("LOOM_DIR", "").strip()
    summary = get_status_summary(loom)
    ready_tasks = summary.get("tasks", {}).get("ready_ids", [])
    inbox_pending = summary.get("inbox", {}).get("pending", 0)
    queue = summary.get("queue", [])
    paused_count = sum(1 for item in queue if item.get("kind") == "paused")
    reviewing_count = sum(1 for item in queue if item.get("kind") == "reviewing")

    state_summary = [
        "CURRENT STATE",
        f"  pending inbox : {inbox_pending}",
        f"  ready tasks   : {len(ready_tasks)}",
        f"  paused queue  : {paused_count}",
        f"  review queue  : {reviewing_count}",
    ]

    lines = [
        "LOOM AGENT BOOTSTRAP",
        "====================",
        "",
        "ROLE",
        "  You are the Manager.",
        "  Turn pending requests into threads/tasks, assign ready threads to workers, and unblock the flow.",
        "",
        "IDENTITY",
        "  role           : manager",
        f"  loom dir       : {loom_dir_env or str(loom)}",
        "",
        *state_summary,
        "",
        "MAIN LOOP",
        "  1. Run: loom agent next --role manager",
        "  2. Complete the single action it returns.",
        "  3. Re-run `loom agent next --role manager` after each state change.",
        "",
        "MANAGER ACTIONS",
        "  ACTION  plan",
        "    Auto-planning stopped because Loom needs an explicit manager routing choice.",
        f"    Use {manager_plan_command()}, {manager_new_thread_command()}, and {manager_new_task_command()}.",
        "",
        "  ACTION  assign",
        "    Ready work exists; wake workers, assign the thread, and hand off context mailbox-first.",
        f"    Use {manager_assign_command()}, {manager_propose_command()}, and {manager_send_command()}.",
        "",
        "  ACTION  unblock",
        "    Clear stale ownership or route paused/reviewing blockers to the right human flow.",
        "",
        "  ACTION  wait",
        "    No immediate manager action is ready; monitor status and loop again later.",
        "",
        "ESSENTIAL COMMANDS",
        "",
        f"  {manager_next_command()}",
        "    Fetch the next planning / assignment / unblock step.",
        f"    Planning batch : {settings.agent.inbox_plan_batch} inbox items",
        f"    Task batch     : {settings.agent.task_batch} tasks",
        f"    Idle wait      : {settings.agent.next_wait_seconds}s between retries",
        f"    Idle retries   : {settings.agent.next_retries}",
        "",
        *_manager_launch_guidance(settings),
        "  Mailbox-first delegation once a worker exists",
        f"    {manager_propose_command()}",
        f"    {manager_send_command()}",
        "    Workers inspect with `loom agent mailbox` / `loom agent mailbox-read` and answer with `loom agent reply`.",
        "",
        "  loom agent status",
        "    Review ready, paused, and reviewing work across the project.",
    ]

    if is_global_mode_active():
        lines.extend(
            [
                "  Global mode is active (-g).",
                "  Omit -g only if you intentionally want a different local workspace.",
            ]
        )

    return lines


def _render_worker_bootstrap(loom: Path) -> list[str]:
    loom_dir_env = os.environ.get("LOOM_DIR", "").strip()
    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    summary = get_status_summary(loom)
    queue = summary.get("queue", [])
    paused_count = sum(1 for item in queue if item.get("kind") == "paused")
    reviewing_count = sum(1 for item in queue if item.get("kind") == "reviewing")
    lines = [
        "LOOM WORKER BOOTSTRAP",
        "=====================",
        "",
        "ROLE",
        "  You are the Worker.",
        "  Pick up assigned work, execute it inside your thread/worktree, and escalate blockers fast.",
        "",
        "IDENTITY",
        "  role           : worker",
        f"  loom dir       : {loom_dir_env or str(loom)}",
        f"  worker id      : {worker_id or '(set LOOM_WORKER_ID before running worker commands)'}",
    ]
    if worker_id:
        lines.extend(["", "WORKTREE CONTEXT", *_current_worker_context_lines(loom, worker_id)])
    lines += [
        "",
        "WORKER LOOP",
        "  1. Run: loom agent next",
        "  2. Follow the single action it returns.",
        "  3. Re-run `loom agent next` after each state change or manager handoff.",
        "",
        "WORKER ACTIONS",
        "  ACTION  pickup",
        "    A new task batch has been assigned to this worker; read the task block and start implementation.",
        "",
        "  ACTION  execute",
        "    Continue work already assigned to this worker and finish with `loom agent done` or `loom agent pause`.",
        "",
        "  ACTION  escalate",
        "    A manager/human-side dependency is blocking execution; escalate immediately with mailbox commands.",
        "",
        "  ACTION  wait",
        "    No task is ready for this worker right now; monitor mailbox and loop later.",
        "",
        "WORKER-SAFE COMMANDS",
        "  loom agent next",
        "  loom agent done <task-id> [--output <.loom/products/...|url>]",
        "  loom agent pause <task-id> --question '<question>'",
        "  loom agent checkpoint '<summary>'",
        "  loom agent resume",
        "  loom agent mailbox",
        "  loom agent mailbox-read <msg-id>",
        "  loom agent whoami",
        "  loom agent ask <to> '<question>'",
        "  loom agent propose <to> '<proposal>' --ref <thread-or-task-id>",
        "  loom agent reply <msg-id> '<reply>'",
        "  loom agent status",
        "",
        "NOTES",
        "  - `loom agent send` requires a singleton role override.",
        "  - Canonical manager planning commands now live under `loom manage`.",
        f"  - Mailbox handoffs become claimable after manager assignment/reassignment: {manager_assign_command()}.",
        "    Once assigned, re-run `loom agent next` to claim the task batch.",
        "  - `loom spawn` is a director/human top-level worker launch entrypoint; workers should not call it.",
    ]
    if paused_count or reviewing_count:
        lines.extend(
            [
                "",
                "CURRENT QUEUE",
                f"  paused    : {paused_count}",
                f"  reviewing : {reviewing_count}",
            ]
        )
    return lines


def _render_reviewer_bootstrap(loom: Path) -> list[str]:
    summary = get_status_summary(loom)
    queue = summary.get("queue", [])
    paused_count = sum(1 for item in queue if item.get("kind") == "paused")
    reviewing_count = sum(1 for item in queue if item.get("kind") == "reviewing")
    lines = [
        "LOOM REVIEWER BOOTSTRAP",
        "=======================",
        "",
        "DO THIS NOW",
        "  Inspect tasks already in `reviewing` and help a human decide whether to accept or reject them.",
        "  Keep re-running `loom agent next --role reviewer` so you refresh what needs review next.",
        "",
        "IDENTITY",
        "  role           : reviewer",
        f"  loom dir       : {os.environ.get('LOOM_DIR', '').strip() or str(loom)}",
        "",
        "CURRENT STATE",
        f"  review queue  : {reviewing_count}",
        f"  paused queue  : {paused_count}",
        "",
        "MAIN LOOP",
        "  Repeat this loop immediately:",
        "    1. Run: loom agent next --role reviewer",
        "    2. If review work is waiting, inspect it with `loom review`.",
        "    3. Accept with `loom review accept <task-id>` or reject with",
        "       `loom review reject <task-id> '<reason>'` as needed.",
        "    4. After each review action or handoff, run",
        "       `loom agent next --role reviewer` again.",
        "",
        "REVIEW LOOP",
        "  1. Run: loom review",
        "  2. Compare reviewing tasks against their acceptance criteria, output, and review history.",
        "  3. Accept with: loom review accept <task-id>",
        "  4. Reject with: loom review reject <task-id> '<reason>'",
        "  5. If a paused human decision must be resolved from the plain CLI,",
        "     use: loom review decide <task-id> <option>",
        "",
        "GUARDRAILS",
        "  - Reviewer work starts after implementation is finished; do not act as manager or worker here.",
        "  - Focus on reviewing: summarize evidence, highlight gaps, and recommend accept/reject clearly.",
        "  - If more runtime work is needed, hand the task back with a concrete rejection note.",
    ]
    return lines


def _singleton_role_next_steps(actor: str) -> list[str]:
    if actor == AgentRole.REVIEWER.value:
        return [
            "Reviewer next steps:",
            "  1. This is execution work, not review work; do not claim or implement it yourself.",
            "  2. Surface the ready task/thread to the director or manager if it needs dispatch.",
            "  3. Use `loom review`, `loom review accept`, or `loom review reject`",
            "     only for tasks already in reviewing.",
            "  4. After the handoff or after queue changes, run",
            "     `loom agent next --role reviewer` again.",
            "",
        ]
    if actor == AgentRole.DIRECTOR.value:
        return [
            "Director next steps:",
            "  1. Decide whether to wake workers, coordinate the manager, or route the queue to review.",
            "  2. Use `loom spawn` to wake execution, `loom manage` to coordinate planning/assignment,",
            "     and `loom review` when human review or decisions are the bottleneck.",
            "  3. After each orchestration step or state change, run",
            "     `loom agent next --role director` again.",
            "",
        ]
    return [
        f"{actor.title()} next steps:",
        "  1. Coordinate with the manager or worker role before mutating task state.",
        "",
    ]


def _singleton_role_plan_steps(actor: str) -> list[str]:
    if actor == AgentRole.REVIEWER.value:
        return [
            "Reviewer next steps:",
            "  1. This is planning work, not review work; do not plan requests yourself.",
            "  2. Surface the request backlog to the director or manager immediately.",
            "  3. After the handoff or after planning clears, run",
            "     `loom agent next --role reviewer` again.",
            "",
        ]
    if actor == AgentRole.DIRECTOR.value:
        return [
            "Director next steps:",
            "  1. Pending requests need coordination now; wake the manager with `loom manage`.",
            "  2. Let the manager arrange the listed requests into threads/tasks and ownership handoffs.",
            "  3. After planning lands, run `loom agent next --role director` again.",
            "",
        ]
    return []


def _singleton_role_idle_steps(
    actor: str,
    *,
    reviewing_count: int,
    paused_count: int,
    inbox_pending: int,
) -> list[str]:
    if actor == AgentRole.REVIEWER.value:
        lines = ["", "Reviewer next steps:"]
        if reviewing_count:
            lines.append("  1. Review work is waiting now; run `loom review`.")
            step = 2
        else:
            lines.append("  1. No review item is ready right now; monitor `loom review` / `loom status`.")
            step = 2
        if paused_count:
            lines.append(f"  {step}. If a paused human decision needs a plain CLI resolution,")
            lines.append("     use `loom review decide <task-id> <option>`.")
            step += 1
        lines.append(f"  {step}. Run `loom agent next --role reviewer` again after each review action")
        lines.append("     or when queue state changes.")
        return lines

    if actor == AgentRole.DIRECTOR.value:
        lines = ["", "Director next steps:"]
        step = 1
        if inbox_pending:
            lines.append(f"  {step}. Pending requests exist; coordinate the manager with `loom manage`.")
            step += 1
        if reviewing_count or paused_count:
            lines.append(f"  {step}. Human/reviewer queue work exists; inspect with `loom review` or plain `loom`.")
            step += 1
        if step == 1:
            lines.append("  1. No immediate queue action is waiting; monitor `loom status` / `loom agent status`.")
            step = 2
        lines.append(f"  {step}. Run `loom agent next --role director` again after each orchestration")
        lines.append("     action or when state changes.")
        return lines

    return []


def _worker_wait_or_escalate_steps(*, reviewing_count: int, paused_count: int, inbox_pending: int) -> list[str]:
    lines = ["", "Worker next steps:"]
    if inbox_pending:
        lines.append("  1. Planning is still pending; escalate to the manager or director immediately.")
        lines.append(
            "  2. Use `loom agent ask manager 'Please clear pending request planning.'` or "
            "`loom agent propose manager '<planning handoff>' --ref <rq-id>`."
        )
        lines.append("  3. After planning clears, run `loom agent next` again.")
        return lines
    if paused_count or reviewing_count:
        lines.append("  1. No executable task is ready for this worker right now.")
        lines.append("  2. A human-side queue is blocking progress; surface concrete context to the manager if needed.")
        lines.append("  3. Re-run `loom agent next` after the queue changes or a new assignment arrives.")
        return lines
    lines.append("  1. Check `loom agent mailbox` for pending manager handoffs.")
    lines.append("  2. If you want more work, proactively ask to claim a thread or task.")
    lines.append("     Example: `loom agent ask manager 'Can I take thread <thread> or task <task-id>?'`")
    lines.append(
        "  3. If you already know the work, propose the handoff yourself with "
        "`loom agent propose manager '<thread/task handoff>' --ref <thread-or-task-id>`."
    )
    return lines


def _manager_unblock_steps(*, paused_count: int, reviewing_count: int, stale_threads: list[str]) -> list[str]:
    lines = ["Manager next steps:"]
    step = 1
    if stale_threads:
        lines.append(
            "  "
            f"{step}. Reassign or refresh stale owned threads: {', '.join(stale_threads)} "
            f"via {manager_assign_command()}."
        )
        step += 1
    if paused_count:
        lines.append(
            f"  {step}. A paused task needs a human decision; coordinate through `loom review` or plain `loom`."
        )
        step += 1
    if reviewing_count:
        lines.append(f"  {step}. Review work is waiting; alert the reviewer/human to clear `loom review`.")
        step += 1
    lines.append(f"  {step}. Re-run `loom agent next --role manager` after the blocker changes.")
    lines.append("")
    return lines


def _render_director_bootstrap(loom: Path) -> list[str]:
    summary = get_status_summary(loom)
    tasks = summary.get("tasks", {})
    inbox = summary.get("inbox", {})
    queue = summary.get("queue", [])
    paused_count = sum(1 for item in queue if item.get("kind") == "paused")
    reviewing_count = sum(1 for item in queue if item.get("kind") == "reviewing")
    lines = [
        "LOOM DIRECTOR BOOTSTRAP",
        "=======================",
        "",
        "You are the Director. Coordinate the full task flow across manager, reviewer, and worker roles.",
        "",
        "IDENTITY",
        "  role           : director",
        f"  loom dir       : {os.environ.get('LOOM_DIR', '').strip() or str(loom)}",
        "",
        "CURRENT STATE",
        f"  pending inbox : {inbox.get('pending', 0)}",
        f"  ready tasks   : {tasks.get('ready', 0)}",
        f"  paused queue  : {paused_count}",
        f"  review queue  : {reviewing_count}",
        "",
        "BEFORE STARTING",
        "  1. Check `loom --help` and the relevant subcommand help before taking action.",
        "  2. If a command is unclear, read the help text first; do not guess.",
        "",
        "STARTUP",
        "  1. Launch exactly one Manager, exactly one Reviewer, and as many Workers as needed in parallel.",
        (
            "  2. When starting a Worker, prefer resuming from an existing checkpoint "
            "under a new agent identity instead of starting from scratch."
        ),
        "",
        "DURING THE ROUND",
        "  1. Continuously inspect agent state and coordinate dependencies, blockers, and handoffs.",
        "  2. Start `loom manage` when planning, assignment, or request -> thread/task work is needed.",
        "  3. Start `loom review` when review or human queue triage is needed.",
        "  4. Use `loom spawn` from the director/human surface when an executor needs waking.",
        "  5. Summarize important progress and decisions in the Director log.",
        "  6. Record and surface bugs or missing capability immediately.",
        "     If Loom itself lacks the needed capability, capture it locally and carry it into the next round.",
        "",
        "ROUND CHECK",
        "  1. Review this round's output, leftovers, and issue list.",
        "  2. Plan the next round's goals and task breakdown.",
        "  3. Restart the required agents, again preferring checkpoint recovery where possible.",
        "  4. If the Director still has work to do directly, start it immediately.",
        "",
        "MAIN LOOP",
        "  1. Run: loom agent next --role director",
        "  2. Follow the returned ACTION and complete the suggested orchestration step.",
        "  3. After each orchestration step or state change, run",
        "     `loom agent next --role director` again.",
        "",
        "DIRECTOR ACTIONS",
        "  ACTION  bootstrap",
        "    Bring up the round: one manager, one reviewer, and the needed workers.",
        "",
        "  ACTION  wake",
        "    Execution is ready; wake the right worker set or start `loom spawn` from the director surface.",
        "",
        "  ACTION  coordinate",
        "    Planning, assignment, or stale ownership needs manager-side coordination.",
        "",
        "  ACTION  review",
        "    Human/reviewer queue work is now the bottleneck; move it through `loom review` or plain `loom`.",
        "",
        "  ACTION  wait",
        "    No immediate orchestration action is waiting; keep monitoring state.",
        "",
        "GUARDRAILS",
        "  - Do not silently collapse into manager, reviewer, or worker behavior.",
        "  - Keep orchestration explicit and preserve `.loom/` as the only runtime source of truth.",
        "  - Read agent state, but do not create or own a director AgentStatus lifecycle.",
        (
            "  - Workers update their own AgentStatus via `loom agent checkpoint`; "
            "manager tracking stays on `agents/manager/_agent.md`."
        ),
    ]
    return lines


@app.command("start")
def start(
    role: StartRoleOption = None,
) -> None:
    """Print bootstrap guidance for the requested role."""
    loom = _resolve_loom()
    resolved_role = _resolve_start_role(role)
    if resolved_role == AgentRole.MANAGER:
        _require_manager_context("start")
        typer.echo("\n".join(_render_manager_bootstrap(loom)))
        return
    if resolved_role == AgentRole.WORKER:
        typer.echo("\n".join(_render_worker_bootstrap(loom)))
        return
    if resolved_role == AgentRole.REVIEWER:
        typer.echo("\n".join(_render_reviewer_bootstrap(loom)))
        return
    typer.echo("\n".join(_render_director_bootstrap(loom)))


@app.command("done")
def done(
    task_id: str = typer.Argument(..., help="Task ID to mark done."),
    output: Annotated[
        str,
        typer.Option(
            "--output",
            help=(
                "Output path or link. Relative local paths are stored under .loom/products/; "
                "legacy worker outputs are rewritten into .loom/products/reports/."
            ),
        ),
    ] = "",
    ready: Annotated[
        bool,
        typer.Option(
            "--ready",
            "--review-ready",
            help="Declare the task explicitly ready for review, bypassing TODO/proposal heuristics.",
        ),
    ] = False,
    summary: Annotated[
        str,
        typer.Option(
            "--summary",
            help="Short delivery summary recorded in the task delivery contract.",
        ),
    ] = "",
    pr_url: Annotated[
        list[str] | None,
        typer.Option(
            "--pr-url",
            "--pr",
            help="GitHub PR URL to record as a delivery artifact (repeatable).",
        ),
    ] = None,
    artifact: Annotated[
        list[str] | None,
        typer.Option(
            "--artifact",
            help=(
                "Structured delivery artifact reference. Repeatable; relative local paths "
                "are normalized under .loom/products/."
            ),
        ),
    ] = None,
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Mark a task as reviewing when it is ready for human review."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("done", role=role)
    _touch_if_agent(loom, actor)
    config_root = _settings_root_for_actor(loom, actor)
    settings = load_settings(config_root)
    before_hook_lines = render_hook_phase_lines(
        settings, actor, config_root=config_root, point="done", when="before", leading_blank=False
    )
    if before_hook_lines:
        typer.echo("\n".join(before_hook_lines))

    pr_list = pr_url or []
    artifact_list = artifact or []
    delivery: DeliveryContract | None = None
    if ready or pr_list or artifact_list or summary:
        delivery = DeliveryContract(
            ready=ready,
            summary=summary or None,
            pr_urls=pr_list,
            artifacts=artifact_list,
        )

    try:
        _, task, blockers = complete_task(loom, task_id, output=output or None, delivery=delivery)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        _emit_error(str(exc))
        raise  # unreachable

    lines = [f"DONE task {task.id}", f"  status : {task.status.value}"]
    if task.output:
        lines.append(f"  output : {task.output}")
    if task.delivery is not None:
        if task.delivery.summary:
            lines.append(f"  delivery_summary: {task.delivery.summary}")
        if task.delivery.artifacts:
            lines.append(f"  artifacts : {', '.join(task.delivery.artifacts)}")
        if task.delivery.pr_urls:
            lines.append(f"  pr_urls : {', '.join(task.delivery.pr_urls)}")
    if blockers:
        lines.append(f"  blocked: {', '.join(blockers)}")
        lines.append("  Waiting for human decision. Run: loom")
    elif task.persistent and task.status == TaskStatus.SCHEDULED:
        lines.append("  persistent : remains scheduled for future sessions")
        lines.append("  No human review was queued.")
    else:
        lines.append("  Waiting for human review. Run: loom review")
    after_hook_lines = render_hook_phase_lines(settings, actor, config_root=config_root, point="done", when="after")
    typer.echo("\n".join([*lines, *after_hook_lines]))


@app.command("pause")
def pause(
    task_id: str = typer.Argument(..., help="Task ID to pause."),
    question: Annotated[str, typer.Option("--question", help="Decision question.")] = "",
    options: Annotated[str, typer.Option("--options", help="JSON array of {id, label, note} options.")] = "",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Pause a task with a decision question."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor_for_command("pause", role=role))

    if not question:
        _emit_error("--question is required to pause a task", code="missing_question")

    try:
        parsed_options = json.loads(options) if options else []
        _, task = pause_task(loom, task_id, question=question, options=parsed_options)
    except (FileNotFoundError, ValueError, InvalidTransitionError, json.JSONDecodeError) as exc:
        _emit_error(str(exc))
        raise  # unreachable

    lines = [
        f"PAUSED task {task.id}",
        f"  status   : {task.status.value}",
        f"  question : {question}",
        "  Waiting for human decision. Run: loom review decide <id> <choice>",
    ]
    typer.echo("\n".join(lines))


@app.command("status")
def agent_status() -> None:
    """Describe current project state."""
    loom = _resolve_loom()
    summary = get_status_summary(loom)
    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    settings = _load_settings_for_actor(loom, worker_id)

    tasks = summary.get("tasks", {})
    by_status = tasks.get("by_status", {})
    inbox = summary.get("inbox", {})
    agents_list = summary.get("agents", [])
    queue = summary.get("queue", [])
    ready_ids = tasks.get("ready_ids", [])
    capabilities = summary.get("capabilities", [])
    worktree_issues = summary.get("worktree_issues", {})

    ready_task_map = {task.id: task for task in get_next_tasks(loom, limit=max(len(ready_ids), 50))}

    lines = [
        "PROJECT STATUS",
        "==============",
        "",
        "TASKS",
        f"  total     : {tasks.get('total', 0)}",
        f"  ready     : {tasks.get('ready', 0)}",
    ]
    for status_name, count in by_status.items():
        if count:
            lines.append(f"  {status_name:<10}: {count}")

    if ready_ids:
        lines += ["", "READY TASKS"]
        for task_id in ready_ids:
            task = ready_task_map.get(task_id)
            if task is None:
                continue
            lines.extend(_format_task_block(loom, task))

    lines += [
        "",
        "INBOX",
        f"  pending   : {inbox.get('pending', 0)}",
        f"  planned   : {inbox.get('by_status', {}).get('planned', 0)}",
    ]

    if agents_list:
        lines += ["", "AGENTS"]
        for agent in agents_list:
            agent_id = agent.get("id", "?")
            status_val = agent.get("status", "unknown")
            summary_val = agent.get("checkpoint_summary", "")
            last_seen = agent.get("last_seen")
            age_text = _format_minutes_ago(last_seen)
            is_offline = isinstance(last_seen, str) and _agent_is_offline(
                last_seen,
                offline_after_minutes=settings.agent.offline_after_minutes,
            )

            line = f"  {agent_id:<12} {status_val}  last_seen:{age_text}"
            if summary_val:
                line += f"  — {summary_val}"
            pending_messages = int(agent.get("pending_messages", 0) or 0)
            replied_messages = int(agent.get("replied_messages", 0) or 0)
            line += f"  mailbox:{pending_messages} pending / {replied_messages} replied"
            if is_offline:
                line += "  WARNING: appears offline"
            lines.append(line)

    owned_threads = summary.get("owned_threads", {})
    if owned_threads:
        lines += ["", "OWNED THREADS"]
        for thread_name, details in sorted(owned_threads.items()):
            owner = details.get("owner", "?")
            state = "stale" if details.get("stale") else "fresh"
            claimed_at = details.get("owned_at") or "unknown"
            lease_expires_at = details.get("lease_expires_at") or "unknown"
            lines.append(
                f" {thread_name:<20} owner:{owner} state:{state} owned_at:{claimed_at} lease_expires:{lease_expires_at}"
            )

    if worker_id:
        lines += ["", "CURRENT WORKER CONTEXT", *_current_worker_context_lines(loom, worker_id)]

    if worktree_issues:
        lines += ["", "WORKTREE ISSUES"]
        for thread_name, issues in sorted(worktree_issues.items()):
            lines.append(f"  {thread_name}")
            for issue in issues:
                lines.append(f"    - {issue}")

    if queue:
        lines += ["", "QUEUE (needs attention)"]
        for item in queue:
            kind = item.get("kind", "?")
            item_id = item.get("id", "?")
            lines.append(f"  [{kind}] {item_id}")

    if capabilities:
        lines += ["", "CAPABILITIES"]
        for capability in capabilities:
            line = f"  {capability['thread']:<20} {capability['phase']}"
            latest = capability.get("latest_completed")
            if isinstance(latest, dict):
                line += f"  latest:{latest.get('id')} [{latest.get('kind')} {latest.get('status')}]"
            lines.append(line)
            follow_up = capability.get("implementation_follow_up")
            if isinstance(follow_up, dict):
                lines.append(f"    implementation follow-up: {follow_up.get('id')} [{follow_up.get('status')}]")
            latest_pr = capability.get("latest_pr")
            if isinstance(latest_pr, dict):
                pr_line = f"    latest pr: {latest_pr.get('url')}"
                if latest_pr.get("branch"):
                    pr_line += f" [{latest_pr.get('branch')}]"
                lines.append(pr_line)

    typer.echo("\n".join(lines))


def spawn_worker_runtime(
    threads: str = "",
    *,
    force: bool = False,
) -> None:
    """Register a new worker agent from the top-level `loom spawn` entrypoint."""
    _require_manager_context("spawn")
    loom = _resolve_loom()
    settings = load_settings(workspace_root(loom))
    _enforce_spawn_limits(loom=loom, settings=settings, force=force)
    payload = spawn_agent(loom, threads=[item.strip() for item in threads.split(",") if item.strip()])
    env_path = payload.get("env", "")
    agent_id = str(payload["id"])
    raw_threads = payload.get("threads", [])
    thread_list = [str(item) for item in raw_threads] if isinstance(raw_threads, list) else []
    thread_text = ", ".join(thread_list) if thread_list else "(unassigned)"
    lines = [
        f"SPAWNED agent {agent_id}",
        f"  env file : {env_path}",
        f"  threads  : {thread_text}",
        "",
        "Worker environment file",
        f"  {env_path}",
        "",
        "Default launch pattern",
        "  Prefer passing environment variables from the outside when starting the worker.",
        "  Read the env file and inject those values into the child-process launch.",
        "  Required variables are usually:",
        f"    LOOM_WORKER_ID={agent_id}",
        f"    LOOM_DIR={loom}",
        (f"    LOOM_THREADS={','.join(thread_list)}" if thread_list else "    LOOM_THREADS=<optional>"),
        "",
    ]

    executor_command = settings.agent.executor_command.strip()
    if executor_command:
        rendered = _format_executor_command(
            executor_command,
            agent_id=agent_id,
            loom_dir=loom,
            threads=thread_list,
            env_path=str(env_path),
        )
        env_prefix = f"LOOM_WORKER_ID={agent_id} LOOM_DIR={loom}"
        if thread_list:
            env_prefix += f" LOOM_THREADS={','.join(thread_list)}"
        lines += [
            "Configured worker command",
            "  [agent].executor_command is set in loom.toml.",
            "  You can launch the worker with either style:",
            f"    source {env_path} && {rendered}",
            f"    {env_prefix} {rendered}",
            "",
        ]
    else:
        lines += [
            "No worker command is configured in loom.toml.",
            "  If you want spawn to print a ready-to-run command, set:",
            "    [agent]",
            '    executor_command = "your launcher command"',
            "  Supported placeholders: {agent_id} {loom_dir} {threads} {env_file}",
            "",
        ]

    lines += [
        "If your subagent runtime cannot set environment variables at all:",
        "  do not use loom spawn for that runtime.",
        "  Either launch the agent from a wrapper process that can inject env vars,",
        "  or use a runtime that supports per-process environment configuration.",
    ]
    typer.echo("\n".join(lines))


@app.command("spawn", hidden=True)
def spawn(
    threads: Annotated[str, typer.Option("--threads", help="Comma-separated thread assignment.")] = "",
    force: Annotated[bool, typer.Option("--force", help="Override worker-count safety limits.")] = False,
) -> None:
    """Legacy entrypoint kept only to print migration guidance."""
    if threads and force:
        suggestion = f"loom spawn --threads {threads} --force"
    elif threads:
        suggestion = f"loom spawn --threads {threads}"
    elif force:
        suggestion = "loom spawn --force"
    else:
        suggestion = manager_spawn_command()
    _emit_error(
        f"`loom agent spawn` moved to `{suggestion}`. Run `{suggestion}` instead.",
        code="moved_command",
    )


@app.command("plan", hidden=True)
def plan(
    rq_id: Annotated[str, typer.Argument(help="Request id to plan.")],
) -> None:
    """Legacy entrypoint kept only to print migration guidance."""
    suggestion = f"loom manage plan {rq_id}"
    _emit_error(
        f"`loom agent plan` moved to `{suggestion}`. Run `{suggestion}` instead.",
        code="moved_command",
    )


@app.command("whoami")
def whoami(role: WorkerRoleOption = AgentRole.WORKER) -> None:
    """Show the current actor identity."""
    actor = _resolve_actor_for_command("whoami", role=role)
    resolved_role = role.value if actor in _SINGLETON_ACTORS else AgentRole.WORKER.value
    lines = ["IDENTITY", f"  id   : {actor}", f"  role : {resolved_role}"]
    if actor not in _SINGLETON_ACTORS:
        lines.extend(_current_worker_context_lines(_resolve_loom(), actor))
    typer.echo("\n".join(lines))


@app.command("checkpoint")
def checkpoint(
    summary: str = typer.Argument(..., help="Checkpoint summary."),
    phase: Annotated[str, typer.Option("--phase", help="Current phase.")] = "implementing",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Update the current agent checkpoint."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("checkpoint", role=role)
    if actor == AgentRole.MANAGER.value:
        record = update_manager_checkpoint(loom, phase=phase, summary=summary)
        typer.echo(
            f"CHECKPOINT recorded\n  agent : {actor}\n  phase : {phase}\n  summary : {record.checkpoint_summary}"
        )
        return
    if actor in _SINGLETON_ACTORS:
        _emit_error(f"{actor} checkpoint updates are not implemented via this command.", code="not_supported")
    record = update_checkpoint(loom, actor, phase=phase, summary=summary)
    typer.echo(
        f"CHECKPOINT recorded\n  agent : {record.id}\n  phase : {phase}\n  summary : {record.checkpoint_summary}"
    )


@app.command("resume")
def resume(role: WorkerRoleOption = AgentRole.WORKER) -> None:
    """Show the current agent checkpoint body."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("resume", role=role)
    if actor == AgentRole.MANAGER.value:
        record = resume_manager(loom)
        typer.echo(f"CHECKPOINT body for {actor}\n\n{record.body}")
        return
    if actor in _SINGLETON_ACTORS:
        _emit_error(f"{actor} resume is not implemented via this command.", code="not_supported")
    record = resume_agent(loom, actor)
    typer.echo(f"CHECKPOINT body for {record.id}\n\n{record.body}")


def _render_mailbox(actor: str) -> None:
    loom = _resolve_loom()
    messages = list_pending_messages(loom, actor)

    if not messages:
        typer.echo(f"MAILBOX {actor}\n  No pending messages.")
        return

    lines = [f"MAILBOX {actor}", f"  count : {len(messages)}", ""]
    for msg in messages:
        ref_part = f"  ref:{msg.ref}" if msg.ref else ""
        lines.append(f"  {msg.id}  type:{msg.type.value}  from:{msg.from_}{ref_part}")
    lines += ["", "To read a message: loom agent mailbox-read <msg-id> (legacy: `loom agent inbox-read`)"]
    typer.echo("\n".join(lines))


@app.command("mailbox")
def mailbox(role: WorkerRoleOption = AgentRole.WORKER) -> None:
    """List pending messages for the current agent mailbox."""
    actor = _resolve_actor_for_command("mailbox", role=role)
    _render_mailbox(actor)


@app.command("inbox", hidden=True)
def inbox(role: WorkerRoleOption = AgentRole.WORKER) -> None:
    """Compatibility alias for `loom agent mailbox`."""
    actor = _resolve_actor_for_command("inbox", role=role)
    _render_mailbox(actor)


def _read_mailbox_message(actor: str, msg_id: str) -> None:
    loom = _resolve_loom()
    pending_dir = agent_pending_dir(loom, actor)
    try:
        _, message = load_message(pending_dir, msg_id)
    except FileNotFoundError as exc:
        _emit_error(str(exc))
        raise  # unreachable

    lines = [
        f"MESSAGE {message.id}",
        f"  from : {message.from_}",
        f"  to   : {message.to}",
        f"  type : {message.type.value}",
        f"  sent : {message.sent}",
    ]
    if message.ref:
        lines.append(f"  ref  : {message.ref}")
    lines += ["", message.body]
    typer.echo("\n".join(lines))


@app.command("mailbox-read")
def mailbox_read(
    msg_id: str = typer.Argument(..., help="Message ID to read (e.g. MSG-001)."),
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Show mailbox message content without moving it."""
    actor = _resolve_actor_for_command("mailbox-read", role=role)
    _read_mailbox_message(actor, msg_id)


@app.command("inbox-read", hidden=True)
def inbox_read(
    msg_id: str = typer.Argument(..., help="Message ID to read (e.g. MSG-001)."),
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Compatibility alias for `loom agent mailbox-read`."""
    actor = _resolve_actor_for_command("inbox-read", role=role)
    _read_mailbox_message(actor, msg_id)


@app.command("send")
def send(
    to: str = typer.Argument(..., help="Recipient agent id."),
    body: str = typer.Argument(..., help="Message body."),
    type_: Annotated[str, typer.Option("--type", help="Message type.")] = "info",
    ref: Annotated[str, typer.Option("--ref", help="Optional related entity id.")] = "",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Send a message to another agent."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("send", role=role)
    message = create_message(
        loom,
        sender=actor,
        recipient=to,
        message_type=MessageType(type_),
        body=body,
        ref=ref or None,
    )
    lines = [
        f"SENT message {message['id']}",
        f"  from : {actor}",
        f"  to   : {to}",
        f"  type : {type_}",
    ]
    if ref:
        lines.append(f"  ref  : {ref}")
    typer.echo("\n".join(lines))


@app.command("ask")
def ask(
    to: str = typer.Argument(..., help="Recipient agent id (or 'manager' / 'human')."),
    question: str = typer.Argument(..., help="The question to ask."),
    ref: Annotated[str, typer.Option("--ref", help="Optional related task/entity id.")] = "",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Shorthand for send --type question."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("ask", role=role)
    message = create_message(
        loom,
        sender=actor,
        recipient=to,
        message_type=MessageType.QUESTION,
        body=question,
        ref=ref or None,
    )
    lines = [
        f"SENT question {message['id']}",
        f"  from : {actor}",
        f"  to   : {to}",
        f"  body : {question}",
    ]
    if ref:
        lines.append(f"  ref  : {ref}")
    typer.echo("\n".join(lines))


@app.command("propose")
def propose(
    to: str = typer.Argument(..., help="Recipient agent id (or 'manager' / 'human')."),
    proposal: str = typer.Argument(..., help="The task proposal body."),
    thread: Annotated[str, typer.Option("--thread", help="Optional related thread name.")] = "",
    ref: Annotated[str, typer.Option("--ref", help="Optional related entity id.")] = "",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Shorthand for send --type task_proposal."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("propose", role=role)
    ref_value = ref or thread or None
    message = create_message(
        loom,
        sender=actor,
        recipient=to,
        message_type=MessageType.TASK_PROPOSAL,
        body=proposal,
        ref=ref_value,
    )
    lines = [
        f"SENT proposal {message['id']}",
        f"  from : {actor}",
        f"  to   : {to}",
        f"  body : {proposal}",
    ]
    if ref_value:
        lines.append(f"  ref  : {ref_value}")
    typer.echo("\n".join(lines))


@app.command("reply")
def reply(
    msg_id: str = typer.Argument(..., help="Pending message id."),
    body: str = typer.Argument(..., help="Reply body."),
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Reply to a pending message and move it to replied."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("reply", role=role)
    payload = reply_to_message(loom, actor, msg_id, body)
    reply_id = payload.get("reply_id", payload.get("id", ""))
    typer.echo(f"REPLIED to {msg_id}\n  reply id : {reply_id}")


def find_task(loom: Path, task_id: str) -> tuple[Path, Task]:
    """Compatibility wrapper used by the human CLI."""
    try:
        return load_task(loom, task_id)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
