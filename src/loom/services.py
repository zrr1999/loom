"""High-level operations for creating and mutating loom entities."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import load_settings
from .duration import normalize_interval
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
from .lease import is_thread_stale, refresh_thread_lease, utc_now
from .models import (
    AgentRecord,
    AgentRole,
    AgentStatus,
    Decision,
    DecisionOption,
    DeliveryContract,
    ManagerRecord,
    Message,
    MessageType,
    RequestItem,
    RequestResolution,
    RequestStatus,
    ReviewEntry,
    Routine,
    RoutineResult,
    RoutineStatus,
    Task,
    TaskKind,
    TaskStatus,
    Thread,
    ThreadPR,
    ThreadWorktree,
    WorktreeRecord,
    WorktreeStatus,
    find_review_blockers,
)
from .repository import (
    agent_dir,
    agent_pending_dir,
    agent_record_path,
    agent_replied_dir,
    agent_worktrees_dir,
    agents_dir,
    load_agent,
    load_manager,
    load_message,
    load_request_item,
    load_routine,
    load_task,
    load_worktree,
    manager_dir,
    manager_path,
    products_dir,
    products_reports_dir,
    requests_dir,
    routines_dir,
    task_file_path,
    worker_agents_dir,
    workspace_root,
    worktree_record_path,
)
from .scheduler import load_all_tasks, load_all_threads
from .state import (
    validate_decision_payload,
    validate_request_transition,
    validate_routine_transition,
    validate_task_scheduled,
    validate_task_transition,
)
from .templates import agent_body, routine_body, task_body, thread_body


class AmbiguousRequestRoutingError(ValueError):
    """Raised when request-to-thread inference is not reliable enough to continue."""


@dataclass(frozen=True)
class TaskMutationResult:
    task: Task
    path: Path
    created: bool
    merge_reason: str | None = None
    priority_changed: bool = False


def parse_csv_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item.strip() for item in value if item.strip()]
    return [item.strip() for item in value.split(",") if item.strip()]


def _merge_unique_items(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item and item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def _normalize_overlap_text(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text.lower()).strip()


def _title_overlap(left: str, right: str) -> bool:
    left_normalized = _normalize_overlap_text(left)
    right_normalized = _normalize_overlap_text(right)
    if not left_normalized or not right_normalized:
        return False
    if left_normalized == right_normalized:
        return True

    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    if not left_tokens or not right_tokens:
        return False

    shared = left_tokens & right_tokens
    if left_normalized in right_normalized or right_normalized in left_normalized:
        return len(shared) >= max(1, min(len(left_tokens), len(right_tokens)) - 1)

    overlap_ratio = len(shared) / max(len(left_tokens), len(right_tokens))
    return len(shared) >= 2 and overlap_ratio >= 0.6


def _elevated_priority(current: int, requested: int) -> int:
    baseline = max(current, requested)
    if baseline >= 100:
        return 100
    return min(100, baseline + 10)


def _find_task_merge_candidate(
    loom: Path,
    *,
    thread_name: str,
    title: str,
    created_from: list[str],
    kind: TaskKind,
) -> tuple[Task, str] | None:
    candidates: list[tuple[int, Task, str]] = []
    for existing in load_all_tasks(loom):
        if existing.thread != thread_name or existing.kind != kind or existing.status != TaskStatus.SCHEDULED:
            continue

        overlap_reasons: list[str] = []
        if created_from and set(existing.created_from) & set(created_from):
            overlap_reasons.append("created_from overlap")
        if _title_overlap(existing.title, title):
            overlap_reasons.append("title overlap")
        if not overlap_reasons:
            continue

        score = 2 if "created_from overlap" in overlap_reasons else 0
        if "title overlap" in overlap_reasons:
            score += 1
        candidates.append((score, existing, " + ".join(overlap_reasons)))

    if not candidates:
        return None

    _score, task, reason = sorted(
        candidates,
        key=lambda item: (-item[0], -item[1].priority, item[1].seq, item[1].id),
    )[0]
    return task, reason


def _split_routine_body_sections(body: str) -> tuple[str, str]:
    marker = "## Run Log"
    if marker not in body:
        msg = "Routine body must include a '## Run Log' section"
        raise ValueError(msg)
    head, _, tail = body.partition(marker)
    return head.rstrip(), tail.lstrip()


def extract_routine_log(body: str) -> str:
    """Return the raw append-only run log section body."""
    _head, log = _split_routine_body_sections(body)
    return log.strip()


def append_routine_log(body: str, *, ran_at: str, result: RoutineResult, note: str = "") -> str:
    """Append a single markdown bullet to the routine run log section."""
    head, log = _split_routine_body_sections(body)
    log_lines = [line.rstrip() for line in log.splitlines()]
    cleaned_lines = [line for line in log_lines if line.strip()]
    if cleaned_lines == ["<!-- append-only notes -->"]:
        cleaned_lines = []

    entry = f"- {ran_at} [{result.value}]"
    if note.strip():
        note_lines = [line.strip() for line in note.strip().splitlines() if line.strip()]
        if note_lines:
            entry = f"{entry} {note_lines[0]}"
            cleaned_lines.append(entry)
            cleaned_lines.extend(f"  {line}" for line in note_lines[1:])
        else:
            cleaned_lines.append(entry)
    else:
        cleaned_lines.append(entry)

    log_text = "\n".join(cleaned_lines) if cleaned_lines else "<!-- append-only notes -->"
    return f"{head}\n\n## Run Log\n\n{log_text}"


def _normalize_thread_signal(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text.lower().strip()).strip("-")


def _request_thread_signal(item: RequestItem) -> str:
    title = derive_task_title(item)
    return _normalize_thread_signal("\n".join(part for part in [title, item.body] if part))


def _signal_mentions_thread(signal: str, thread_name: str) -> bool:
    signal_parts = [part for part in signal.split("-") if part]
    thread_parts = [part for part in thread_name.split("-") if part]
    width = len(thread_parts)
    if width == 0 or len(signal_parts) < width:
        return False
    return any(signal_parts[index : index + width] == thread_parts for index in range(len(signal_parts) - width + 1))


def _ambiguous_thread_message(
    item: RequestItem,
    *,
    reason: str,
    available_threads: list[str],
    candidate_threads: list[str] | None = None,
) -> str:
    lines = [
        f"could not infer a target thread for {item.id}: {reason}",
        f"available threads: {', '.join(available_threads)}",
    ]
    if candidate_threads:
        lines.append(f"matching threads: {', '.join(candidate_threads)}")
    lines.append(
        f"choose one explicitly with `loom manage plan {item.id} --thread <name>` "
        "or create a new thread first with `loom manage new-thread --name <name>`"
    )
    return "\n".join(lines)


def _resolve_target_thread(
    item: RequestItem,
    threads: dict[str, Thread],
    *,
    requested_thread: str | None = None,
) -> Thread:
    if requested_thread:
        canonical = canonical_thread_name(requested_thread)
        thread = threads.get(canonical)
        if thread is None:
            raise FileNotFoundError(f"thread '{canonical}' does not exist")
        return thread

    if len(threads) == 1:
        return next(iter(threads.values()))

    signal = _request_thread_signal(item)
    candidates = sorted(
        (thread for thread in threads.values() if _signal_mentions_thread(signal, thread.name)),
        key=lambda thread: thread.name,
    )
    if len(candidates) == 1:
        return candidates[0]

    available = sorted(threads)
    if candidates:
        raise AmbiguousRequestRoutingError(
            _ambiguous_thread_message(
                item,
                reason="multiple existing threads match the request text",
                available_threads=available,
                candidate_threads=[thread.name for thread in candidates],
            )
        )
    raise AmbiguousRequestRoutingError(
        _ambiguous_thread_message(
            item,
            reason="request text does not clearly match an existing thread",
            available_threads=available,
        )
    )


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
    manager_dir(loom).mkdir(parents=True, exist_ok=True)
    products_dir(loom).mkdir(parents=True, exist_ok=True)
    products_reports_dir(loom).mkdir(parents=True, exist_ok=True)
    manager_file = manager_path(loom)
    if not manager_file.exists():
        manager = ManagerRecord(last_seen=datetime.now(UTC).isoformat(timespec="seconds"), checkpoint_summary="ready")
        write_model(manager_file, manager)


def ensure_worktree_storage(loom: Path, agent_id: str) -> Path:
    path = agent_worktrees_dir(loom, agent_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_worktree_name(name: str) -> str:
    resolved = canonical_thread_name(name)
    if not resolved:
        raise ValueError("worktree name must not be empty")
    return resolved


def _resolve_worktree_path(loom: Path, agent_id: str, *, name: str, value: str = "") -> Path:
    root = ensure_worktree_storage(loom, agent_id).resolve()
    candidate = Path(value).expanduser() if value.strip() else root / name
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"worktree path '{resolved}' must stay under '{root}' for worker '{agent_id}'") from exc
    return resolved


def _detect_git_branch(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _load_thread_metadata(loom: Path, thread_name: str) -> tuple[Path, Thread]:
    canonical = canonical_thread_name(thread_name)
    threads = load_all_threads(loom)
    thread = threads.get(canonical)
    if thread is None:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")
    return loom / "threads" / canonical / "_thread.md", thread


def _worktree_identity(record: WorktreeRecord) -> tuple[str, str, str]:
    return record.worker, record.name, str(Path(record.path).resolve())


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _ensure_unique_worktree_path(
    records: list[WorktreeRecord],
    *,
    candidate: Path,
    candidate_name: str,
) -> None:
    for existing in records:
        existing_path = Path(existing.path).resolve()
        if existing_path == candidate:
            raise ValueError(f"path '{candidate}' is already registered as worktree '{existing.name}'")
        if _paths_overlap(existing_path, candidate):
            raise ValueError(
                f"path '{candidate}' overlaps existing worktree '{existing.name}' at '{existing_path}'; "
                "nested or overlapping worktree paths are not allowed"
            )


def _active_thread_worktree_index(thread: Thread, record: WorktreeRecord) -> int | None:
    identity = _worktree_identity(record)
    for idx in range(len(thread.worktrees) - 1, -1, -1):
        item = thread.worktrees[idx]
        item_identity = (item.worker, item.name, str(Path(item.path).resolve()))
        if item.removed_at is None and item_identity == identity:
            return idx
    return None


def _write_thread_worktree_entry(
    loom: Path,
    thread_name: str,
    record: WorktreeRecord,
    *,
    removed_at: str | None = None,
) -> Thread:
    path, thread = _load_thread_metadata(loom, thread_name)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    existing_idx = _active_thread_worktree_index(thread, record)
    worktrees = list(thread.worktrees)
    if removed_at is not None:
        final_status = WorktreeStatus.ARCHIVED if record.status != WorktreeStatus.ARCHIVED else record.status
        if existing_idx is None:
            worktrees.append(
                ThreadWorktree(
                    name=record.name,
                    worker=record.worker,
                    path=str(Path(record.path).resolve()),
                    branch=record.branch,
                    status=final_status,
                    created_at=record.created_at or now,
                    removed_at=removed_at,
                )
            )
        else:
            existing = worktrees[existing_idx]
            worktrees[existing_idx] = existing.model_copy(
                update={
                    "path": str(Path(record.path).resolve()),
                    "branch": record.branch,
                    "status": final_status,
                    "removed_at": removed_at,
                }
            )
    else:
        entry = ThreadWorktree(
            name=record.name,
            worker=record.worker,
            path=str(Path(record.path).resolve()),
            branch=record.branch,
            status=record.status,
            created_at=record.created_at or now,
        )
        if existing_idx is None:
            worktrees.append(entry)
        else:
            preserved_created_at = worktrees[existing_idx].created_at or entry.created_at
            worktrees[existing_idx] = entry.model_copy(update={"created_at": preserved_created_at})

    updated = thread.model_copy(update={"worktrees": worktrees})
    write_model(path, updated)
    return updated


def _move_worktree_thread_link(
    loom: Path,
    previous_thread: str | None,
    next_thread: str | None,
    record: WorktreeRecord,
    *,
    removed_at: str | None = None,
) -> None:
    if previous_thread and previous_thread != next_thread:
        _write_thread_worktree_entry(
            loom,
            previous_thread,
            record,
            removed_at=removed_at or datetime.now(UTC).isoformat(timespec="seconds"),
        )
    if next_thread:
        _write_thread_worktree_entry(loom, next_thread, record)


_GITHUB_PR_RE = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _looks_like_local_output_reference(output: str) -> bool:
    text = output.strip()
    if not text or "\n" in text:
        return False
    if any(char.isspace() for char in text):
        return False
    return _URL_RE.match(text) is None


def _sanitize_product_relative_path(path: Path) -> Path:
    parts = [part for part in path.parts if part not in ("", ".")]
    if not parts:
        raise ValueError("output path must not be empty")
    if any(part == ".." for part in parts):
        raise ValueError("output path must stay within .loom/products/")
    if parts[:2] == [".loom", "products"]:
        parts = parts[2:]
    elif parts[:1] == [".loom"]:
        parts = parts[1:]
    if not parts:
        raise ValueError("output path must name a file or directory under .loom/products/")
    return Path(*parts)


def _rewrite_legacy_worker_output_path(path: Path) -> Path:
    parts = [part for part in path.parts if part not in ("", ".")]
    if len(parts) < 5 or parts[:2] != [".loom", "agents"]:
        return path

    output_index: int | None = None
    if len(parts) >= 6 and parts[2] == "workers" and parts[4] == "outputs":
        output_index = 4
    elif parts[3] == "outputs":
        output_index = 3

    if output_index is None or len(parts) <= output_index + 1:
        return path

    return Path("reports", *parts[output_index + 1 :])


def normalize_task_output(loom: Path, output: str | None) -> str | None:
    if output is None:
        return None

    text = output.strip()
    if not text or not _looks_like_local_output_reference(text):
        return output

    workspace = workspace_root(loom).resolve()
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(products_dir(loom).resolve())
        except ValueError:
            try:
                relative = resolved.relative_to(workspace)
            except ValueError as exc:
                raise ValueError(f"output path '{resolved}' must stay inside the workspace or .loom/products/") from exc
    else:
        relative = candidate

    normalized_relative = _sanitize_product_relative_path(_rewrite_legacy_worker_output_path(relative))
    normalized = Path(".loom") / "products" / normalized_relative
    (workspace / normalized).parent.mkdir(parents=True, exist_ok=True)
    return normalized.as_posix()


def _normalize_delivery_contract(loom: Path, delivery: DeliveryContract | None) -> DeliveryContract | None:
    if delivery is None:
        return None
    normalized_artifacts = [normalize_task_output(loom, artifact) or artifact for artifact in delivery.artifacts]
    normalized_pr_urls = [url.strip() for url in delivery.pr_urls if url.strip()]
    return delivery.model_copy(update={"artifacts": normalized_artifacts, "pr_urls": normalized_pr_urls})


def _record_thread_pr_artifacts(
    loom: Path,
    task: Task,
    *,
    output: str | None = None,
    delivery: DeliveryContract | None = None,
) -> None:
    contract = delivery if delivery is not None else task.delivery
    pr_urls = list(dict.fromkeys(contract.pr_urls)) if contract is not None else []
    if pr_urls:
        matches = [(url, _GITHUB_PR_RE.fullmatch(url)) for url in pr_urls]
        matches = [(url, match) for url, match in matches if match is not None]
    else:
        text = output if output is not None else (task.output or "")
        matches = [(match.group(0), match) for match in _GITHUB_PR_RE.finditer(text)]
    if not matches:
        return

    path, thread = _load_thread_metadata(loom, task.thread)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    active_worktree = next((item for item in reversed(thread.worktrees) if item.removed_at is None), None)
    artifacts = list(thread.pr_artifacts)
    changed = False
    for url, match in matches:
        owner, repo, number = match.groups()
        index = next((idx for idx, item in enumerate(artifacts) if item.url == url), None)
        payload = {
            "url": url,
            "provider": "github",
            "repository": f"{owner}/{repo}",
            "number": int(number),
            "branch": active_worktree.branch if active_worktree else None,
            "worker": active_worktree.worker if active_worktree else None,
            "worktree": active_worktree.name if active_worktree else None,
            "updated_at": now,
        }
        if index is None:
            artifacts.append(
                ThreadPR(
                    **payload,
                    task_ids=[task.id],
                    recorded_at=now,
                )
            )
            changed = True
            continue

        existing = artifacts[index]
        task_ids = list(existing.task_ids)
        if task.id not in task_ids:
            task_ids.append(task.id)
        updated = existing.model_copy(update=payload | {"task_ids": task_ids})
        if updated.model_dump(mode="python") != existing.model_dump(mode="python"):
            artifacts[index] = updated
            changed = True

    if changed:
        write_model(path, thread.model_copy(update={"pr_artifacts": artifacts}))


def _worktree_has_dirty_git_state(path: Path) -> bool:
    git_dir = path / ".git"
    if not git_dir.exists():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def load_all_worktrees(loom: Path, agent_id: str) -> list[WorktreeRecord]:
    root = agent_worktrees_dir(loom, agent_id)
    if not root.exists():
        return []
    return [load_worktree(loom, agent_id, path.stem)[1] for path in sorted(root.glob("*.md"))]


def resolve_current_worktree(
    loom: Path,
    agent_id: str,
    *,
    cwd: Path | None = None,
) -> tuple[Path, WorktreeRecord] | None:
    current_dir = (cwd or Path.cwd()).resolve()
    matches: list[tuple[int, Path, WorktreeRecord]] = []
    for record in load_all_worktrees(loom, agent_id):
        checkout_root = Path(record.path).resolve()
        try:
            current_dir.relative_to(checkout_root)
        except ValueError:
            continue
        matches.append((len(checkout_root.parts), checkout_root, record))
    if not matches:
        return None
    _depth, checkout_root, record = max(matches, key=lambda item: item[0])
    return checkout_root, record


def resolve_actor_workspace_root(loom: Path, agent_id: str = "", *, cwd: Path | None = None) -> Path:
    if agent_id:
        current = resolve_current_worktree(loom, agent_id, cwd=cwd)
        if current is not None:
            checkout_root, _record = current
            return checkout_root
    return workspace_root(loom)


def add_worktree(
    loom: Path,
    agent_id: str,
    *,
    name: str,
    path: str = "",
    branch: str = "",
    status: WorktreeStatus = WorktreeStatus.REGISTERED,
) -> tuple[WorktreeRecord, Path]:
    resolved_name = _resolve_worktree_name(name.strip())
    resolved_path = _resolve_worktree_path(loom, agent_id, name=resolved_name, value=path)
    if resolved_path.exists() and not resolved_path.is_dir():
        raise ValueError(f"worktree path '{resolved_path}' must be a directory")

    existing = load_all_worktrees(loom, agent_id)
    if any(record.name == resolved_name for record in existing):
        raise ValueError(f"worktree '{resolved_name}' already exists")
    _ensure_unique_worktree_path(existing, candidate=resolved_path, candidate_name=resolved_name)
    resolved_path.mkdir(parents=True, exist_ok=True)

    resolved_branch = branch.strip() or _detect_git_branch(resolved_path)
    if not resolved_branch:
        raise ValueError(
            f"could not determine git branch for '{resolved_path}'; create the git worktree there "
            "first or pass --branch explicitly"
        )

    now = datetime.now(UTC).isoformat(timespec="seconds")
    record = WorktreeRecord(
        name=resolved_name,
        path=str(resolved_path),
        branch=resolved_branch,
        status=status,
        worker=agent_id,
        created_at=now,
        updated_at=now,
        body=(
            "Worker-local discovery metadata. Thread-owned linkage/history lives on "
            ".loom/threads/<thread>/_thread.md, while other workers only see worktrees "
            "inside their own agent subtree."
        ),
    )
    record_path = worktree_record_path(loom, agent_id, resolved_name)
    write_model(record_path, record)
    append_event(
        loom,
        "worktree.registered",
        "worktree",
        record.name,
        {
            "path": record.path,
            "branch": record.branch,
            "status": record.status.value,
            "worker": agent_id,
        },
    )
    return record, record_path


def attach_worktree(
    loom: Path,
    agent_id: str,
    name: str,
    *,
    thread: str | None = None,
    status: WorktreeStatus | None = None,
    clear: bool = False,
) -> tuple[Path, WorktreeRecord]:
    path, record = load_worktree(loom, agent_id, name)
    if record.worker != agent_id:
        raise ValueError(f"worktree '{record.name}' belongs to worker '{record.worker}', not '{agent_id}'")

    normalized_thread = canonical_thread_name(thread) if thread else None
    updates: dict[str, object] = {
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if clear:
        updates["thread"] = None
        updates["status"] = status or WorktreeStatus.REGISTERED
    else:
        if thread is not None:
            updates["thread"] = normalized_thread
        updates["status"] = status or (WorktreeStatus.ACTIVE if normalized_thread else record.status)

    updated = record.model_copy(update=updates)
    previous_thread = record.thread
    effective_thread = updated.thread
    if previous_thread != effective_thread:
        _move_worktree_thread_link(
            loom,
            previous_thread,
            effective_thread,
            updated,
            removed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    elif effective_thread:
        _write_thread_worktree_entry(loom, effective_thread, updated)
    write_model(path, updated)
    append_event(
        loom,
        "worktree.attached",
        "worktree",
        updated.name,
        {
            "worker": updated.worker,
            "thread": updated.thread,
            "status": updated.status.value,
            "clear": clear,
        },
    )
    return path, updated


def remove_worktree(loom: Path, agent_id: str, name: str, *, force: bool = False) -> tuple[Path, WorktreeRecord]:
    path, record = load_worktree(loom, agent_id, name)
    if record.worker != agent_id:
        raise ValueError(f"worktree '{record.name}' belongs to worker '{record.worker}', not '{agent_id}'")
    if record.thread and not force:
        raise ValueError(
            f"worktree '{record.name}' is still attached to thread metadata; "
            "clear it first with `loom agent worktree attach <name> --clear` or pass --force"
        )
    checkout_path = Path(record.path).resolve()
    if checkout_path.exists() and _worktree_has_dirty_git_state(checkout_path) and not force:
        raise ValueError(
            f"worktree '{record.name}' at '{checkout_path}' has uncommitted changes; "
            "clean it first or pass --force for full cleanup"
        )

    removed_at = datetime.now(UTC).isoformat(timespec="seconds")
    if record.thread:
        _write_thread_worktree_entry(
            loom,
            record.thread,
            record.model_copy(update={"status": WorktreeStatus.ARCHIVED}),
            removed_at=removed_at,
        )

    append_event(
        loom,
        "worktree.removed",
        "worktree",
        record.name,
        {"path": record.path, "worker": record.worker, "thread": record.thread, "forced": force},
    )
    if checkout_path.exists():
        shutil.rmtree(checkout_path)
    path.unlink()
    return path, record


def create_request_item(loom: Path, description: str) -> tuple[RequestItem, Path]:
    """Create a new pending request item."""
    body = description.strip()
    if not body:
        raise ValueError("description must not be empty")

    request_root = requests_dir(loom)
    seq = next_inbox_seq(request_root)
    rq_id = f"RQ-{seq:03d}"
    item = RequestItem(id=rq_id, body=body)
    path = request_root / f"{rq_id}.md"
    write_model(path, item)
    append_event(loom, "request.created", "request", item.id, {"status": item.status.value})
    return item, path


def create_inbox_item(loom: Path, description: str) -> tuple[RequestItem, Path]:
    """Compatibility alias for request creation."""
    return create_request_item(loom, description)


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
    now = utc_now()
    updated_at = now.isoformat(timespec="seconds")
    updated_body = f"## Checkpoint\n\n**phase** {phase}\n**updated** {updated_at}\n\n{summary}\n\n## Notes\n\n"
    updated = agent.model_copy(
        update={
            "last_seen": updated_at,
            "status": AgentStatus.ACTIVE,
            "checkpoint_summary": summary[:120],
            "body": updated_body,
        }
    )
    write_model(path, updated)

    refreshed_threads: list[str] = []
    for thread_name, thread in load_all_threads(loom).items():
        if thread.owner != agent_id:
            continue
        refreshed = refresh_thread_lease(thread, loom, now=now)
        write_model(loom / "threads" / thread_name / "_thread.md", refreshed)
        refreshed_threads.append(thread_name)

    append_event(
        loom,
        "agent.checkpointed",
        "agent",
        agent_id,
        {"phase": phase, "refreshed_threads": refreshed_threads},
    )
    return updated


def update_manager_checkpoint(loom: Path, *, phase: str, summary: str) -> ManagerRecord:
    ensure_agent_layout(loom)
    path, manager = load_manager(loom)
    updated_at = utc_now().isoformat(timespec="seconds")
    updated_body = f"## Checkpoint\n\n**phase** {phase}\n**updated** {updated_at}\n\n{summary}\n\n## Notes\n\n"
    updated = manager.model_copy(
        update={
            "last_seen": updated_at,
            "status": "active",
            "checkpoint_summary": summary[:120],
            "body": updated_body,
        }
    )
    write_model(path, updated)
    append_event(
        loom,
        "manager.checkpointed",
        "agent",
        "manager",
        {"phase": phase},
    )
    return updated


def resume_agent(loom: Path, agent_id: str) -> AgentRecord:
    _, agent = load_agent(loom, agent_id)
    return agent


def resume_manager(loom: Path) -> ManagerRecord:
    ensure_agent_layout(loom)
    _, manager = load_manager(loom)
    return manager


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


def adjust_thread_priority(loom: Path, thread_name: str, *, priority: int) -> tuple[Path, Thread]:
    """Persist a thread priority change and return the updated record."""
    canonical = canonical_thread_name(thread_name)
    threads = load_all_threads(loom)
    if canonical not in threads:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")

    thread = threads[canonical]
    path = loom / "threads" / canonical / "_thread.md"
    updated = thread.model_copy(update={"priority": priority})
    write_model(path, updated)
    append_event(
        loom,
        "thread.priority_updated",
        "thread",
        canonical,
        {"from": thread.priority, "to": updated.priority},
    )
    return path, updated


def adjust_task_priority(loom: Path, task_id: str, *, priority: int) -> tuple[Path, Task]:
    """Persist a task priority change and return the updated record."""
    path, task = load_task(loom, task_id)
    updated = task.model_copy(update={"priority": priority})
    write_model(path, updated)
    append_event(
        loom,
        "task.priority_updated",
        "task",
        task.id,
        {"from": task.priority, "to": updated.priority, "thread": task.thread},
    )
    return path, updated


def create_routine(
    loom: Path,
    *,
    routine_id: str,
    title: str,
    interval: str,
    assigned_to: str | None = None,
    created_from: str | list[str] | None = None,
    responsibilities: str = "",
) -> tuple[Routine, Path]:
    """Create a new routine file under `.loom/routines/`."""
    routines_root = routines_dir(loom)
    routines_root.mkdir(parents=True, exist_ok=True)
    path = routines_root / f"{routine_id}.md"
    if path.exists():
        raise FileExistsError(f"routine '{routine_id}' already exists")

    routine = Routine(
        id=routine_id,
        title=title.strip() or routine_id,
        status=RoutineStatus.ACTIVE,
        interval=normalize_interval(interval),
        assigned_to=assigned_to.strip() if assigned_to else None,
        created_from=parse_csv_list(created_from),
        body=routine_body(responsibilities),
    )
    write_model(path, routine)
    append_event(
        loom,
        "routine.created",
        "routine",
        routine.id,
        {
            "status": routine.status.value,
            "interval": routine.interval,
            "assigned_to": routine.assigned_to,
            "created_from": routine.created_from,
        },
    )
    return routine, path


def set_routine_status(loom: Path, routine_id: str, *, target_status: RoutineStatus) -> tuple[Path, Routine]:
    """Update a routine lifecycle status."""
    path, routine = load_routine(loom, routine_id)
    validate_routine_transition(routine.status, target_status)
    updated = routine.model_copy(update={"status": target_status})
    write_model(path, updated)
    append_event(
        loom,
        "routine.transitioned",
        "routine",
        routine.id,
        {"from": routine.status.value, "to": updated.status.value},
    )
    return path, updated


def record_routine_run(
    loom: Path,
    routine_id: str,
    *,
    result: RoutineResult,
    note: str = "",
    ran_at: str | None = None,
) -> tuple[Path, Routine]:
    """Persist routine run metadata and append to its markdown run log."""
    path, routine = load_routine(loom, routine_id)
    effective_ran_at = ran_at or datetime.now(UTC).isoformat(timespec="seconds")
    updated = routine.model_copy(
        update={
            "last_run": effective_ran_at,
            "last_result": result,
            "body": append_routine_log(routine.body, ran_at=effective_ran_at, result=result, note=note),
        }
    )
    write_model(path, updated)
    append_event(
        loom,
        "routine.run_recorded",
        "routine",
        routine.id,
        {"last_run": updated.last_run, "last_result": updated.last_result.value if updated.last_result else None},
    )
    return path, updated


def trigger_routine(loom: Path, routine_id: str, *, forced: bool = False) -> dict[str, object]:
    """Send a routine trigger message to the assigned worker."""
    _path, routine = load_routine(loom, routine_id)
    if not routine.assigned_to:
        msg = f"routine '{routine.id}' has no assigned worker; direct manager execution is not supported"
        raise ValueError(msg)

    body_lines = [
        f"Routine is {'force-triggered' if forced else 'due'}.",
        f"Execute the responsibilities in .loom/routines/{routine.id}.md and reply with the run result summary.",
    ]
    message = create_message(
        loom,
        sender="manager",
        recipient=routine.assigned_to,
        message_type=MessageType.ROUTINE_TRIGGER,
        body=" ".join(body_lines),
        ref=routine.id,
    )
    append_event(
        loom,
        "routine.triggered",
        "routine",
        routine.id,
        {"recipient": routine.assigned_to, "forced": forced, "message_id": message["id"]},
    )
    return {"routine": routine, "message": message}


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
    persistent: bool = False,
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
        persistent=True if persistent else None,
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


def create_or_merge_task(
    loom: Path,
    *,
    thread_name: str,
    title: str,
    kind: TaskKind = TaskKind.IMPLEMENTATION,
    priority: int = 50,
    acceptance: str = "",
    depends_on: str | list[str] | None = None,
    created_from: str | list[str] | None = None,
    persistent: bool = False,
    background: str = "",
    implementation_direction: str = "",
) -> TaskMutationResult:
    canonical_thread = canonical_thread_name(thread_name)
    parsed_created_from = parse_csv_list(created_from)
    merge_candidate = _find_task_merge_candidate(
        loom,
        thread_name=canonical_thread,
        title=title,
        created_from=parsed_created_from,
        kind=kind,
    )
    if merge_candidate is None:
        task, path = create_task(
            loom,
            thread_name=canonical_thread,
            title=title,
            kind=kind,
            priority=priority,
            acceptance=acceptance,
            depends_on=depends_on,
            created_from=parsed_created_from,
            persistent=persistent,
            background=background,
            implementation_direction=implementation_direction,
        )
        return TaskMutationResult(task=task, path=path, created=True)

    task, reason = merge_candidate
    path = task_file_path(loom, task)
    merged_created_from = _merge_unique_items(task.created_from, parsed_created_from)
    merged_depends_on = _merge_unique_items(task.depends_on, parse_csv_list(depends_on))
    elevated_priority = _elevated_priority(task.priority, priority)
    updates: dict[str, object] = {
        "priority": elevated_priority,
        "created_from": merged_created_from,
        "depends_on": merged_depends_on,
        "persistent": True if task.persistent or persistent else None,
    }
    if not task.acceptance and acceptance.strip():
        updates["acceptance"] = acceptance.strip()
        updates["status"] = TaskStatus.SCHEDULED

    updated = Task.model_validate(task.model_dump(mode="python") | updates)
    write_model(path, updated)
    append_event(
        loom,
        "task.merged",
        "task",
        task.id,
        {
            "thread": task.thread,
            "reason": reason,
            "priority_from": task.priority,
            "priority_to": updated.priority,
            "created_from": updated.created_from,
        },
    )
    return TaskMutationResult(
        task=updated,
        path=path,
        created=False,
        merge_reason=reason,
        priority_changed=updated.priority != task.priority,
    )


def transition_task(
    loom: Path,
    task_id: str,
    target_status: TaskStatus,
    *,
    output: str | None = None,
    delivery: DeliveryContract | None = None,
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
        updates["output"] = normalize_task_output(loom, output)
    if delivery is not None:
        updates["delivery"] = _normalize_delivery_contract(loom, delivery)
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


def complete_task(
    loom: Path,
    task_id: str,
    *,
    output: str | None = None,
    delivery: DeliveryContract | None = None,
) -> tuple[Path, Task, list[str]]:
    path, task = load_task(loom, task_id)
    normalized_delivery = _normalize_delivery_contract(loom, delivery)
    if task.persistent:
        updates: dict[str, object] = {}
        normalized_output = normalize_task_output(loom, output)
        if normalized_output is not None:
            updates["output"] = normalized_output
        if normalized_delivery is not None:
            updates["delivery"] = normalized_delivery
        updated = task.model_copy(update=updates)
        write_model(path, updated)
        _record_thread_pr_artifacts(loom, updated, output=normalized_output, delivery=normalized_delivery)
        append_event(
            loom,
            "task.persistent_recorded",
            "task",
            task.id,
            {
                "thread": task.thread,
                "output": normalized_output,
                "delivery_ready": normalized_delivery.ready if normalized_delivery is not None else None,
            },
        )
        return path, updated, []

    blockers = (
        []
        if normalized_delivery is not None and normalized_delivery.ready
        else find_review_blockers(task, output=output)
    )
    if not blockers:
        path, updated = transition_task(
            loom,
            task_id,
            TaskStatus.REVIEWING,
            output=output,
            delivery=normalized_delivery,
        )
        _record_thread_pr_artifacts(loom, updated, output=output, delivery=normalized_delivery)
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
    path, updated = transition_task(
        loom,
        task_id,
        TaskStatus.PAUSED,
        output=output,
        delivery=normalized_delivery,
        decision=decision,
    )
    return path, updated, blockers


def claim_thread(loom: Path, thread_name: str, *, agent_id: str) -> tuple[Path, Thread]:
    """Claim a thread for an agent.  One active owner per thread maximum."""
    threads = load_all_threads(loom)
    canonical = canonical_thread_name(thread_name)
    if canonical not in threads:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")

    thread = threads[canonical]
    if thread.owner and thread.owner != agent_id and not is_thread_stale(thread):
        raise ValueError(f"thread '{canonical}' is already owned by '{thread.owner}'")

    path = loom / "threads" / canonical / "_thread.md"
    now = utc_now()
    if thread.owner == agent_id:
        refreshed = refresh_thread_lease(thread, loom, now=now)
        if refreshed.model_dump(mode="python") != thread.model_dump(mode="python"):
            write_model(path, refreshed)
            append_event(
                loom,
                "thread.lease_refreshed",
                "thread",
                canonical,
                {"agent": agent_id, "lease_expires_at": refreshed.owner_lease_expires_at},
            )
        return path, refreshed

    claimed_at = now.isoformat(timespec="seconds")
    updated = refresh_thread_lease(thread.model_copy(update={"owner": agent_id, "owned_at": claimed_at}), loom, now=now)
    write_model(path, updated)
    event_name = "thread.reclaimed" if thread.owner and is_thread_stale(thread, now=now) else "thread.claimed"
    append_event(
        loom,
        event_name,
        "thread",
        canonical,
        {
            "agent": agent_id,
            "owned_at": claimed_at,
            "previous_owner": thread.owner,
            "lease_expires_at": updated.owner_lease_expires_at,
        },
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

    updated = thread.model_copy(
        update={
            "owner": None,
            "owned_at": None,
            "owner_heartbeat_at": None,
            "owner_lease_expires_at": None,
        }
    )
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


def assign_thread(loom: Path, thread_name: str, *, agent_id: str, note: str = "") -> tuple[Path, Thread]:
    """Explicitly assign a thread to an agent, including bounded reassignment."""
    threads = load_all_threads(loom)
    canonical = canonical_thread_name(thread_name)
    if canonical not in threads:
        raise FileNotFoundError(f"thread '{canonical}' does not exist")

    thread = threads[canonical]
    if thread.owner == agent_id:
        return claim_thread(loom, canonical, agent_id=agent_id)
    if not thread.owner or is_thread_stale(thread):
        return claim_thread(loom, canonical, agent_id=agent_id)

    path = loom / "threads" / canonical / "_thread.md"
    now = utc_now()
    claimed_at = now.isoformat(timespec="seconds")
    updated = refresh_thread_lease(thread.model_copy(update={"owner": agent_id, "owned_at": claimed_at}), loom, now=now)
    write_model(path, updated)
    append_event(
        loom,
        "thread.reassigned",
        "thread",
        canonical,
        {
            "agent": agent_id,
            "owned_at": claimed_at,
            "previous_owner": thread.owner,
            "lease_expires_at": updated.owner_lease_expires_at,
            "note": note,
        },
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


def plan_request_item(loom: Path, rq_id: str, *, thread_name: str | None = None) -> dict[str, object]:
    request_path, item = load_request_item(loom, rq_id)
    validate_request_transition(item.status, RequestStatus.PROCESSING)

    processing_item = item.model_copy(update={"status": RequestStatus.PROCESSING})
    write_model(request_path, processing_item)
    append_event(loom, "request.processing", "request", item.id, {"status": processing_item.status.value})

    settings = load_settings(workspace_root(loom))
    created_thread_name: str | None = None
    try:
        threads = load_all_threads(loom)
        if not threads and not thread_name:
            thread, _, _duplicates = create_thread(
                loom,
                name="general",
                priority=settings.threads.default_priority,
            )
            threads = {thread.name: thread}
            created_thread_name = thread.name

        target_thread = _resolve_target_thread(item, threads, requested_thread=thread_name)
        title = derive_task_title(item)
        acceptance = "- [ ] 覆盖需求描述中的核心行为\n- [ ] 产出可供人工验收的结果"
        task_result = create_or_merge_task(
            loom,
            thread_name=target_thread.name,
            title=title,
            priority=target_thread.priority,
            acceptance=acceptance,
            created_from=[item.id],
            background=item.body,
            implementation_direction=f"围绕 {item.id} 先拆出第一条可执行任务, 后续再继续细化。",
        )
    except Exception:
        rollback_item = item.model_copy(update={"status": RequestStatus.PENDING})
        write_model(request_path, rollback_item)
        append_event(loom, "request.reverted", "request", item.id, {"status": rollback_item.status.value})
        raise

    task = task_result.task
    path = task_result.path
    resolved_to = [task.id]
    resolution = RequestResolution.TASK if task_result.created else RequestResolution.MERGED
    resolution_note = None
    if not task_result.created:
        note = f"Merged into existing task {task.id} ({task_result.merge_reason or 'overlap detected'})."
        if task_result.priority_changed:
            note += f" Priority elevated to {task.priority}."
        resolution_note = note
    updated_item = item.model_copy(
        update={
            "status": RequestStatus.DONE,
            "resolved_as": resolution,
            "resolved_to": resolved_to,
            "resolution_note": resolution_note,
            "planned_to": None,
        }
    )
    write_model(request_path, updated_item)
    append_event(
        loom,
        "request.resolved",
        "request",
        item.id,
        {
            "resolved_as": updated_item.resolved_as.value if updated_item.resolved_as else None,
            "resolved_to": resolved_to,
            "created_thread": created_thread_name,
            "resolution_note": resolution_note,
        },
    )

    return {
        "rq_id": item.id,
        "status": updated_item.status.value,
        "resolved_as": updated_item.resolved_as.value if updated_item.resolved_as else None,
        "resolved_to": resolved_to,
        "resolution_note": resolution_note,
        "created_thread": created_thread_name,
        "tasks": [{"id": task.id, "file": str(path)}] if task_result.created else [],
    }


def plan_inbox_item(loom: Path, rq_id: str, *, thread_name: str | None = None) -> dict[str, object]:
    """Compatibility alias for request-to-task triage."""
    return plan_request_item(loom, rq_id, thread_name=thread_name)


def derive_task_title(item: RequestItem) -> str:
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
    path, updated = transition_task(loom, task_id, TaskStatus.SCHEDULED, rejection_note=note, review_entry=entry)
    return path, updated


def accept_task(loom: Path, task_id: str, *, note: str = "") -> tuple[Path, Task]:
    entry = ReviewEntry(
        kind="accept",
        actor="human",
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        note=note,
        source="cli",
    )
    return transition_task(loom, task_id, TaskStatus.DONE, review_entry=entry)


def format_review_summary(task: Task, *, thread: Thread | None = None) -> list[str]:
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
    if task.delivery is not None:
        lines.append("  delivery:")
        lines.append(f"    ready: {'yes' if task.delivery.ready else 'no'}")
        if task.delivery.summary:
            lines.append(f"    summary: {task.delivery.summary}")
        if task.delivery.artifacts:
            lines.append("    artifacts:")
            lines.extend(f"      - {artifact}" for artifact in task.delivery.artifacts)
        if task.delivery.pr_urls:
            lines.append("    pr_urls:")
            lines.extend(f"      - {url}" for url in task.delivery.pr_urls)
    if thread and thread.pr_artifacts:
        lines.append("  thread_prs:")
        for artifact in thread.pr_artifacts:
            pr_line = f"    - {artifact.url}"
            if artifact.branch:
                pr_line += f" (branch: {artifact.branch})"
            lines.append(pr_line)
    if thread and thread.worktrees:
        active_worktrees = [item for item in thread.worktrees if item.removed_at is None]
        if active_worktrees:
            lines.append("  thread_worktrees:")
            for item in active_worktrees:
                lines.append(f"    - {item.name} [{item.status.value}] {item.path}")

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
