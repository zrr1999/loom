from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from loom.config import ConfiguredHookSettings, LoomSettings, dump_settings, load_settings


def test_load_settings_rejects_legacy_done_before_after_hooks(tmp_path: Path) -> None:
    (tmp_path / "loom.toml").write_text(
        (
            "[hooks.done.before]\n"
            'worker = "Legacy before reminder"\n\n'
            "[hooks.done.after]\n"
            'worker = "Legacy after reminder"\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings(tmp_path)


def test_load_settings_accepts_array_hook_entries(tmp_path: Path) -> None:
    (tmp_path / "loom.toml").write_text(
        (
            "[[hooks]]\n"
            'id = "review-pass"\n'
            'points = ["done"]\n\n'
            "[[hooks]]\n"
            'builtin = "commit-message-policy"\n'
            'points = ["next", "next"]\n'
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert [(hook.id, hook.builtin, hook.points) for hook in settings.hooks] == [
        ("review-pass", "", ["done"]),
        ("", "commit-message-policy", ["next"]),
    ]


def test_load_settings_merges_legacy_hook_uses_into_array_entries(tmp_path: Path) -> None:
    (tmp_path / "loom.toml").write_text(
        ('[hooks.next]\nuses = ["reminders", "worker-done-review"]\n\n[hooks.done]\nuses = ["worker-done-review"]\n'),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert [(hook.id, hook.builtin, hook.points) for hook in settings.hooks] == [
        ("reminders", "", ["next"]),
        ("worker-done-review", "", ["next", "done"]),
    ]


def test_load_settings_rejects_duplicate_hook_entries(tmp_path: Path) -> None:
    (tmp_path / "loom.toml").write_text(
        (
            "[[hooks]]\n"
            'builtin = "worker-done-review"\n'
            'points = ["done"]\n\n'
            "[[hooks]]\n"
            'builtin = "worker-done-review"\n'
            'points = ["next"]\n'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="Duplicate `\\[\\[hooks\\]\\]` entry"):
        load_settings(tmp_path)


def test_dump_settings_uses_array_hook_format() -> None:
    text = dump_settings(
        LoomSettings(
            hooks=[
                ConfiguredHookSettings(id="review-pass", points=["done"]),
                ConfiguredHookSettings(builtin="worker-done-review", points=["done"]),
            ]
        )
    )

    assert text.count("[[hooks]]") == 2
    assert 'id = "review-pass"' in text
    assert 'builtin = "worker-done-review"' in text
    assert 'points = ["done"]' in text
    assert "[hooks.done]" not in text
