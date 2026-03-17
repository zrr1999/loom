"""Runtime path resolution for local vs global loom state."""

from __future__ import annotations

import os
from pathlib import Path

_ROOT_OVERRIDE: Path | None = None


def set_root(root: Path | None) -> None:
    global _ROOT_OVERRIDE
    _ROOT_OVERRIDE = root


def is_global_mode_active() -> bool:
    return _ROOT_OVERRIDE == global_root()


def resolve_root(root: Path | None = None) -> Path:
    if root is not None:
        return root
    if _ROOT_OVERRIDE is not None:
        return _ROOT_OVERRIDE
    # Support LOOM_DIR env var injected by spawn into agent .env files.
    # LOOM_DIR points to the .loom/ directory itself; the workspace root is its parent.
    loom_dir_env = os.environ.get("LOOM_DIR", "").strip()
    if loom_dir_env:
        loom_path = Path(loom_dir_env)
        # If LOOM_DIR points to .loom/ itself, return its parent as root.
        if loom_path.name == ".loom":
            return loom_path.parent
        return loom_path
    return Path.cwd()


def global_root() -> Path:
    return Path.home()
