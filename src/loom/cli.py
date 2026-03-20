"""loom CLI — the human interface."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import typer
from loguru import logger

from .agent import app as agent_app
from .agent import spawn_worker_runtime
from .agent import start as agent_start
from .config import ensure_settings
from .history import read_events
from .migration import ensure_name_based_threads, ensure_worker_agent_subtree
from .models import AgentRole, Decision, TaskStatus
from .prompting import select, text
from .repository import load_inbox_item, load_task, require_loom, root_config_path
from .runtime import global_root, set_root
from .scheduler import get_interaction_queue, get_pending_inbox_items, get_status_summary, load_all_tasks
from .services import (
    accept_task,
    create_inbox_item,
    decide_task,
    ensure_agent_layout,
    format_review_summary,
    plan_inbox_item,
    reject_task,
    release_claim,
    release_thread,
    transition_task,
)
from .state import InvalidTransitionError

logger.remove()
logger.add(sys.stderr, level="WARNING")

app = typer.Typer(
    name="loom",
    help="A CLI tool where humans weave requirements and agents execute tasks.",
)
app.add_typer(agent_app, name="agent", help="Agent commands (machine-friendly).")
inbox_app = typer.Typer(help="Inbox commands.")
app.add_typer(inbox_app, name="inbox")


def _resolve_loom() -> Path:
    try:
        loom = require_loom()
        ensure_worker_agent_subtree(loom)
        ensure_name_based_threads(loom)
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
            "`loom review`, and `loom accept <task-id>` / `loom reject <task-id> '<reason>'` as needed."
        ),
        err=True,
    )
    raise typer.Exit(1)


@app.command()
def init(
    project: str = typer.Option("", help="Project name."),
    global_mode: bool = typer.Option(False, "-g", help="Use the home-level loom directory."),
) -> None:
    """Initialize .loom/ and ensure root loom.toml exists."""
    set_root(global_root() if global_mode else None)
    root = global_root() if global_mode else Path.cwd()
    loom = root / ".loom"
    loom.mkdir(exist_ok=True)
    (loom / "inbox").mkdir(exist_ok=True)
    (loom / "threads").mkdir(exist_ok=True)
    ensure_agent_layout(loom)

    project_name = project or root.name
    _, created = ensure_settings(root, project_name)
    action = "Created" if created else "Using existing"
    typer.echo(f"{action} {root_config_path(loom).name} and ensured .loom/ structure for '{project_name}'.")


@inbox_app.command("add")
def inbox_add(
    description: str = typer.Argument(..., help="Requirement description."),
) -> None:
    """Add a new requirement to the inbox."""
    loom = _resolve_loom()
    try:
        item, path = create_inbox_item(loom, description)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Created {item.id}: {path}")


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


@app.command()
def manage() -> None:
    """Open the manager bootstrap guide from the top-level CLI."""
    agent_start(role=AgentRole.MANAGER)


@app.command()
def spawn(
    threads: str = typer.Option("", "--threads", help="Comma-separated thread assignment."),
) -> None:
    """Register a new worker agent from the top-level CLI."""
    spawn_worker_runtime(threads=threads)


@app.command()
def review() -> None:
    """List reviewing tasks without entering the interactive approval loop."""
    _require_non_worker_review_context()
    loom = _resolve_loom()

    tasks = [task for task in load_all_tasks(loom) if task.status == TaskStatus.REVIEWING]
    if not tasks:
        typer.echo("No tasks in reviewing status.")
        return

    for task in tasks:
        for line in format_review_summary(task):
            typer.echo(line)
        typer.echo('  next: use `loom accept <id>` or `loom reject <id> "reason"`')


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


@app.command()
def accept(task_id: str = typer.Argument(..., help="Task ID to accept.")) -> None:
    """Accept a reviewing task -> done."""
    loom = _resolve_loom()
    try:
        accept_task(loom, task_id)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Accepted {task_id} -> done.")


@app.command()
def reject(
    task_id: str = typer.Argument(..., help="Task ID to reject."),
    note: str = typer.Argument(..., help="Rejection reason."),
) -> None:
    """Reject a task back to scheduled."""
    loom = _resolve_loom()
    try:
        reject_task(loom, task_id, note)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Rejected {task_id} -> scheduled. Note: {note}")


@app.command()
def decide(
    task_id: str = typer.Argument(..., help="Task ID to decide."),
    option: str = typer.Argument(..., help="Decision (option id or free text)."),
) -> None:
    """Resolve a paused task's decision -> scheduled."""
    loom = _resolve_loom()
    try:
        decide_task(loom, task_id, option)
    except (FileNotFoundError, ValueError, InvalidTransitionError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Decided {task_id} -> scheduled.")


@app.command()
def release(
    target: str = typer.Argument(..., help="Thread name or task ID to release ownership of."),
    note: str = typer.Argument(..., help="Reason for releasing."),
) -> None:
    """Release thread ownership (or a legacy task claim) back to the pool."""
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
            typer.echo(f"Planned {item['id']} -> {planned['planned_to']}.")
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
        typer.echo('No pending approvals. Add a requirement with `loom inbox add "..."`.')
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
      l  release the selected claimed queue item (prompts for reason)
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
