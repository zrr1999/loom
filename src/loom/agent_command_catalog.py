"""Canonical manager-facing agent command examples and doc renderers."""

from __future__ import annotations

DEFAULT_COMMAND_PREFIX = "loom"
README_COMMAND_PREFIX = DEFAULT_COMMAND_PREFIX


def _command(prefix: str, suffix: str) -> str:
    return f"{prefix} {suffix}".strip()


def manager_start_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "manage")


def manager_next_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent next --role manager")


def manager_new_thread_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "manage new-thread --name <name> [--priority <n>]")


def manager_new_task_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(
        prefix,
        "manage new-task --thread <id> --title '<title>' --acceptance '<criteria>' [--persistent]",
    )


def manager_plan_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "manage plan <rq-id> [--thread <name>]")


def manager_done_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent done <task-id> --output <.loom/products/...|url> --role manager")


def manager_priority_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "manage priority [--task <id> | --thread <name>] [--set <n>]")


def manager_assign_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "manage assign --thread <name> --worker <agent-id>")


def manager_pause_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent pause <task-id> --question '<question>' --role manager")


def manager_spawn_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "spawn [--threads <backend,frontend>] [--force]")


def manager_propose_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager")


def manager_send_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent send <agent-id> '<extra context>' --ref <task-id> --role manager")


def render_manager_command_contract(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    lines = [
        f"- Bootstrap the manager loop: `{manager_start_command(prefix)}`",
        f"- Fetch the next action: `{manager_next_command(prefix)}`",
        f"- Create a planning thread: `{manager_new_thread_command(prefix)}`",
        f"- Create a planned task: `{manager_new_task_command(prefix)}`",
        f"- Plan a pending request directly: `{manager_plan_command(prefix)}`",
        (
            "  - If Loom cannot clearly infer the target thread, the command exits non-zero and "
            "tells the manager to rerun it with `--thread` or create a new thread first."
            if prefix == DEFAULT_COMMAND_PREFIX
            else "  - If Loom cannot clearly match the request to an existing thread, it stops and "
            "asks the manager to choose `--thread` explicitly or create a new thread first."
        ),
        f"- Finish completed manager-owned work: `{manager_done_command(prefix)}`",
        f"- Pause for a human decision: `{manager_pause_command(prefix)}`",
        f"- Assign a thread to a worker: `{manager_assign_command(prefix)}`",
        f"- Inspect or adjust task/thread priority: `{manager_priority_command(prefix)}`",
        f"- Delegate the initial handoff: `{manager_propose_command(prefix)}`",
        f"- Send follow-up context: `{manager_send_command(prefix)}`",
    ]
    return "\n".join(lines)


def render_manager_command_access(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    worker_safe = [
        "agent new-task --thread <id> --title '<title>' --acceptance '<criteria>' [--persistent]",
        "agent next",
        "agent done <id> --output <.loom/products/...|url>",
        "agent pause <id> --question ... --options ...",
        'agent checkpoint "..."',
        "agent resume",
        "agent mailbox",
        "agent mailbox-read <msg-id>",
        "agent whoami",
        "agent worktree list|add|attach|remove",
        'agent ask <to> "..."',
        'agent propose <to> "..."',
        'agent reply <msg-id> "..."',
    ]
    singleton_only = [
        'agent send <to> "..." [--role <manager|director|reviewer>]',
    ]
    manager_only = [
        "manage",
        "manage new-thread --name <name> [--priority <n>]",
        "manage new-task --thread <id> --title '<title>' --acceptance '<criteria>'",
        "manage plan <rq-id> [--thread <name>]",
        "manage assign --thread <name> --worker <agent-id>",
        "manage priority [--task <id> | --thread <name>] [--set <n>]",
    ]

    lines = [
        ("- Worker-safe `loom agent` commands default to the worker role and require `LOOM_WORKER_ID`."),
        *[f"  - `{_command(prefix, suffix)}`" for suffix in worker_safe],
        (
            "- Mailbox commands can also target singleton mailboxes with "
            "`--role manager`, `--role director`, or `--role reviewer`."
        ),
        f"  - `{_command(prefix, 'agent mailbox --role <manager|director|reviewer>')}`",
        f"  - `{_command(prefix, 'agent mailbox-read <msg-id> --role <manager|director|reviewer>')}`",
        f"  - `{_command(prefix, 'agent reply <msg-id> "..." --role <manager|director|reviewer>')}`",
        ("- Singleton-only `loom agent` commands require `--role manager`, `--role director`, or `--role reviewer`."),
        *[f"  - `{_command(prefix, suffix)}`" for suffix in singleton_only],
        f"- Read-only status remains available without a worker id: `{_command(prefix, 'agent status')}`",
        "- Director/orchestrator bootstrap in this repo: `just start`.",
        "- Director and human share the full top-level `loom` command surface.",
        "- Manager entrypoints outside `loom agent`: require a clean manager process without `LOOM_WORKER_ID`.",
        *[f"  - `{_command(prefix, suffix)}`" for suffix in manager_only],
        f"- Human/director worker-launch entrypoint: `{manager_spawn_command(prefix)}`",
        "- Reviewer/human entrypoints outside `loom agent`:",
        f"  - `{_command(prefix, 'review')}`",
        f"  - `{_command(prefix, 'review accept <id>')}`",
        f"  - `{_command(prefix, 'review reject <id> "reason"')}`",
        f"  - `{_command(prefix, 'review decide <id> <option>')}`",
    ]
    return "\n".join(lines)
