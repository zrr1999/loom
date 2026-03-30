"""Configuration handling for root-level `loom.toml` and `loom-hooks.toml`."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .runtime import resolve_root

HookPoint = Literal["next", "done"]
_ROLE_TEXT_KEYS = ("all", "manager", "worker", "director", "reviewer")
_ROLE_HOOK_KEYS = set(_ROLE_TEXT_KEYS) | {"uses", "examples"}
_LEGACY_NEXT_HOOK_ID = "__legacy_next__"


class ProjectSettings(BaseModel):
    name: str = ""


class AgentSettings(BaseModel):
    inbox_plan_batch: int = 10
    task_batch: int = 1
    next_wait_seconds: float = 60.0
    next_retries: int = 5
    executor_command: str = ""
    offline_after_minutes: int = 30
    spawn_limit_active_workers: int = 8
    spawn_limit_idle_workers: int = 2

    @field_validator("spawn_limit_active_workers", "spawn_limit_idle_workers")
    @classmethod
    def validate_spawn_limits(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Spawn worker limits must be >= 0.")
        return value


class ThreadDefaults(BaseModel):
    default_priority: int = 50


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _dedupe_points(values: Sequence[HookPoint]) -> list[HookPoint]:
    seen: set[HookPoint] = set()
    deduped: list[HookPoint] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _string_key_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    if not all(isinstance(key, str) for key in value):
        return None
    return cast(dict[str, object], value)


def _coerce_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Hook uses must be a list of strings.")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Hook uses must be a list of strings.")
        stripped = item.strip()
        if stripped:
            cleaned.append(stripped)
    return _dedupe(cleaned)


class RoleHooksSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all: str = ""
    manager: str = ""
    worker: str = ""
    director: str = ""
    reviewer: str = ""


def _coerce_hook_ref(value: object, *, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    return value.strip()


class ConfiguredHookSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    builtin: str = ""
    points: list[HookPoint] = Field(default_factory=list)

    @field_validator("id", "builtin", mode="before")
    @classmethod
    def normalize_ref(cls, value: object, info) -> str:
        return _coerce_hook_ref(value, field_name=f"`{info.field_name}`")

    @field_validator("points", mode="before")
    @classmethod
    def normalize_points(cls, value: object) -> list[HookPoint]:
        if not isinstance(value, list) or not value:
            raise ValueError("Configured hooks must declare non-empty `points`.")
        points: list[HookPoint] = []
        for item in value:
            if item not in {"next", "done"}:
                raise ValueError("Configured hook points must be `next` and/or `done`.")
            points.append(cast(HookPoint, item))
        return _dedupe_points(points)

    @model_validator(mode="after")
    def validate_source(self) -> ConfiguredHookSettings:
        has_id = bool(self.id)
        has_builtin = bool(self.builtin)
        if has_id == has_builtin:
            raise ValueError("Each `[[hooks]]` entry must declare exactly one of `id` or `builtin`.")
        return self

    @property
    def selector(self) -> tuple[str, str]:
        if self.builtin:
            return ("builtin", self.builtin)
        return ("id", self.id)


class HookDefinitionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[HookPoint] = Field(default_factory=list)
    before: RoleHooksSettings = Field(default_factory=RoleHooksSettings)
    after: RoleHooksSettings = Field(default_factory=RoleHooksSettings)

    @field_validator("points", mode="before")
    @classmethod
    def normalize_points(cls, value: object) -> list[HookPoint]:
        if not isinstance(value, list) or not value:
            raise ValueError("Hook definitions must declare non-empty `points`.")
        points: list[HookPoint] = []
        for item in value:
            if item not in {"next", "done"}:
                raise ValueError("Hook definition points must be `next` and/or `done`.")
            points.append(cast(HookPoint, item))
        return _dedupe_points(points)

    @field_validator("after")
    @classmethod
    def require_some_phase(cls, after: RoleHooksSettings, info) -> RoleHooksSettings:
        before = info.data.get("before")
        if before is None:
            return after
        if any(getattr(before, key) for key in _ROLE_TEXT_KEYS):
            return after
        if any(getattr(after, key) for key in _ROLE_TEXT_KEYS):
            return after
        raise ValueError("Hook definitions must declare at least one `before` or `after` message.")


class HookRegistrySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hooks: dict[str, HookDefinitionSettings] = Field(default_factory=dict)


class LoomSettings(BaseModel):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    threads: ThreadDefaults = Field(default_factory=ThreadDefaults)
    hooks: list[ConfiguredHookSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_hook_entries(self) -> LoomSettings:
        seen: dict[tuple[str, str], int] = {}
        for index, hook in enumerate(self.hooks, start=1):
            selector = hook.selector
            if selector in seen:
                label = f"builtin `{selector[1]}`" if selector[0] == "builtin" else f"id `{selector[1]}`"
                previous = seen[selector]
                raise ValueError(
                    f"Duplicate `[[hooks]]` entry for {label}; merge its points into one entry "
                    f"(entries {previous} and {index})."
                )
            seen[selector] = index
        return self


def config_path(base_dir: Path | None = None) -> Path:
    root = resolve_root(base_dir)
    return root / "loom.toml"


def hook_registry_path(base_dir: Path | None = None) -> Path:
    root = resolve_root(base_dir)
    return root / "loom-hooks.toml"


def default_settings(project_name: str = "") -> LoomSettings:
    return LoomSettings(project=ProjectSettings(name=project_name))


def _looks_like_role_hook_table(value: object) -> bool:
    mapping = _string_key_dict(value)
    return mapping is not None and any(key in _ROLE_HOOK_KEYS for key in mapping)


def _role_text_mapping(value: object) -> dict[str, str]:
    mapping = _string_key_dict(value)
    if mapping is None:
        return {}
    lines: dict[str, str] = {}
    for key in _ROLE_TEXT_KEYS:
        raw = mapping.get(key)
        if isinstance(raw, str) and raw:
            lines[key] = raw
    return lines


def _uses_value(mapping: dict[str, object]) -> object:
    if "uses" in mapping:
        return mapping["uses"]
    return mapping.get("examples")


def _normalize_legacy_next_uses(value: object) -> list[str] | None:
    mapping = _string_key_dict(value)
    if mapping is None:
        return None
    if set(mapping).issubset({"uses", "examples"}):
        return _coerce_string_list(_uses_value(mapping))
    if _looks_like_role_hook_table(mapping):
        uses = _coerce_string_list(_uses_value(mapping))
        uses.append(_LEGACY_NEXT_HOOK_ID)
        return _dedupe(uses)
    return None


def _normalize_legacy_done_uses(value: object) -> list[str] | None:
    mapping = _string_key_dict(value)
    if mapping is None:
        return None
    if set(mapping).issubset({"uses", "examples"}):
        return _coerce_string_list(_uses_value(mapping))
    return None


def _append_legacy_hook_entry(
    entries: list[dict[str, object]],
    indexes: dict[tuple[str, str], int],
    *,
    hook_id: str,
    point: HookPoint,
) -> None:
    selector = ("id", hook_id)
    index = indexes.get(selector)
    if index is None:
        indexes[selector] = len(entries)
        entries.append({"id": hook_id, "points": [point]})
        return
    points = cast(list[HookPoint], entries[index]["points"])
    if point not in points:
        points.append(point)


def _normalize_legacy_hook_settings(data: dict[str, object]) -> dict[str, object]:
    raw_hooks = data.get("hooks")
    if isinstance(raw_hooks, list):
        return data

    hooks = _string_key_dict(data.get("hooks"))
    if hooks is None:
        return data
    if set(hooks) - {"next", "done"}:
        return data

    entries: list[dict[str, object]] = []
    indexes: dict[tuple[str, str], int] = {}

    if "next" in hooks:
        next_uses = _normalize_legacy_next_uses(hooks["next"])
        if next_uses is None:
            return data
        for hook_id in next_uses:
            _append_legacy_hook_entry(entries, indexes, hook_id=hook_id, point="next")
    if "done" in hooks:
        done_uses = _normalize_legacy_done_uses(hooks["done"])
        if done_uses is None:
            return data
        for hook_id in done_uses:
            _append_legacy_hook_entry(entries, indexes, hook_id=hook_id, point="done")

    normalized = dict(data)
    normalized["hooks"] = entries
    return normalized


def _load_raw_config_data(base_dir: Path) -> dict[str, object]:
    path = config_path(base_dir)
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_settings(base_dir: Path | None = None) -> LoomSettings:
    base = resolve_root(base_dir)
    raw = _load_raw_config_data(base)
    if not raw:
        return default_settings(base.name)

    settings = LoomSettings.model_validate(_normalize_legacy_hook_settings(raw))
    if not settings.project.name:
        settings.project.name = base.name
    return settings


def _legacy_next_hook_definition(data: dict[str, object]) -> HookDefinitionSettings | None:
    hooks = _string_key_dict(data.get("hooks"))
    if hooks is None:
        return None
    next_value = hooks.get("next")
    role_text = _role_text_mapping(next_value)
    if not role_text:
        return None
    return HookDefinitionSettings(points=["next"], after=RoleHooksSettings.model_validate(role_text))


def _legacy_inline_hook_registry(data: dict[str, object]) -> dict[str, HookDefinitionSettings]:
    hooks: dict[str, HookDefinitionSettings] = {}
    next_hook = _legacy_next_hook_definition(data)
    if next_hook is not None:
        hooks[_LEGACY_NEXT_HOOK_ID] = next_hook
    return hooks


def load_hook_registry(base_dir: Path | None = None) -> HookRegistrySettings:
    base = resolve_root(base_dir)
    path = hook_registry_path(base)
    raw_registry = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    registry = HookRegistrySettings.model_validate(raw_registry or {})
    inline_legacy = _legacy_inline_hook_registry(_load_raw_config_data(base))
    if inline_legacy:
        collisions = sorted(set(registry.hooks) & set(inline_legacy))
        if collisions:
            raise ValueError(f"loom-hooks.toml cannot redefine reserved legacy hook ids: {', '.join(collisions)}")
        registry = HookRegistrySettings(hooks={**registry.hooks, **inline_legacy})
    return registry


def _toml_string(value: str) -> str:
    if "\n" in value:
        return '"""\n' + value.rstrip("\n") + '\n"""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_role_hook_lines(hooks: RoleHooksSettings) -> list[str]:
    rendered_hook_lines: list[str] = []
    for key in _ROLE_TEXT_KEYS:
        value = getattr(hooks, key)
        if value:
            rendered_hook_lines.append(f"{key} = {_toml_string(value)}\n")
    return rendered_hook_lines


def _render_hook_registry_block(hook_id: str, hook: HookDefinitionSettings) -> list[str]:
    rendered = [f"[hooks.{hook_id}]\n"]
    points = ", ".join(_toml_string(point) for point in hook.points)
    rendered.append(f"points = [{points}]\n")
    before_lines = _render_role_hook_lines(hook.before)
    after_lines = _render_role_hook_lines(hook.after)
    if before_lines:
        rendered.extend(["\n", f"[hooks.{hook_id}.before]\n", *before_lines])
    if after_lines:
        rendered.extend(["\n", f"[hooks.{hook_id}.after]\n", *after_lines])
    rendered.append("\n")
    return rendered


def dump_settings(settings: LoomSettings) -> str:
    executor_command = settings.agent.executor_command.replace("\\", "\\\\").replace('"', '\\"')
    parts = [
        f'[project]\nname = "{settings.project.name}"\n\n',
        "[agent]\n"
        f"inbox_plan_batch = {settings.agent.inbox_plan_batch}\n"
        f"task_batch = {settings.agent.task_batch}\n"
        f"next_wait_seconds = {settings.agent.next_wait_seconds}\n"
        f"next_retries = {settings.agent.next_retries}\n"
        f'executor_command = "{executor_command}"\n'
        f"offline_after_minutes = {settings.agent.offline_after_minutes}\n"
        f"spawn_limit_active_workers = {settings.agent.spawn_limit_active_workers}\n"
        f"spawn_limit_idle_workers = {settings.agent.spawn_limit_idle_workers}\n"
        "# TODO: resume/reattach-related agent config is still under research.\n"
        "# Possible future settings may include explicit resume command templates.\n\n",
        f"[threads]\ndefault_priority = {settings.threads.default_priority}\n\n",
    ]
    if settings.hooks:
        for index, hook in enumerate(settings.hooks):
            if index:
                parts.append("\n")
            parts.append("[[hooks]]\n")
            if hook.builtin:
                parts.append(f"builtin = {_toml_string(hook.builtin)}\n")
            else:
                parts.append(f"id = {_toml_string(hook.id)}\n")
            points = ", ".join(_toml_string(point) for point in hook.points)
            parts.append(f"points = [{points}]\n")
    else:
        parts.extend(
            [
                "# [[hooks]]\n",
                '# builtin = "commit-message-policy"\n',
                '# points = ["next"]\n',
                "\n",
                "# [[hooks]]\n",
                '# builtin = "worker-done-review"\n',
                '# points = ["done"]\n',
            ]
        )
    parts.extend(
        [
            "\n",
            "# Hook definitions live in `loom-hooks.toml`.\n",
            '# Use `builtin = "..."` for built-ins or `id = "..."` for repo-local hook ids.\n',
        ]
    )
    return "".join(parts)


def dump_hook_registry(registry: HookRegistrySettings | None = None) -> str:
    if registry is None or not registry.hooks:
        return "".join(
            [
                "# Repo-local Loom hook definitions.\n",
                "# `loom.toml` decides which hooks are active and in what order.\n",
                "# Built-in hook ids do not need to be repeated here.\n",
                "\n",
                "# [hooks.repo-next-reminder]\n",
                '# points = ["next"]\n',
                "\n",
                "# [hooks.repo-next-reminder.before]\n",
                '# worker = "Run the focused tests before handing off."\n',
                "\n",
                "# [hooks.repo-next-reminder.after]\n",
                '# manager = "Keep the next handoff mailbox-first."\n',
                "\n",
                "# [hooks.repo-done-summary]\n",
                '# points = ["done"]\n',
                "\n",
                "# [hooks.repo-done-summary.after]\n",
                '# worker = "Double-check the handoff summary after `loom agent done`."\n',
            ]
        )

    parts: list[str] = []
    for hook_id, hook in registry.hooks.items():
        if hook_id == _LEGACY_NEXT_HOOK_ID:
            continue
        parts.extend(_render_hook_registry_block(hook_id, hook))
    return "".join(parts)


def ensure_settings(base_dir: Path | None = None, project_name: str = "") -> tuple[LoomSettings, bool]:
    base = resolve_root(base_dir)
    path = config_path(base)
    if path.exists():
        return load_settings(base), False

    settings = default_settings(project_name or base.name)
    path.write_text(dump_settings(settings), encoding="utf-8")
    return settings, True


def ensure_hook_registry(base_dir: Path | None = None) -> tuple[HookRegistrySettings, bool]:
    base = resolve_root(base_dir)
    path = hook_registry_path(base)
    if path.exists():
        return load_hook_registry(base), False

    registry = HookRegistrySettings()
    path.write_text(dump_hook_registry(registry), encoding="utf-8")
    return registry, True
