from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifact_activation import active_artifact_currency_resolver, register_current_resource_artifact

pytestmark = pytest.mark.unit


def _write_project(project_dir: Path, schema_version: object) -> dict[str, object]:
    project = {"schema_version": schema_version}
    (project_dir / "project.json").write_text(json.dumps(project), encoding="utf-8")
    return project


def test_runtime_resolver_rejects_a_numeric_string_schema_version(tmp_path: Path) -> None:
    project = _write_project(tmp_path, "8")

    with pytest.raises(ValueError, match="schema_version must be an integer"):
        active_artifact_currency_resolver(tmp_path, project)


def test_formal_write_gate_rejects_a_numeric_string_schema_version(tmp_path: Path) -> None:
    _write_project(tmp_path, "8")

    with pytest.raises(ValueError, match="schema_version must be an integer"):
        register_current_resource_artifact(
            tmp_path,
            resource_type="characters",
            resource_id="hero",
        )


def test_runtime_resolver_rejects_a_future_schema_version(tmp_path: Path) -> None:
    project = _write_project(tmp_path, 9)

    with pytest.raises(ValueError, match="schema_version 9 is newer than supported version 8"):
        active_artifact_currency_resolver(tmp_path, project)


def test_formal_write_gate_rejects_a_future_schema_version(tmp_path: Path) -> None:
    _write_project(tmp_path, 9)

    with pytest.raises(ValueError, match="schema_version 9 is newer than supported version 8"):
        register_current_resource_artifact(
            tmp_path,
            resource_type="characters",
            resource_id="hero",
        )
