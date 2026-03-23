"""Helpers for thread-ownership lease freshness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .config import load_settings
from .models import Thread
from .repository import workspace_root

if TYPE_CHECKING:
    from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat_seconds(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def lease_timeout(loom: Path) -> timedelta:
    settings = load_settings(workspace_root(loom))
    minutes = max(settings.agent.offline_after_minutes, 1)
    return timedelta(minutes=minutes)


def refresh_thread_lease(thread: Thread, loom: Path, *, now: datetime | None = None) -> Thread:
    observed_at = now or utc_now()
    expires_at = observed_at + lease_timeout(loom)
    return thread.model_copy(
        update={
            "owner_heartbeat_at": isoformat_seconds(observed_at),
            "owner_lease_expires_at": isoformat_seconds(expires_at),
        }
    )


def is_thread_stale(thread: Thread, *, now: datetime | None = None) -> bool:
    if not thread.owner:
        return False
    expires_at = parse_timestamp(thread.owner_lease_expires_at)
    if expires_at is None:
        return False
    observed_at = now or utc_now()
    return expires_at <= observed_at
