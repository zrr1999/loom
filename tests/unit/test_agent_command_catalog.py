from __future__ import annotations

from pathlib import Path

from loom.agent_command_catalog import (
    README_COMMAND_PREFIX,
    render_manager_command_access,
    render_manager_command_contract,
)
from loom.doc_generation import (
    render_readme_task_storage_model,
    render_task_file_model,
    render_task_status_guide,
    render_task_transition_guide,
    render_worker_agent_next_json_example,
    render_worker_agent_next_text_example,
)

ROOT = Path(__file__).resolve().parents[2]


def _extract_generated_block(text: str, marker: str) -> str:
    begin = f"<!-- BEGIN: {marker} -->"
    end = f"<!-- END: {marker} -->"
    start = text.index(begin) + len(begin)
    finish = text.index(end)
    return text[start:finish].strip()


def test_readme_manager_command_blocks_match_catalog():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert _extract_generated_block(text, "task-storage-model") == render_readme_task_storage_model()
    assert _extract_generated_block(text, "manager-command-contract") == render_manager_command_contract(
        prefix=README_COMMAND_PREFIX
    )
    assert _extract_generated_block(text, "manager-command-access") == render_manager_command_access(
        prefix=README_COMMAND_PREFIX
    )


def test_cli_reference_manager_command_blocks_match_catalog():
    text = (ROOT / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")

    assert _extract_generated_block(text, "manager-command-contract") == render_manager_command_contract()
    assert _extract_generated_block(text, "manager-command-access") == render_manager_command_access()


def test_data_model_task_file_block_matches_catalog():
    text = (ROOT / "docs" / "reference" / "data-model.md").read_text(encoding="utf-8")

    assert _extract_generated_block(text, "task-file-model") == render_task_file_model()


def test_design_generated_blocks_match_catalog():
    cli_design = (ROOT / "design" / "cli-design.md").read_text(encoding="utf-8")
    approval_guide = (ROOT / "design" / "approval-queue-tui-implementation-guide.md").read_text(encoding="utf-8")

    assert _extract_generated_block(cli_design, "worker-agent-next-text-example") == (
        render_worker_agent_next_text_example()
    )
    assert _extract_generated_block(cli_design, "worker-agent-next-json-example") == (
        render_worker_agent_next_json_example()
    )
    assert _extract_generated_block(approval_guide, "task-status-guide") == render_task_status_guide()
    assert _extract_generated_block(approval_guide, "task-transition-guide") == render_task_transition_guide()


def test_canonical_role_files_exist():
    role_dir = ROOT / "roles"

    for name in (
        "loom-director.md",
        "loom-manager.md",
        "loom-reviewer.md",
        "loom-worker.md",
    ):
        assert (role_dir / name).is_file()
