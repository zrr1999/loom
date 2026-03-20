"""Read / write markdown files with YAML frontmatter."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol

import yaml

if TYPE_CHECKING:
    from pathlib import Path


class FrontmatterModel(Protocol):
    @classmethod
    def model_validate(cls, obj: object) -> Any: ...

    def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, Any]: ...


FRONTMATTER_DELIMITER = "---"


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith(f"{FRONTMATTER_DELIMITER}\n"):
        return "", text

    remainder = text[len(f"{FRONTMATTER_DELIMITER}\n") :]
    closing_marker = f"\n{FRONTMATTER_DELIMITER}\n"
    closing_index = remainder.find(closing_marker)
    if closing_index == -1:
        msg = "Invalid frontmatter: missing closing delimiter"
        raise ValueError(msg)

    metadata_text = remainder[:closing_index]
    body = remainder[closing_index + len(closing_marker) :]
    return metadata_text, body


def _normalize_yaml_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_yaml_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_value(item) for item in value]
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def read_raw(path: Path) -> tuple[dict[str, Any], str]:
    """Read a markdown file, return (metadata dict, body text)."""
    text = path.read_text(encoding="utf-8")
    metadata_text, body = _split_frontmatter(text)
    if not metadata_text:
        return {}, body

    metadata = yaml.safe_load(metadata_text) or {}
    if not isinstance(metadata, dict):
        msg = "Frontmatter must parse to a mapping"
        raise ValueError(msg)
    normalized = {str(key): _normalize_yaml_value(value) for key, value in metadata.items()}
    return normalized, body


def write_raw(path: Path, metadata: dict[str, Any], body: str) -> None:
    """Write metadata + body back to a markdown file."""
    normalized_metadata = _normalize_yaml_value(metadata)
    yaml_text = yaml.safe_dump(
        normalized_metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    frontmatter_text = f"{FRONTMATTER_DELIMITER}\n{yaml_text}\n{FRONTMATTER_DELIMITER}\n\n{body.strip()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter_text + "\n", encoding="utf-8")


def read_model[T: FrontmatterModel](path: Path, model_cls: type[T]) -> T:
    """Read a markdown file and parse into a Pydantic model.

    The markdown body is passed as the ``body`` field.
    """
    meta, body = read_raw(path)
    meta["body"] = body
    return model_cls.model_validate(meta)


def write_model(path: Path, obj: FrontmatterModel) -> None:
    """Dump a Pydantic model back to a markdown file.

    The ``body`` field becomes the markdown body; everything else becomes
    YAML frontmatter.
    """
    data = obj.model_dump(mode="json", exclude_none=True)
    body = data.pop("body", "")
    write_raw(path, data, body)
