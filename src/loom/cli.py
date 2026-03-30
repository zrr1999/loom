"""loom CLI — the human interface."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from loguru import logger

from .agent import app as agent_app
from .agent import spawn_worker_runtime
from .agent import start as agent_start
from .config import ensure_hook_registry, ensure_settings
from .history import read_events
from .migration import (
    ensure_manager_agent_subtree,
    ensure_name_based_threads,
    ensure_request_storage,
    ensure_routine_storage,
    ensure_thread_ownership_metadata,
    ensure_thread_worktree_metadata,
    ensure_worker_agent_subtree,
)
from .models import AgentRole, Decision, RequestItem, RequestStatus, RoutineStatus, Task, TaskKind, TaskStatus, Thread
from .prompting import select, text
from .repository import load_inbox_item, load_routine, load_task, requests_dir, require_loom, root_config_path
from .runtime import global_root, set_root
from .scheduler import (
    get_due_routines,
    get_interaction_queue,
    get_pending_inbox_items,
    get_status_summary,
    load_all_routines,
    load_all_tasks,
    load_all_threads,
    sort_key,
)
from .services import (
    AmbiguousRequestRoutingError,
    accept_task,
    adjust_task_priority,
    adjust_thread_priority,
    assign_thread,
    create_or_merge_task,
    create_request_item,
    create_thread,
    decide_task,
    ensure_agent_layout,
    extract_routine_log,
    format_review_summary,
    plan_inbox_item,
    reject_task,
    release_claim,
    release_thread,
    set_routine_status,
    trigger_routine,
)
from .state import InvalidTransitionError

logger.remove()
logger.add(sys.stderr, level="WARNING")

app = typer.Typer(
    name="loom",
    help="A CLI tool where humans weave requirements and agents execute tasks.",
)
app.add_typer(agent_app, name="agent", help="Agent commands (machine-friendly).")
request_app = typer.Typer(help="Request commands.")
app.add_typer(request_app, name="request")
inbox_app = typer.Typer(help="Inbox commands.")
app.add_typer(inbox_app, name="inbox")
routine_app = typer.Typer(help="Routine commands.")
app.add_typer(routine_app, name="routine")
manage_app = typer.Typer(help="Manager commands.", invoke_without_command=True)
app.add_typer(manage_app, name="manage")
review_app = typer.Typer(help="Review and approval commands.", invoke_without_command=True)
app.add_typer(review_app, name="review")


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
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _require_non_worker_review_context() -> None:
    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    if not worker_id:
        return
    typer.echo(
        (
            "ERROR [worker_not_allowed]: loom review is reviewer/human-only. "
            f"LOOM_WORKER_ID={worker_id!r} is set, so this process is running as a worker. "
            "Finish runtime work with `loom agent done <task-id>` or "
            "`loom agent pause <task-id> --question '...'`, then switch to a clean reviewer "
            "or human process without `LOOM_WORKER_ID` and use `loom agent start --role reviewer`, "
            "`loom review`, and `loom review accept <task-id>` / "
            "`loom review reject <task-id> '<reason>'` as needed."
        ),
        err=True,
    )
    raise typer.Exit(1)


def _require_non_worker_manage_context(command_name: str) -> None:
    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    if not worker_id:
        return
    typer.echo(
        (
            f"ERROR [worker_not_allowed]: loom {command_name} is manager-only. "
            f"LOOM_WORKER_ID={worker_id!r} is set, so this process is running as a worker. "
            "Finish runtime work with `loom agent done <task-id>` or "
            "`loom agent pause <task-id> --question '...'`, then switch to a clean manager "
            "process without `LOOM_WORKER_ID` and use `loom manage` / `loom manage priority` there."
        ),
        err=True,
    )
    raise typer.Exit(1)


def _format_thread_priority_line(thread: Thread) -> str:
    owner = thread.owner or "-"
    return f"  {thread.name:<24} priority={thread.priority:<3} owner={owner}"


def _format_task_priority_line(task: Task) -> str:
    return (
        f"  {task.id:<24} priority={task.priority:<3} "
        f"status={task.status.value:<10} thread={task.thread:<20} {task.title}"
    )


def _sorted_tasks_for_priority_view(tasks: list[Task], threads: dict[str, Thread]) -> list[Task]:
    return sorted(tasks, key=lambda task: sort_key(task, threads))


@app.command()
def init(
    project: str = typer.Option("", help="Project name."),
    global_mode: bool = typer.Option(False, "-g", help="Use the home-level loom directory."),
) -> None:
    """Initialize .loom/ and ensure root config files exist."""
    set_root(global_root() if global_mode else None)
    root = global_root() if global_mode else Path.cwd()
    loom = root / ".loom"
    loom.mkdir(exist_ok=True)
    (loom / "threads").mkdir(exist_ok=True)
    ensure_request_storage(loom)
    ensure_routine_storage(loom)
    ensure_manager_agent_subtree(loom)
    ensure_agent_layout(loom)

    project_name = project or root.name
    _, created_settings = ensure_settings(root, project_name)
    _, created_registry = ensure_hook_registry(root)
    config_action = "Created" if created_settings else "Using existing"
    registry_action = "created" if created_registry else "using existing"
    typer.echo(
        f"{config_action} {root_config_path(loom).name}, {registry_action} loom-hooks.toml, "
        f"and ensured .loom/ structure for '{project_name}'."
    )


def _add_request(description: str) -> None:
    loom = _resolve_loom()
    try:
        item, path = create_request_item(loom, description)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Created {item.id}: {path}")


@request_app.command("add")
def request_add(
    description: str = typer.Argument(..., help="Requirement description."),
) -> None:
    """Add a new request."""
    _add_request(description)


@inbox_app.command("add")
def inbox_add(
    description: str = typer.Argument(..., help="Requirement description."),
) -> None:
    """Compatibility alias for `loom request add`."""
    _add_request(description)


def _format_request_line(item: RequestItem) -> list[str]:
    first_line = item.body.splitlines()[0] if item.body else item.id
    lines = [f"{item.id}  {item.status.value}  {first_line}"]
    if item.status == RequestStatus.DONE and item.resolved_as is not None:
        targets = ", ".join(item.resolved_to) if item.resolved_to else "-"
        lines.append(f"  resolved_as   : {item.resolved_as.value}")
        lines.append(f"  resolved_to   : {targets}")
        if item.resolution_note:
            lines.append(f"  note          : {item.resolution_note}")
    return lines


def _list_requests(*, pending_only: bool) -> None:
    loom = _resolve_loom()
    request_root = requests_dir(loom)
    items = get_pending_inbox_items(loom) if pending_only else None
    if pending_only:
        if not items:
            typer.echo("No pending requests.")
            return
        for item in items:
            typer.echo(f"{item['id']}  {item['status']}  {item['title']}")
            typer.echo(f"  file          : {item['file']}")
        return

    request_items = []
    for path in sorted(request_root.glob("RQ-*.md")):
        request_items.append(load_inbox_item(loom, path.stem)[1])
    if not request_items:
        typer.echo("No requests.")
        return
    for item in request_items:
        for line in _format_request_line(item):
            typer.echo(line)
        typer.echo(f"  file          : {request_root / f'{item.id}.md'}")


@request_app.command("ls")
@request_app.command("list")
def request_list(
    pending: bool = typer.Option(False, "--pending", help="Show only pending requests."),
) -> None:
    """List requests and their resolution state."""
    _list_requests(pending_only=pending)


@inbox_app.command("ls")
@inbox_app.command("list")
def inbox_list(
    pending: bool = typer.Option(False, "--pending", help="Show only pending requests."),
) -> None:
    """Compatibility alias for `loom request ls`."""
    _list_requests(pending_only=pending)


def _format_routine_due_phrase(routine_summary: dict[str, Any]) -> str:
    next_due = cast("dict[str, str] | None", routine_summary.get("next_due"))
    if not next_due:
        return "none due"
    if next_due["when"] == "now":
        return f"next due now ({next_due['id']})"
    return f"next due in {next_due['when']} ({next_due['id']})"


@routine_app.command("ls")
@routine_app.command("list")
def routine_list() -> None:
    """List routines and their due/run status."""
    loom = _resolve_loom()
    routines = load_all_routines(loom)
    if not routines:
        typer.echo("No routines.")
        return

    due_ids = {routine.id for routine in get_due_routines(loom, limit=0)}
    for routine in routines:
        typer.echo(f"{routine.id}  {routine.status.value}  {routine.title}")
        typer.echo(f"  interval      : {routine.interval}")
        typer.echo(f"  assigned_to   : {routine.assigned_to or '-'}")
        typer.echo(f"  due           : {'now' if routine.id in due_ids else '-'}")
        typer.echo(f"  last_run      : {routine.last_run or '-'}")
        typer.echo(f"  last_result   : {routine.last_result.value if routine.last_result else '-'}")
        typer.echo(f"  file          : {loom / 'routines' / f'{routine.id}.md'}")


@routine_app.command("pause")
def routine_pause(routine_id: str) -> None:
    """Pause an active routine."""
    _require_non_worker_manage_context("routine pause")
    loom = _resolve_loom()
    try:
        path, routine = set_routine_status(loom, routine_id, target_status=RoutineStatus.PAUSED)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"PAUSED routine {routine.id}\n  status        : {routine.status.value}\n  file          : {path}")


@routine_app.command("resume")
def routine_resume(routine_id: str) -> None:
    """Resume a paused or disabled routine."""
    _require_non_worker_manage_context("routine resume")
    loom = _resolve_loom()
    try:
        path, routine = set_routine_status(loom, routine_id, target_status=RoutineStatus.ACTIVE)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"RESUMED routine {routine.id}\n  status        : {routine.status.value}\n  file          : {path}")


@routine_app.command("run")
def routine_run(routine_id: str) -> None:
    """Force-trigger a routine through the routine_trigger message path."""
    _require_non_worker_manage_context("routine run")
    loom = _resolve_loom()
    try:
        result = trigger_routine(loom, routine_id, forced=True)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    routine = cast("Any", result["routine"])
    message = cast("dict[str, Any]", result["message"])
    typer.echo(
        "\n".join(
            [
                f"TRIGGERED routine {routine.id}",
                f"  assigned_to   : {routine.assigned_to or '-'}",
                f"  message       : {message['id']}",
                f"  type          : {message['type']}",
            ]
        )
    )


@routine_app.command("log")
def routine_log(routine_id: str) -> None:
    """Show the append-only run log for a routine."""
    loom = _resolve_loom()
    try:
        _path, routine = load_routine(loom, routine_id)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"RUN LOG {routine.id}")
    log_text = extract_routine_log(routine.body)
    if not log_text or log_text == "<!-- append-only notes -->":
        typer.echo("  (empty)")
        return
    for line in log_text.splitlines():
        typer.echo(f"  {line}")


@app.command()
def status() -> None:
    """Show project progress overview."""
    loom = _resolve_loom()
    summary: dict[str, Any] = get_status_summary(loom)

    typer.echo(f"Config:  {root_config_path(loom)}")
    typer.echo(f"Threads: {summary['threads']}")
    tasks = cast("dict[str, Any]", summary["tasks"])
    typer.echo(f"Tasks:   {tasks['total']} total, {tasks['ready']} ready")
    for status_name, count in sorted(cast("dict[str, int]", tasks["by_status"]).items()):
        typer.echo(f"  {status_name}: {count}")

    inbox = cast("dict[str, Any]", summary["inbox"])
    typer.echo(f"Inbox:   {inbox['pending']} pending / {inbox['total']} total")
    for status_name, count in sorted(cast("dict[str, int]", inbox["by_status"]).items()):
        typer.echo(f"  inbox.{status_name}: {count}")

    routines = cast("dict[str, Any]", summary["routines"])
    routine_statuses = cast("dict[str, int]", routines["by_status"])
    typer.echo(
        "Routines: "
        f"{routine_statuses.get(RoutineStatus.ACTIVE.value, 0)} active · "
        f"{routine_statuses.get(RoutineStatus.PAUSED.value, 0)} paused · "
        f"{routine_statuses.get(RoutineStatus.DISABLED.value, 0)} disabled · "
        f"{_format_routine_due_phrase(routines)}"
    )

    queue = cast("list[dict[str, Any]]", summary["queue"])
    if queue:
        typer.echo("Queue:")
        for item in queue:
            typer.echo(f"  {item['kind']}: {item['id']} - {item['title']}")

    capabilities = cast("list[dict[str, Any]]", summary.get("capabilities", []))
    if capabilities:
        typer.echo("Capabilities:")
        for capability in capabilities:
            line = f"  {capability['thread']}: {capability['phase']}"
            latest = capability.get("latest_completed")
            if isinstance(latest, dict):
                line += f" (latest {latest['id']} [{latest['kind']} {latest['status']}])"
            typer.echo(line)
            follow_up = capability.get("implementation_follow_up")
            if isinstance(follow_up, dict):
                typer.echo(f"    implementation follow-up: {follow_up['id']} [{follow_up['status']}]")
            latest_pr = capability.get("latest_pr")
            if isinstance(latest_pr, dict):
                pr_line = f"    latest pr: {latest_pr['url']}"
                if latest_pr.get("branch"):
                    pr_line += f" [{latest_pr['branch']}]"
                typer.echo(pr_line)

    worktree_issues = cast("dict[str, list[str]]", summary.get("worktree_issues", {}))
    if worktree_issues:
        typer.echo("Worktree issues:")
        for thread_name, issues in sorted(worktree_issues.items()):
            typer.echo(f"  {thread_name}:")
            for issue in issues:
                typer.echo(f"    - {issue}")


@manage_app.callback()
def manage_main(ctx: typer.Context) -> None:
    """Open the manager bootstrap guide when no subcommand is provided."""
    if ctx.invoked_subcommand is not None:
        _require_non_worker_manage_context(f"manage {ctx.invoked_subcommand}")
        return
    agent_start(role=AgentRole.MANAGER)


@manage_app.command("priority")
def manage_priority(
    task_id: str = typer.Option("", "--task", help="Task id to inspect or update."),
    thread_name: str = typer.Option("", "--thread", help="Thread name to inspect or update."),
    set_to: int | None = typer.Option(None, "--set", min=0, max=100, help="Priority value to persist (0-100)."),
) -> None:
    """List and adjust task or thread priorities from the manager CLI."""
    _require_non_worker_manage_context("manage priority")
    if task_id and thread_name:
        typer.echo("Error: choose either --task or --thread, not both.", err=True)
        raise typer.Exit(1)
    if set_to is not None and not (task_id or thread_name):
        typer.echo("Error: --set requires either --task or --thread.", err=True)
        raise typer.Exit(1)

    loom = _resolve_loom()
    update_lines: list[str] = []
    try:
        if task_id:
            if set_to is not None:
                path, task = adjust_task_priority(loom, task_id, priority=set_to)
                update_lines = [
                    f"Updated task {task.id} priority -> {task.priority}.",
                    f"  file: {path}",
                    "",
                ]
                task_id = task.id
            else:
                _path, task = load_task(loom, task_id)
                task_id = task.id
        elif thread_name and set_to is not None:
            path, thread = adjust_thread_priority(loom, thread_name, priority=set_to)
            update_lines = [
                f"Updated thread {thread.name} priority -> {thread.priority}.",
                f"  file: {path}",
                "",
            ]
            thread_name = thread.name
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    threads = load_all_threads(loom)
    tasks = load_all_tasks(loom)
    if thread_name:
        tasks = [task for task in tasks if task.thread == thread_name]
        threads = {name: thread for name, thread in threads.items() if name == thread_name}
    if task_id:
        tasks = [task for task in tasks if task.id == task_id]

    typer.echo("MANAGE PRIORITY")
    if update_lines:
        typer.echo("\n".join(update_lines).rstrip())

    typer.echo("THREADS")
    if threads:
        for thread in sorted(threads.values(), key=lambda thread: (-thread.priority, thread.name)):
            typer.echo(_format_thread_priority_line(thread))
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("TASKS")
    ordered_tasks = _sorted_tasks_for_priority_view(tasks, load_all_threads(loom))
    if ordered_tasks:
        for task in ordered_tasks:
            typer.echo(_format_task_priority_line(task))
    else:
        typer.echo("  (none)")


@manage_app.command("new-thread")
def manage_new_thread(
    name: str = typer.Option("", help="Thread name."),
    priority: int = typer.Option(50, help="Thread priority."),
) -> None:
    """Create a new thread from the manager CLI."""
    _require_non_worker_manage_context("manage new-thread")
    loom = _resolve_loom()
    try:
        thread, path, duplicates = create_thread(loom, name=name, priority=priority)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    lines = [
        f"CREATED thread {thread.name}",
        f"  priority : {thread.priority}",
        f"  path     : {path.parent}",
    ]
    if duplicates:
        lines.append(f"  WARNING  : thread name '{thread.name}' already used by {', '.join(duplicates)}")
    typer.echo("\n".join(lines))


@manage_app.command("new-task")
def manage_new_task(
    thread: str = typer.Option(..., "--thread", help="Canonical thread name (e.g. backend)."),
    title: str = typer.Option("", help="Task title."),
    kind: Annotated[TaskKind, typer.Option("--kind", help="Task kind.")] = TaskKind.IMPLEMENTATION,
    priority: int = typer.Option(50, help="Task priority."),
    acceptance: str = typer.Option("", help="Acceptance criteria."),
    depends_on: str = typer.Option("", help="Comma-separated dependency IDs."),
    after: str = typer.Option("", "--after", help="Sugar for --depends-on: single task ID this task comes after."),
    created_from: str = typer.Option("", help="Comma-separated source inbox RQ IDs."),
    persistent: bool = typer.Option(False, "--persistent", help="Keep the task scheduled after each completion."),
    background: str = typer.Option("", help="Task background section content."),
    implementation_direction: str = typer.Option("", help="Implementation direction section content."),
) -> None:
    """Create a new task file from the manager CLI."""
    _require_non_worker_manage_context("manage new-task")
    loom = _resolve_loom()
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
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

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


@manage_app.command("plan")
def manage_plan(
    rq_id: str = typer.Argument(..., help="Request id to plan."),
    thread: str = typer.Option("", "--thread", help="Explicit target thread when inference is ambiguous."),
) -> None:
    """Plan a pending request into the next task."""
    _require_non_worker_manage_context("manage plan")
    loom = _resolve_loom()
    try:
        planned = plan_inbox_item(loom, rq_id, thread_name=thread or None)
    except (FileNotFoundError, ValueError, InvalidTransitionError, AmbiguousRequestRoutingError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    lines = [
        f"PLANNED {planned['rq_id']}",
        f"  resolved_as : {planned['resolved_as']}",
        f"  resolved_to : {', '.join(cast('list[str]', planned['resolved_to']))}",
    ]
    resolution_note = cast("str | None", planned.get("resolution_note"))
    if resolution_note:
        lines.append(f"  note        : {resolution_note}")
    created_thread = cast("str | None", planned.get("created_thread"))
    if created_thread:
        lines.append(f"  created_thread : {created_thread}")
    tasks = cast("list[dict[str, str]]", planned.get("tasks", []))
    for task in tasks:
        lines.append(f"  task       : {task['id']} ({task['file']})")
    typer.echo("\n".join(lines))


@manage_app.command("assign")
def manage_assign(
    thread_name: str = typer.Option(..., "--thread", help="Thread name to assign."),
    worker_id: str = typer.Option(..., "--worker", help="Worker id that should own the thread."),
) -> None:
    """Assign or reclaim a thread for a specific worker."""
    _require_non_worker_manage_context("manage assign")
    loom = _resolve_loom()
    try:
        path, thread = assign_thread(loom, thread_name, agent_id=worker_id, note="explicit manager assignment")
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    lines = [
        f"ASSIGNED thread {thread.name}",
        f"  owner  : {thread.owner}",
        f"  file   : {path}",
    ]
    if thread.owner_lease_expires_at:
        lines.append(f"  lease  : {thread.owner_lease_expires_at}")
    typer.echo("\n".join(lines))


def _list_review_queue() -> None:
    loom = _resolve_loom()
    tasks = [task for task in load_all_tasks(loom) if task.status == TaskStatus.REVIEWING]
    if not tasks:
        typer.echo("No tasks in reviewing status.")
        return

    for task in tasks:
        for line in format_review_summary(task, thread=load_all_threads(loom).get(task.thread)):
            typer.echo(line)
        typer.echo('  next: use `loom review accept <id>` or `loom review reject <id> "reason"`')


@review_app.callback()
def review_main(ctx: typer.Context) -> None:
    """List reviewing tasks without entering the interactive approval loop."""
    _require_non_worker_review_context()
    if ctx.invoked_subcommand is not None:
        return
    _list_review_queue()


def _accept_review_task(task_id: str) -> None:
    loom = _resolve_loom()
    try:
        accept_task(loom, task_id)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Accepted {task_id} -> done.")


def _reject_review_task(task_id: str, note: str) -> None:
    loom = _resolve_loom()
    try:
        reject_task(loom, task_id, note)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Rejected {task_id} -> scheduled. Note: {note}")


def _decide_review_task(task_id: str, option: str) -> None:
    loom = _resolve_loom()
    try:
        decide_task(loom, task_id, option)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Decided {task_id} -> scheduled.")


@review_app.command("accept")
def review_accept(task_id: str = typer.Argument(..., help="Task ID to accept.")) -> None:
    """Accept a reviewing task -> done."""
    _require_non_worker_review_context()
    _accept_review_task(task_id)


@review_app.command("reject")
def review_reject(
    task_id: str = typer.Argument(..., help="Task ID to reject."),
    note: str = typer.Argument(..., help="Rejection reason."),
) -> None:
    """Reject a task back to scheduled."""
    _require_non_worker_review_context()
    _reject_review_task(task_id, note)


@review_app.command("decide")
def review_decide(
    task_id: str = typer.Argument(..., help="Task ID to decide."),
    option: str = typer.Argument(..., help="Decision (option id or free text)."),
) -> None:
    """Resolve a paused task's decision -> scheduled."""
    _require_non_worker_review_context()
    _decide_review_task(task_id, option)


@app.command()
def spawn(
    threads: str = typer.Option("", "--threads", help="Comma-separated thread assignment."),
    force: bool = typer.Option(False, "--force", help="Override worker-count safety limits."),
) -> None:
    """Register a new worker agent from the top-level CLI."""
    spawn_worker_runtime(threads=threads, force=force)


@app.command()
def log(limit: int = typer.Option(20, min=1, help="Maximum number of log entries to show.")) -> None:
    """Show state transition history."""
    loom = _resolve_loom()
    events = read_events(loom)
    if not events:
        typer.echo("No history yet.")
        return

    for event in events[-limit:]:
        typer.echo(f"{event['timestamp']} {event['event']} {event['entity_kind']}:{event['entity_id']}")
        details = cast("dict[str, Any]", event.get("details", {}))
        if details:
            detail_text = ", ".join(f"{key}={value}" for key, value in details.items() if value not in (None, "", []))
            if detail_text:
                typer.echo(f"  {detail_text}")


@app.command(hidden=True)
def accept(task_id: str = typer.Argument(..., help="Task ID to accept.")) -> None:
    """Compatibility alias for `loom review accept`."""
    _require_non_worker_review_context()
    _accept_review_task(task_id)


@app.command(hidden=True)
def reject(
    task_id: str = typer.Argument(..., help="Task ID to reject."),
    note: str = typer.Argument(..., help="Rejection reason."),
) -> None:
    """Compatibility alias for `loom review reject`."""
    _require_non_worker_review_context()
    _reject_review_task(task_id, note)


@app.command(hidden=True)
def decide(
    task_id: str = typer.Argument(..., help="Task ID to decide."),
    option: str = typer.Argument(..., help="Decision (option id or free text)."),
) -> None:
    """Compatibility alias for `loom review decide`."""
    _require_non_worker_review_context()
    _decide_review_task(task_id, option)


@app.command()
def release(
    target: str = typer.Argument(..., help="Thread name or task ID to release ownership of."),
    note: str = typer.Argument(..., help="Reason for releasing."),
) -> None:
    """Release stale thread ownership back to the pool."""
    loom = _resolve_loom()
    try:
        from .scheduler import load_all_threads

        threads = load_all_threads(loom)
        if target in threads:
            release_thread(loom, target, note=note)
            typer.echo(f"Released thread {target}.")
            return
        release_claim(loom, target, note=note)
        typer.echo(f"Released {target}.")
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _render_item_detail(loom: Path, item: dict[str, Any]) -> None:
    typer.echo(f"[{item['kind']}] {item['id']}: {item['title']}")
    typer.echo(f"  file: {item['file']}")

    if item["kind"] in {"paused", "reviewing"}:
        _, task = load_task(loom, item["id"])
        for line in format_review_summary(task)[1:]:
            typer.echo(line)
        if task.decision:
            decision = task.decision
            if isinstance(decision, dict):
                decision = Decision.model_validate(decision)
            if isinstance(decision, Decision):
                typer.echo(f"  question: {decision.question}")
                for option in decision.options:
                    suffix = f" - {option.note}" if option.note else ""
                    typer.echo(f"    {option.id}: {option.label}{suffix}")


def _render_inbox_item_detail(loom: Path, item: dict[str, Any]) -> None:
    typer.echo(f"[inbox] {item['id']}: {item['title']}")
    typer.echo(f"  file: {item['file']}")
    _, inbox_item = load_inbox_item(loom, item["id"])
    typer.echo("  body:")
    for line in inbox_item.body.splitlines() or [""]:
        typer.echo(f"    {line}")


def _prompt_inbox_action() -> str:
    return select("Inbox item action", ["plan", "skip", "open", "detail"], default="plan")


def _handle_inbox_item(loom: Path, item: dict[str, Any]) -> str:
    while True:
        action = _prompt_inbox_action()
        if action == "skip":
            return "skipped"
        if action == "open":
            _open_in_editor(item["file"])
            continue
        if action == "detail":
            _render_inbox_item_detail(loom, item)
            continue
        if action == "plan":
            try:
                planned = plan_inbox_item(loom, item["id"])
            except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
                typer.echo(f"Error: {exc}", err=True)
                return "errors"
            typer.echo(f"Resolved {item['id']} -> {', '.join(cast('list[str]', planned['resolved_to']))}.")
            return "planned"


def _prompt_action(item: dict[str, Any]) -> str:
    if item["kind"] == "paused":
        return select("Paused task action", ["decide", "skip", "open", "detail"], default="skip")
    if item["kind"] == "reviewing":
        return select("Reviewing task action", ["accept", "reject", "skip", "open", "detail"], default="skip")
    return select("Action", ["skip"], default="skip")


def _open_in_editor(path: str) -> None:
    editor = os.environ.get("EDITOR")
    if editor:
        subprocess.run([editor, path], check=False)
    else:
        typer.echo(f"Open manually: {path}")


def _handle_paused_item(loom: Path, item: dict[str, Any]) -> str:
    while True:
        action = _prompt_action(item)
        if action == "skip":
            return "skipped"
        if action == "open":
            _open_in_editor(item["file"])
            continue
        if action == "detail":
            _render_item_detail(loom, item)
            continue
        if action == "decide":
            _, task = load_task(loom, item["id"])
            decision = task.decision
            if isinstance(decision, dict):
                decision = Decision.model_validate(decision)
            option_choices = [option.id for option in decision.options] if isinstance(decision, Decision) else []
            default = option_choices[0] if option_choices else ""
            option = select("Decision", option_choices or [default or "custom"], default=default or "custom")
            if option == "custom":
                option = text("Decision")
            decide_task(loom, item["id"], option)
            typer.echo(f"Decided {item['id']} -> scheduled.")
            return "decided"


def _handle_reviewing_item(loom: Path, item: dict[str, Any]) -> str:
    while True:
        action = _prompt_action(item)
        if action == "skip":
            return "skipped"
        if action == "open":
            _open_in_editor(item["file"])
            continue
        if action == "detail":
            _render_item_detail(loom, item)
            continue
        if action == "accept":
            accept_task(loom, item["id"])
            typer.echo(f"Accepted {item['id']} -> done.")
            return "accepted"
        if action == "reject":
            note = text("Reject note")
            reject_task(loom, item["id"], note)
            typer.echo(f"Rejected {item['id']} -> scheduled.")
            return "rejected"


def _run_queue(loom: Path) -> None:
    queue = get_interaction_queue(loom)
    if not queue:
        typer.echo('No pending approvals. Add a request with `loom request add "..."` (or `loom inbox add "..."`).')
        return

    summary: dict[str, int] = {"decided": 0, "accepted": 0, "rejected": 0, "skipped": 0}
    visited: set[tuple[str, str]] = set()

    while True:
        queue = [item for item in get_interaction_queue(loom) if (item["kind"], item["id"]) not in visited]
        if not queue:
            break

        item = queue[0]
        _render_item_detail(loom, item)
        result = _handle_paused_item(loom, item) if item["kind"] == "paused" else _handle_reviewing_item(loom, item)
        summary[result] = summary.get(result, 0) + 1
        visited.add((item["kind"], item["id"]))

    typer.echo("Queue summary:")
    for key in ["decided", "accepted", "rejected", "skipped"]:
        if summary.get(key):
            typer.echo(f"  {key}: {summary[key]}")


def _run_inbox_queue(loom: Path) -> None:
    queue = get_pending_inbox_items(loom)
    if not queue:
        typer.echo("No pending inbox items.")
        return

    summary: dict[str, int] = {"planned": 0, "skipped": 0, "errors": 0}
    for item in queue:
        typer.echo(f"[inbox] {item['id']}: {item['title']}")
        typer.echo(f"  file: {item['file']}")
        result = _handle_inbox_item(loom, item)
        summary[result] = summary.get(result, 0) + 1

    typer.echo("Inbox planning summary:")
    for key in ["planned", "skipped", "errors"]:
        if summary.get(key):
            typer.echo(f"  {key}: {summary[key]}")


@inbox_app.callback(invoke_without_command=True)
def inbox_main(ctx: typer.Context) -> None:
    """Run the interactive inbox planning loop when no subcommand is provided."""
    if ctx.invoked_subcommand is not None:
        return
    loom = _resolve_loom()
    _run_inbox_queue(loom)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    global_mode: bool = typer.Option(False, "-g", help="Use the home-level loom directory."),
    plain: bool = typer.Option(False, "--plain", help="Use the plain prompt-based approval loop instead of the TUI."),
) -> None:
    """Enter the default interactive queue when no subcommand is provided."""
    set_root(global_root() if global_mode else None)
    if ctx.invoked_subcommand is not None:
        return

    loom = _resolve_loom()
    if plain:
        _run_queue(loom)
        return

    try:
        from .tui import run_tui

        run_tui(loom)
    except ImportError as exc:
        typer.echo(f"Error: {exc}", err=True)
        typer.echo("Hint: install the TUI extra with `uv sync --extra tui`, or run `loom --plain`.", err=True)
        raise typer.Exit(1) from exc


@app.command()
def tui() -> None:
    """Open the Textual approval-queue TUI (requires the 'tui' optional dependency).

    Browse and act on paused / reviewing queue items interactively, and add
    new requirements into `.loom/inbox/` from inside the TUI.

    Key bindings inside the TUI:
      a  accept the selected reviewing task
      r  reject the selected reviewing task (prompts for reason)
      d  decide on the selected paused task (prompts for choice)
      n  add a new inbox requirement (multi-line)
      l  release the selected thread-owned queue item (prompts for reason)
      R  refresh the queue from disk
      w  toggle watch mode (polls .loom/ every 1s)
      ?  show the in-app shortcut/help overlay
      q  quit
    """
    loom = _resolve_loom()
    try:
        from .tui import run_tui

        run_tui(loom)
    except ImportError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def find_task(loom: Path, task_id: str) -> tuple[Path, Any]:
    return load_task(loom, task_id)
