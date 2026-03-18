"""Small Typer-native prompt helpers."""

from __future__ import annotations

from collections.abc import Sequence

import typer


def _shortcut(choice: str) -> str:
    if choice == "detail":
        return "?"
    if len(choice) == 1:
        return choice.lower()
    return choice[0].lower()


def select(message: str, choices: Sequence[str], default: str) -> str:
    tokens: list[str] = []
    lookup: dict[str, str] = {}
    default_token = _shortcut(default)

    for choice in choices:
        token = _shortcut(choice)
        display = token.upper() if choice == default and token != "?" else token
        tokens.append(display)
        lookup[token.lower()] = choice
        lookup[choice.lower()] = choice

    prompt = f"{message} [{' / '.join(tokens)}]"
    try:
        answer = typer.prompt(prompt, default=default_token).strip()
    except (typer.Abort, EOFError):
        return default

    if not answer:
        return default

    return lookup.get(answer.lower(), default)


def text(message: str, default: str = "") -> str:
    try:
        return typer.prompt(message, default=default).strip()
    except (typer.Abort, EOFError):
        return default
