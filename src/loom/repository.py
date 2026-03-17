"""Filesystem helpers for locating and loading loom entities."""

from __future__ import annotations

from pathlib import Path

from .config import config_path, load_settings
from .frontmatter import read_model
from .models import AgentRecord, InboxItem, ManagerRecord, Message, Task
from .runtime import resolve_root


def loom_dir(base_dir: Path | None = None) -> Path:
    root = resolve_root(base_dir)
    return root / ".loom"


def require_loom(base_dir: Path | None = None) -> Path:
    path = loom_dir(base_dir)
    if not path.exists():
        msg = ".loom/ not found. Run `loom init` first."
        raise FileNotFoundError(msg)
    return path


def workspace_root(loom: Path) -> Path:
    return loom.parent


def get_settings(loom: Path):
    return load_settings(workspace_root(loom))


def root_config_path(loom: Path) -> Path:
    return config_path(workspace_root(loom))


def find_task_path(loom: Path, task_id: str) -> Path:
    threads_dir = loom / "threads"
    if not threads_dir.exists():
        raise FileNotFoundError(f"task '{task_id}' not found")

    for thread_dir in sorted(threads_dir.iterdir()):
        if not thread_dir.is_dir():
            continue
        for path in sorted(thread_dir.glob("*.md")):
            if path.name == "_thread.md":
                continue
            if path.stem == task_id or path.stem.startswith(f"{task_id}-"):
                return path

    raise FileNotFoundError(f"task '{task_id}' not found")


def load_task(loom: Path, task_id: str) -> tuple[Path, Task]:
    path = find_task_path(loom, task_id)
    return path, read_model(path, Task)


def task_file_path(loom: Path, task: Task) -> Path:
    return loom / "threads" / task.thread / f"{task.id}.md"


def find_inbox_path(loom: Path, rq_id: str) -> Path:
    path = loom / "inbox" / f"{rq_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"inbox item '{rq_id}' not found")
    return path


def load_inbox_item(loom: Path, rq_id: str) -> tuple[Path, InboxItem]:
    path = find_inbox_path(loom, rq_id)
    return path, read_model(path, InboxItem)


def agents_dir(loom: Path) -> Path:
    return loom / "agents"


def manager_path(loom: Path) -> Path:
    return agents_dir(loom) / "_manager.md"


def agent_dir(loom: Path, agent_id: str) -> Path:
    return agents_dir(loom) / agent_id


def agent_record_path(loom: Path, agent_id: str) -> Path:
    return agent_dir(loom, agent_id) / "_agent.md"


def agent_pending_dir(loom: Path, agent_id: str) -> Path:
    return agent_dir(loom, agent_id) / "inbox" / "pending"


def agent_replied_dir(loom: Path, agent_id: str) -> Path:
    return agent_dir(loom, agent_id) / "inbox" / "replied"


def load_agent(loom: Path, agent_id: str) -> tuple[Path, AgentRecord]:
    path = agent_record_path(loom, agent_id)
    if not path.exists():
        raise FileNotFoundError(f"agent '{agent_id}' not found")
    return path, read_model(path, AgentRecord)


def load_manager(loom: Path) -> tuple[Path, ManagerRecord]:
    path = manager_path(loom)
    if not path.exists():
        raise FileNotFoundError("manager not found")
    return path, read_model(path, ManagerRecord)


def message_path(message_dir: Path, msg_id: str) -> Path:
    path = message_dir / f"{msg_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"message '{msg_id}' not found")
    return path


def load_message(message_dir: Path, msg_id: str) -> tuple[Path, Message]:
    path = message_path(message_dir, msg_id)
    return path, read_model(path, Message)
