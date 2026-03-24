from __future__ import annotations

from pathlib import Path

import pytest

from loom.models import WorktreeStatus

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
