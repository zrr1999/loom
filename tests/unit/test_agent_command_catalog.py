from __future__ import annotations

from pathlib import Path

from loom.agent_command_catalog import (
    README_COMMAND_PREFIX,
    render_manager_command_access,
    render_manager_command_contract,
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


def test_role_manager_command_blocks_match_catalog():
    text = (ROOT / "roles" / "loom-manager.md").read_text(encoding="utf-8")

    assert _extract_generated_block(text, "manager-command-contract") == render_manager_command_contract()
    assert _extract_generated_block(text, "manager-command-access") == render_manager_command_access()
