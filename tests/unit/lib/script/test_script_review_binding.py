"""Content confirmation resolves episode bindings; a missing bound file must not substitute another episode's canonical-path file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.script.script_review import (
    ForeignFormalScriptError,
    formal_script_filename,
    formal_script_overwrite,
    prompt_authoring_generated,
    review_status,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _project(binding: str) -> dict[str, Any]:
    return {
        "content_mode": "narration",
        "generation_mode": "standard",
        "episodes": [{"episode": 1, "script_file": binding}],
    }


def _project_dir(tmp_path: Path, *, canonical_holds: int | None, bound_present: bool) -> Path:
    project_dir = tmp_path / "demo"
    _write_json(project_dir / "drafts" / "episode_1" / "script_plan_segments.json", {"segments": [{"id": "E1S01"}]})
    if canonical_holds is not None:
        _write_json(project_dir / "scripts" / "episode_1.json", {"episode": canonical_holds, "segments": []})
    if bound_present:
        _write_json(project_dir / "scripts" / "custom.json", {"episode": 1, "segments": []})
    return project_dir


def test_absent_binding_is_not_grandfathered_by_the_file_at_the_canonical_path(tmp_path: Path) -> None:
    """Migration-skipped shape: missing bound file and another episode's script at the canonical path means no output here, never confirmed."""

    project_dir = _project_dir(tmp_path, canonical_holds=2, bound_present=False)
    project = _project("scripts/custom.json")

    assert prompt_authoring_generated(project_dir, project, 1) is False
    assert review_status(project_dir, project, 1) == "pending_review"


def test_present_binding_counts_as_this_episodes_output(tmp_path: Path) -> None:
    project_dir = _project_dir(tmp_path, canonical_holds=None, bound_present=True)
    project = _project("scripts/custom.json")

    assert formal_script_filename(project_dir, project, 1) == "custom.json"
    assert prompt_authoring_generated(project_dir, project, 1) is True


def test_unbound_episode_still_falls_back_to_the_canonical_path(tmp_path: Path) -> None:
    project_dir = _project_dir(tmp_path, canonical_holds=1, bound_present=False)
    project: dict[str, Any] = {
        "content_mode": "narration",
        "generation_mode": "standard",
        "episodes": [{"episode": 1}],
    }

    assert prompt_authoring_generated(project_dir, project, 1) is True


def test_canonical_fallback_refuses_a_file_that_holds_another_episode(tmp_path: Path) -> None:
    """Missing bound files with another episode's canonical-path script yield no formal script for this episode; overwrite manifests treat it as absent.

    Falling back would let content confirmation rebuild another episode's live script and bind this episode to it.
    """

    project_dir = _project_dir(tmp_path, canonical_holds=2, bound_present=False)
    project = _project("scripts/custom.json")

    with pytest.raises(ForeignFormalScriptError) as excinfo:
        formal_script_filename(project_dir, project, 1)
    assert (excinfo.value.episode, excinfo.value.filename) == (1, "episode_1.json")
    assert formal_script_overwrite(project_dir, project, 1) is None


def test_canonical_fallback_refuses_a_file_that_is_not_a_script_object(tmp_path: Path) -> None:
    """Match v14→v15 premigration-rename ownership checks: unreadable objects do not fall back either."""

    project_dir = _project_dir(tmp_path, canonical_holds=None, bound_present=False)
    (project_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (project_dir / "scripts" / "episode_1.json").write_text("[]", encoding="utf-8")
    project = _project("scripts/custom.json")

    with pytest.raises(ForeignFormalScriptError):
        formal_script_filename(project_dir, project, 1)


def test_canonical_fallback_refuses_a_boolean_episode(tmp_path: Path) -> None:
    """Script ``episode`` as JSON ``true`` has ``True == 1``; ``!=`` comparison would falsely allow fallback as episode 1."""

    project_dir = _project_dir(tmp_path, canonical_holds=None, bound_present=False)
    _write_json(project_dir / "scripts" / "episode_1.json", {"episode": True, "segments": []})
    project = _project("scripts/custom.json")

    with pytest.raises(ForeignFormalScriptError):
        formal_script_filename(project_dir, project, 1)


def test_canonical_fallback_stands_when_the_path_is_free_or_holds_this_episode(tmp_path: Path) -> None:
    """Fallback still works when canonical paths are empty first-write destinations or hold this episode's script."""

    free = _project_dir(tmp_path / "free", canonical_holds=None, bound_present=False)
    assert formal_script_filename(free, _project("scripts/custom.json"), 1) == "episode_1.json"

    mine = _project_dir(tmp_path / "mine", canonical_holds=1, bound_present=False)
    assert formal_script_filename(mine, _project("scripts/custom.json"), 1) == "episode_1.json"
