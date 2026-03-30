from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loom.frontmatter import read_model, write_model
from loom.models import TaskStatus, Thread, ThreadWorktree, WorktreeStatus

WORKER_ID = "aaap"


@pytest.fixture()
def loom(tmp_path: Path) -> Path:
    from loom.services import create_thread, ensure_agent_layout, ensure_worktree_storage

    loom_dir = tmp_path / ".loom"
    loom_dir.mkdir()
    (loom_dir / "threads").mkdir()
    (loom_dir / "inbox").mkdir()
    ensure_agent_layout(loom_dir)
    ensure_worktree_storage(loom_dir, WORKER_ID)
    create_thread(loom_dir, name="worktree-flow", priority=90)
    return loom_dir


def test_add_worktree_persists_worker_local_record(loom: Path) -> None:
    from loom.repository import load_worktree
    from loom.services import add_worktree

    record, path = add_worktree(
        loom,
        WORKER_ID,
        name="feature-a",
        branch="feat/worktree-a",
    )

    checkout = loom / "agents" / "workers" / WORKER_ID / "worktrees" / "feature-a"
    assert path.exists()
    assert checkout.is_dir()
    assert record.name == "feature-a"
    assert record.path == str(checkout.resolve())
    assert record.branch == "feat/worktree-a"
    assert record.worker == WORKER_ID
    assert record.status == WorktreeStatus.REGISTERED

    _loaded_path, loaded = load_worktree(loom, WORKER_ID, "feature-a")
    assert loaded.name == "feature-a"
    assert loaded.path == str(checkout.resolve())


def test_add_worktree_rejects_paths_outside_worker_root(loom: Path, tmp_path: Path) -> None:
    from loom.services import add_worktree

    outside = tmp_path / "elsewhere" / "feature-a"
    with pytest.raises(ValueError, match="must stay under"):
        add_worktree(
            loom,
            WORKER_ID,
            name="feature-a",
            path=str(outside),
            branch="feat/worktree-a",
        )


def test_load_all_worktrees_is_worker_local(loom: Path) -> None:
    from loom.services import add_worktree, load_all_worktrees

    add_worktree(loom, WORKER_ID, name="feature-a", branch="feat/worktree-a")
    add_worktree(loom, "aaar", name="feature-b", branch="feat/worktree-b")

    assert [record.name for record in load_all_worktrees(loom, WORKER_ID)] == ["feature-a"]
    assert [record.name for record in load_all_worktrees(loom, "aaar")] == ["feature-b"]


def test_resolve_current_worktree_matches_nested_checkout_path(loom: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from loom.services import add_worktree, resolve_current_worktree

    add_worktree(loom, WORKER_ID, name="feature-a", branch="feat/worktree-a")
    checkout = loom / "agents" / "workers" / WORKER_ID / "worktrees" / "feature-a" / "src"
    checkout.mkdir(parents=True)
    monkeypatch.chdir(checkout)

    current = resolve_current_worktree(loom, WORKER_ID)

    assert current is not None
    checkout_root, record = current
    assert checkout_root == (loom / "agents" / "workers" / WORKER_ID / "worktrees" / "feature-a").resolve()
    assert record.name == "feature-a"
    assert record.worker == WORKER_ID


def test_resolve_actor_workspace_root_falls_back_to_primary_workspace(
    loom: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loom.services import add_worktree, resolve_actor_workspace_root

    add_worktree(loom, WORKER_ID, name="feature-a", branch="feat/worktree-a")
    monkeypatch.chdir(loom.parent)

    resolved = resolve_actor_workspace_root(loom, WORKER_ID)

    assert resolved == loom.parent


def test_attach_worktree_updates_only_current_worker_record(loom: Path) -> None:
    from loom.services import add_worktree, attach_worktree

    add_worktree(loom, WORKER_ID, name="feature-a", branch="feat/worktree-a")

    _path, attached = attach_worktree(loom, WORKER_ID, "feature-a", thread="worktree-flow")
    assert attached.worker == WORKER_ID
    assert attached.thread == "worktree-flow"
    assert attached.status == WorktreeStatus.ACTIVE
    thread = read_model(loom / "threads" / "worktree-flow" / "_thread.md", Thread)
    assert len(thread.worktrees) == 1
    assert thread.worktrees[0].name == "feature-a"
    assert thread.worktrees[0].worker == WORKER_ID
    assert thread.worktrees[0].removed_at is None

    with pytest.raises(FileNotFoundError, match="not found for worker 'aaar'"):
        attach_worktree(loom, "aaar", "feature-a", thread="worktree-flow")


def test_remove_worktree_requires_clear_or_force(loom: Path) -> None:
    from loom.repository import worktree_record_path
    from loom.services import add_worktree, attach_worktree, remove_worktree

    add_worktree(loom, WORKER_ID, name="feature-a", branch="feat/worktree-a")
    attach_worktree(loom, WORKER_ID, "feature-a", thread="worktree-flow")

    with pytest.raises(ValueError, match="clear it first"):
        remove_worktree(loom, WORKER_ID, "feature-a")

    path, record = remove_worktree(loom, WORKER_ID, "feature-a", force=True)
    assert record.name == "feature-a"
    assert path == worktree_record_path(loom, WORKER_ID, "feature-a")
    assert not path.exists()
    assert not Path(record.path).exists()

    thread = read_model(loom / "threads" / "worktree-flow" / "_thread.md", Thread)
    assert len(thread.worktrees) == 1
    assert thread.worktrees[0].name == "feature-a"
    assert thread.worktrees[0].removed_at is not None
    assert thread.worktrees[0].status == WorktreeStatus.ARCHIVED


def test_remove_worktree_requires_force_when_checkout_dirty(loom: Path) -> None:
    from loom.repository import worktree_record_path
    from loom.services import add_worktree, remove_worktree

    record, _path = add_worktree(loom, WORKER_ID, name="feature-dirty", branch="feat/worktree-dirty")
    checkout = Path(record.path)
    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True, text=True)
    (checkout / "dirty.txt").write_text("pending\n", encoding="utf-8")

    with pytest.raises(ValueError, match="uncommitted changes"):
        remove_worktree(loom, WORKER_ID, "feature-dirty")

    removed_path, removed_record = remove_worktree(loom, WORKER_ID, "feature-dirty", force=True)
    assert removed_record.name == "feature-dirty"
    assert removed_path == worktree_record_path(loom, WORKER_ID, "feature-dirty")
    assert not checkout.exists()


def test_add_worktree_rejects_overlapping_paths(loom: Path) -> None:
    from loom.services import add_worktree

    add_worktree(loom, WORKER_ID, name="feature-a", branch="feat/worktree-a")

    with pytest.raises(ValueError, match="overlaps existing worktree"):
        add_worktree(
            loom,
            WORKER_ID,
            name="feature-a-nested",
            path="feature-a/nested",
            branch="feat/worktree-a-nested",
        )


def test_clearing_worktree_preserves_thread_history_and_allows_reattach(loom: Path) -> None:
    from loom.services import add_worktree, attach_worktree, create_thread

    add_worktree(loom, WORKER_ID, name="feature-a", branch="feat/worktree-a")
    attach_worktree(loom, WORKER_ID, "feature-a", thread="worktree-flow")
    _, cleared = attach_worktree(loom, WORKER_ID, "feature-a", clear=True)

    assert cleared.thread is None
    thread = read_model(loom / "threads" / "worktree-flow" / "_thread.md", Thread)
    assert len(thread.worktrees) == 1
    assert thread.worktrees[0].removed_at is not None

    create_thread(loom, name="review-flow", priority=80)

    attach_worktree(loom, WORKER_ID, "feature-a", thread="review-flow")
    review_thread = read_model(loom / "threads" / "review-flow" / "_thread.md", Thread)
    assert len(review_thread.worktrees) == 1
    assert review_thread.worktrees[0].removed_at is None


def test_complete_task_records_thread_pr_artifact(loom: Path) -> None:
    from loom.services import complete_task, create_task

    task, _ = create_task(
        loom,
        thread_name="worktree-flow",
        title="Ship worktree redesign",
        acceptance="- [ ] merged",
    )

    _path, updated, blockers = complete_task(
        loom,
        task.id,
        output="https://github.com/acme/loom/pull/42",
    )
    assert blockers == []
    assert updated.status == TaskStatus.REVIEWING

    thread = read_model(loom / "threads" / "worktree-flow" / "_thread.md", Thread)
    assert len(thread.pr_artifacts) == 1
    assert thread.pr_artifacts[0].url == "https://github.com/acme/loom/pull/42"
    assert thread.pr_artifacts[0].repository == "acme/loom"
    assert thread.pr_artifacts[0].number == 42
    assert thread.pr_artifacts[0].task_ids == [task.id]


def test_complete_task_normalizes_local_output_into_products_tree(loom: Path) -> None:
    from loom.services import complete_task, create_task

    task, _ = create_task(
        loom,
        thread_name="worktree-flow",
        title="Write human review report",
        acceptance="- [ ] report path recorded",
    )

    _path, updated, blockers = complete_task(
        loom,
        task.id,
        output="./reports/worktree-flow-006.md",
    )

    assert blockers == []
    assert updated.output == ".loom/products/reports/worktree-flow-006.md"
    assert (loom / "products" / "reports").is_dir()


def test_complete_task_rewrites_legacy_worker_output_paths_into_products_reports(loom: Path) -> None:
    from loom.services import complete_task, create_task

    task, _ = create_task(
        loom,
        thread_name="worktree-flow",
        title="Migrate legacy worker output path",
        acceptance="- [ ] output recorded in products tree",
    )

    _path, updated, blockers = complete_task(
        loom,
        task.id,
        output=".loom/agents/workers/aaap/outputs/worktree-flow-006.md",
    )

    assert blockers == []
    assert updated.output == ".loom/products/reports/worktree-flow-006.md"
    assert (loom / "products" / "reports").is_dir()


def test_status_summary_reports_worktree_reference_issues(loom: Path) -> None:
    from loom.scheduler import get_status_summary
    from loom.services import add_worktree, attach_worktree

    add_worktree(loom, WORKER_ID, name="feature-stale", branch="feat/worktree-stale")
    attach_worktree(loom, WORKER_ID, "feature-stale", thread="worktree-flow")
    worktree_record_path = loom / "agents" / "workers" / WORKER_ID / "worktrees" / "feature-stale.md"
    worktree_record_path.unlink()

    add_worktree(loom, WORKER_ID, name="feature-missing", branch="feat/worktree-missing")
    attach_worktree(loom, WORKER_ID, "feature-missing", thread="worktree-flow")
    missing_checkout = loom / "agents" / "workers" / WORKER_ID / "worktrees" / "feature-missing"
    missing_checkout.rmdir()

    thread_path = loom / "threads" / "worktree-flow" / "_thread.md"
    thread = read_model(thread_path, Thread)
    invalid = ThreadWorktree(
        name="feature-cross",
        worker=WORKER_ID,
        path=str((loom / "agents" / "workers" / "aaar" / "worktrees" / "feature-cross").resolve()),
        branch="feat/worktree-cross",
        status=WorktreeStatus.ACTIVE,
    )
    write_model(thread_path, thread.model_copy(update={"worktrees": [*thread.worktrees, invalid]}))

    summary = get_status_summary(loom)

    issues = summary["worktree_issues"]["worktree-flow"]
    assert any("feature-stale" in issue and "stale" in issue for issue in issues)
    assert any("feature-missing" in issue and "missing its checkout path" in issue for issue in issues)
    assert any("feature-cross" in issue and "cross-worker-invalid" in issue for issue in issues)


def test_complete_task_with_delivery_contract_bypasses_todo_heuristics(loom: Path) -> None:
    """Explicit delivery contract with ready=True should bypass TODO/proposal blockers."""
    from loom.models import DeliveryContract, TaskStatus
    from loom.services import complete_task, create_task

    task, _ = create_task(
        loom,
        thread_name="worktree-flow",
        title="Task with TODO output",
        acceptance="- [ ] done",
    )

    _path, updated, blockers = complete_task(
        loom,
        task.id,
        output="TODO: follow-up in next sprint",
        delivery=DeliveryContract(
            ready=True,
            summary="Core work complete; TODO is a non-blocking follow-up",
        ),
    )

    assert blockers == []
    assert updated.status == TaskStatus.REVIEWING
    assert updated.delivery is not None
    assert updated.delivery.ready is True
    assert updated.delivery.summary == "Core work complete; TODO is a non-blocking follow-up"


def test_complete_task_records_pr_from_delivery_contract(loom: Path) -> None:
    """PR URLs provided in delivery contract should be recorded as thread PR artifacts."""
    from loom.models import DeliveryContract, TaskStatus, Thread
    from loom.services import complete_task, create_task

    task, _ = create_task(
        loom,
        thread_name="worktree-flow",
        title="Task with structured PR",
        acceptance="- [ ] merged",
    )

    _path, updated, blockers = complete_task(
        loom,
        task.id,
        delivery=DeliveryContract(
            ready=True,
            pr_urls=["https://github.com/acme/loom/pull/77"],
        ),
    )

    assert blockers == []
    assert updated.status == TaskStatus.REVIEWING

    thread = read_model(loom / "threads" / "worktree-flow" / "_thread.md", Thread)
    assert len(thread.pr_artifacts) == 1
    assert thread.pr_artifacts[0].url == "https://github.com/acme/loom/pull/77"
    assert thread.pr_artifacts[0].repository == "acme/loom"
    assert thread.pr_artifacts[0].number == 77
    assert task.id in thread.pr_artifacts[0].task_ids


def test_complete_task_delivery_pr_preferred_over_text_scraping(loom: Path) -> None:
    """Structured delivery PR URL takes precedence over any URL in freeform output."""
    from loom.models import DeliveryContract, Thread
    from loom.services import complete_task, create_task

    task, _ = create_task(
        loom,
        thread_name="worktree-flow",
        title="Task with both PR sources",
        acceptance="- [ ] merged",
    )

    _path, _updated, blockers = complete_task(
        loom,
        task.id,
        output="see also https://github.com/acme/loom/pull/10 for context",
        delivery=DeliveryContract(
            ready=True,
            pr_urls=["https://github.com/acme/loom/pull/88"],
        ),
    )

    assert blockers == []
    thread = read_model(loom / "threads" / "worktree-flow" / "_thread.md", Thread)
    urls = [a.url for a in thread.pr_artifacts]
    # Structured URL is recorded; freeform text PR is ignored when delivery.pr_urls is set
    assert "https://github.com/acme/loom/pull/88" in urls
    assert "https://github.com/acme/loom/pull/10" not in urls
