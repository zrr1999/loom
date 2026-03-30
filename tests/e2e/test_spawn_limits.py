from __future__ import annotations

from pathlib import Path

from loom.cli import app


def _write_spawn_config(path: Path, *, active_limit: int, idle_limit: int) -> None:
    path.write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\n"
            "inbox_plan_batch = 10\n"
            "task_batch = 1\n"
            "next_wait_seconds = 60.0\n"
            "next_retries = 5\n"
            'executor_command = ""\n'
            "offline_after_minutes = 30\n"
            f"spawn_limit_active_workers = {active_limit}\n"
            f"spawn_limit_idle_workers = {idle_limit}\n\n"
            "[threads]\n"
            "default_priority = 50\n"
        ),
        encoding="utf-8",
    )


def test_spawn_rejects_when_idle_worker_limit_is_reached(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    _write_spawn_config(isolated_project / "loom.toml", active_limit=4, idle_limit=1)

    first = runner.invoke(app, ["spawn"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["spawn"])
    assert second.exit_code == 1
    assert "spawn_limit_reached" in second.output
    assert "idle workers 1/1" in second.output
    assert "loom spawn --force" in second.output


def test_spawn_force_overrides_worker_limit(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    _write_spawn_config(isolated_project / "loom.toml", active_limit=1, idle_limit=8)

    first = runner.invoke(app, ["spawn"])
    assert first.exit_code == 0, first.output
    agent_id = first.output.splitlines()[0].split()[-1]

    checkpoint = runner.invoke(
        app,
        ["agent", "checkpoint", "working", "--phase", "implementing"],
        env={"LOOM_WORKER_ID": agent_id},
    )
    assert checkpoint.exit_code == 0, checkpoint.output

    blocked = runner.invoke(app, ["spawn"])
    assert blocked.exit_code == 1
    assert "active workers 1/1" in blocked.output

    forced = runner.invoke(app, ["spawn", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "SPAWNED agent" in forced.output
