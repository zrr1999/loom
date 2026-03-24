from __future__ import annotations

import pytest
import yaml

from loom.cli import app
from loom.models import AgentRole, Task, TaskKind, TaskStatus


def _create_assigned_task(runner) -> str:
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Claimed task",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ],
    )
    assert task_result.exit_code == 0, task_result.output
    task_id = task_result.output.splitlines()[0].split()[-1]

    claim_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env={"LOOM_WORKER_ID": "x7k2"})
    assert claim_result.exit_code == 0, claim_result.output
    assert task_id in claim_result.output
    return task_id


def _shared_command_args(command_name: str, runner) -> list[str]:
    if command_name == "new-thread":
        return ["agent", "new-thread", "--name", "backend"]
    if command_name == "new-task":
        assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
        return [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Demo",
            "--acceptance",
            "- [ ] ready",
        ]
    if command_name == "next":
        return ["agent", "next"]
    if command_name == "done":
        return ["agent", "done", "backend-001", "--output", "./output/demo"]
    if command_name == "pause":
        return [
            "agent",
            "pause",
            "backend-001",
            "--question",
            "Ship now?",
            "--options",
            '[{"id":"A","label":"Yes","note":"ship it"}]',
        ]
    if command_name == "propose":
        return ["agent", "propose", "worker-001", "task handoff", "--ref", "backend-001"]
    if command_name == "send":
        return ["agent", "send", "worker-001", "extra context", "--ref", "backend-001"]
    raise AssertionError(f"unknown command: {command_name}")


def _shared_manager_override_args(command_name: str, runner) -> list[str]:
    if command_name == "new-thread":
        return ["agent", "new-thread", "--name", "backend", "--role", "manager"]
    if command_name == "new-task":
        assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
        return [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Demo",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ]
    if command_name == "next":
        return ["agent", "next", "--role", "manager"]
    if command_name == "done":
        task_id = _create_assigned_task(runner)
        return ["agent", "done", task_id, "--output", "./output/demo", "--role", "manager"]
    if command_name == "pause":
        task_id = _create_assigned_task(runner)
        return [
            "agent",
            "pause",
            task_id,
            "--question",
            "Ship now?",
            "--options",
            '[{"id":"A","label":"Yes","note":"ship it"}]',
            "--role",
            "manager",
        ]
    if command_name == "propose":
        return ["agent", "propose", "worker-001", "task handoff", "--ref", "backend-001", "--role", "manager"]
    if command_name == "send":
        return ["agent", "send", "worker-001", "extra context", "--ref", "backend-001", "--role", "manager"]
    raise AssertionError(f"unknown command: {command_name}")


def _read_frontmatter(path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:end])) or {}


def test_init_creates_default_structure(runner, isolated_project):
    result = runner.invoke(app, ["init", "--project", "demo"])

    assert result.exit_code == 0, result.output
    loom_dir = isolated_project / ".loom"
    assert loom_dir.is_dir()
    assert (loom_dir / "inbox").is_dir()
    assert (loom_dir / "threads").is_dir()
    assert not (loom_dir / "worktrees").exists()
    assert (loom_dir / "agents" / "_manager.md").exists()
    assert (loom_dir / "agents" / "workers").is_dir()
    config = (isolated_project / "loom.toml").read_text(encoding="utf-8")
    assert 'name = "demo"' in config
    assert "inbox_plan_batch = 10" in config
    assert "task_batch = 1" in config
    assert "next_wait_seconds = 0.0" in config
    assert "next_retries = 0" in config
    assert 'executor_command = ""' in config
    assert "offline_after_minutes = 30" in config
    assert "[hooks.next]" in config
    assert '# examples = ["commit-message-policy"]' in config
    assert "[hooks.done.before]" in config
    assert "Before `loom agent done`, refresh your checkpoint and re-scan the diff." in config
    assert '# examples = ["worker-done-review"]' in config
    assert "[hooks.done.after]" in config
    assert "After `loom agent done`, make sure the review handoff names tests, output, and blockers." in config


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

    env = {"LOOM_WORKER_ID": "x7k2"}

    thread_result = runner.invoke(
        app, ["agent", "new-thread", "--name", "backend", "--priority", "90", "--role", "manager"], env=env
    )
    assert thread_result.exit_code == 0, thread_result.output
    assert "CREATED thread backend" in thread_result.output
    assert "priority : 90" in thread_result.output
    assert "id       :" not in thread_result.output

    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "实现 token 刷新接口",
            "--acceptance",
            "- [ ] POST /auth/refresh 返回新 access token",
            "--created-from",
            "RQ-001,RQ-002",
            "--role",
            "manager",
        ],
        env=env,
    )
    assert task_result.exit_code == 0, task_result.output
    assert "CREATED task" in task_result.output
    assert "status : scheduled" in task_result.output
    # extract task_id from output: "CREATED task backend-001"
    task_id = task_result.output.splitlines()[0].split()[-1]
    assert task_id == "backend-001"

    task_file = isolated_project / ".loom" / "threads" / "backend" / "001.md"
    task_content = task_file.read_text(encoding="utf-8")
    assert "id: backend-001" in task_content
    assert "status: scheduled" in task_content
    assert "created_from:" in task_content
    assert "- RQ-001" in task_content
    assert "## 背景" in task_content
    assert "## 实现方向" in task_content
    assert "补充任务背景。" not in task_content
    assert "补充实现方向。" not in task_content

    next_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert next_result.exit_code == 0, next_result.output
    assert "ACTION  task" in next_result.output
    assert task_id in next_result.output
    assert "none:" not in next_result.output

    done_result = runner.invoke(app, ["agent", "done", task_id, "--output", "./output/demo"], env=env)
    assert done_result.exit_code == 0, done_result.output
    assert f"DONE task {task_id}" in done_result.output
    assert "reviewing" in done_result.output

    review_result = runner.invoke(app, ["review"])
    assert review_result.exit_code == 0, review_result.output
    assert task_id in review_result.output
    assert "output: ./output/demo" in review_result.output
    assert "loom review accept <id>" in review_result.output

    accept_result = runner.invoke(app, ["review", "accept", task_id])
    assert accept_result.exit_code == 0, accept_result.output

    status_result = runner.invoke(app, ["agent", "status"])
    assert status_result.exit_code == 0, status_result.output
    assert "PROJECT STATUS" in status_result.output
    assert "done" in status_result.output


def test_status_and_review_show_design_only_capability_lines(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0

    design_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Design auth flow",
            "--kind",
            "design",
            "--acceptance",
            "- [ ] design captured",
            "--role",
            "manager",
        ],
    )
    assert design_result.exit_code == 0, design_result.output
    design_task_id = design_result.output.splitlines()[0].split()[-1]

    implementation_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Implement auth flow",
            "--acceptance",
            "- [ ] auth shipped",
            "--after",
            design_task_id,
            "--role",
            "manager",
        ],
    )
    assert implementation_result.exit_code == 0, implementation_result.output
    implementation_task_id = implementation_result.output.splitlines()[0].split()[-1]

    claim_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert claim_result.exit_code == 0, claim_result.output
    assert design_task_id in claim_result.output
    assert "kind       : design" in claim_result.output

    done_result = runner.invoke(app, ["agent", "done", design_task_id, "--output", "design/auth-flow.md"], env=env)
    assert done_result.exit_code == 0, done_result.output

    review_result = runner.invoke(app, ["review"])
    assert review_result.exit_code == 0, review_result.output
    assert f"{design_task_id}: Design auth flow" in review_result.output
    assert "kind: design" in review_result.output

    accept_result = runner.invoke(app, ["review", "accept", design_task_id])
    assert accept_result.exit_code == 0, accept_result.output

    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0, status_result.output
    assert "Capabilities:" in status_result.output
    assert "backend: design-only" in status_result.output
    assert f"latest {design_task_id} [design done]" in status_result.output
    assert f"implementation follow-up: {implementation_task_id} [scheduled]" in status_result.output

    agent_status_result = runner.invoke(app, ["agent", "status"])
    assert agent_status_result.exit_code == 0, agent_status_result.output
    assert "CAPABILITIES" in agent_status_result.output
    assert "design-only" in agent_status_result.output
    assert f"latest:{design_task_id} [design done]" in agent_status_result.output
    assert f"implementation follow-up: {implementation_task_id} [scheduled]" in agent_status_result.output

    metadata = yaml.safe_load(
        (isolated_project / ".loom" / "threads" / "backend" / "001.md").read_text(encoding="utf-8").split("---", 2)[1]
    )
    task = Task.model_validate(metadata | {"body": ""})
    assert task.kind == TaskKind.DESIGN


def test_agent_worktree_commands_require_worker_id(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "worktree", "list"])
    assert result.exit_code == 1
    assert "ERROR [missing_worker_id]" in result.output


def test_agent_worktree_cli_flow_is_worker_local(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    env = {"LOOM_WORKER_ID": "aaap"}
    other_env = {"LOOM_WORKER_ID": "aaar"}
    checkout = isolated_project / ".loom" / "agents" / "workers" / "aaap" / "worktrees" / "feature-a"

    add_result = runner.invoke(
        app,
        [
            "agent",
            "worktree",
            "add",
            "feature-a",
            "--branch",
            "feat/worktree-a",
        ],
        env=env,
    )
    assert add_result.exit_code == 0, add_result.output
    assert "REGISTERED worktree feature-a" in add_result.output
    assert f"path   : {checkout}" in add_result.output

    attach_result = runner.invoke(
        app,
        [
            "agent",
            "worktree",
            "attach",
            "feature-a",
            "--thread",
            "worktree-flow",
        ],
        env=env,
    )
    assert attach_result.exit_code == 0, attach_result.output
    assert "ATTACHED worktree feature-a" in attach_result.output
    assert "worker : aaap" in attach_result.output
    assert "thread : worktree-flow" in attach_result.output

    list_result = runner.invoke(app, ["agent", "worktree", "list"], env=env)
    assert list_result.exit_code == 0, list_result.output
    assert "feature-a  active" in list_result.output
    assert f"path    : {checkout}" in list_result.output
    assert "branch  : feat/worktree-a" in list_result.output
    assert "worker  : aaap" in list_result.output
    assert "thread  : worktree-flow" in list_result.output

    other_list_result = runner.invoke(app, ["agent", "worktree", "list"], env=other_env)
    assert other_list_result.exit_code == 0, other_list_result.output
    assert "No worker-local worktrees." in other_list_result.output

    remove_blocked = runner.invoke(app, ["agent", "worktree", "remove", "feature-a"], env=env)
    assert remove_blocked.exit_code == 1
    assert "clear it first" in remove_blocked.output

    clear_result = runner.invoke(app, ["agent", "worktree", "attach", "feature-a", "--clear"], env=env)
    assert clear_result.exit_code == 0, clear_result.output
    assert "CLEARED worktree feature-a" in clear_result.output

    remove_result = runner.invoke(app, ["agent", "worktree", "remove", "feature-a"], env=env)
    assert remove_result.exit_code == 0, remove_result.output
    assert "REMOVED worktree feature-a" in remove_result.output
    assert "record removed only" in remove_result.output

    legacy_surface = runner.invoke(app, ["worktree", "list"])
    assert legacy_surface.exit_code != 0


def test_worker_secondary_checkout_surfaces_worktree_context(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    env = {"LOOM_WORKER_ID": "aaap", "LOOM_DIR": str(isolated_project / ".loom")}
    checkout = isolated_project / ".loom" / "agents" / "workers" / "aaap" / "worktrees" / "feature-a"

    add_result = runner.invoke(
        app,
        ["agent", "worktree", "add", "feature-a", "--branch", "feat/worktree-a"],
        env={"LOOM_WORKER_ID": "aaap"},
    )
    assert add_result.exit_code == 0, add_result.output
    attach_result = runner.invoke(
        app,
        ["agent", "worktree", "attach", "feature-a", "--thread", "worktree-flow"],
        env={"LOOM_WORKER_ID": "aaap"},
    )
    assert attach_result.exit_code == 0, attach_result.output

    monkeypatch.chdir(checkout)

    whoami_result = runner.invoke(app, ["agent", "whoami"], env=env)
    assert whoami_result.exit_code == 0, whoami_result.output
    assert "worktree      : feature-a" in whoami_result.output
    assert f"checkout root : {checkout}" in whoami_result.output
    assert "thread        : worktree-flow" in whoami_result.output

    start_result = runner.invoke(app, ["agent", "start", "--role", "worker"], env=env)
    assert start_result.exit_code == 0, start_result.output
    assert "WORKTREE CONTEXT" in start_result.output
    assert f"checkout root : {checkout}" in start_result.output
    assert "worktree      : feature-a" in start_result.output

    status_result = runner.invoke(app, ["agent", "status"], env=env)
    assert status_result.exit_code == 0, status_result.output
    assert "CURRENT WORKER CONTEXT" in status_result.output
    assert "worktree      : feature-a" in status_result.output


def test_agent_next_uses_secondary_checkout_config_for_worker_hooks(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    env = {"LOOM_WORKER_ID": "aaap", "LOOM_DIR": str(isolated_project / ".loom")}
    checkout = isolated_project / ".loom" / "agents" / "workers" / "aaap" / "worktrees" / "feature-a"

    add_result = runner.invoke(
        app,
        ["agent", "worktree", "add", "feature-a", "--branch", "feat/worktree-a"],
        env={"LOOM_WORKER_ID": "aaap"},
    )
    assert add_result.exit_code == 0, add_result.output

    primary_config = isolated_project / "loom.toml"
    updated_primary = primary_config.read_text(encoding="utf-8").replace(
        '# worker = "Run tests before `loom agent done`."\n',
        'worker = "primary worker hook"\n',
    )
    primary_config.write_text(updated_primary, encoding="utf-8")
    (checkout / "loom.toml").write_text(
        updated_primary.replace("primary worker hook", "secondary worker hook"),
        encoding="utf-8",
    )

    monkeypatch.chdir(checkout)

    result = runner.invoke(app, ["agent", "next"], env=env)

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert "secondary worker hook" in result.output
    assert "primary worker hook" not in result.output


def test_agent_done_pauses_incomplete_work_with_decision_request(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"], env=env).exit_code == 0

    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Incomplete task",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ],
        env=env,
    )
    assert task_result.exit_code == 0, task_result.output
    task_id = task_result.output.splitlines()[0].split()[-1]

    claim_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env)
    assert claim_result.exit_code == 0, claim_result.output
    assert task_id in claim_result.output

    done_result = runner.invoke(
        app,
        ["agent", "done", task_id, "--output", "proposal-only summary\nTODO: finish tests"],
        env=env,
    )
    assert done_result.exit_code == 0, done_result.output
    assert f"DONE task {task_id}" in done_result.output
    assert "paused" in done_result.output
    assert "blocked: TODOs, proposal-only output" in done_result.output
    assert "Waiting for human decision" in done_result.output

    review_result = runner.invoke(app, ["review"])
    assert review_result.exit_code == 0, review_result.output
    assert "No tasks in reviewing status." in review_result.output

    task_content = (isolated_project / ".loom" / "threads" / "backend" / "001.md").read_text(encoding="utf-8")
    assert "status: paused" in task_content
    assert "output: 'proposal-only summary" in task_content
    assert "This task still looks incomplete (TODOs, proposal-only output)." in task_content
    assert "id: resume" in task_content
    assert "id: split" in task_content


def test_pause_decide_and_queue_listing(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"], env=env).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "OAuth provider selection",
            "--acceptance",
            "- [ ] 完成 provider 选择",
            "--role",
            "manager",
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

    default_result = runner.invoke(app, ["--plain"])
    assert default_result.exit_code == 0, default_result.output
    assert f"[paused] {task_id}" in default_result.output

    decide_result = runner.invoke(app, ["review", "decide", task_id, "A"])
    assert decide_result.exit_code == 0, decide_result.output
    task_content = (isolated_project / ".loom" / "threads" / "backend" / "001.md").read_text(encoding="utf-8")
    assert "decided: A" in task_content
    assert "status: scheduled" in task_content


def test_pause_command_requires_question_flag(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"], env=env).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Need decision",
            "--acceptance",
            "- [ ] done",
            "--role",
            "manager",
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
    env = {"LOOM_WORKER_ID": "planner1"}
    assert runner.invoke(app, ["inbox", "add", "支持 Google OAuth 登录"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next"], env=env)
    assert result.exit_code == 0, result.output

    assert "ACTION  plan" in result.output
    assert "RQ-001" in result.output
    assert "Worker next steps:" in result.output
    assert "notify the manager or director immediately" in result.output
    assert "After planning clears, run `loom agent next` again." in result.output
    assert "none:" not in result.output

    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")
    assert "status: pending" in inbox_content


@pytest.mark.parametrize(
    ("role", "args", "env", "expected"),
    [
        (
            "worker",
            ["agent", "next", "--plan-limit", "0"],
            {"LOOM_WORKER_ID": "x7k2"},
            "Worker reminder: run the focused test slice before `loom agent done`.",
        ),
        (
            "manager",
            ["agent", "next", "--plan-limit", "0", "--role", "manager"],
            None,
            "Manager reminder: keep handoffs mailbox-first.",
        ),
        (
            "director",
            ["agent", "next", "--plan-limit", "0", "--role", "director"],
            None,
            "Director reminder: wake only the roles needed for the next step.",
        ),
        (
            "reviewer",
            ["agent", "next", "--plan-limit", "0", "--role", "reviewer"],
            None,
            "Reviewer reminder: compare the diff against each acceptance checkbox.",
        ),
    ],
)
def test_next_appends_role_specific_soft_hooks(runner, isolated_project, role, args, env, expected):
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0
    (isolated_project / "loom.toml").write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\n"
            "inbox_plan_batch = 10\n"
            "task_batch = 1\n"
            "next_wait_seconds = 0.0\n"
            "next_retries = 0\n"
            'executor_command = ""\n'
            "offline_after_minutes = 30\n\n"
            "[threads]\n"
            "default_priority = 50\n\n"
            "[hooks.next]\n"
            'all = "Shared reminder: soft hooks stay advisory."\n'
            'manager = "Manager reminder: keep handoffs mailbox-first."\n'
            'worker = "Worker reminder: run the focused test slice before `loom agent done`."\n'
            'director = "Director reminder: wake only the roles needed for the next step."\n'
            'reviewer = "Reviewer reminder: compare the diff against each acceptance checkbox."\n'
            'examples = ["commit-message-policy"]\n'
        ),
        encoding="utf-8",
    )
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Claimed task",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ],
    )
    assert task_result.exit_code == 0, task_result.output

    result = runner.invoke(app, args, env=env)

    assert result.exit_code == 0, result.output
    if role == "reviewer":
        assert "ACTION  idle" in result.output
    else:
        assert "ACTION  task" in result.output
    assert "SOFT HOOKS" in result.output
    assert "Shared reminder: soft hooks stay advisory." in result.output
    assert expected in result.output
    if role == "worker":
        assert "Built-in example: commit-message-policy" in result.output
        assert "commit-msg hook format" in result.output
    else:
        assert "Built-in example: commit-message-policy" not in result.output


def test_next_appends_soft_hooks_to_plan_output(runner, isolated_project):
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0
    (isolated_project / "loom.toml").write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\n"
            "inbox_plan_batch = 10\n"
            "task_batch = 1\n"
            "next_wait_seconds = 0.0\n"
            "next_retries = 0\n"
            'executor_command = ""\n'
            "offline_after_minutes = 30\n\n"
            "[threads]\n"
            "default_priority = 50\n\n"
            "[hooks.next]\n"
            'worker = "Escalate the planning blocker immediately."\n'
        ),
        encoding="utf-8",
    )
    assert runner.invoke(app, ["inbox", "add", "Need planning"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next"], env={"LOOM_WORKER_ID": "planner1"})

    assert result.exit_code == 0, result.output
    assert "ACTION  plan" in result.output
    assert "SOFT HOOKS" in result.output
    assert "Escalate the planning blocker immediately." in result.output


def test_next_appends_soft_hooks_to_idle_output(runner, isolated_project):
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0
    (isolated_project / "loom.toml").write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\n"
            "inbox_plan_batch = 10\n"
            "task_batch = 1\n"
            "next_wait_seconds = 0.0\n"
            "next_retries = 0\n"
            'executor_command = ""\n'
            "offline_after_minutes = 30\n\n"
            "[threads]\n"
            "default_priority = 50\n\n"
            "[hooks.next]\n"
            'director = "Idle reminder: re-check queue state before waking more workers."\n'
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["agent", "next", "--role", "director"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert "SOFT HOOKS" in result.output
    assert "Idle reminder: re-check queue state before waking more workers." in result.output


def test_done_appends_soft_hooks_before_and_after_result(runner, isolated_project):
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0
    (isolated_project / "loom.toml").write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\n"
            "inbox_plan_batch = 10\n"
            "task_batch = 1\n"
            "next_wait_seconds = 0.0\n"
            "next_retries = 0\n"
            'executor_command = ""\n'
            "offline_after_minutes = 30\n\n"
            "[threads]\n"
            "default_priority = 50\n\n"
            "[hooks.next]\n"
            'all = "Shared reminder: soft hooks stay advisory."\n\n'
            "[hooks.done.before]\n"
            'all = "Before done: treat this as an advisory checklist, not a gate."\n'
            'worker = "Before done: refresh your checkpoint and re-scan the diff for surprises."\n\n'
            "[hooks.done.after]\n"
            'worker = "After done: double-check that the handoff names the output, tests, and any blocker state."\n'
        ),
        encoding="utf-8",
    )
    task_id = _create_assigned_task(runner)

    result = runner.invoke(app, ["agent", "done", task_id, "--output", "./output/demo"], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 0, result.output
    assert "SOFT HOOKS  done/before" in result.output
    assert "Before done: treat this as an advisory checklist, not a gate." in result.output
    assert "Before done: refresh your checkpoint and re-scan the diff for surprises." in result.output
    assert f"DONE task {task_id}" in result.output
    assert "SOFT HOOKS  done/after" in result.output
    assert "After done: double-check that the handoff names the output, tests, and any blocker state." in result.output
    assert result.output.index("SOFT HOOKS  done/before") < result.output.index(f"DONE task {task_id}")
    assert result.output.index(f"DONE task {task_id}") < result.output.index("SOFT HOOKS  done/after")


def test_done_appends_built_in_worker_done_review_example(runner, isolated_project):
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0
    (isolated_project / "loom.toml").write_text(
        (
            '[project]\nname = "demo"\n\n'
            "[agent]\n"
            "inbox_plan_batch = 10\n"
            "task_batch = 1\n"
            "next_wait_seconds = 0.0\n"
            "next_retries = 0\n"
            'executor_command = ""\n'
            "offline_after_minutes = 30\n\n"
            "[threads]\n"
            "default_priority = 50\n\n"
            "[hooks.next]\n\n"
            "[hooks.done.before]\n"
            'examples = ["worker-done-review"]\n'
        ),
        encoding="utf-8",
    )
    task_id = _create_assigned_task(runner)

    result = runner.invoke(app, ["agent", "done", task_id], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 0, result.output
    assert "SOFT HOOKS  done/before" in result.output
    assert "Advisory only; these reminders do not block execution." in result.output
    assert "Built-in example: worker-done-review" in result.output
    assert "Inspect the diff before finishing." in result.output
    assert "Did this change grow the code? If so, does that growth earn its keep?" in result.output
    assert "Can you simplify the result further without losing value?" in result.output
    assert "Refresh your checkpoint summary and confirm the focused tests/validations you ran." in result.output
    assert f"DONE task {task_id}" in result.output


def test_done_omits_soft_hooks_when_unconfigured(runner, isolated_project):
    assert runner.invoke(app, ["init", "--project", "demo"]).exit_code == 0
    task_id = _create_assigned_task(runner)

    result = runner.invoke(app, ["agent", "done", task_id, "--output", "./output/demo"], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 0, result.output
    assert f"DONE task {task_id}" in result.output
    assert "SOFT HOOKS" not in result.output


def test_default_queue_interactive_flow_handles_paused_and_reviewing_only(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert (
        runner.invoke(
            app, ["agent", "new-thread", "--name", "backend", "--priority", "90", "--role", "manager"], env=env
        ).exit_code
        == 0
    )

    paused_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Paused task",
            "--acceptance",
            "- [ ] paused",
            "--role",
            "manager",
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
            "backend",
            "--title",
            "Review task",
            "--acceptance",
            "- [ ] review",
            "--role",
            "manager",
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
        ["--plain"],
        input="?\nd\nA\n?\nr\nNeed fixes\n",
    )

    assert result.exit_code == 0, result.output
    assert "Paused task action [d / S / o / ?]" in result.output
    assert "Reviewing task action [a / r / S / o / ?]" in result.output
    assert "Queue summary:" in result.output
    assert "decided: 1" in result.output
    assert "rejected: 1" in result.output

    paused_content = (isolated_project / ".loom" / "threads" / "backend" / "001.md").read_text(encoding="utf-8")
    reviewing_content = (isolated_project / ".loom" / "threads" / "backend" / "002.md").read_text(encoding="utf-8")
    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")

    assert "decided: A" in paused_content
    assert "status: scheduled" in paused_content
    assert "status: scheduled" in reviewing_content
    assert "rejection_note: Need fixes" in reviewing_content
    assert "status: pending" in inbox_content


def test_default_queue_ignores_pending_inbox_items(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Add OAuth login"]).exit_code == 0

    result = runner.invoke(app, ["--plain"])

    assert result.exit_code == 0, result.output
    assert "No pending approvals." in result.output


def test_default_entry_prefers_tui(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    called: list[str] = []

    def fake_run_tui(loom):
        called.append(str(loom))

    monkeypatch.setattr("loom.tui.run_tui", fake_run_tui)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert called


def test_plain_flag_keeps_prompt_queue(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    def fail_run_tui(_loom):
        raise AssertionError("run_tui should not be called for --plain")

    monkeypatch.setattr("loom.tui.run_tui", fail_run_tui)

    result = runner.invoke(app, ["--plain"])

    assert result.exit_code == 0, result.output
    assert "No pending approvals." in result.output


def test_tui_help_mentions_approval_queue(runner, isolated_project):
    result = runner.invoke(app, ["tui", "--help"])

    assert result.exit_code == 0, result.output
    assert "approval" in result.output.lower()
    assert "tui" in result.output.lower()
    assert "new inbox requirement" in result.output.lower()


def test_inbox_add_rejects_empty_description(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["inbox", "add", "   "])

    assert result.exit_code == 1, result.output
    assert "description must not be empty" in result.output


def test_status_migrates_legacy_thread_ids_and_rewrites_references(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    legacy_thread_dir = isolated_project / ".loom" / "threads" / "AA"
    legacy_thread_dir.mkdir(parents=True)
    (legacy_thread_dir / "_thread.md").write_text(
        ("---\nid: AA\nname: backend\npriority: 50\ncreated: '2026-03-18'\n---\n\n## 目标\n\nlegacy\n"),
        encoding="utf-8",
    )
    (legacy_thread_dir / "AA-001-demo.md").write_text(
        (
            "---\n"
            "id: AA-001-demo\n"
            "thread: AA\n"
            "seq: 1\n"
            "title: Demo\n"
            "status: scheduled\n"
            "priority: 50\n"
            "depends_on: []\n"
            "created_from: []\n"
            "created: '2026-03-18'\n"
            "acceptance: '- [ ] ready'\n"
            "---\n\n"
            "## 背景\n\nlegacy\n\n## 实现方向\n\nlegacy\n"
        ),
        encoding="utf-8",
    )
    (legacy_thread_dir / "AA-002-follow-up.md").write_text(
        (
            "---\n"
            "id: AA-002-follow-up\n"
            "thread: AA\n"
            "seq: 2\n"
            "title: Follow up\n"
            "status: scheduled\n"
            "priority: 50\n"
            "depends_on:\n"
            "  - AA-001-demo\n"
            "created_from: []\n"
            "created: '2026-03-18'\n"
            "acceptance: '- [ ] ready'\n"
            "---\n\n"
            "## 背景\n\nlegacy\n\n## 实现方向\n\nlegacy\n"
        ),
        encoding="utf-8",
    )
    (isolated_project / ".loom" / "inbox" / "RQ-001.md").write_text(
        (
            "---\n"
            "id: RQ-001\n"
            "created: '2026-03-18'\n"
            "status: planned\n"
            "planned_to:\n"
            "  - AA-001-demo\n"
            "---\n\n"
            "Legacy request\n"
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    backend_dir = isolated_project / ".loom" / "threads" / "backend"
    assert backend_dir.is_dir()
    assert not legacy_thread_dir.exists()
    assert (backend_dir / "_thread.md").exists()
    thread_content = (backend_dir / "_thread.md").read_text(encoding="utf-8")
    assert "name: backend" in thread_content
    assert "id:" not in thread_content

    first_task = backend_dir / "001.md"
    second_task = backend_dir / "002.md"
    assert first_task.exists()
    assert second_task.exists()
    assert "id: backend-001" in first_task.read_text(encoding="utf-8")
    assert "thread: backend" in first_task.read_text(encoding="utf-8")
    assert "depends_on:" in second_task.read_text(encoding="utf-8")
    assert "- backend-001" in second_task.read_text(encoding="utf-8")
    assert "- backend-001" in (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")


def test_status_migrates_legacy_task_ids_from_sequence_only_thread_files(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    thread_dir = isolated_project / ".loom" / "threads" / "backend"
    thread_dir.mkdir(parents=True)
    (thread_dir / "_thread.md").write_text(
        ("---\nname: backend\npriority: 50\ncreated: '2026-03-18'\n---\n\n## 目标\n\nlegacy\n"),
        encoding="utf-8",
    )
    (thread_dir / "001.md").write_text(
        (
            "---\n"
            "id: thaa-001\n"
            "thread: backend\n"
            "seq: 1\n"
            "title: Demo\n"
            "status: scheduled\n"
            "priority: 50\n"
            "depends_on: []\n"
            "created_from: []\n"
            "created: '2026-03-18'\n"
            "acceptance: '- [ ] ready'\n"
            "---\n\n"
            "## 背景\n\nlegacy\n\n## 实现方向\n\nlegacy\n"
        ),
        encoding="utf-8",
    )
    (thread_dir / "002.md").write_text(
        (
            "---\n"
            "id: thaa-002\n"
            "thread: backend\n"
            "seq: 2\n"
            "title: Follow up\n"
            "status: scheduled\n"
            "priority: 50\n"
            "depends_on:\n"
            "  - thaa-001\n"
            "created_from: []\n"
            "created: '2026-03-18'\n"
            "acceptance: '- [ ] ready'\n"
            "---\n\n"
            "## 背景\n\nlegacy\n\n## 实现方向\n\nlegacy\n"
        ),
        encoding="utf-8",
    )
    (isolated_project / ".loom" / "inbox" / "RQ-001.md").write_text(
        ("---\nid: RQ-001\ncreated: '2026-03-18'\nstatus: planned\nplanned_to:\n  - thaa-002\n---\n\nLegacy request\n"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    assert "task 'thaa-001' not found" not in result.output
    assert "id: backend-001" in (thread_dir / "001.md").read_text(encoding="utf-8")
    second_task = (thread_dir / "002.md").read_text(encoding="utf-8")
    assert "id: backend-002" in second_task
    assert "- backend-001" in second_task
    assert "- backend-002" in (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")


def test_default_queue_does_not_repeat_processed_items_on_next_run(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"], env=env).exit_code == 0

    paused_task = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Paused task",
            "--acceptance",
            "- [ ] paused",
            "--role",
            "manager",
        ],
        env=env,
    )
    paused_id = paused_task.output.splitlines()[0].split()[-1]

    reviewing_task = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Review task",
            "--acceptance",
            "- [ ] review",
            "--role",
            "manager",
        ],
        env=env,
    )
    reviewing_id = reviewing_task.output.splitlines()[0].split()[-1]

    assert runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env).exit_code == 0
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
    assert runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=env).exit_code == 0
    assert runner.invoke(app, ["agent", "done", reviewing_id], env=env).exit_code == 0

    first_run = runner.invoke(app, ["--plain"], input="d\nA\na\n")
    assert first_run.exit_code == 0, first_run.output
    assert "Queue summary:" in first_run.output
    assert "decided: 1" in first_run.output
    assert "accepted: 1" in first_run.output

    second_run = runner.invoke(app, ["--plain"])
    assert second_run.exit_code == 0, second_run.output
    assert "No pending approvals." in second_run.output
    assert paused_id not in second_run.output
    assert reviewing_id not in second_run.output


def test_inbox_command_without_subcommand_runs_interactive_planning(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Add OAuth login"]).exit_code == 0

    result = runner.invoke(app, ["inbox"], input="\n")

    assert result.exit_code == 0, result.output
    assert "Inbox item action [P / s / o / ?]" in result.output
    assert "[inbox] RQ-001:" in result.output
    assert "Resolved RQ-001 ->" in result.output
    assert "Inbox planning summary:" in result.output
    assert "planned: 1" in result.output

    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")
    assert "status: done" in inbox_content
    assert "resolved_as: task" in inbox_content
    assert "resolved_to:" in inbox_content


def test_inbox_command_without_subcommand_shows_empty_message(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["inbox"])

    assert result.exit_code == 0, result.output
    assert "No pending inbox items." in result.output


def test_request_add_and_list_preserve_inbox_compatibility(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    add_result = runner.invoke(app, ["request", "add", "Add OAuth login"])
    assert add_result.exit_code == 0, add_result.output
    assert ".loom/requests/RQ-001.md" in add_result.output
    assert (isolated_project / ".loom" / "requests" / "RQ-001.md").exists()
    assert (isolated_project / ".loom" / "inbox" / "RQ-001.md").exists()

    pending_result = runner.invoke(app, ["request", "ls", "--pending"])
    assert pending_result.exit_code == 0, pending_result.output
    assert "RQ-001  pending" in pending_result.output

    alias_result = runner.invoke(app, ["inbox", "ls", "--pending"])
    assert alias_result.exit_code == 0, alias_result.output
    assert "RQ-001  pending" in alias_result.output


def test_request_list_shows_resolution_visibility(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    request_dir = isolated_project / ".loom" / "requests"
    (request_dir / "RQ-001.md").write_text(
        (
            "---\n"
            "id: RQ-001\n"
            "created: '2026-03-18'\n"
            "status: done\n"
            "resolved_as: task\n"
            "resolved_to:\n"
            "  - backend-001\n"
            "---\n\n"
            "Add OAuth login\n"
        ),
        encoding="utf-8",
    )
    (request_dir / "RQ-002.md").write_text(
        (
            "---\n"
            "id: RQ-002\n"
            "created: '2026-03-18'\n"
            "status: done\n"
            "resolved_as: routine\n"
            "resolved_to:\n"
            "  - scan-github-issues\n"
            "---\n\n"
            "Scan GitHub issues\n"
        ),
        encoding="utf-8",
    )
    (request_dir / "RQ-003.md").write_text(
        (
            "---\n"
            "id: RQ-003\n"
            "created: '2026-03-18'\n"
            "status: done\n"
            "resolved_as: merged\n"
            "resolved_to:\n"
            "  - backend-001\n"
            "resolution_note: Covered by the active auth task.\n"
            "---\n\n"
            "Also add auth logging\n"
        ),
        encoding="utf-8",
    )
    (request_dir / "RQ-004.md").write_text(
        (
            "---\n"
            "id: RQ-004\n"
            "created: '2026-03-18'\n"
            "status: done\n"
            "resolved_as: rejected\n"
            "resolved_to: []\n"
            "resolution_note: Out of scope for this repo.\n"
            "---\n\n"
            "Replace Python with Rust\n"
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["request", "ls"])

    assert result.exit_code == 0, result.output
    assert "RQ-001  done" in result.output
    assert "resolved_as   : task" in result.output
    assert "resolved_to   : backend-001" in result.output
    assert "resolved_as   : routine" in result.output
    assert "resolved_to   : scan-github-issues" in result.output
    assert "resolved_as   : merged" in result.output
    assert "Covered by the active auth task." in result.output
    assert "resolved_as   : rejected" in result.output
    assert "Out of scope for this repo." in result.output


def test_scheduler_respects_dependencies_and_thread_priority(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert (
        runner.invoke(
            app, ["agent", "new-thread", "--name", "backend", "--priority", "80", "--role", "manager"], env=env
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["agent", "new-thread", "--name", "frontend", "--priority", "95", "--role", "manager"], env=env
        ).exit_code
        == 0
    )

    backend_task = (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "backend",
                "--title",
                "backend base",
                "--acceptance",
                "- [ ] base ready",
                "--role",
                "manager",
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
                "frontend",
                "--title",
                "frontend shell",
                "--acceptance",
                "- [ ] shell ready",
                "--role",
                "manager",
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
                "backend",
                "--title",
                "backend follow-up",
                "--acceptance",
                "- [ ] follow-up ready",
                "--depends-on",
                frontend_task,
                "--role",
                "manager",
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
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["inbox", "add", "Add OAuth login"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Add audit log"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next"], env=env)

    assert result.exit_code == 0, result.output
    assert "ACTION  plan" in result.output
    assert "RQ-001" in result.output
    assert "RQ-002" in result.output
    assert "Worker next steps:" in result.output
    assert "notify the manager or director immediately" in result.output
    assert "After planning clears, run `loom agent next` again." in result.output
    assert "none:" not in result.output
    inbox_content = (isolated_project / ".loom" / "inbox" / "RQ-001.md").read_text(encoding="utf-8")
    assert "status: pending" in inbox_content


def test_agent_next_respects_configured_inbox_plan_batch(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
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
    env = {"LOOM_WORKER_ID": "x7k2"}
    config_path = isolated_project / "loom.toml"
    config_path.write_text(
        (
            '[project]\nname = "demo"\n\n'
            '[agent]\ninbox_plan_batch = 10\ntask_batch = 2\nexecutor_command = ""\noffline_after_minutes = 30\n\n'
            "[threads]\ndefault_priority = 50\n"
        ),
        encoding="utf-8",
    )
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"], env=env).exit_code == 0
    first_task = (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "backend",
                "--title",
                "Task one",
                "--acceptance",
                "- [ ] one",
                "--role",
                "manager",
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
                "backend",
                "--title",
                "Task two",
                "--acceptance",
                "- [ ] two",
                "--role",
                "manager",
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

    result = runner.invoke(app, ["agent", "next", "--role", "manager"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert called["sleep"] == 0
    assert "none:" not in result.output


def test_agent_next_idle_worker_suggests_proactive_claims(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next"], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert "Worker next steps:" in result.output
    assert "proactively ask to claim a thread or task" in result.output
    assert "loom agent propose manager '<thread/task handoff>' --ref <thread-or-task-id>" in result.output


def test_agent_next_wait_retries_cli_overrides(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("loom.agent.time.sleep", fake_sleep)

    result = runner.invoke(
        app,
        ["agent", "next", "--wait-seconds", "0.25", "--retries", "2", "--role", "manager"],
    )

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert sleeps == [0.25, 0.25]
    assert "none:" not in result.output


def test_agent_next_wait_retries_stop_when_work_appears(runner, isolated_project, monkeypatch):
    assert runner.invoke(app, ["init"]).exit_code == 0

    task = Task(
        id="backend-001-late-work",
        thread="backend",
        seq=1,
        title="Late work",
        status=TaskStatus.SCHEDULED,
        acceptance="- [ ] done",
    )
    attempts = {"tasks": 0}
    sleeps: list[float] = []

    def fake_pending_inbox_items(*_args, **_kwargs):
        return []

    def fake_get_next_tasks(*_args, **_kwargs):
        attempts["tasks"] += 1
        if attempts["tasks"] < 3:
            return []
        return [task]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("loom.agent.get_pending_inbox_items", fake_pending_inbox_items)
    monkeypatch.setattr("loom.agent.get_next_tasks", fake_get_next_tasks)
    monkeypatch.setattr("loom.agent.time.sleep", fake_sleep)

    result = runner.invoke(
        app,
        ["agent", "next", "--wait-seconds", "0.25", "--retries", "5", "--role", "manager"],
    )

    assert result.exit_code == 0, result.output
    assert "ACTION  task" in result.output
    assert "backend-001-late-work" in result.output
    assert attempts["tasks"] == 3
    assert sleeps == [0.25, 0.25]
    assert "ACTION  idle" not in result.output
    assert "none:" not in result.output


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

    result = runner.invoke(app, ["agent", "next", "--role", "manager"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert sleeps == [0.1, 0.1, 0.1]
    assert "none:" not in result.output


def test_agent_next_wait_retries_tty_feedback_uses_stderr(runner, isolated_project, monkeypatch, capsys):
    assert runner.invoke(app, ["init"]).exit_code == 0

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("loom.agent.time.sleep", fake_sleep)
    monkeypatch.setattr("loom.agent._interactive_wait_feedback_enabled", lambda: True)
    from loom import agent as agent_module

    agent_module.next_task(plan_limit=0, task_limit=0, thread="", wait_seconds=0.25, retries=2, role=AgentRole.MANAGER)
    captured = capsys.readouterr()

    assert "ACTION  idle" in captured.out
    assert "WAITING  attempt 1/3  retries:2  wait_seconds:0.25  remaining:2" in captured.err
    assert "WAITING  attempt 2/3  retries:2  wait_seconds:0.25  remaining:1" in captured.err
    assert "WAITING" not in captured.out
    assert sleeps == [0.25, 0.25]


def test_log_shows_recorded_events(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"], env=env).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Record event",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
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
    assert "thread.created thread:backend" in result.output
    assert f"task.created task:{task_id}" in result.output
    assert f"task.transitioned task:{task_id}" in result.output


def test_manage_returns_loop_prompt(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["manage"])

    assert result.exit_code == 0, result.output
    assert "DO THIS NOW" in result.output
    assert "RIGHT NOW" not in result.output
    assert "CURRENT STATE" in result.output
    assert "loom agent next" in result.output
    assert "loom agent done <task-id> --output <path-or-url> --role manager" in result.output
    assert "loom agent pause <task-id> --question '<question>' --role manager" in result.output
    assert "ESSENTIAL COMMANDS" in result.output
    assert "COMMAND REFERENCE" not in result.output
    assert "WORKSPACE" not in result.output
    assert "loom agent checkpoint" not in result.output
    assert "Global mode is active (-g)." not in result.output
    assert "Ask the director or host system to create or wake a worker runtime." in result.output
    assert "loom spawn [--threads <backend,frontend>]" not in result.output
    assert "loom agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager" in result.output
    assert "loom agent send <agent-id> '<extra context>' --ref <task-id> --role manager" in result.output


def test_manage_with_executor_command_mentions_spawn(runner, isolated_project):
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

    result = runner.invoke(app, ["manage"])

    assert result.exit_code == 0, result.output
    assert "loom spawn [--threads <backend,frontend>]" in result.output
    assert "configured launch command" in result.output


def test_manage_global_mode_mentions_global_guidance(runner, isolated_project, monkeypatch):
    home = isolated_project / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert runner.invoke(app, ["init", "-g", "--project", "global-demo"]).exit_code == 0

    result = runner.invoke(app, ["-g", "manage"])

    assert result.exit_code == 0, result.output
    assert "Global mode is active (-g)." in result.output


def test_manage_rejects_worker(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["manage"], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 1
    assert "worker_not_allowed" in result.output


def test_manage_priority_lists_threads_and_tasks(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["agent", "new-thread", "--name", "backend", "--priority", "80", "--role", "manager"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["agent", "new-thread", "--name", "frontend", "--priority", "95", "--role", "manager"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "backend",
                "--title",
                "Backend base",
                "--priority",
                "40",
                "--acceptance",
                "- [ ] ready",
                "--role",
                "manager",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "frontend",
                "--title",
                "Frontend shell",
                "--priority",
                "60",
                "--acceptance",
                "- [ ] ready",
                "--role",
                "manager",
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["manage", "priority"])

    assert result.exit_code == 0, result.output
    assert "MANAGE PRIORITY" in result.output
    assert "THREADS" in result.output
    assert "TASKS" in result.output
    assert "frontend" in result.output
    assert "backend" in result.output
    assert "Frontend shell" in result.output
    assert "Backend base" in result.output
    assert result.output.index("frontend") < result.output.index("backend")
    assert result.output.index("frontend-001") < result.output.index("backend-001")


def test_manage_priority_updates_task_frontmatter_and_scheduler_order(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "backend",
                "--title",
                "First task",
                "--priority",
                "10",
                "--acceptance",
                "- [ ] ready",
                "--role",
                "manager",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "backend",
                "--title",
                "Second task",
                "--priority",
                "80",
                "--acceptance",
                "- [ ] ready",
                "--role",
                "manager",
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["manage", "priority", "--task", "backend-001", "--set", "99"])

    assert result.exit_code == 0, result.output
    assert "Updated task backend-001 priority -> 99." in result.output
    metadata = _read_frontmatter(isolated_project / ".loom" / "threads" / "backend" / "001.md")
    assert metadata["priority"] == 99

    next_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--task-limit", "2", "--role", "manager"])

    assert next_result.exit_code == 0, next_result.output
    assert next_result.output.index("backend-001") < next_result.output.index("backend-002")


def test_manage_priority_updates_thread_frontmatter_and_scheduler_order(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["agent", "new-thread", "--name", "backend", "--priority", "50", "--role", "manager"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["agent", "new-thread", "--name", "frontend", "--priority", "80", "--role", "manager"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "backend",
                "--title",
                "Backend task",
                "--acceptance",
                "- [ ] ready",
                "--role",
                "manager",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "agent",
                "new-task",
                "--thread",
                "frontend",
                "--title",
                "Frontend task",
                "--acceptance",
                "- [ ] ready",
                "--role",
                "manager",
            ],
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["manage", "priority", "--thread", "backend", "--set", "99"])

    assert result.exit_code == 0, result.output
    assert "Updated thread backend priority -> 99." in result.output
    metadata = _read_frontmatter(isolated_project / ".loom" / "threads" / "backend" / "_thread.md")
    assert metadata["priority"] == 99

    next_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--task-limit", "2", "--role", "manager"])

    assert next_result.exit_code == 0, next_result.output
    assert next_result.output.index("backend-001") < next_result.output.index("frontend-001")


def test_manage_priority_rejects_worker(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["manage", "priority"], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 1
    assert "worker_not_allowed" in result.output


def test_review_rejects_worker_and_points_to_reviewer_flow(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["review"], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 1, result.output
    assert "worker_not_allowed" in result.output
    assert "loom review is reviewer/human-only" in result.output
    assert "loom agent start --role reviewer" in result.output
    assert "loom review accept <task-id>" in result.output


def test_agent_spawn_reports_migration_guidance(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "spawn", "--threads", "backend"])

    assert result.exit_code == 1, result.output
    assert "moved_command" in result.output
    assert "`loom agent spawn` moved to `loom spawn --threads backend`" in result.output


def test_manage_new_thread_new_task_assign_and_plan_commands(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Need OAuth login"]).exit_code == 0

    thread_result = runner.invoke(app, ["manage", "new-thread", "--name", "backend", "--priority", "80"])
    assert thread_result.exit_code == 0, thread_result.output
    assert "CREATED thread backend" in thread_result.output

    task_result = runner.invoke(
        app,
        [
            "manage",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Manager scheduled task",
            "--acceptance",
            "- [ ] ready",
        ],
    )
    assert task_result.exit_code == 0, task_result.output
    assert "CREATED task backend-001" in task_result.output

    assign_result = runner.invoke(app, ["manage", "assign", "--thread", "backend", "--worker", "worker-123"])
    assert assign_result.exit_code == 0, assign_result.output
    assert "ASSIGNED thread backend" in assign_result.output
    assert "owner  : worker-123" in assign_result.output

    plan_result = runner.invoke(app, ["manage", "plan", "RQ-001"])
    assert plan_result.exit_code == 0, plan_result.output
    assert "PLANNED RQ-001" in plan_result.output
    assert "resolved_as : task" in plan_result.output


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

    spawn_result = runner.invoke(app, ["spawn", "--threads", "backend,frontend"])
    assert spawn_result.exit_code == 0, spawn_result.output
    assert "SPAWNED agent" in spawn_result.output
    assert "Worker environment file" in spawn_result.output
    assert "Default launch pattern" in spawn_result.output
    assert "No worker command is configured in loom.toml." in spawn_result.output
    # extract agent_id from "SPAWNED agent <id>"
    agent_id = spawn_result.output.splitlines()[0].split()[-1]
    assert f".loom/agents/workers/{agent_id}/{agent_id}.env" in spawn_result.output
    assert "If your subagent runtime cannot set environment variables at all:" in spawn_result.output
    assert "<your-agent-cmd>" not in spawn_result.output
    env = {"LOOM_WORKER_ID": agent_id}

    whoami_result = runner.invoke(app, ["agent", "whoami"], env=env)
    assert whoami_result.exit_code == 0, whoami_result.output
    assert agent_id in whoami_result.output
    assert "worker" in whoami_result.output

    checkpoint_result = runner.invoke(
        app, ["agent", "checkpoint", "working on auth", "--phase", "implementing"], env=env
    )
    assert checkpoint_result.exit_code == 0, checkpoint_result.output
    assert "CHECKPOINT recorded" in checkpoint_result.output

    resume_result = runner.invoke(app, ["agent", "resume"], env=env)
    assert resume_result.exit_code == 0, resume_result.output
    assert "working on auth" in resume_result.output

    send_result = runner.invoke(app, ["agent", "send", agent_id, "please check", "--role", "manager"])
    assert send_result.exit_code == 0, send_result.output
    assert "SENT message" in send_result.output
    # extract msg_id from "SENT message MSG-xxx"
    msg_id = send_result.output.splitlines()[0].split()[-1]

    inbox_result = runner.invoke(app, ["agent", "inbox"], env=env)
    assert inbox_result.exit_code == 0, inbox_result.output
    assert msg_id in inbox_result.output

    status_result = runner.invoke(app, ["agent", "status"])
    assert status_result.exit_code == 0, status_result.output
    assert "mailbox:1 pending / 0 replied" in status_result.output

    reply_result = runner.invoke(app, ["agent", "reply", msg_id, "got it"], env=env)
    assert reply_result.exit_code == 0, reply_result.output
    assert "REPLIED" in reply_result.output

    status_result = runner.invoke(app, ["agent", "status"])
    assert status_result.exit_code == 0, status_result.output
    assert "mailbox:0 pending / 1 replied" in status_result.output

    log_result = runner.invoke(app, ["log"])
    assert log_result.exit_code == 0, log_result.output
    assert "message.sent message:" in log_result.output
    assert "message.replied message:" in log_result.output


def test_spawn_rejects_worker_context(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["spawn"], env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 1
    assert "worker_not_allowed" in result.output
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

    result = runner.invoke(app, ["spawn", "--threads", "backend"])

    assert result.exit_code == 0, result.output
    agent_id = result.output.splitlines()[0].split()[-1]
    env_path = isolated_project / ".loom" / "agents" / "workers" / agent_id / f"{agent_id}.env"
    inline_command = (
        f"LOOM_WORKER_ID={agent_id} LOOM_DIR={isolated_project / '.loom'} "
        f"LOOM_THREADS=backend opencode run --loom-agent {agent_id}"
    )
    assert "Configured worker command" in result.output
    assert f"opencode run --loom-agent {agent_id}" in result.output
    assert f"source {env_path} && opencode run --loom-agent {agent_id}" in result.output
    assert inline_command in result.output


def test_status_migrates_legacy_worker_runtime_layout(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    legacy_root = isolated_project / ".loom" / "agents" / "abcd"
    (legacy_root / "inbox" / "pending").mkdir(parents=True)
    (legacy_root / "inbox" / "replied").mkdir(parents=True)
    (legacy_root / "_agent.md").write_text(
        (
            "---\n"
            "id: abcd\n"
            "role: worker\n"
            "status: idle\n"
            "threads:\n"
            "  - backend\n"
            "checkpoint_summary: idle\n"
            "---\n\n"
            "## Checkpoint\n\nlegacy\n"
        ),
        encoding="utf-8",
    )
    (legacy_root / "abcd.env").write_text("LOOM_WORKER_ID=abcd\n", encoding="utf-8")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    migrated_root = isolated_project / ".loom" / "agents" / "workers" / "abcd"
    assert migrated_root.exists()
    assert not legacy_root.exists()
    assert (migrated_root / "_agent.md").exists()
    assert (migrated_root / "abcd.env").exists()
    assert (isolated_project / ".loom" / "agents" / "_manager.md").exists()


def test_agent_commands_require_worker_id_by_default(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "new-thread", "--name", "backend"])

    assert result.exit_code == 1, result.output
    assert "LOOM_WORKER_ID is required" in result.output
    assert "Use --role manager / --role director / --role reviewer" in result.output


def test_agent_commands_allow_manager_override(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"])

    assert result.exit_code == 0, result.output
    assert "CREATED thread backend" in result.output


@pytest.mark.parametrize("command_name", ["new-thread", "new-task", "send"])
def test_worker_default_rejects_singleton_only_commands(runner, isolated_project, command_name):
    assert runner.invoke(app, ["init"]).exit_code == 0
    env = {"LOOM_WORKER_ID": "x7k2"}

    if command_name == "new-thread":
        args = ["agent", "new-thread", "--name", "backend"]
    elif command_name == "new-task":
        assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
        args = ["agent", "new-task", "--thread", "backend", "--title", "Demo", "--acceptance", "- [ ] ready"]
    else:
        args = ["agent", "send", "manager", "raw message"]

    result = runner.invoke(app, args, env=env)

    assert result.exit_code == 1, result.output
    assert "worker_command_not_allowed" in result.output
    assert "worker role" in result.output


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("worker", "LOOM WORKER BOOTSTRAP"),
        ("reviewer", "LOOM REVIEWER BOOTSTRAP"),
        ("director", "LOOM DIRECTOR BOOTSTRAP"),
    ],
)
def test_agent_start_supports_role_specific_bootstrap_guides(runner, isolated_project, role, expected):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "start", "--role", role])

    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_agent_start_director_uses_round_orchestration_guidance(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "start", "--role", "director"])

    assert result.exit_code == 0, result.output
    assert "BEFORE STARTING" in result.output
    assert "STARTUP" in result.output
    assert "DURING THE ROUND" in result.output
    assert "ROUND CHECK" in result.output
    assert "loom agent next --role director" in result.output
    assert "loom agent next --role director` again" in result.output


def test_agent_start_reviewer_explicitly_loops_on_agent_next(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "start", "--role", "reviewer"])

    assert result.exit_code == 0, result.output
    assert "loom agent next --role reviewer" in result.output
    assert "loom agent next --role reviewer` again" in result.output


def test_agent_next_director_shows_role_specific_guidance(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["manage", "new-thread", "--name", "backend"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "manage",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Ready work",
            "--acceptance",
            "- [ ] ready",
        ],
    )
    assert task_result.exit_code == 0, task_result.output

    result = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--role", "director"])

    assert result.exit_code == 0, result.output
    assert "ACTION  task" in result.output
    assert "Director next steps:" in result.output
    assert "Re-run with `--role manager`" not in result.output


def test_agent_next_reviewer_idle_points_to_review_and_repeat(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["manage", "new-thread", "--name", "backend"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "manage",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Review me",
            "--acceptance",
            "- [ ] ready",
        ],
    )
    task_id = task_result.output.splitlines()[0].split()[-1]
    worker_env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=worker_env).exit_code == 0
    assert runner.invoke(app, ["agent", "done", task_id, "--output", "./out"], env=worker_env).exit_code == 0

    result = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--role", "reviewer"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert "Reviewer next steps:" in result.output
    assert "run `loom review`" in result.output
    assert "loom agent next --role reviewer` again" in result.output


def test_agent_next_reviewer_ignores_execution_backlog(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["manage", "new-thread", "--name", "backend"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "manage",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Ready work",
            "--acceptance",
            "- [ ] ready",
        ],
    )

    assert task_result.exit_code == 0, task_result.output

    result = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--role", "reviewer"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert "Reviewer next steps:" in result.output
    assert "No review item is ready right now" in result.output
    assert "READY TASKS" not in result.output
    assert "Ready work" not in result.output


def test_agent_next_reviewer_ignores_planning_backlog(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Need OAuth login"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next", "--role", "reviewer"])

    assert result.exit_code == 0, result.output
    assert "ACTION  idle" in result.output
    assert "Reviewer next steps:" in result.output
    assert "UNPLANNED REQUESTS" not in result.output
    assert "This is planning work, not review work" not in result.output


def test_agent_next_director_plan_points_to_manager_and_repeat(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["inbox", "add", "Need OAuth login"]).exit_code == 0

    result = runner.invoke(app, ["agent", "next", "--role", "director"])

    assert result.exit_code == 0, result.output
    assert "ACTION  plan" in result.output
    assert "Director next steps:" in result.output
    assert "start or wake the manager with `loom manage`" in result.output
    assert "loom agent next --role director` again" in result.output


def test_human_review_still_lists_reviewing_tasks(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    worker_env = {"LOOM_WORKER_ID": "x7k2"}
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0

    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Reviewable task",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ],
    )
    task_id = task_result.output.splitlines()[0].split()[-1]

    assert runner.invoke(app, ["agent", "next", "--plan-limit", "0"], env=worker_env).exit_code == 0
    assert runner.invoke(app, ["agent", "done", task_id, "--output", "./out"], env=worker_env).exit_code == 0

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 0, result.output
    assert task_id in result.output
    assert "loom review accept <id>" in result.output


@pytest.mark.parametrize("role", ["manager", "director", "reviewer"])
def test_shared_commands_accept_singleton_role_override_without_worker_id(runner, isolated_project, role):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["agent", "new-thread", "--name", f"{role}-thread", "--role", role])

    assert result.exit_code == 0, result.output
    assert f"CREATED thread {role}-thread" in result.output
    if role != "manager":
        assert not (isolated_project / ".loom" / "agents" / role / "_agent.md").exists()


def test_legacy_agent_env_reports_migration_guidance(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(
        app,
        ["agent", "new-thread", "--name", "backend"],
        env={"LOOM_AGENT_ID": "legacy-worker"},
    )

    assert result.exit_code == 1, result.output
    assert "missing_worker_id" in result.output
    assert "LOOM_AGENT_ID is no longer used; rename it to LOOM_WORKER_ID." in result.output


@pytest.mark.parametrize(
    "command_name",
    ["new-thread", "new-task", "next", "done", "pause", "propose", "send"],
)
def test_shared_manager_facing_commands_require_identity_without_singleton_role(runner, isolated_project, command_name):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, _shared_command_args(command_name, runner))

    assert result.exit_code == 1, result.output
    assert "missing_worker_id" in result.output
    assert "LOOM_WORKER_ID is required" in result.output


@pytest.mark.parametrize(
    "command_name",
    ["new-thread", "new-task", "next", "done", "pause", "propose", "send"],
)
def test_shared_manager_facing_commands_accept_manager_override(runner, isolated_project, command_name):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, _shared_manager_override_args(command_name, runner))

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("args", [["manage"], ["spawn"]])
def test_manager_only_agent_commands_reject_worker_context_matrix(runner, isolated_project, args):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, args, env={"LOOM_WORKER_ID": "x7k2"})

    assert result.exit_code == 1, result.output
    assert "worker_not_allowed" in result.output
    assert "manager-only" in result.output


def test_agent_next_shows_ready_tasks_for_manager_without_claiming(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Manager claimed task",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ],
    )
    task_id = task_result.output.splitlines()[0].split()[-1]

    out = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--role", "manager"]).output

    assert "ACTION  task" in out
    assert "ACTOR   manager" in out
    assert "READY TASKS" in out
    assert "Ask the director or host system to start or wake a worker runtime with LOOM_WORKER_ID + LOOM_DIR." in out
    assert "loom agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager" in out
    assert "loom agent send <agent-id> '<extra context>' --ref <task-id> --role manager" in out
    assert task_id in out

    task_content = (isolated_project / ".loom" / "threads" / "backend" / "001.md").read_text(encoding="utf-8")
    assert "status: scheduled" in task_content


def test_agent_next_manager_with_executor_command_mentions_spawn(runner, isolated_project):
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
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Manager prompt",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ],
    )
    task_id = task_result.output.splitlines()[0].split()[-1]

    out = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--role", "manager"]).output

    assert "loom spawn [--threads <backend,frontend>]" in out
    assert "loom agent propose <agent-id> '<task handoff>' --ref <task-id> --role manager" in out
    assert "loom agent send <agent-id> '<extra context>' --ref <task-id> --role manager" in out
    assert task_id in out


def test_rejected_mailbox_handoff_task_can_be_reclaimed_by_another_worker(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["agent", "new-thread", "--name", "backend", "--role", "manager"]).exit_code == 0
    task_result = runner.invoke(
        app,
        [
            "agent",
            "new-task",
            "--thread",
            "backend",
            "--title",
            "Retry delegated work",
            "--acceptance",
            "- [ ] ready",
            "--role",
            "manager",
        ],
    )
    assert task_result.exit_code == 0, task_result.output
    task_id = task_result.output.splitlines()[0].split()[-1]

    first_worker = {"LOOM_WORKER_ID": "worker-1"}
    reused_worker = {"LOOM_WORKER_ID": "worker-2"}

    claim_result = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--thread", "backend"], env=first_worker)
    assert claim_result.exit_code == 0, claim_result.output
    assert task_id in claim_result.output

    done_result = runner.invoke(app, ["agent", "done", task_id, "--output", "./output/retry"], env=first_worker)
    assert done_result.exit_code == 0, done_result.output

    reject_result = runner.invoke(app, ["review", "reject", task_id, "Needs retry"])
    assert reject_result.exit_code == 0, reject_result.output
    thread_meta = _read_frontmatter(isolated_project / ".loom" / "threads" / "backend" / "_thread.md")
    assert thread_meta["owner"] == "worker-1"

    handoff_result = runner.invoke(
        app,
        ["agent", "propose", "worker-2", "Please retry the rejected task", "--ref", task_id, "--role", "manager"],
    )
    assert handoff_result.exit_code == 0, handoff_result.output
    assign_result = runner.invoke(app, ["manage", "assign", "--thread", "backend", "--worker", "worker-2"])
    assert assign_result.exit_code == 0, assign_result.output

    manager_next = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--role", "manager"])
    assert manager_next.exit_code == 0, manager_next.output
    assert "ACTION  task" in manager_next.output
    assert task_id in manager_next.output

    worker_inbox = runner.invoke(app, ["agent", "mailbox"], env=reused_worker)
    assert worker_inbox.exit_code == 0, worker_inbox.output
    assert "MSG-001" in worker_inbox.output
    assert f"ref:{task_id}" in worker_inbox.output

    retry_claim = runner.invoke(app, ["agent", "next", "--plan-limit", "0", "--thread", "backend"], env=reused_worker)
    assert retry_claim.exit_code == 0, retry_claim.output
    assert "ACTION  task" in retry_claim.output
    assert task_id in retry_claim.output

    thread_meta = _read_frontmatter(isolated_project / ".loom" / "threads" / "backend" / "_thread.md")
    assert thread_meta["owner"] == "worker-2"


def test_manager_mailbox_commands_can_read_and_reply(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    worker_env = {"LOOM_WORKER_ID": "worker-1"}
    ask_result = runner.invoke(
        app,
        ["agent", "ask", "manager", "Can I reclaim backend-001 now?", "--ref", "backend-001"],
        env=worker_env,
    )
    assert ask_result.exit_code == 0, ask_result.output
    assert "SENT question MSG-001" in ask_result.output

    manager_inbox = runner.invoke(app, ["agent", "mailbox", "--role", "manager"])
    assert manager_inbox.exit_code == 0, manager_inbox.output
    assert "MAILBOX manager" in manager_inbox.output
    assert "MSG-001" in manager_inbox.output
    assert "type:question" in manager_inbox.output

    manager_read = runner.invoke(app, ["agent", "mailbox-read", "MSG-001", "--role", "manager"])
    assert manager_read.exit_code == 0, manager_read.output
    assert "Can I reclaim backend-001 now?" in manager_read.output

    reply_result = runner.invoke(
        app,
        [
            "agent",
            "reply",
            "MSG-001",
            "Yes. The task is back in scheduled; run `loom agent next`.",
            "--role",
            "manager",
        ],
    )
    assert reply_result.exit_code == 0, reply_result.output
    assert "REPLIED to MSG-001" in reply_result.output
    assert "reply id : MSG-001" in reply_result.output

    manager_after = runner.invoke(app, ["agent", "mailbox", "--role", "manager"])
    assert manager_after.exit_code == 0, manager_after.output
    assert "No pending messages." in manager_after.output

    worker_inbox = runner.invoke(app, ["agent", "mailbox"], env=worker_env)
    assert worker_inbox.exit_code == 0, worker_inbox.output
    assert "type:answer" in worker_inbox.output

    worker_message = _read_frontmatter(
        isolated_project / ".loom" / "agents" / "workers" / "worker-1" / "inbox" / "pending" / "MSG-001.md"
    )
    assert worker_message["reply_ref"] == "MSG-001"
    assert worker_message["ref"] == "backend-001"


def test_empty_default_queue_shows_add_requirement_hint(runner, isolated_project):
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["--plain"])

    assert result.exit_code == 0, result.output
    assert "No pending approvals." in result.output
    assert "loom inbox add" in result.output
