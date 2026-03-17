"""Configuration handling for root-level `loom.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from .runtime import resolve_root


class ProjectSettings(BaseModel):
    name: str = ""


class AgentSettings(BaseModel):
    inbox_plan_batch: int = 10
    task_batch: int = 1
    next_wait_seconds: float = 0.0
    next_retries: int = 0
    executor_command: str = ""
    offline_after_minutes: int = 30


class ThreadDefaults(BaseModel):
    default_priority: int = 50


class LoomSettings(BaseModel):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    threads: ThreadDefaults = Field(default_factory=ThreadDefaults)


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


def dump_settings(settings: LoomSettings) -> str:
    executor_command = settings.agent.executor_command.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "[project]\n"
        f'name = "{settings.project.name}"\n\n'
        "[agent]\n"
        f"inbox_plan_batch = {settings.agent.inbox_plan_batch}\n"
        f"task_batch = {settings.agent.task_batch}\n"
        f"next_wait_seconds = {settings.agent.next_wait_seconds}\n"
        f"next_retries = {settings.agent.next_retries}\n"
        f'executor_command = "{executor_command}"\n'
        f"offline_after_minutes = {settings.agent.offline_after_minutes}\n"
        "# TODO: resume/reattach-related agent config is still under research.\n"
        "# Possible future settings may include explicit resume command templates.\n\n"
        "[threads]\n"
        f"default_priority = {settings.threads.default_priority}\n"
    )


def ensure_settings(base_dir: Path | None = None, project_name: str = "") -> tuple[LoomSettings, bool]:
    base = resolve_root(base_dir)
    path = config_path(base)
    if path.exists():
        return load_settings(base), False

    settings = default_settings(project_name or base.name)
    path.write_text(dump_settings(settings), encoding="utf-8")
    return settings, True
