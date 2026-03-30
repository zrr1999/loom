"""Generate drift-prone documentation blocks from canonical source code."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .agent_command_catalog import (
    README_COMMAND_PREFIX,
    render_manager_command_access,
    render_manager_command_contract,
)
from .models import TASK_TRANSITIONS, TaskStatus

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class GeneratedBlock:
    """A single marker-delimited documentation block."""

    path: Path
    marker: str
    renderer: Callable[[], str]


def render_readme_task_storage_model() -> str:
    """Render the canonical README summary for thread/task storage identity."""

    return (
        "Thread directories stay human-readable (`.loom/threads/backend/`). "
        "`_thread.md` stores the canonical metadata for that directory. "
        "Task files inside a thread are sequence-only (`001.md`, `002.md`), and each task's "
        "frontmatter `id` is generated as `<thread-name>-<seq>` (for example `backend-001`)."
    )


def render_task_file_model() -> str:
    """Render the canonical task-file reference block."""

    active_statuses = " | ".join(status.value for status in TaskStatus if status != TaskStatus.CLAIMED)
    lines = [
        "- filename is only the per-thread sequence (`001.md`, `002.md`, ...)",
        "- `id`: global task id composed as `<thread-name>-<seq>` (for example `backend-001`)",
        f"- `status`: `{active_statuses}`",
        ("  - legacy `claimed` task statuses are read only for backward-compat migration"),
        (
            "  - migration moves old task-level claims to thread ownership in `_thread.md` "
            "(`owner`, `owned_at`, `owner_lease_expires_at`) and rewrites the task to `scheduled`"
        ),
        "- `persistent`: optional `true` flag for long-running tasks that should stay scheduled after each completion",
        "- `depends_on`: cross-thread task dependencies",
        "- `created_from`: source request IDs (`RQ-xxx`)",
        (
            "- `claim`: *(deprecated)* legacy task-level claim; migration strips it from task files "
            "after upgrading old workspaces because ownership is now thread-level"
        ),
        "- `decision`: required while `paused`",
        "- `rejection_note`: legacy compatibility mirror for the latest rejection note recorded in `review_history`",
        "- `review_history`: append-only accept/reject event history",
        "- `acceptance`: required before entering `scheduled`",
        "- `delivery`: optional explicit review handoff contract (`ready`, `artifacts`, `pr_urls`)",
        (
            "- `output`: task-level delivery reference; relative local paths are normalized under "
            "`.loom/products/`, while URLs / freeform review notes stay as entered"
        ),
        (
            "- `reviewing`: allowed when either the explicit `delivery.ready` contract is true "
            "or the legacy body/output heuristics find no TODO / proposal-only / known-follow-up markers"
        ),
        (
            "- task markdown keeps `## 背景` / `## 实现方向` sections, but leaves them empty "
            "unless real context is provided"
        ),
    ]
    return "\n".join(lines)


def render_worker_agent_next_text_example() -> str:
    """Render a canonical worker `loom agent next` example."""

    return "\n".join(
        [
            "ACTION  pickup",
            "COUNT   1",
            "ACTOR   x7k2",
            "THREAD  backend",
            "",
            "ASSIGNED TASKS",
            "  TASK  backend-003",
            "    title      : Build login page",
            "    kind       : implementation",
            "    thread     : backend",
            "    status     : scheduled",
            "    priority   : 50",
            "    file       : .loom/threads/backend/003.md",
            "    acceptance :",
            "      - [ ] Render the login form",
            "",
            "When finished with each task:",
            "  loom agent done <task-id> [--output <.loom/products/...|url>]",
            "",
            "If blocked and need a decision:",
            "  loom agent pause <task-id> --question '<question>'",
        ]
    )


def render_worker_agent_next_json_example() -> str:
    """Render the recommended future JSON shape for worker `loom agent next`."""

    return "\n".join(
        [
            "{",
            '  "action": "pickup",',
            '  "count": 1,',
            '  "actor": "x7k2",',
            '  "threads": ["backend"],',
            '  "tasks": [',
            "    {",
            '      "id": "backend-003",',
            '      "thread": "backend",',
            '      "title": "Build login page",',
            '      "kind": "implementation",',
            '      "status": "scheduled",',
            '      "priority": 50,',
            '      "depends_on": [],',
            '      "acceptance": "- [ ] Render the login form",',
            '      "file": ".loom/threads/backend/003.md"',
            "    }",
            "  ]",
            "}",
        ]
    )


def render_task_status_guide() -> str:
    """Render the current task status guidance for design docs."""

    return "\n".join(
        [
            "TaskStatus enum:",
            '  DRAFT = "draft" ← not in interactive queue',
            '  SCHEDULED = "scheduled" ← next for an agent; active ownership lives on the thread',
            '  CLAIMED = "claimed" ← deprecated legacy task status; read only for backward-compat migration',
            '  PAUSED = "paused" ← QUEUE: awaiting human decision',
            '  REVIEWING = "reviewing" ← QUEUE: awaiting human approval',
            '  DONE = "done" ← terminal',
        ]
    )


def render_task_transition_guide() -> str:
    """Render the current task transition guidance for design docs."""

    def _format_targets(status: TaskStatus) -> str:
        canonical_order = {
            TaskStatus.DRAFT: [TaskStatus.SCHEDULED],
            TaskStatus.SCHEDULED: [TaskStatus.REVIEWING, TaskStatus.PAUSED],
            TaskStatus.CLAIMED: [TaskStatus.REVIEWING, TaskStatus.PAUSED, TaskStatus.SCHEDULED],
            TaskStatus.REVIEWING: [TaskStatus.DONE, TaskStatus.SCHEDULED],
            TaskStatus.PAUSED: [TaskStatus.SCHEDULED],
            TaskStatus.DONE: [TaskStatus.SCHEDULED],
        }
        allowed = TASK_TRANSITIONS[status]
        targets = [candidate.name for candidate in canonical_order[status] if candidate in allowed]
        if not targets:
            return "{}"
        return "{" + ", ".join(targets) + "}"

    return "\n".join(
        [
            "TASK STATE MACHINE (TASK_TRANSITIONS):",
            f"  DRAFT → {_format_targets(TaskStatus.DRAFT)}".replace("{SCHEDULED}", "SCHEDULED"),
            f"  SCHEDULED → {_format_targets(TaskStatus.SCHEDULED)}",
            (
                f"  CLAIMED → {_format_targets(TaskStatus.CLAIMED)} "
                "← backward-compat reads only; new tasks use thread ownership instead"
            ),
            f"  REVIEWING → {_format_targets(TaskStatus.REVIEWING)}",
            f"  PAUSED → {_format_targets(TaskStatus.PAUSED)}".replace("{SCHEDULED}", "SCHEDULED"),
            f"  DONE → {_format_targets(TaskStatus.DONE)}".replace("{SCHEDULED}", "SCHEDULED"),
        ]
    )


def _replace_generated_block(text: str, marker: str, body: str) -> str:
    begin = f"<!-- BEGIN: {marker} -->"
    end = f"<!-- END: {marker} -->"
    try:
        start = text.index(begin) + len(begin)
        finish = text.index(end)
    except ValueError as exc:
        msg = f"Missing generated block markers for '{marker}'"
        raise ValueError(msg) from exc
    return f"{text[:start]}\n{body}\n{text[finish:]}"


def generated_blocks(root: Path = ROOT) -> list[GeneratedBlock]:
    """Return all generated documentation blocks in the repository."""

    return [
        GeneratedBlock(
            path=root / "README.md",
            marker="task-storage-model",
            renderer=render_readme_task_storage_model,
        ),
        GeneratedBlock(
            path=root / "README.md",
            marker="manager-command-contract",
            renderer=lambda: render_manager_command_contract(prefix=README_COMMAND_PREFIX),
        ),
        GeneratedBlock(
            path=root / "README.md",
            marker="manager-command-access",
            renderer=lambda: render_manager_command_access(prefix=README_COMMAND_PREFIX),
        ),
        GeneratedBlock(
            path=root / "docs" / "reference" / "data-model.md",
            marker="task-file-model",
            renderer=render_task_file_model,
        ),
        GeneratedBlock(
            path=root / "design" / "cli-design.md",
            marker="worker-agent-next-text-example",
            renderer=render_worker_agent_next_text_example,
        ),
        GeneratedBlock(
            path=root / "design" / "cli-design.md",
            marker="worker-agent-next-json-example",
            renderer=render_worker_agent_next_json_example,
        ),
        GeneratedBlock(
            path=root / "design" / "approval-queue-tui-implementation-guide.md",
            marker="task-status-guide",
            renderer=render_task_status_guide,
        ),
        GeneratedBlock(
            path=root / "design" / "approval-queue-tui-implementation-guide.md",
            marker="task-transition-guide",
            renderer=render_task_transition_guide,
        ),
        GeneratedBlock(
            path=root / "docs" / "reference" / "cli.md",
            marker="manager-command-contract",
            renderer=render_manager_command_contract,
        ),
        GeneratedBlock(
            path=root / "docs" / "reference" / "cli.md",
            marker="manager-command-access",
            renderer=render_manager_command_access,
        ),
    ]


def sync_generated_docs(*, check: bool = False, root: Path = ROOT) -> list[Path]:
    """Write or verify all generated documentation blocks."""

    changed_paths: list[Path] = []
    blocks_by_path: dict[Path, list[GeneratedBlock]] = defaultdict(list)
    for block in generated_blocks(root):
        blocks_by_path[block.path].append(block)

    for path, blocks in blocks_by_path.items():
        original = path.read_text(encoding="utf-8")
        updated = original
        for block in blocks:
            updated = _replace_generated_block(updated, block.marker, block.renderer())
        if updated != original:
            changed_paths.append(path)
            if not check:
                path.write_text(updated, encoding="utf-8")

    return changed_paths


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for syncing generated documentation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated docs are out of date")
    args = parser.parse_args(list(argv) if argv is not None else None)

    changed_paths = sync_generated_docs(check=args.check)
    if not changed_paths:
        print("Generated docs are up to date.")
        return 0

    for path in changed_paths:
        action = "Out of date" if args.check else "Updated"
        print(f"{action}: {path.relative_to(ROOT)}")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
