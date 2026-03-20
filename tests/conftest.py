from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("LOOM_WORKER_ID", raising=False)
    monkeypatch.delenv("LOOM_AGENT_ID", raising=False)
    monkeypatch.delenv("LOOM_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path
