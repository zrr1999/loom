"""loom agent — machine-friendly subcommands for agent integration."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import typer

from .config import load_settings
from .migration import ensure_name_based_threads
from .models import AgentStatus, MessageType, Task
from .repository import agent_pending_dir, load_message, load_task, require_loom, task_file_path, workspace_root
from .runtime import global_root, is_global_mode_active, set_root
from .scheduler import get_next_tasks, get_pending_inbox_items, get_status_summary
from .services import (
    claim_task,
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


@app.callback(invoke_without_command=True)
def agent_root_options(
    ctx: typer.Context,
    global_mode: bool = typer.Option(False, "-g", help="Use the home-level loom directory."),
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
        ensure_name_based_threads(loom)
        return loom
    except FileNotFoundError as exc:
        _emit_error(str(exc), code="loom_not_found")
        raise  # unreachable; satisfies type checker


def _resolve_actor(*, manager: bool) -> str:
    if manager:
        return "manager"

    agent_id = os.environ.get("LOOM_AGENT_ID", "").strip()
    if not agent_id:
        _emit_error(
            "LOOM_AGENT_ID is required for this command. Use --manager to act as manager.",
            code="missing_agent_id",
        )
        raise  # unreachable
    return agent_id


def _require_manager_context(command_name: str) -> None:
    agent_id = os.environ.get("LOOM_AGENT_ID", "").strip()
    if agent_id:
        _emit_error(
            f"loom agent {command_name} is manager-only. LOOM_AGENT_ID={agent_id!r} is set, "
            "so this process is running as an executor. Start a clean manager process without "
            "LOOM_AGENT_ID in the environment and run the command there.",
            code="executor_not_allowed",
        )


def _format_executor_command(template: str, *, agent_id: str, loom_dir: Path, threads: list[str], env_path: str) -> str:
    return (
        template.replace("{agent_id}", agent_id)
        .replace("{loom_dir}", str(loom_dir))
        .replace("{threads}", ",".join(threads))
        .replace("{env_file}", env_path)
    )


def _format_task_block(loom: Path, task: Task) -> list[str]:
    lines = [
        f"  TASK  {task.id}",
        f"    title      : {task.title}",
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
    if actor == "manager":
        return
    touch_agent(loom, actor, status=AgentStatus.ACTIVE)


@app.command("new-thread")
def new_thread(
    name: str = typer.Option("", help="Thread name."),
    priority: int = typer.Option(50, help="Thread priority."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Create a new thread with an auto-assigned short internal id."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor(manager=manager))

    try:
        thread, path, duplicates = create_thread(loom, name=name, priority=priority)
    except ValueError as exc:
        _emit_error(str(exc))
        raise  # unreachable

    lines = [
        f"CREATED thread {thread.name}",
        f"  id       : {thread.id}",
        f"  priority : {thread.priority}",
        f"  path     : {path.parent}",
    ]
    if duplicates:
        lines.append(f"  WARNING  : thread name '{thread.name}' already used by {', '.join(duplicates)}")
    typer.echo("\n".join(lines))


@app.command("new-task")
def new_task(
    thread: str = typer.Option(..., "--thread", help="Canonical thread name (e.g. backend)."),
    title: str = typer.Option("", help="Task title."),
    priority: int = typer.Option(50, help="Task priority."),
    acceptance: str = typer.Option("", help="Acceptance criteria."),
    depends_on: str = typer.Option("", help="Comma-separated dependency IDs."),
    after: str = typer.Option("", "--after", help="Sugar for --depends-on: single task ID this task comes after."),
    created_from: str = typer.Option("", help="Comma-separated source inbox RQ IDs."),
    background: str = typer.Option("", help="Task background section content."),
    implementation_direction: str = typer.Option("", help="Implementation direction section content."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Create a new task file in the given thread."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor(manager=manager))

    # Merge --after into depends_on
    merged_deps = depends_on
    if after:
        merged_deps = ",".join(filter(None, [depends_on, after]))

    try:
        task, path = create_task(
            loom,
            thread_id=thread,
            title=title,
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
        f"  status : {task.status.value}",
        f"  thread : {task.thread}",
        f"  file   : {path}",
    ]
    typer.echo("\n".join(lines))


@app.command("next")
def next_task(
    thread: str = typer.Option("", "--thread", help="Limit to a specific thread."),
    plan_limit: int = typer.Option(0, "--plan-limit", min=0, help="Plan up to this many pending inbox items first."),
    task_limit: int = typer.Option(0, "--task-limit", min=0, help="Return up to this many ready tasks."),
    wait_seconds: float | None = typer.Option(
        None,
        "--wait-seconds",
        help="Seconds to wait between retries when action is idle.",
    ),
    retries: int | None = typer.Option(
        None,
        "--retries",
        min=0,
        help="Retry count when no plan/task action is ready.",
    ),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Get the next agent action."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
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
        )
        if tasks:
            break

        if attempt < effective_retries and effective_wait_seconds > 0:
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
            "  2. Run: loom agent new-thread --name <name> [--priority <n>] --manager",
            "  3. Run: loom agent new-task --thread <id> --title '<title>' --acceptance '<criteria>' --manager",
            "  4. Repeat `loom agent next --manager` after all requirements above are arranged.",
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
        typer.echo("\n".join(lines))
        return

    if actor == "manager":
        task_lines = []
        for task in tasks:
            task_lines.extend(_format_task_block(loom, task))

        lines = [
            "ACTION  task",
            f"COUNT   {len(tasks)}",
            "ACTOR   manager",
            "",
            "READY TASKS",
            *task_lines,
            "Manager next steps:",
            "  1. Start or wake a worker agent if needed: loom agent spawn [--threads <backend,frontend>]",
            "  2. Tell the worker to run `loom agent next` in its own executor environment.",
            "  3. Keep using `loom agent status` to monitor ready / paused / reviewing work.",
            "",
        ]
        typer.echo("\n".join(lines))
        return

    claimed_tasks = [claim_task(loom, task.id, agent_id=actor)[1] for task in tasks]

    task_lines = []
    for task in claimed_tasks:
        task_lines.extend(_format_task_block(loom, task))

    lines = [
        "ACTION  task",
        f"COUNT   {len(claimed_tasks)}",
        f"ACTOR   {actor}",
        "",
        "CLAIMED TASKS",
        *task_lines,
        "When finished with each task:",
        "  loom agent done <task-id> [--output <path-or-url>]",
        "",
        "If blocked and need a decision:",
        "  loom agent pause <task-id> --question '<question>'",
    ]
    typer.echo("\n".join(lines))


@app.command("start")
def start() -> None:
    """Print the manager bootstrap guide."""
    _require_manager_context("start")

    loom = _resolve_loom()
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
        "      Run: loom agent next --manager",
        "",
        "      If output starts with ACTION  plan:",
        "        Arrange the listed human requirements into threads/tasks with:",
        "          loom agent new-thread --name <name> --manager",
        "          loom agent new-task --thread <id> --title '<title>' --acceptance '<criteria>' --manager",
        "        Then run loom agent next --manager again.",
        "",
        "      If output starts with ACTION  task:",
        "        Execute every claimed task that was returned.",
        "        Finish each completed task with done.",
        "        If blocked on a human decision, pause the task.",
        "        After handling all returned tasks, run loom agent next --manager again.",
        "",
        "      If output starts with ACTION  idle:",
        "        No executable work is ready right now.",
        "        Inspect the waiting-on section, then wait or exit.",
        "",
        "ESSENTIAL COMMANDS",
        "",
        "  loom agent next --manager",
        "    Fetch planning work or the next ready task batch.",
        f"    Planning batch : {settings.agent.inbox_plan_batch} inbox items",
        f"    Task batch     : {settings.agent.task_batch} tasks",
        f"    Idle wait      : {settings.agent.next_wait_seconds}s between retries",
        f"    Idle retries   : {settings.agent.next_retries}",
        "",
        "  loom agent done <task-id> --output <path-or-url>",
        "    Mark a finished task as reviewing, or pause it if incomplete markers remain.",
        "",
        "  loom agent pause <task-id> --question '<question>'",
        "    Release the claim and ask the human for a decision.",
        "",
        "  loom agent spawn [--threads <backend,frontend>]",
        "    Start or wake an executor assignment.",
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

    typer.echo("\n".join(lines))


@app.command("done")
def done(
    task_id: str = typer.Argument(..., help="Task ID to mark done."),
    output: str = typer.Option("", "--output", help="Output path or link."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Mark a task as reviewing when it is ready for human review."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor(manager=manager))

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
    question: str = typer.Option("", "--question", help="Decision question."),
    options: str = typer.Option("", "--options", help="JSON array of {id, label, note} options."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Pause a task with a decision question."""
    loom = _resolve_loom()
    _touch_if_agent(loom, _resolve_actor(manager=manager))

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
            if is_offline:
                line += "  WARNING: appears offline"
            lines.append(line)

    if queue:
        lines += ["", "QUEUE (needs attention)"]
        for item in queue:
            kind = item.get("kind", "?")
            item_id = item.get("id", "?")
            lines.append(f"  [{kind}] {item_id}")

    typer.echo("\n".join(lines))


@app.command("spawn")
def spawn(
    threads: str = typer.Option("", "--threads", help="Comma-separated thread assignment."),
) -> None:
    """Register a new executor agent."""
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
        "Executor environment file",
        f"  {env_path}",
        "",
        "Default launch pattern",
        "  Prefer passing environment variables from the outside when starting the executor.",
        "  Read the env file and inject those values into the child-process launch.",
        "  Required variables are usually:",
        f"    LOOM_AGENT_ID={agent_id}",
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
        env_prefix = f"LOOM_AGENT_ID={agent_id} LOOM_DIR={loom}"
        if thread_list:
            env_prefix += f" LOOM_THREADS={','.join(thread_list)}"
        lines += [
            "Configured executor command",
            "  [agent].executor_command is set in loom.toml.",
            "  You can launch the executor with either style:",
            f"    source {env_path} && {rendered}",
            f"    {env_prefix} {rendered}",
            "",
        ]
    else:
        lines += [
            "No executor command is configured in loom.toml.",
            "  If you want spawn to print a ready-to-run command, set:",
            "    [agent]",
            '    executor_command = "your launcher command"',
            "  Supported placeholders: {agent_id} {loom_dir} {threads} {env_file}",
            "",
        ]

    lines += [
        "If your subagent runtime cannot set environment variables at all:",
        "  do not use loom agent spawn for that runtime.",
        "  Either launch the agent from a wrapper process that can inject env vars,",
        "  or use a runtime that supports per-process environment configuration.",
    ]
    typer.echo("\n".join(lines))


@app.command("whoami")
def whoami(manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID.")) -> None:
    """Show the current actor identity."""
    actor = _resolve_actor(manager=manager)
    role = "manager" if actor == "manager" else "executor"
    typer.echo(f"IDENTITY\n  id   : {actor}\n  role : {role}")


@app.command("checkpoint")
def checkpoint(
    summary: str = typer.Argument(..., help="Checkpoint summary."),
    phase: str = typer.Option("implementing", "--phase", help="Current phase."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Update the current agent checkpoint."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
    if actor == "manager":
        _emit_error("manager checkpoint updates are not implemented via this command.", code="not_supported")
    record = update_checkpoint(loom, actor, phase=phase, summary=summary)
    typer.echo(
        f"CHECKPOINT recorded\n  agent : {record.id}\n  phase : {phase}\n  summary : {record.checkpoint_summary}"
    )


@app.command("resume")
def resume(manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID.")) -> None:
    """Show the current agent checkpoint body."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
    if actor == "manager":
        _emit_error("manager resume is not implemented via this command.", code="not_supported")
    record = resume_agent(loom, actor)
    typer.echo(f"CHECKPOINT body for {record.id}\n\n{record.body}")


@app.command("inbox")
def inbox(manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID.")) -> None:
    """List pending messages for the current agent."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
    if actor == "manager":
        _emit_error("manager inbox is not implemented via this command.", code="not_supported")
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
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Show message content without moving it."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
    if actor == "manager":
        _emit_error("manager inbox-read is not implemented via this command.", code="not_supported")
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
    type_: str = typer.Option("info", "--type", help="Message type."),
    ref: str = typer.Option("", "--ref", help="Optional related entity id."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Send a message to another agent."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
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
    ref: str = typer.Option("", "--ref", help="Optional related task/entity id."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Shorthand for send --type question."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
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
    thread: str = typer.Option("", "--thread", help="Optional related thread id."),
    ref: str = typer.Option("", "--ref", help="Optional related entity id."),
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Shorthand for send --type task_proposal."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
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
    manager: bool = typer.Option(False, "--manager", help="Run as manager without LOOM_AGENT_ID."),
) -> None:
    """Reply to a pending message and move it to replied."""
    loom = _resolve_loom()
    actor = _resolve_actor(manager=manager)
    if actor == "manager":
        _emit_error("manager reply is not implemented via this command.", code="not_supported")
    payload = reply_to_message(loom, actor, msg_id, body)
    typer.echo(f"REPLIED to {msg_id}\n  reply id : {payload.get('reply_id', '')}")


def find_task(loom: Path, task_id: str) -> tuple[Path, Task]:
    """Compatibility wrapper used by the human CLI."""
    try:
        return load_task(loom, task_id)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
