"""Filesystem helpers for locating and loading loom entities."""

from __future__ import annotations

from pathlib import Path

from .config import config_path, load_settings
from .frontmatter import read_model, read_raw
from .ids import canonical_thread_name, split_task_id, task_filename
from .models import AgentRecord, ManagerRecord, Message, RequestItem, Routine, Task, WorktreeRecord
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

    parsed = split_task_id(task_id)
    if parsed is not None:
        thread_name, seq = parsed
        path = threads_dir / thread_name / task_filename(seq)
        if path.exists():
            return path

    for thread_dir in sorted(threads_dir.iterdir()):
        if not thread_dir.is_dir():
            continue
        for path in sorted(thread_dir.glob("*.md")):
            if path.name == "_thread.md":
                continue
            if path.stem == task_id or path.stem.startswith(f"{task_id}-"):
                return path
            metadata, _body = read_raw(path)
            if metadata.get("id") == task_id:
                return path

    raise FileNotFoundError(f"task '{task_id}' not found")


def load_task(loom: Path, task_id: str) -> tuple[Path, Task]:
    path = find_task_path(loom, task_id)
    return path, read_model(path, Task)


def task_file_path(loom: Path, task: Task) -> Path:
    return loom / "threads" / task.thread / task_filename(task.seq)


def requests_dir(loom: Path) -> Path:
    requests_path = loom / "requests"
    if requests_path.exists():
        return requests_path
    return loom / "inbox"


def routines_dir(loom: Path) -> Path:
    return loom / "routines"


def products_dir(loom: Path) -> Path:
    return loom / "products"


def products_reports_dir(loom: Path) -> Path:
    return products_dir(loom) / "reports"


def find_request_path(loom: Path, rq_id: str) -> Path:
    path = requests_dir(loom) / f"{rq_id}.md"
    if not path.exists():
        legacy_path = loom / "inbox" / f"{rq_id}.md"
        if legacy_path.exists():
            return legacy_path
        raise FileNotFoundError(f"request '{rq_id}' not found")
    return path


def load_request_item(loom: Path, rq_id: str) -> tuple[Path, RequestItem]:
    path = find_request_path(loom, rq_id)
    return path, read_model(path, RequestItem)


def find_inbox_path(loom: Path, rq_id: str) -> Path:
    return find_request_path(loom, rq_id)


def load_inbox_item(loom: Path, rq_id: str) -> tuple[Path, RequestItem]:
    return load_request_item(loom, rq_id)


def find_routine_path(loom: Path, routine_id: str) -> Path:
    path = routines_dir(loom) / f"{routine_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"routine '{routine_id}' not found")
    return path


def load_routine(loom: Path, routine_id: str) -> tuple[Path, Routine]:
    path = find_routine_path(loom, routine_id)
    return path, read_model(path, Routine)


def agents_dir(loom: Path) -> Path:
    return loom / "agents"


def worker_agents_dir(loom: Path) -> Path:
    return agents_dir(loom) / "workers"


def manager_dir(loom: Path) -> Path:
    return agents_dir(loom) / "manager"


def legacy_manager_path(loom: Path) -> Path:
    return agents_dir(loom) / "_manager.md"


def manager_path(loom: Path) -> Path:
    preferred = manager_dir(loom) / "_agent.md"
    legacy = legacy_manager_path(loom)
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def legacy_agent_dir(loom: Path, agent_id: str) -> Path:
    return agents_dir(loom) / agent_id


def agent_dir(loom: Path, agent_id: str) -> Path:
    preferred = worker_agents_dir(loom) / agent_id
    legacy = legacy_agent_dir(loom, agent_id)
    if (preferred / "_agent.md").exists() or not legacy.exists():
        return preferred
    return legacy


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


def agent_worktrees_dir(loom: Path, agent_id: str) -> Path:
    return agent_dir(loom, agent_id) / "worktrees"


def worktree_record_path(loom: Path, agent_id: str, name: str) -> Path:
    canonical = canonical_thread_name(name)
    return agent_worktrees_dir(loom, agent_id) / f"{canonical}.md"


def find_worktree_path(loom: Path, agent_id: str, name: str) -> Path:
    path = worktree_record_path(loom, agent_id, name)
    if not path.exists():
        raise FileNotFoundError(f"worktree '{canonical_thread_name(name)}' not found for worker '{agent_id}'")
    return path


def load_worktree(loom: Path, agent_id: str, name: str) -> tuple[Path, WorktreeRecord]:
    path = find_worktree_path(loom, agent_id, name)
    return path, read_model(path, WorktreeRecord)
