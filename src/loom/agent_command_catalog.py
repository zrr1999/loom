"""Canonical manager-facing agent command examples and doc renderers."""

from __future__ import annotations

DEFAULT_COMMAND_PREFIX = "loom"
README_COMMAND_PREFIX = "uvx --from git+https://github.com/zrr1999/loom loom"


def _command(prefix: str, suffix: str) -> str:
    return f"{prefix} {suffix}".strip()


def manager_start_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "manage")


def manager_next_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent next --role manager")


def manager_new_thread_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent new-thread --name <name> [--priority <n>] --role manager")


def manager_new_task_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent new-task --thread <id> --title '<title>' --acceptance '<criteria>' --role manager")


def manager_done_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent done <task-id> --output <path-or-url> --role manager")


def manager_pause_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "agent pause <task-id> --question '<question>' --role manager")


def manager_spawn_command(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    return _command(prefix, "spawn [--threads <backend,frontend>]")


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
        f"- Finish completed manager-owned work: `{manager_done_command(prefix)}`",
        f"- Pause for a human decision: `{manager_pause_command(prefix)}`",
        f"- Spawn or wake a worker when configured: `{manager_spawn_command(prefix)}`",
        f"- Delegate the initial handoff: `{manager_propose_command(prefix)}`",
        f"- Send follow-up context: `{manager_send_command(prefix)}`",
    ]
    return "\n".join(lines)


def render_manager_command_access(prefix: str = DEFAULT_COMMAND_PREFIX) -> str:
    worker_safe = [
        "agent next",
        "agent done <id> --output path",
        "agent pause <id> --question ... --options ...",
        'agent checkpoint "..."',
        "agent resume",
        "agent inbox",
        "agent inbox-read <msg-id>",
        "agent whoami",
        'agent ask <to> "..."',
        'agent propose <to> "..."',
        'agent reply <msg-id> "..."',
    ]
    singleton_only = [
        "agent new-thread [--role <manager|director|reviewer>]",
        "agent new-task --thread backend [--role <manager|director|reviewer>]",
        'agent send <to> "..." [--role <manager|director|reviewer>]',
    ]
    manager_only = [
        "manage",
        "spawn [--threads <backend,frontend>]",
    ]

    lines = [
        ("- Worker-safe `loom agent` commands default to the worker role and require `LOOM_WORKER_ID`."),
        *[f"  - `{_command(prefix, suffix)}`" for suffix in worker_safe],
        ("- Singleton-only `loom agent` commands require `--role manager`, `--role director`, or `--role reviewer`."),
        *[f"  - `{_command(prefix, suffix)}`" for suffix in singleton_only],
        f"- Read-only status remains available without a worker id: `{_command(prefix, 'agent status')}`",
        "- Director/orchestrator bootstrap in this repo: `just start`.",
        "- Director and human share the full top-level `loom` command surface.",
        "- Manager entrypoints outside `loom agent`: require a clean manager process without `LOOM_WORKER_ID`.",
        *[f"  - `{_command(prefix, suffix)}`" for suffix in manager_only],
        f"- Reviewer entrypoint outside `loom agent`: `{_command(prefix, 'review')}`",
    ]
    return "\n".join(lines)
