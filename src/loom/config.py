"""Configuration handling for root-level `loom.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .runtime import resolve_root
from .soft_hooks import (
    available_done_after_hook_examples,
    available_done_before_hook_examples,
    available_next_hook_examples,
)


class ProjectSettings(BaseModel):
    name: str = ""


class AgentSettings(BaseModel):
    inbox_plan_batch: int = 10
    task_batch: int = 1
    next_wait_seconds: float = 0.0
    next_retries: int = 0
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


def _validate_examples(value: list[str], *, available_examples: tuple[str, ...], field_name: str) -> list[str]:
    valid_examples = set(available_examples)
    unknown = sorted({name for name in value if name not in valid_examples})
    if unknown:
        available = ", ".join(available_examples)
        detail = f" Available examples: {available}." if available else ""
        raise ValueError(f"Unknown {field_name} entries: {', '.join(unknown)}.{detail}")
    return value


class RoleHooksSettings(BaseModel):
    all: str = ""
    manager: str = ""
    worker: str = ""
    director: str = ""
    reviewer: str = ""
    examples: list[str] = Field(default_factory=list)


class NextHooksSettings(RoleHooksSettings):
    @field_validator("examples")
    @classmethod
    def validate_examples(cls, value: list[str]) -> list[str]:
        return _validate_examples(
            value,
            available_examples=available_next_hook_examples(),
            field_name="hooks.next.examples",
        )


class DoneBeforeHooksSettings(RoleHooksSettings):
    @field_validator("examples")
    @classmethod
    def validate_examples(cls, value: list[str]) -> list[str]:
        return _validate_examples(
            value,
            available_examples=available_done_before_hook_examples(),
            field_name="hooks.done.before.examples",
        )


class DoneAfterHooksSettings(RoleHooksSettings):
    @field_validator("examples")
    @classmethod
    def validate_examples(cls, value: list[str]) -> list[str]:
        return _validate_examples(
            value,
            available_examples=available_done_after_hook_examples(),
            field_name="hooks.done.after.examples",
        )


class DoneHooksSettings(BaseModel):
    before: DoneBeforeHooksSettings = Field(default_factory=DoneBeforeHooksSettings)
    after: DoneAfterHooksSettings = Field(default_factory=DoneAfterHooksSettings)


class HooksSettings(BaseModel):
    next: NextHooksSettings = Field(default_factory=NextHooksSettings)
    done: DoneHooksSettings = Field(default_factory=DoneHooksSettings)


class LoomSettings(BaseModel):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    threads: ThreadDefaults = Field(default_factory=ThreadDefaults)
    hooks: HooksSettings = Field(default_factory=HooksSettings)


def config_path(base_dir: Path | None = None) -> Path:
    root = resolve_root(base_dir)
    return root / "loom.toml"


def default_settings(project_name: str = "") -> LoomSettings:
    return LoomSettings(project=ProjectSettings(name=project_name))


def load_settings(base_dir: Path | None = None) -> LoomSettings:
    base = resolve_root(base_dir)
    path = config_path(base)
    if not path.exists():
        return default_settings(base.name)

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = LoomSettings.model_validate(data)
    if not settings.project.name:
        settings.project.name = base.name
    return settings


def _toml_string(value: str) -> str:
    if "\n" in value:
        return '"""\n' + value.rstrip("\n") + '\n"""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_role_hook_lines(hooks: RoleHooksSettings) -> list[str]:
    rendered_hook_lines: list[str] = []
    for key in ("all", "manager", "worker", "director", "reviewer"):
        value = getattr(hooks, key)
        if value:
            rendered_hook_lines.append(f"{key} = {_toml_string(value)}\n")
    if hooks.examples:
        examples = ", ".join(_toml_string(name) for name in hooks.examples)
        rendered_hook_lines.append(f"examples = [{examples}]\n")
    return rendered_hook_lines


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
        "[hooks.next]\n",
    ]
    next_hooks = settings.hooks.next
    rendered_hook_lines = _render_role_hook_lines(next_hooks)
    if rendered_hook_lines:
        parts.extend(rendered_hook_lines)
    else:
        parts.extend(
            [
                '# all = "Shared reminder shown to every role."\n',
                '# worker = "Run tests before `loom agent done`."\n',
                '# examples = ["commit-message-policy"]\n',
            ]
        )
    parts.extend(["\n", "[hooks.done.before]\n"])
    done_before_hooks = _render_role_hook_lines(settings.hooks.done.before)
    if done_before_hooks:
        parts.extend(done_before_hooks)
    else:
        parts.extend(
            [
                '# worker = "Before `loom agent done`, refresh your checkpoint and re-scan the diff."\n',
                '# examples = ["worker-done-review"]\n',
            ]
        )
    parts.extend(["\n", "[hooks.done.after]\n"])
    done_after_hooks = _render_role_hook_lines(settings.hooks.done.after)
    if done_after_hooks:
        parts.extend(done_after_hooks)
    else:
        parts.extend(
            [
                (
                    '# worker = "After `loom agent done`, make sure the review handoff '
                    'names tests, output, and blockers."\n'
                ),
            ]
        )
    return "".join(parts)


def ensure_settings(base_dir: Path | None = None, project_name: str = "") -> tuple[LoomSettings, bool]:
    base = resolve_root(base_dir)
    path = config_path(base)
    if path.exists():
        return load_settings(base), False

    settings = default_settings(project_name or base.name)
    path.write_text(dump_settings(settings), encoding="utf-8")
    return settings, True
