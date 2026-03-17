"""Small Typer-native prompt helpers."""

from __future__ import annotations

from collections.abc import Sequence

import typer


def select(message: str, choices: Sequence[str], default: str) -> str:
    choices_text = "/".join(choices)
    prompt = f"{message} [{choices_text}]"
    try:
        answer = typer.prompt(prompt, default=default).strip()
    except (typer.Abort, EOFError):
        return default

    if answer in choices:
        return answer
    if not answer:
        return default
    return default


def text(message: str, default: str = "") -> str:
    try:
        return typer.prompt(message, default=default).strip()
    except (typer.Abort, EOFError):
        return default
