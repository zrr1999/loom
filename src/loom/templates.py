"""Default markdown body templates for loom-managed files."""

from __future__ import annotations


def default_config_toml(project_name: str, inbox_plan_batch: int = 10) -> str:
    return (
        "[project]\n"
        f'name = "{project_name}"\n\n'
        "[agent]\n"
        f"inbox_plan_batch = {inbox_plan_batch}\n"
        "task_batch = 1\n"
        'executor_command = ""\n'
        "offline_after_minutes = 30\n"
        "spawn_limit_active_workers = 8\n"
        "spawn_limit_idle_workers = 2\n"
        "# TODO: resume/reattach-related agent config is still under research.\n"
        "# Possible future settings may include explicit resume command templates.\n\n"
        "[threads]\n"
        "default_priority = 50\n"
    )


def thread_body() -> str:
    return "## 目标\n\n描述这个 thread 负责的范围。\n\n## 约定\n\n- 补充这个执行线的约束"


def task_body(background: str = "", implementation_direction: str = "") -> str:
    background_text = background.strip()
    implementation_text = implementation_direction.strip()
    return f"## 背景\n\n{background_text}\n\n## 实现方向\n\n{implementation_text}"


def agent_body() -> str:
    return "## Checkpoint\n\n未记录。\n\n## Notes\n\n"
