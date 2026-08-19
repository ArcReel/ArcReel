from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifact_activation import active_artifact_currency_resolver, register_current_resource_artifact
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION

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
    future_version = CURRENT_PROJECT_SCHEMA_VERSION + 1
    project = _write_project(tmp_path, future_version)

    with pytest.raises(ValueError, match=f"schema_version {future_version} is newer than supported version"):
        active_artifact_currency_resolver(tmp_path, project)


def test_formal_write_gate_rejects_a_future_schema_version(tmp_path: Path) -> None:
    future_version = CURRENT_PROJECT_SCHEMA_VERSION + 1
    _write_project(tmp_path, future_version)

    with pytest.raises(ValueError, match=f"schema_version {future_version} is newer than supported version"):
        register_current_resource_artifact(
            tmp_path,
            resource_type="characters",
            resource_id="hero",
        )
