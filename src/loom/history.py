"""Append-only event history for loom state changes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def log_path(loom_dir: Path) -> Path:
    return loom_dir / "log.jsonl"


def append_event(
    loom_dir: Path,
    event: str,
    entity_kind: str,
    entity_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    path = log_path(loom_dir)
    payload = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": event,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "details": details or {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=True) + "\n")


def read_events(loom_dir: Path) -> list[dict[str, Any]]:
    path = log_path(loom_dir)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
