"""Soft-hook advisory snippets appended to agent output."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .config import ConfiguredHookSettings, HookDefinitionSettings, RoleHooksSettings, load_hook_registry
from .models import AgentRole

if TYPE_CHECKING:
    from .config import LoomSettings


@dataclass(frozen=True)
class HookDefinition:
    points: tuple[Literal["next", "done"], ...]
    before: dict[str, str] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)


_BUILT_IN_HOOKS: dict[str, HookDefinition] = {
    "commit-message-policy": HookDefinition(
        points=("next",),
        after={
            AgentRole.WORKER.value: (
                "Built-in hook: commit-message-policy\n"
                "Follow the repo commit-msg hook format: `<emoji> <type>(<scope>)?: <subject>`.\n"
                "Example: `✨ feat(soft-hooks): add role-specific reminders`."
            )
        },
    ),
    "worker-done-review": HookDefinition(
        points=("done",),
        before={
            AgentRole.WORKER.value: (
                "Built-in hook: worker-done-review\n"
                "Inspect the diff before finishing.\n"
                "Did this change grow the code? If so, does that growth earn its keep?\n"
                "Can you simplify the result further without losing value?\n"
                "Refresh your checkpoint summary and confirm the focused tests/validations you ran."
            )
        },
    ),
}


@dataclass(frozen=True)
class HookView:
    before: dict[str, str]
    after: dict[str, str]


def _role_for_actor(actor: str) -> str:
    if actor in {
        AgentRole.MANAGER.value,
        AgentRole.DIRECTOR.value,
        AgentRole.REVIEWER.value,
    }:
        return actor
    return AgentRole.WORKER.value


def _available_builtins(point: Literal["next", "done"]) -> tuple[str, ...]:
    return tuple(sorted(name for name, hook in _BUILT_IN_HOOKS.items() if point in hook.points))


def available_next_hook_uses() -> tuple[str, ...]:
    """Return built-in hook ids available for `[[hooks]]` entries with `points = ["next"]`."""

    return _available_builtins("next")


def available_done_hook_uses() -> tuple[str, ...]:
    """Return built-in hook ids available for `[[hooks]]` entries with `points = ["done"]`."""

    return _available_builtins("done")


def _select_role_text(phase: dict[str, str] | RoleHooksSettings, role: str) -> str:
    if isinstance(phase, dict):
        shared = (phase.get("all") or "").strip()
        specific = (phase.get(role) or "").strip()
    else:
        shared = phase.all.strip()
        specific = getattr(phase, role).strip()
    return "\n".join(part for part in (shared, specific) if part)


def _resolve_registry_hook(definition: HookDefinitionSettings, point: Literal["next", "done"]) -> HookView:
    if point not in definition.points:
        raise ValueError(f"Hook is not registered for `{point}`.")
    return HookView(
        before={key: getattr(definition.before, key) for key in ("all", "manager", "worker", "director", "reviewer")},
        after={key: getattr(definition.after, key) for key in ("all", "manager", "worker", "director", "reviewer")},
    )


def _resolve_builtin_hook(
    hook_id: str,
    *,
    point: Literal["next", "done"],
    entry: ConfiguredHookSettings | None = None,
) -> HookView | None:
    builtin = _BUILT_IN_HOOKS.get(hook_id)
    if builtin is None:
        return None
    if point not in builtin.points:
        if entry is not None and entry.builtin:
            raise ValueError(f"Built-in hook `{hook_id}` cannot be configured for point `{point}`.")
        raise ValueError(f"Built-in hook `{hook_id}` cannot be used for point `{point}`.")
    return HookView(before=builtin.before, after=builtin.after)


def _resolve_hooks(
    settings: LoomSettings,
    *,
    config_root: Path,
    point: Literal["next", "done"],
) -> Sequence[HookView]:
    entries = [hook for hook in settings.hooks if point in hook.points]
    registry = load_hook_registry(config_root)

    collisions = sorted(set(registry.hooks) & set(_BUILT_IN_HOOKS))
    if collisions:
        detail = ", ".join(collisions)
        raise ValueError(f"`loom-hooks.toml` cannot redefine built-in hook ids: {detail}")

    resolved: list[HookView] = []
    for entry in entries:
        if entry.builtin:
            builtin = _resolve_builtin_hook(entry.builtin, point=point, entry=entry)
            if builtin is None:
                raise ValueError(f"Unknown built-in hook `{entry.builtin}` in `[[hooks]]`.")
            resolved.append(builtin)
            continue

        definition = registry.hooks.get(entry.id)
        if definition is None:
            builtin = _resolve_builtin_hook(entry.id, point=point)
            if builtin is not None:
                resolved.append(builtin)
                continue
            raise ValueError(f"Unknown hook id `{entry.id}` in `[[hooks]]`.")
        resolved.append(_resolve_registry_hook(definition, point))
    return resolved


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


def render_hook_phase_lines(
    settings: LoomSettings,
    actor: str,
    *,
    config_root: Path,
    point: Literal["next", "done"],
    when: Literal["before", "after"],
    leading_blank: bool = True,
) -> list[str]:
    """Render advisory hook output for a lifecycle phase."""

    role = _role_for_actor(actor)
    hooks = list(_resolve_hooks(settings, config_root=config_root, point=point))
    if when == "after":
        hooks.reverse()

    snippets: list[str] = []
    for hook in hooks:
        phase = hook.before if when == "before" else hook.after
        text = _select_role_text(phase, role)
        if text:
            snippets.append(text)
    return _render_hook_lines(
        snippets=snippets,
        title=f"SOFT HOOKS  {point}/{when}",
        leading_blank=leading_blank,
    )
