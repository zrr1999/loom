"""Soft-hook advisory snippets appended to agent output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from .models import AgentRole

if TYPE_CHECKING:
    from .config import LoomSettings


_BUILT_IN_NEXT_HOOKS: dict[str, dict[str, str]] = {
    "commit-message-policy": {
        AgentRole.WORKER.value: (
            "Built-in example: commit-message-policy\n"
            "Follow the repo commit-msg hook format: `<emoji> <type>(<scope>)?: <subject>`.\n"
            "Example: `✨ feat(soft-hooks): add role-specific reminders`."
        )
    }
}
_BUILT_IN_DONE_BEFORE_HOOKS: dict[str, dict[str, str]] = {
    "worker-done-review": {
        AgentRole.WORKER.value: (
            "Built-in example: worker-done-review\n"
            "Inspect the diff before finishing.\n"
            "Did this change grow the code? If so, does that growth earn its keep?\n"
            "Can you simplify the result further without losing value?\n"
            "Refresh your checkpoint summary and confirm the focused tests/validations you ran."
        )
    }
}
_BUILT_IN_DONE_AFTER_HOOKS: dict[str, dict[str, str]] = {}


class RoleScopedHooks(Protocol):
    all: str
    manager: str
    worker: str
    director: str
    reviewer: str
    examples: list[str]


def available_next_hook_examples() -> tuple[str, ...]:
    """Return the built-in hook examples that `loom agent next` knows how to render."""

    return tuple(sorted(_BUILT_IN_NEXT_HOOKS))


def available_done_before_hook_examples() -> tuple[str, ...]:
    """Return the built-in examples that `loom agent done` can render before completion."""

    return tuple(sorted(_BUILT_IN_DONE_BEFORE_HOOKS))


def available_done_after_hook_examples() -> tuple[str, ...]:
    """Return the built-in examples that `loom agent done` can render after completion."""

    return tuple(sorted(_BUILT_IN_DONE_AFTER_HOOKS))


def _role_for_actor(actor: str) -> str:
    if actor in {
        AgentRole.MANAGER.value,
        AgentRole.DIRECTOR.value,
        AgentRole.REVIEWER.value,
    }:
        return actor
    return AgentRole.WORKER.value


def _collect_hook_snippets(
    hooks: RoleScopedHooks,
    *,
    role: str,
    built_in_examples: dict[str, dict[str, str]],
) -> list[str]:
    snippets = []
    for text in (hooks.all, getattr(hooks, role)):
        cleaned = text.strip()
        if cleaned:
            snippets.append(cleaned)

    for name in hooks.examples:
        example = built_in_examples.get(name, {})
        cleaned = (example.get(role) or example.get("all") or "").strip()
        if cleaned:
            snippets.append(cleaned)
    return snippets


def _render_hook_lines(
    *,
    snippets: list[str],
    title: str,
    leading_blank: bool,
) -> list[str]:
    if not snippets:
        return []

    lines: list[str] = []
    if leading_blank:
        lines.append("")
    lines.extend(
        [
            title,
            "  Advisory only; these reminders do not block execution.",
        ]
    )
    for snippet in snippets:
        lines.append("")
        for line in snippet.splitlines():
            lines.append(f"  {line}" if line else "")
    return lines


def render_next_hook_lines(settings: LoomSettings, actor: str) -> list[str]:
    """Render advisory next-output hooks for the acting role."""

    role = _role_for_actor(actor)
    snippets = _collect_hook_snippets(
        settings.hooks.next,
        role=role,
        built_in_examples=_BUILT_IN_NEXT_HOOKS,
    )
    return _render_hook_lines(snippets=snippets, title="SOFT HOOKS", leading_blank=True)


def render_done_hook_lines(
    settings: LoomSettings,
    actor: str,
    *,
    when: Literal["before", "after"],
    leading_blank: bool = True,
) -> list[str]:
    """Render advisory done-output hooks for the acting role and hook point."""

    role = _role_for_actor(actor)
    done_hooks = settings.hooks.done.before if when == "before" else settings.hooks.done.after
    built_in_examples = _BUILT_IN_DONE_BEFORE_HOOKS if when == "before" else _BUILT_IN_DONE_AFTER_HOOKS
    snippets = _collect_hook_snippets(done_hooks, role=role, built_in_examples=built_in_examples)
    return _render_hook_lines(
        snippets=snippets,
        title=f"SOFT HOOKS  done/{when}",
        leading_blank=leading_blank,
    )
