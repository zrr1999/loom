"""ID generation for threads, tasks, and inbox items.

Thread IDs: AA, AB, AC … AZ, BA … ZZ (676 possible)
Task IDs:   {thread_id}-{seq:03d}-{slug}
Inbox IDs:  RQ-{seq:03d}
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _thread_id_to_int(tid: str) -> int:
    """Convert 'AA'→0, 'AB'→1, … 'AZ'→25, 'BA'→26, … 'ZZ'→675."""
    return (ord(tid[0]) - ord("A")) * 26 + (ord(tid[1]) - ord("A"))


def _int_to_thread_id(n: int) -> str:
    """Convert 0→'AA', 1→'AB', … 25→'AZ', 26→'BA', … 675→'ZZ'."""
    return chr(ord("A") + n // 26) + chr(ord("A") + n % 26)


def next_thread_id(threads_dir: Path) -> str:
    """Return the next available thread ID by scanning existing directories."""
    existing: set[int] = set()
    if threads_dir.exists():
        for d in threads_dir.iterdir():
            if d.is_dir() and re.fullmatch(r"[A-Z]{2}", d.name):
                existing.add(_thread_id_to_int(d.name))
    n = 0
    while n in existing:
        n += 1
    if n > 675:
        msg = "Exhausted all thread IDs (AA-ZZ)"
        raise RuntimeError(msg)
    return _int_to_thread_id(n)


def next_task_seq(thread_dir: Path) -> int:
    """Return the next task sequence number within a thread directory."""
    max_seq = 0
    if thread_dir.exists():
        for f in thread_dir.glob("*.md"):
            if f.name == "_thread.md":
                continue
            m = re.match(r"[A-Z]{2}-(\d{3})", f.name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def next_inbox_seq(inbox_dir: Path) -> int:
    """Return the next RQ sequence number by scanning existing inbox files."""
    max_seq = 0
    if inbox_dir.exists():
        for f in inbox_dir.glob("RQ-*.md"):
            m = re.match(r"RQ-(\d{3})", f.name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def next_message_seq(message_dir: Path) -> int:
    """Return the next MSG sequence number by scanning message files."""
    max_seq = 0
    if message_dir.exists():
        for f in message_dir.glob("MSG-*.md"):
            m = re.match(r"MSG-(\d{3})", f.name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
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


def slugify(text: str) -> str:
    """Convert a title to kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    return text.strip("-")[:60]
