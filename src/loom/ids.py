"""ID generation helpers for tasks, inbox items, messages, and thread names."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def slugify(text: str) -> str:
    """Convert text to a kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    return text.strip("-")[:60]


def canonical_thread_name(name: str) -> str:
    """Normalize a thread name into the canonical storage identity."""
    canonical = slugify(name)
    if canonical:
        return canonical
    msg = "thread name must include at least one letter, number, or CJK character"
    raise ValueError(msg)


THREAD_ID_PREFIX = "th"
THREAD_ID_PATTERN = re.compile(r"^th[a-z]{2,}$")
TASK_ID_PATTERN = re.compile(r"^(?P<thread_id>th[a-z]{2,})-(?P<seq>\d{3})$")


def _alpha_suffix(index: int, *, min_length: int = 1) -> str:
    """Encode a zero-based integer as an a-z suffix with a minimum length."""
    if index < 0:
        msg = "index must be >= 0"
        raise ValueError(msg)

    alphabet = "abcdefghijklmnopqrstuvwxyz"
    chars: list[str] = []
    value = index
    while True:
        chars.append(alphabet[value % 26])
        value //= 26
        if value == 0:
            break
    return "".join(reversed(chars)).rjust(min_length, "a")


def is_short_thread_id(value: str) -> bool:
    """Return True when a thread id matches the short incremental scheme."""
    return bool(THREAD_ID_PATTERN.fullmatch(value))


def next_thread_id(existing_ids: Iterable[str]) -> str:
    """Return the next short thread id (`thaa`, `thab`, ...)."""
    used = {value for value in existing_ids if is_short_thread_id(value)}
    index = 0
    while True:
        candidate = f"{THREAD_ID_PREFIX}{_alpha_suffix(index, min_length=2)}"
        if candidate not in used:
            return candidate
        index += 1


def task_id(thread_id: str, seq: int) -> str:
    """Build a globally unique task id from a short thread id and per-thread sequence."""
    return f"{thread_id}-{seq:03d}"


def task_filename(seq: int) -> str:
    """Build the on-disk task filename for a per-thread sequence number."""
    return f"{seq:03d}.md"


def split_task_id(value: str) -> tuple[str, int] | None:
    """Parse a task id in the short `<thread-id>-NNN` format."""
    match = TASK_ID_PATTERN.fullmatch(value)
    if match is None:
        return None
    return match.group("thread_id"), int(match.group("seq"))


def next_task_seq(thread_dir: Path) -> int:
    """Return the next task sequence number within a thread directory."""
    max_seq = 0
    if thread_dir.exists():
        for path in thread_dir.glob("*.md"):
            if path.name == "_thread.md":
                continue
            stem = path.stem
            if stem.isdigit():
                max_seq = max(max_seq, int(stem))
                continue
            match = re.search(r"(?<!\d)(\d{3})(?!\d)", stem)
            if match:
                max_seq = max(max_seq, int(match.group(1)))
    return max_seq + 1


def next_inbox_seq(inbox_dir: Path) -> int:
    """Return the next RQ sequence number by scanning existing inbox files."""
    max_seq = 0
    if inbox_dir.exists():
        for path in inbox_dir.glob("RQ-*.md"):
            match = re.match(r"RQ-(\d{3})", path.name)
            if match:
                max_seq = max(max_seq, int(match.group(1)))
    return max_seq + 1


def next_message_seq(message_dir: Path) -> int:
    """Return the next MSG sequence number by scanning message files."""
    max_seq = 0
    if message_dir.exists():
        for path in message_dir.glob("MSG-*.md"):
            match = re.match(r"MSG-(\d{3})", path.name)
            if match:
                max_seq = max(max_seq, int(match.group(1)))
    return max_seq + 1


def next_agent_id(agents_dir: Path) -> str:
    """Return the next 4-char agent id."""
    existing = {entry.name for entry in agents_dir.iterdir() if entry.is_dir()} if agents_dir.exists() else set()
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    for a in alphabet:
        for b in alphabet:
            for c in alphabet:
                for d in alphabet:
                    agent_id = f"{a}{b}{c}{d}"
                    if agent_id not in existing:
                        return agent_id
    msg = "Exhausted all agent IDs"
    raise RuntimeError(msg)
