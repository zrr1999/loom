from __future__ import annotations

from loom.cli import app


def test_init_creates_default_structure(runner, isolated_project):
    result = runner.invoke(app, ["init", "--project", "demo"])

    assert result.exit_code == 0, result.output
    loom_dir = isolated_project / ".loom"
    assert loom_dir.is_dir()
    assert (loom_dir / "inbox").is_dir()
    assert (loom_dir / "threads").is_dir()
    assert (loom_dir / "agents" / "_manager.md").exists()
    config = (isolated_project / "loom.toml").read_text(encoding="utf-8")
    assert 'name = "demo"' in config
    assert "inbox_plan_batch = 10" in config
    assert "task_batch = 1" in config
    assert "next_wait_seconds = 0.0" in config
    assert "next_retries = 0" in config
    assert 'executor_command = ""' in config
    assert "offline_after_minutes = 30" in config


def test_init_is_idempotent_and_preserves_existing_config(runner, isolated_project):
    first = runner.invoke(app, ["init", "--project", "demo"])
    assert first.exit_code == 0, first.output
    config_path = isolated_project / "loom.toml"
    config_path.write_text(
        (
            '[project]\nname = "custom"\n\n'
            '[agent]\ninbox_plan_batch = 3\ntask_batch = 2\nexecutor_command = "runner"\noffline_after_minutes = 99\n\n'
            "[threads]\ndefault_priority = 77\n"
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["init", "--project", "ignored"])

    assert result.exit_code == 0, result.output
    assert (isolated_project / ".loom").is_dir()
    assert 'name = "custom"' in config_path.read_text(encoding="utf-8")
    assert 'executor_command = "runner"' in config_path.read_text(encoding="utf-8")
    assert "offline_after_minutes = 99" in config_path.read_text(encoding="utf-8")


def test_happy_path_lifecycle_and_status_summary(runner, isolated_project):
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0

    env = {"LOOM_AGENT_ID": "x7k2"}

    thread_result = runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--priority", "90"], env=env)
    assert thread_result.exit_code == 0, thread_result.output
    assert "CREATED thread AA" in thread_result.output

    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "AA",
            "--title",
            "实现 token 刷新接口",
            "--acceptance",
            "- [ ] POST /auth/refresh 返回新 access token",
            "--created-from",
            "RQ-001,RQ-002",
        ],
        env=env,
    )
    assert task_result.exit_code == 0, task_result.output
    assert "CREATED task" in task_result.output
    assert "status : scheduled" in task_result.output
    # extract task_id from output: "CREATED task AA-001-..."
    task_id = task_result.output.splitlines()[0].split()[-1]

    task_file = isolated_project / ".loom" / "threads" / "AA" / f"{task_id}.md"
    task_content = task_file.read_text(encoding="utf-8")
    assert "status: scheduled" in task_content
    assert "created_from:" in task_content
    assert "- RQ-001" in task_content
    assert "## 背景" in task_content

    next_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert next_result.exit_code == 0, next_result.output
    assert "ACTION  task" in next_result.output
    assert task_id in next_result.output
    assert "none: false" in next_result.output

    done_result = runner.invoke(app, ["agent", "done", task_id, "--output", "./output/demo"], env=env)
    assert done_result.exit_code == 0, done_result.output
    assert f"DONE task {task_id}" in done_result.output
    assert "reviewing" in done_result.output

    review_result = runner.invoke(app, ["review"])
    assert review_result.exit_code == 0, review_result.output
    assert task_id in review_result.output
    assert "output: ./output/demo" in review_result.output
    assert "loom accept <id>" in review_result.output

    accept_result = runner.invoke(app, ["accept", task_id])
    assert accept_result.exit_code == 0, accept_result.output

    status_result = runner.invoke(app, ["agent", "status"])
    assert status_result.exit_code == 0, status_result.output
    assert "PROJECT STATUS" in status_result.output
    assert "done" in status_result.output


def test_pause_decide_and_queue_listing(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend"], env=env).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "AA",
            "--title",
            "OAuth provider selection",
            "--acceptance",
            "- [ ] 完成 provider 选择",
        ],
        env=env,
    )
    task_id = task_result.output.splitlines()[0].split()[-1]

    claim_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert claim_result.exit_code == 0, claim_result.output
    assert task_id in claim_result.output

    pause_result = runner.invoke(
        app,
        [
            "agent",
            "pause",
            task_id,
            "--question",
            "Use Google or GitHub?",
            "--options",
            '[{"id":"A","label":"Google","note":"broader reach"}]',
        ],
        env=env,
    )
    assert pause_result.exit_code == 0, pause_result.output

    default_result = runner.invoke(app, [])
    assert default_result.exit_code == 0, default_result.output
    assert f"[paused] {task_id}" in default_result.output

    decide_result = runner.invoke(app, ["decide", task_id, "A"])
    assert decide_result.exit_code == 0, decide_result.output
    task_content = (isolated_project / ".loom" / "threads" / "AA" / f"{task_id}.md").read_text(encoding="utf-8")
    assert "decided: A" in task_content
    assert "status: scheduled" in task_content


def test_pause_command_requires_question_flag(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend"], env=env).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "AA",
            "--title",
            "Need decision",
            "--acceptance",
            "- [ ] done",
        ],
        env=env,
    )
    task_id = task_result.output.splitlines()[0].split()[-1]

    claim_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert claim_result.exit_code == 0, claim_result.output
    assert task_id in claim_result.output

    # Without --question, pause must fail
    result = runner.invoke(app, ["agent", "pause", task_id], env=env)

    assert result.exit_code == 1
    assert "missing_question" in result.output


def test_next_returns_plan_action_when_inbox_pending(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "planner1"}
    assert runner.invoke(app, ["inbox", "add", "支持 Google OAuth 登录"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next"], env=env)
    assert result.exit_code == 0, result.output

    assert "ACTION  plan" in result.output
    assert "RQ-001" in result.output
    assert "loom agent new-thread" in result.output
    assert "none: false" in result.output

    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")
    assert "status: pending" in inbox_content


def test_default_queue_interactive_flow_handles_paused_and_reviewing_only(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--priority", "90"], env=env).exit_code == 0

    paused_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "AA",
            "--title",
            "Paused task",
            "--acceptance",
            "- [ ] paused",
        ],
        env=env,
    )
    paused_id = paused_result.output.splitlines()[0].split()[-1]

    reviewing_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "AA",
            "--title",
            "Review task",
            "--acceptance",
            "- [ ] review",
        ],
        env=env,
    )
    reviewing_id = reviewing_result.output.splitlines()[0].split()[-1]

    first_claim = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert first_claim.exit_code == 0, first_claim.output
    assert paused_id in first_claim.output

    assert (
        runner.invoke(
            app,
            [
                "agent",
                "pause",
                paused_id,
                "--question",
                "Ship now?",
                "--options",
                '[{"id":"A","label":"Yes","note":"ship it"}]',
            ],
            env=env,
        ).exit_code
        == 0
    )

    second_claim = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert second_claim.exit_code == 0, second_claim.output
    assert reviewing_id in second_claim.output

    assert runner.invoke(app, ["agent", "done", reviewing_id, "--output", "./artifacts/review"], env=env).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Add OAuth login"])

    result = runner.invoke(
        app,
        [],
        input="detail\ndecide\nA\ndetail\nreject\nNeed fixes\n",
    )

    assert result.exit_code == 0, result.output
    assert "Queue summary:" in result.output
    assert "decided: 1" in result.output
    assert "rejected: 1" in result.output

    paused_content = (isolated_project / ".loom" / "threads" / "AA" / f"{paused_id}.md").read_text(encoding="utf-8")
    reviewing_content = (isolated_project / ".loom" / "threads" / "AA" / f"{reviewing_id}.md").read_text(
        encoding="utf-8"
    )
    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")

    assert "decided: A" in paused_content
    assert "status: scheduled" in paused_content
    assert "status: scheduled" in reviewing_content
    assert "rejection_note: Need fixes" in reviewing_content
    assert "status: pending" in inbox_content


def test_default_queue_ignores_pending_inbox_items(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Add OAuth login"]).exit_code == 0

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "No pending approvals." in result.output
    assert "loom inbox add" in result.output


def test_inbox_command_without_subcommand_runs_interactive_planning(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Add OAuth login"]).exit_code == 0

    result = runner.invoke(app, ["inbox"], input="\n")

    assert result.exit_code == 0, result.output
    assert "[inbox] RQ-001:" in result.output
    assert "Planned RQ-001 ->" in result.output
    assert "Inbox planning summary:" in result.output
    assert "planned: 1" in result.output

    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")
    assert "status: planned" in inbox_content
    assert "planned_to:" in inbox_content


def test_inbox_command_without_subcommand_shows_empty_message(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["inbox"])

    assert result.exit_code == 0, result.output
    assert "No pending inbox items." in result.output


def test_scheduler_respects_dependencies_and_thread_priority(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--priority", "80"], env=env).exit_code == 0
    assert runner.invoke(app, ["agent", "new-thread", "--name", "frontend", "--priority", "95"], env=env).exit_code == 0

    backend_task = (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "AA",
                "--title",
                "backend base",
                "--acceptance",
                "- [ ] base ready",
            ],
            env=env,
        )
        .output.splitlines()[0]
        .split()[-1]
    )

    frontend_task = (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "AB",
                "--title",
                "frontend shell",
                "--acceptance",
                "- [ ] shell ready",
            ],
            env=env,
        )
        .output.splitlines()[0]
        .split()[-1]
    )

    dependent_task = (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "AA",
                "--title",
                "backend follow-up",
                "--acceptance",
                "- [ ] follow-up ready",
                "--depends-on",
                frontend_task,
            ],
            env=env,
        )
        .output.splitlines()[0]
        .split()[-1]
    )

    next_out = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env).output
    assert frontend_task in next_out

    assert runner.invoke(app, ["agent", "done", frontend_task], env=env).exit_code == 0
    assert runner.invoke(app, ["accept", frontend_task]).exit_code == 0

    next_out_after = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env).output
    assert backend_task in next_out_after
    assert dependent_task not in next_out_after


def test_agent_next_prioritizes_planning_pending_inbox_items(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    assert runner.invoke(app, ["inbox", "add", "Add OAuth login"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Add audit log"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next"], env=env)

    assert result.exit_code == 0, result.output
    assert "ACTION  plan" in result.output
    assert "RQ-001" in result.output
    assert "RQ-002" in result.output
    assert "loom agent new-thread" in result.output
    assert "none: false" in result.output
    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")
    assert "status: pending" in inbox_content


def test_agent_next_respects_configured_inbox_plan_batch(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    config_path = isolated_project / "loom.toml"
    config_path.write_text(
        (
            '[project]\nname = "demo"\n\n'
            '[agent]\ninbox_plan_batch = 1\ntask_batch = 2\nexecutor_command = ""\noffline_after_minutes = 30\n\n'
            "[threads]\ndefault_priority = 50\n"
        ),
        encoding="utf-8",
    )
    assert runner.invoke(app, ["inbox", "add", "A"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "B"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next"], env=env)

    assert result.exit_code == 0, result.output
    assert "ACTION  plan" in result.output
    assert "COUNT   1" in result.output
    assert "RQ-001" in result.output
    # Only 1 item in the batch — RQ-002 should not appear
    assert "RQ-002" not in result.output


def test_agent_next_returns_multiple_ready_tasks_when_configured(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    config_path = isolated_project / "loom.toml"
    config_path.write_text(
        (
            '[project]\nname = "demo"\n\n'
            '[agent]\ninbox_plan_batch = 10\ntask_batch = 2\nexecutor_command = ""\noffline_after_minutes = 30\n\n'
            "[threads]\ndefault_priority = 50\n"
        ),
        encoding="utf-8",
    )
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend"], env=env).exit_code == 0
    first_task = (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "AA",
                "--title",
                "Task one",
                "--acceptance",
                "- [ ] one",
            ],
            env=env,
        )
        .output.splitlines()[0]
        .split()[-1]
    )
    second_task = (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "AA",
                "--title",
                "Task two",
                "--acceptance",
                "- [ ] two",
            ],
            env=env,
        )
        .output.splitlines()[0]
        .split()[-1]
    )

    out = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env).output

    assert "ACTION  task" in out
    assert "COUNT   2" in out
    assert first_task in out
    assert second_task in out


def test_agent_next_idle_default_does_not_sleep(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    called = {"sleep": 0}

    def fail_sleep(_seconds: float) -> None:
        called["sleep"] += 1
        raise AssertionError("sleep should not be called with default next settings")

    monkeypatch.setattr("loom.agent.time.sleep", fail_sleep)

    result = runner.invoke(app, ["agent", "next", "--manager"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert called["sleep"] == 0


def test_agent_next_wait_retries_cli_overrides(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("loom.agent.time.sleep", fake_sleep)

    result = runner.invoke(
        app,
        ["agent", "next", "--wait-seconds", "0.25", "--retries", "2", "--manager"],
    )

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert sleeps == [0.25, 0.25]


def test_agent_next_wait_retries_uses_config_defaults(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    config_path = isolated_project / "loom.toml"
    config_path.write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\ninbox_plan_batch = 10\ntask_batch = 1\nnext_wait_seconds = 0.1\nnext_retries = 3\n"
            'executor_command = ""\noffline_after_minutes = 30\n\n'
            "[threads]\ndefault_priority = 50\n"
        ),
        encoding="utf-8",
    )

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("loom.agent.time.sleep", fake_sleep)

    result = runner.invoke(app, ["agent", "next", "--manager"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert sleeps == [0.1, 0.1, 0.1]


def test_log_shows_recorded_events(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_AGENT_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend"], env=env).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "AA",
            "--title",
            "Record event",
            "--acceptance",
            "- [ ] ready",
        ],
        env=env,
    )
    task_id = task_result.output.splitlines()[0].split()[-1]

    claim_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert claim_result.exit_code == 0, claim_result.output
    assert task_id in claim_result.output

    assert runner.invoke(app, ["agent", "done", task_id, "--output", "./out"], env=env).exit_code == 0

    result = runner.invoke(app, ["log"])

    assert result.exit_code == 0, result.output
    assert "thread.created thread:AA" in result.output
    assert f"task.created task:{task_id}" in result.output
    assert f"task.transitioned task:{task_id}" in result.output


def test_agent_start_returns_loop_prompt(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "start"])

    assert result.exit_code == 0, result.output
    assert "DO THIS NOW" in result.output
    assert "RIGHT NOW" not in result.output
    assert "CURRENT STATE" in result.output
    assert "loom agent next" in result.output
    assert "done <task-id>" in result.output
    assert "pause <task-id>" in result.output
    assert "WORKSPACE" in result.output
    assert "Global mode is active (-g)." not in result.output


def test_agent_start_global_mode_mentions_global_guidance(runner, isolated_project, monkeypatch):
    home = isolated_project / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert runner.invoke(app, ["init", "-g", "--project", "global-demo"]).exit_code == 0

    result = runner.invoke(app, ["agent", "-g", "start"])

    assert result.exit_code == 0, result.output
    assert "Global mode is active (-g)." in result.output


def test_agent_start_rejects_executor(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "start"], env={"LOOM_AGENT_ID": "x7k2"})

    assert result.exit_code == 1
    assert "executor_not_allowed" in result.output


def test_global_flag_uses_home_directory(runner, isolated_project, monkeypatch):
    home = isolated_project / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(app, ["init", "-g", "--project", "global-demo"])

    assert result.exit_code == 0, result.output
    assert (home / ".loom").is_dir()
    assert (home / "loom.toml").exists()


def test_spawn_whoami_checkpoint_resume_and_inbox_flow(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    spawn_result = runner.invoke(app, ["agent", "spawn", "--threads", "AA,AB"])
    assert spawn_result.exit_code == 0, spawn_result.output
    assert "SPAWNED agent" in spawn_result.output
    assert "Executor environment file" in spawn_result.output
    assert "Default launch pattern" in spawn_result.output
    assert "No executor command is configured in loom.toml." in spawn_result.output
    # extract agent_id from "SPAWNED agent <id>"
    agent_id = spawn_result.output.splitlines()[0].split()[-1]
    assert f".loom/agents/{agent_id}/{agent_id}.env" in spawn_result.output
    assert "If your subagent runtime cannot set environment variables at all:" in spawn_result.output
    assert "<your-agent-cmd>" not in spawn_result.output
    env = {"LOOM_AGENT_ID": agent_id}

    whoami_result = runner.invoke(app, ["agent", "whoami"], env=env)
    assert whoami_result.exit_code == 0, whoami_result.output
    assert agent_id in whoami_result.output
    assert "executor" in whoami_result.output

    checkpoint_result = runner.invoke(
        app, ["agent", "checkpoint", "working on auth", "--phase", "implementing"], env=env
    )
    assert checkpoint_result.exit_code == 0, checkpoint_result.output
    assert "CHECKPOINT recorded" in checkpoint_result.output

    resume_result = runner.invoke(app, ["agent", "resume"], env=env)
    assert resume_result.exit_code == 0, resume_result.output
    assert "working on auth" in resume_result.output

    send_result = runner.invoke(app, ["agent", "send", agent_id, "please check", "--manager"])
    assert send_result.exit_code == 0, send_result.output
    assert "SENT message" in send_result.output
    # extract msg_id from "SENT message MSG-xxx"
    msg_id = send_result.output.splitlines()[0].split()[-1]

    inbox_result = runner.invoke(app, ["agent", "inbox"], env=env)
    assert inbox_result.exit_code == 0, inbox_result.output
    assert msg_id in inbox_result.output

    reply_result = runner.invoke(app, ["agent", "reply", msg_id, "got it"], env=env)
    assert reply_result.exit_code == 0, reply_result.output
    assert "REPLIED" in reply_result.output


def test_spawn_rejects_executor_context(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "spawn"], env={"LOOM_AGENT_ID": "x7k2"})

    assert result.exit_code == 1
    assert "executor_not_allowed" in result.output
    assert "manager-only" in result.output


def test_spawn_uses_configured_executor_command(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    config_path = isolated_project / "loom.toml"
    config_path.write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\ninbox_plan_batch = 10\ntask_batch = 1\n"
            'executor_command = "opencode run --loom-agent {agent_id}"\n'
            "offline_after_minutes = 30\n\n"
            "[threads]\ndefault_priority = 50\n"
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["agent", "spawn", "--threads", "AA"])

    assert result.exit_code == 0, result.output
    agent_id = result.output.splitlines()[0].split()[-1]
    assert "Configured executor command" in result.output
    assert f"opencode run --loom-agent {agent_id}" in result.output
    assert (
        f"source {isolated_project / '.loom' / 'agents' / agent_id / f'{agent_id}.env'} && opencode run --loom-agent {agent_id}"
        in result.output
    )
    assert (
        f"LOOM_AGENT_ID={agent_id} LOOM_DIR={isolated_project / '.loom'} LOOM_THREADS=AA opencode run --loom-agent {agent_id}"
        in result.output
    )


def test_agent_commands_require_agent_id_without_manager_flag(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "new-thread", "--name", "backend"])

    assert result.exit_code == 1, result.output
    assert "LOOM_AGENT_ID is required" in result.output


def test_agent_commands_allow_manager_override(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--manager"])

    assert result.exit_code == 0, result.output
    assert "CREATED thread AA" in result.output


def test_agent_next_shows_ready_tasks_for_manager_without_claiming(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--manager"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "AA",
            "--title",
            "Manager claimed task",
            "--acceptance",
            "- [ ] ready",
            "--manager",
        ],
    )
    task_id = task_result.output.splitlines()[0].split()[-1]

    out = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--manager"]).output

    assert "ACTION  task" in out
    assert "ACTOR   manager" in out
    assert "READY TASKS" in out
    assert "loom agent spawn [--threads <AA,AB>]" in out
    assert task_id in out

    task_content = (isolated_project / ".loom" / "threads" / "AA" / f"{task_id}.md").read_text(encoding="utf-8")
    assert "status: scheduled" in task_content


def test_empty_default_queue_shows_add_requirement_hint(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "No pending approvals." in result.output
    assert "loom inbox add" in result.output
