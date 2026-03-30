"""Compact duration parsing and formatting helpers."""

from __future__ import annotations

import math
import re
from datetime import timedelta

_INTERVAL_PATTERN = re.compile(r"^\s*(\d+)([mhdMHD])\s*$")

_SECONDS_PER_UNIT = {
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
}


def normalize_interval(value: str) -> str:
    """Normalize a compact interval like ``30m`` or ``1D`` into canonical form."""
    match = _INTERVAL_PATTERN.fullmatch(value)
    if not match:
        msg = "interval must use compact duration syntax like 30m, 6h, or 1d"
        raise ValueError(msg)
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if amount <= 0:
        msg = "interval must be greater than zero"
        raise ValueError(msg)
    return f"{amount}{unit}"


def parse_interval(value: str) -> timedelta:
    """Parse a normalized compact interval into a ``timedelta``."""
    normalized = normalize_interval(value)
    amount = int(normalized[:-1])
    unit = normalized[-1]
    return timedelta(seconds=amount * _SECONDS_PER_UNIT[unit])


def format_compact_duration(delta: timedelta) -> str:
    """Render a positive duration using the same compact unit family."""
    total_seconds = max(int(delta.total_seconds()), 0)
    if total_seconds < 60:
        return "now"
    if total_seconds % _SECONDS_PER_UNIT["d"] == 0:
        return f"{total_seconds // _SECONDS_PER_UNIT['d']}d"
    if total_seconds % _SECONDS_PER_UNIT["h"] == 0:
        return f"{total_seconds // _SECONDS_PER_UNIT['h']}h"

    minutes = max(math.ceil(total_seconds / _SECONDS_PER_UNIT["m"]), 1)
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"
