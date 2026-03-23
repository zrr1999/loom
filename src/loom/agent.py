"""loom agent — machine-friendly subcommands for agent integration."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import typer

from .agent_command_catalog import (
    manager_done_command,
    manager_new_task_command,
    manager_new_thread_command,
    manager_next_command,
    manager_pause_command,
    manager_propose_command,
    manager_send_command,
    manager_spawn_command,
)
from .config import load_settings
from .migration import ensure_name_based_threads, ensure_thread_ownership_metadata, ensure_worker_agent_subtree
from .models import AgentRole, AgentStatus, MessageType, Task, TaskKind
from .repository import agent_pending_dir, load_message, load_task, require_loom, task_file_path, workspace_root
from .runtime import global_root, is_global_mode_active, set_root
from .scheduler import get_next_tasks, get_pending_inbox_items, get_status_summary
from .services import (
    claim_thread,
    complete_task,
    create_message,
    create_task,
    create_thread,
    list_pending_messages,
    pause_task,
    reply_to_message,
    resume_agent,
    spawn_agent,
    touch_agent,
    update_checkpoint,
)
from .state import InvalidTransitionError

if TYPE_CHECKING:
    from pathlib import Path

app = typer.Typer(invoke_without_command=True)
_SINGLETON_ACTORS = {
    AgentRole.MANAGER.value,
    AgentRole.DIRECTOR.value,
    AgentRole.REVIEWER.value,
}
_ROLE_HELP = "Run as worker, manager, director, or reviewer. Defaults to worker."
_START_ROLE_HELP = "Show bootstrap guidance for worker, manager, director, or reviewer."
WorkerRoleOption = Annotated[AgentRole, typer.Option("--role", help=_ROLE_HELP)]
StartRoleOption = Annotated[AgentRole, typer.Option("--role", help=_START_ROLE_HELP)]
GlobalModeOption = Annotated[bool, typer.Option("-g", help="Use the home-level loom directory.")]
_WORKER_SAFE_COMMANDS = (
    "next",
    "done",
    "pause",
    "status",
    "whoami",
    "checkpoint",
    "resume",
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
        ensure_worker_agent_subtree(loom)
        ensure_name_based_threads(loom)
        ensure_thread_ownership_metadata(loom)
        return loom
    except FileNotFoundError as exc:
        _emit_error(str(exc), code="loom_not_found")
        raise  # unreachable; satisfies type checker


def _resolve_actor(*, role: AgentRole) -> str:
    if role != AgentRole.WORKER:
        return role.value

    worker_id = os.environ.get("LOOM_WORKER_ID", "").strip()
    legacy_agent_id = os.environ.get("LOOM_AGENT_ID", "").strip()
    if not worker_id:
        migration_note = ""
        if legacy_agent_id:
            migration_note = " LOOM_AGENT_ID is no longer used; rename it to LOOM_WORKER_ID."
        _emit_error(
            (
                "LOOM_WORKER_ID is required for worker commands. "
                "Use --role manager / --role director / --role reviewer, or set LOOM_WORKER_ID."
                f"{migration_note}"
            ),
            code="missing_worker_id",
        )
        raise  # unreachable
    return worker_id


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
        lines.append(f"  1. Start or wake a worker agent if needed: {manager_spawn_command()}")
    else:
        lines.append(
            "  1. Ask the director or host system to start or wake a worker runtime with LOOM_WORKER_ID + LOOM_DIR."
        )
    lines.extend(
        [
            (f"  2. Prefer mailbox-first delegation: {manager_propose_command()}"),
            (f"  3. Follow up with {manager_send_command()} when needed."),
            "  4. Tell the worker to run `loom agent next` in its own executor environment.",
            "  5. Keep using `loom agent status` to monitor ready / paused / reviewing work.",
            "",
        ]
    )
    return lines


def _manager_launch_guidance(settings: object) -> list[str]:
    if _has_configured_executor_command(settings):
        return [
            f"  {manager_spawn_command()}",
            "    Create or wake a worker assignment and print the configured launch command.",
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


def _touch_if_agent(loom: Path, actor: str) -> None:
    if actor in _SINGLETON_ACTORS:
        return
    touch_agent(loom, actor, status=AgentStatus.ACTIVE)


@app.command("new-thread")
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


@app.command("new-task")
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
        task, path = create_task(
            loom,
            thread_name=thread,
            title=title,
            kind=kind,
            priority=priority,
            acceptance=acceptance,
            depends_on=merged_deps,
            created_from=created_from,
            background=background,
            implementation_direction=implementation_direction,
        )
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        _emit_error(str(exc))
        raise  # unreachable

    lines = [
        f"CREATED task {task.id}",
        f"  kind   : {task.kind.value}",
        f"  status : {task.status.value}",
        f"  thread : {task.thread}",
        f"  file   : {path}",
    ]
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
    settings = load_settings(workspace_root(loom))
    effective_wait_seconds = settings.agent.next_wait_seconds if wait_seconds is None else wait_seconds
    effective_retries = settings.agent.next_retries if retries is None else retries

    if effective_wait_seconds < 0:
        _emit_error("--wait-seconds must be >= 0", code="invalid_wait_seconds")
    if effective_retries < 0:
        _emit_error("--retries must be >= 0", code="invalid_retries")

    pending_inbox: list[dict[str, object]] = []
    tasks: list[Task] = []
    for attempt in range(effective_retries + 1):
        pending_inbox = get_pending_inbox_items(loom, limit=plan_limit or settings.agent.inbox_plan_batch)
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

        if attempt < effective_retries and effective_wait_seconds > 0:
            if _interactive_wait_feedback_enabled():
                _emit_wait_feedback(
                    attempt=attempt,
                    retries=effective_retries,
                    wait_seconds=effective_wait_seconds,
                )
            time.sleep(effective_wait_seconds)

    if pending_inbox:
        item_lines = []
        for item in pending_inbox:
            item_lines.append(f"  {item['id']}  {item.get('title', item.get('body', ''))}")
            item_lines.append(f"    file   : {item.get('file', '')}")
        lines = [
            "ACTION  plan",
            f"COUNT   {len(pending_inbox)}",
            "",
            "These human requirements have not been arranged into threads/tasks yet.",
            "Manager action is required before worker execution can continue.",
            "",
            "UNPLANNED REQUIREMENTS",
            *item_lines,
            "",
            "Manager next steps:",
            "  1. Create or choose a thread for each requirement.",
            f"  2. Run: {manager_new_thread_command()}",
            f"  3. Run: {manager_new_task_command()}",
            f"  4. Repeat `{manager_next_command()}` after all requirements above are arranged.",
            "",
        ]
        typer.echo("\n".join(lines))
        return

    if not tasks:
        summary = get_status_summary(loom)
        queue = summary.get("queue", [])
        reviewing_count = sum(1 for item in queue if item.get("kind") == "reviewing")
        paused_count = sum(1 for item in queue if item.get("kind") == "paused")
        inbox_pending = summary.get("inbox_pending", 0)
        lines = [
            "ACTION  idle",
            "",
            "No ready tasks or planning actions available.",
        ]
        if reviewing_count or paused_count or inbox_pending:
            lines.append("")
            lines.append("WAITING ON")
            if reviewing_count:
                lines.append(f"  reviewing : {reviewing_count} (human must accept or reject)")
            if paused_count:
                lines.append(f"  paused    : {paused_count} (human must decide)")
            if inbox_pending:
                lines.append(f"  inbox     : {inbox_pending} pending items")
        if actor in _SINGLETON_ACTORS:
            typer.echo("\n".join(lines))
            return

        lines.extend(
            [
                "",
                "Worker next steps:",
                "  1. Check `loom agent inbox` for pending manager handoffs.",
                "  2. If you want more work, proactively ask to claim a thread or task.",
                "     Example: `loom agent ask manager 'Can I take thread <thread> or task <task-id>?'`",
                (
                    "  3. If you already know the work, propose the handoff yourself with "
                    "`loom agent propose manager '<thread/task handoff>' --ref <thread-or-task-id>`."
                ),
            ]
        )
        typer.echo("\n".join(lines))
        return

    if actor in _SINGLETON_ACTORS:
        task_lines = []
        for task in tasks:
            task_lines.extend(_format_task_block(loom, task))

        lines = [
            "ACTION  task",
            f"COUNT   {len(tasks)}",
            f"ACTOR   {actor}",
            "",
            "READY TASKS",
            *task_lines,
            *(
                _manager_mailbox_steps(settings)
                if actor == AgentRole.MANAGER.value
                else [
                    f"{actor.title()} next steps:",
                    "  1. Coordinate with the manager or worker role before mutating task state.",
                    (
                        "  2. Re-run with `--role manager` for manager-loop actions, or configure "
                        "LOOM_WORKER_ID to claim as a worker."
                    ),
                    "",
                ]
            ),
        ]
        typer.echo("\n".join(lines))
        return

    # Claim thread(s) for the tasks about to be dispatched.
    claimed_threads: set[str] = set()
    for task in tasks:
        if task.thread not in claimed_threads:
            claim_thread(loom, task.thread, agent_id=actor)
            claimed_threads.add(task.thread)

    task_lines = []
    for task in tasks:
        task_lines.extend(_format_task_block(loom, task))

    thread_text = ", ".join(sorted(claimed_threads))
    lines = [
        "ACTION  task",
        f"COUNT   {len(tasks)}",
        f"ACTOR   {actor}",
        f"THREAD  {thread_text}",
        "",
        "ASSIGNED TASKS",
        *task_lines,
        "When finished with each task:",
        "  loom agent done <task-id> [--output <path-or-url>]",
        "",
        "If blocked and need a decision:",
        "  loom agent pause <task-id> --question '<question>'",
    ]
    typer.echo("\n".join(lines))


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
        "DO THIS NOW",
        "  You are the manager bootstrap process.",
        "  Immediately enter the main loop below and keep repeating it until work is exhausted.",
        "  Do not stop after reading this guide.",
        "",
        "IDENTITY",
        "  role           : manager",
        f"  loom dir       : {loom_dir_env or str(loom)}",
        "",
        *state_summary,
        "",
        "MAIN LOOP",
        "  Repeat this loop immediately:",
        "",
        "    STEP 1 — fetch the next action",
        f"      Run: {manager_next_command()}",
        "",
        "      If output starts with ACTION  plan:",
        "        Arrange the listed human requirements into threads/tasks with:",
        f"          {manager_new_thread_command()}",
        f"          {manager_new_task_command()}",
        f"        Then run {manager_next_command()} again.",
        "",
        "      If output starts with ACTION  task:",
        "        Execute every task returned from the claimed thread(s).",
        f"        Finish each completed task with `{manager_done_command()}`.",
        f"        If blocked on a human decision, use `{manager_pause_command()}`.",
        f"        After handling all returned tasks, run {manager_next_command()} again.",
        "",
        "      If output starts with ACTION  idle:",
        "        No executable work is ready right now.",
        "        Inspect the waiting-on section, then wait or exit.",
        "",
        "ESSENTIAL COMMANDS",
        "",
        f"  {manager_next_command()}",
        "    Fetch planning work or the next ready task batch.",
        f"    Planning batch : {settings.agent.inbox_plan_batch} inbox items",
        f"    Task batch     : {settings.agent.task_batch} tasks",
        f"    Idle wait      : {settings.agent.next_wait_seconds}s between retries",
        f"    Idle retries   : {settings.agent.next_retries}",
        "",
        f"  {manager_done_command()}",
        "    Mark a finished task as reviewing, or pause it if incomplete markers remain.",
        "",
        f"  {manager_pause_command()}",
        "    Release the claim and ask the human for a decision.",
        "",
        *_manager_launch_guidance(settings),
        "  Mailbox-first delegation once a worker exists",
        f"    {manager_propose_command()}",
        f"    {manager_send_command()}",
        "    Workers inspect with `loom agent inbox` / `loom agent inbox-read` and answer with `loom agent reply`.",
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
        "DO THIS NOW",
        "  Work under one concrete `LOOM_WORKER_ID` and keep changes task-scoped.",
        "  Use only worker-safe `loom agent` commands from this process.",
        "",
        "IDENTITY",
        "  role           : worker",
        f"  loom dir       : {loom_dir_env or str(loom)}",
        f"  worker id      : {worker_id or '(set LOOM_WORKER_ID before running worker commands)'}",
        "",
        "WORKER LOOP",
        "  1. Run: loom agent next",
        "  2. Read pending manager handoffs: loom agent inbox / loom agent inbox-read <msg-id>",
        (
            "  3. Implement the assigned task inside your claimed thread "
            "or ask for clarification with loom agent ask / propose / reply"
        ),
        "  4. Finish with loom agent done <task-id> [--output <path-or-url>]",
        "  5. If blocked on a decision, use loom agent pause <task-id> --question '<question>'",
        "",
        "WORKER-SAFE COMMANDS",
        "  loom agent next",
        "  loom agent done <task-id> [--output <path-or-url>]",
        "  loom agent pause <task-id> --question '<question>'",
        "  loom agent checkpoint '<summary>'",
        "  loom agent resume",
        "  loom agent inbox",
        "  loom agent inbox-read <msg-id>",
        "  loom agent whoami",
        "  loom agent ask <to> '<question>'",
        "  loom agent propose <to> '<proposal>' --ref <thread-or-task-id>",
        "  loom agent reply <msg-id> '<reply>'",
        "  loom agent status",
        "",
        "NOTES",
        "  - `loom agent new-thread`, `loom agent new-task`, and `loom agent send` require a singleton role override.",
        "  - `loom spawn` is the manager-owned worker launch entrypoint; workers should not call it.",
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
        "",
        "IDENTITY",
        "  role           : reviewer",
        f"  loom dir       : {os.environ.get('LOOM_DIR', '').strip() or str(loom)}",
        "",
        "CURRENT STATE",
        f"  review queue  : {reviewing_count}",
        f"  paused queue  : {paused_count}",
        "",
        "REVIEW LOOP",
        "  1. Run: loom review",
        "  2. Compare each reviewing task against its acceptance criteria and output.",
        "  3. Accept with: loom accept <task-id>",
        "  4. Reject with: loom reject <task-id> '<reason>'",
        "",
        "GUARDRAILS",
        "  - Reviewer work starts after implementation is finished; do not act as manager or worker here.",
        "  - If more runtime work is needed, hand the task back with a concrete rejection note.",
    ]
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
        "DO THIS NOW",
        "  Stay above the runtime loop and decide which role should act next.",
        "  Director and human share the full top-level `loom` command surface, but runtime truth stays in `.loom/`.",
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
        "ORCHESTRATION LOOP",
        "  1. Inspect status with `loom status` or `loom agent status`.",
        "  2. Launch manager work with `loom manage` when planning or thread assignment is needed.",
        "  3. Launch workers via `loom spawn` when configured, then hand them mailbox-driven task context.",
        "  4. Launch reviewer work with `loom review` when tasks are waiting for human review.",
        "",
        "GUARDRAILS",
        "  - Do not silently collapse into manager, worker, or reviewer behavior.",
        "  - Keep orchestration explicit and preserve `.loom/` as the only runtime source of truth.",
    ]
    return lines


@app.command("start")
def start(
    role: StartRoleOption = AgentRole.MANAGER,
) -> None:
    """Print bootstrap guidance for the requested role."""
    loom = _resolve_loom()
    if role == AgentRole.MANAGER:
        _require_manager_context("start")
        typer.echo("\n".join(_render_manager_bootstrap(loom)))
        return
    if role == AgentRole.WORKER:
        typer.echo("\n".join(_render_worker_bootstrap(loom)))
        return
    if role == AgentRole.REVIEWER:
        typer.echo("\n".join(_render_reviewer_bootstrap(loom)))
        return
    typer.echo("\n".join(_render_director_bootstrap(loom)))


@app.command("done")
def done(
    task_id: str = typer.Argument(..., help="Task ID to mark done."),
    output: Annotated[str, typer.Option("--output", help="Output path or link.")] = "",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Mark a task as reviewing when it is ready for human review."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor_for_command("done", role=role))

    try:
        _, task, blockers = complete_task(loom, task_id, output=output or None)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        _emit_error(str(exc))
        raise  # unreachable

    lines = [f"DONE task {task.id}", f"  status : {task.status.value}"]
    if output:
        lines.append(f"  output : {output}")
    if blockers:
        lines.append(f"  blocked: {', '.join(blockers)}")
        lines.append("  Waiting for human decision. Run: loom")
    else:
        lines.append("  Waiting for human review. Run: loom review")
    typer.echo("\n".join(lines))


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
        "  Waiting for human decision. Run: loom decide <id> <choice>",
    ]
    typer.echo("\n".join(lines))


@app.command("status")
def agent_status() -> None:
    """Describe current project state."""
    loom = _resolve_loom()
    summary = get_status_summary(loom)
    settings = load_settings(workspace_root(loom))

    tasks = summary.get("tasks", {})
    by_status = tasks.get("by_status", {})
    inbox = summary.get("inbox", {})
    agents_list = summary.get("agents", [])
    queue = summary.get("queue", [])
    ready_ids = tasks.get("ready_ids", [])
    capabilities = summary.get("capabilities", [])

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
            is_offline = False
            if isinstance(last_seen, str):
                try:
                    parsed = datetime.fromisoformat(last_seen)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    age_minutes = max(int((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() // 60), 0)
                    is_offline = age_minutes >= settings.agent.offline_after_minutes
                except ValueError:
                    is_offline = False

            line = f"  {agent_id:<12} {status_val}  last_seen:{age_text}"
            if summary_val:
                line += f"  — {summary_val}"
            pending_messages = int(agent.get("pending_messages", 0) or 0)
            replied_messages = int(agent.get("replied_messages", 0) or 0)
            line += f"  mailbox:{pending_messages} pending / {replied_messages} replied"
            if is_offline:
                line += "  WARNING: appears offline"
            lines.append(line)

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

    typer.echo("\n".join(lines))


def spawn_worker_runtime(
    threads: str = "",
) -> None:
    """Register a new worker agent from the top-level `loom spawn` entrypoint."""
    _require_manager_context("spawn")
    loom = _resolve_loom()
    settings = load_settings(workspace_root(loom))
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
) -> None:
    """Legacy entrypoint kept only to print migration guidance."""
    suggestion = f"loom spawn --threads {threads}" if threads else manager_spawn_command()
    _emit_error(
        f"`loom agent spawn` moved to `{suggestion}`. Run `{suggestion}` instead.",
        code="moved_command",
    )


@app.command("whoami")
def whoami(role: WorkerRoleOption = AgentRole.WORKER) -> None:
    """Show the current actor identity."""
    actor = _resolve_actor_for_command("whoami", role=role)
    resolved_role = role.value if actor in _SINGLETON_ACTORS else AgentRole.WORKER.value
    typer.echo(f"IDENTITY\n  id   : {actor}\n  role : {resolved_role}")


@app.command("checkpoint")
def checkpoint(
    summary: str = typer.Argument(..., help="Checkpoint summary."),
    phase: Annotated[str, typer.Option("--phase", help="Current phase.")] = "implementing",
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Update the current agent checkpoint."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("checkpoint", role=role)
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
    if actor in _SINGLETON_ACTORS:
        _emit_error(f"{actor} resume is not implemented via this command.", code="not_supported")
    record = resume_agent(loom, actor)
    typer.echo(f"CHECKPOINT body for {record.id}\n\n{record.body}")


@app.command("inbox")
def inbox(role: WorkerRoleOption = AgentRole.WORKER) -> None:
    """List pending messages for the current agent."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("inbox", role=role)
    if actor in _SINGLETON_ACTORS:
        _emit_error(f"{actor} inbox is not implemented via this command.", code="not_supported")
    messages = list_pending_messages(loom, actor)

    if not messages:
        typer.echo(f"INBOX {actor}\n  No pending messages.")
        return

    lines = [f"INBOX {actor}", f"  count : {len(messages)}", ""]
    for msg in messages:
        ref_part = f"  ref:{msg.ref}" if msg.ref else ""
        lines.append(f"  {msg.id}  type:{msg.type.value}  from:{msg.from_}{ref_part}")
    lines += ["", "To read a message: loom agent inbox-read <msg-id>"]
    typer.echo("\n".join(lines))


@app.command("inbox-read")
def inbox_read(
    msg_id: str = typer.Argument(..., help="Message ID to read (e.g. MSG-001)."),
    role: WorkerRoleOption = AgentRole.WORKER,
) -> None:
    """Show message content without moving it."""
    loom = _resolve_loom()
    actor = _resolve_actor_for_command("inbox-read", role=role)
    if actor in _SINGLETON_ACTORS:
        _emit_error(f"{actor} inbox-read is not implemented via this command.", code="not_supported")
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
    if actor in _SINGLETON_ACTORS:
        _emit_error(f"{actor} reply is not implemented via this command.", code="not_supported")
    payload = reply_to_message(loom, actor, msg_id, body)
    typer.echo(f"REPLIED to {msg_id}\n  reply id : {payload.get('reply_id', '')}")


def find_task(loom: Path, task_id: str) -> tuple[Path, Task]:
    """Compatibility wrapper used by the human CLI."""
    try:
        return load_task(loom, task_id)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
