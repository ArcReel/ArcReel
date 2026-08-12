from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestError,
    ArtifactObservation,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_provenance import build_episode_script_basis
from lib.project_manager import ProjectManager
from lib.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditor,
    script_revision,
)

pytestmark = pytest.mark.integration


def _segment(segment_id: str, *, text: str = "风吹过旷野。") -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "duration_seconds": 4,
        "novel_text": text,
        "characters_in_segment": [],
        "image_prompt": {
            "scene": "荒野",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "generated_assets": {},
    }


def _script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": [_segment("E1S01"), _segment("E1S02"), _segment("E1S03")],
    }


@pytest.fixture
def editor(tmp_path: Path) -> tuple[ProjectManager, ScriptBatchEditor, Path]:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="narration")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script("demo", _script(), "episode_1.json")
    project_dir = pm.get_project_path("demo")
    step1 = project_dir / "drafts" / "episode_1" / "step1_segments.json"
    step1.parent.mkdir(parents=True, exist_ok=True)
    step1.write_text(json.dumps({"segments": [{"segment_id": "E1S01"}]}), encoding="utf-8")
    return pm, ScriptBatchEditor(pm), project_dir


def _command(pm: ProjectManager, operations: list[dict[str, Any]]) -> ScriptBatchEditCommand:
    current = pm.load_script("demo", "episode_1.json")
    return ScriptBatchEditCommand.model_validate(
        {
            "script": "episode_1.json",
            "expected_revision": script_revision(current),
            "operations": operations,
        }
    )


def test_multi_operation_commit_updates_manifest_and_returns_revision(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    before = pm.load_script("demo", "episode_1.json")

    result = service.execute(
        "demo",
        _command(
            pm,
            [
                {"op": "update", "id": "E1S01", "fields": {"note": "保留"}},
                {"op": "move_after", "id": "E1S03", "after_id": None},
                {"op": "insert_after", "after_id": "E1S01", "item": _segment("E1S04")},
                {"op": "remove", "id": "E1S02"},
            ],
        ),
    )

    assert result.success is True
    assert result.before_revision == script_revision(before)
    saved = pm.load_script("demo", "episode_1.json")
    assert result.revision == script_revision(saved)
    assert result.revision != result.before_revision
    assert [segment["segment_id"] for segment in saved["segments"]] == ["E1S03", "E1S01", "E1S04"]
    assert saved["segments"][1]["note"] == "保留"

    adapter = ProjectArtifactManifestAdapter(project_dir)
    entry = adapter.get_entry(ArtifactKey.episode_script(1))
    assert entry is not None
    step1 = json.loads((project_dir / "drafts" / "episode_1" / "step1_segments.json").read_text(encoding="utf-8"))
    project = pm.load_project("demo")
    assert entry.basis_digest == build_episode_script_basis(step1, project=project).digest
    assert entry.artifact_path == "scripts/episode_1.json"


@pytest.mark.parametrize("failure_index", [0, 1, 2])
def test_invalid_operation_at_any_position_writes_nothing(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path], failure_index: int
) -> None:
    pm, service, project_dir = editor
    script_path = project_dir / "scripts" / "episode_1.json"
    project_path = project_dir / "project.json"
    before_script = script_path.read_bytes()
    before_project = project_path.read_bytes()
    operations = [
        {"op": "update", "id": "E1S01", "fields": {"note": "one"}},
        {"op": "update", "id": "E1S02", "fields": {"note": "two"}},
        {"op": "update", "id": "E1S03", "fields": {"note": "three"}},
    ]
    operations[failure_index] = {"op": "remove", "id": "missing"}

    result = service.execute("demo", _command(pm, operations))

    assert result.success is False
    assert result.problems[0].code == "operation_invalid"
    assert result.problems[0].operation_index == failure_index
    assert script_path.read_bytes() == before_script
    assert project_path.read_bytes() == before_project
    assert not (project_dir / ".arcreel_artifacts.json").exists()


def test_invalid_second_field_reports_exact_operation_field(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    before = (project_dir / "scripts" / "episode_1.json").read_bytes()

    result = service.execute(
        "demo",
        _command(
            pm,
            [
                {
                    "op": "update",
                    "id": "E1S01",
                    "fields": {"note": "rolled back", "image_prompt.missing.deep": "bad"},
                }
            ],
        ),
    )

    assert result.success is False
    problem = result.problems[0]
    assert problem.operation_index == 0
    assert problem.locations[0].path == (
        "operations",
        0,
        "fields",
        "image_prompt",
        "missing",
        "deep",
    )
    assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before


def test_mixed_speech_reports_operation_and_field_location(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    script_path = project_dir / "scripts" / "episode_1.json"
    before = script_path.read_bytes()

    result = service.execute(
        "demo",
        _command(
            pm,
            [
                {
                    "op": "update",
                    "id": "E1S02",
                    "fields": {"video_prompt.dialogue": [{"speaker": "阿黎", "line": "快走。"}]},
                }
            ],
        ),
    )

    assert result.success is False
    problem = result.problems[0]
    assert problem.code == "mixed_speech"
    assert problem.operation_index == 0
    assert problem.unit_id == "E1S02"
    assert problem.locations[0].path in {
        ("novel_text",),
        ("video_prompt", "dialogue", 0, "line"),
    }
    assert problem.next_action == "replan_unit"
    assert script_path.read_bytes() == before


def test_stale_revision_conflicts_without_writing(editor: tuple[ProjectManager, ScriptBatchEditor, Path]) -> None:
    pm, service, project_dir = editor
    script_path = project_dir / "scripts" / "episode_1.json"
    before = script_path.read_bytes()
    command = _command(pm, [{"op": "update", "id": "E1S01", "fields": {"note": "stale"}}])
    command = command.model_copy(update={"expected_revision": "sha256-v1:" + "0" * 64})

    result = service.execute("demo", command)

    assert result.success is False
    assert result.problems[0].code == "revision_conflict"
    assert result.problems[0].operation_index is None
    assert result.revision == script_revision(pm.load_script("demo", "episode_1.json"))
    assert script_path.read_bytes() == before


def test_rejected_edit_does_not_persist_project_read_migration(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    from lib.project_change_hints import register_project_change_listener

    pm, service, project_dir = editor
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project.pop("style_template_id", None)
    project["style"] = "Anime"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    before_project = project_path.read_bytes()
    script_path = project_dir / "scripts" / "episode_1.json"
    before_script = script_path.read_bytes()
    command = _command(pm, [{"op": "update", "id": "E1S01", "fields": {"note": "stale"}}])
    command = command.model_copy(update={"expected_revision": "sha256-v1:" + "0" * 64})
    events: list[tuple[str, str, tuple[str, ...]]] = []
    unregister = register_project_change_listener(lambda name, source, paths: events.append((name, source, paths)))

    try:
        result = service.execute("demo", command)
    finally:
        unregister()

    assert result.success is False
    assert result.problems[0].code == "revision_conflict"
    assert project_path.read_bytes() == before_project
    assert script_path.read_bytes() == before_script
    assert events == []


@pytest.mark.parametrize(
    ("container", "value"),
    [("segments", 123), ("video_units", None)],
)
def test_malformed_item_container_returns_schema_failure_without_writes(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
    container: str,
    value: object,
) -> None:
    pm, service, project_dir = editor
    script_path = project_dir / "scripts" / "episode_1.json"
    malformed = json.loads(script_path.read_text(encoding="utf-8"))
    for key in ("segments", "video_units"):
        malformed.pop(key, None)
    malformed[container] = value
    script_path.write_text(json.dumps(malformed, ensure_ascii=False), encoding="utf-8")
    before_script = script_path.read_bytes()
    project_path = project_dir / "project.json"
    before_project = project_path.read_bytes()
    command = ScriptBatchEditCommand.model_validate(
        {
            "script": "episode_1.json",
            "expected_revision": script_revision(malformed),
            "operations": [{"op": "update", "id": "E1S01", "fields": {"note": "invalid"}}],
        }
    )

    result = service.execute("demo", command)

    assert result.success is False
    problem = result.problems[0]
    assert problem.code == "schema_invalid"
    assert problem.reason == "stored_schema_invalid"
    assert problem.next_action == "repair_script"
    assert problem.operation_index is None
    assert problem.unit_id is None
    assert problem.locations[0].path == (container,)
    assert script_path.read_bytes() == before_script
    assert project_path.read_bytes() == before_project


def test_same_content_episode_rebind_conflicts_without_writing(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    current = pm.load_script("demo", "episode_1.json")
    command = ScriptBatchEditCommand.model_validate(
        {
            "episode": 1,
            "expected_script_file": "scripts/episode_1.json",
            "expected_revision": script_revision(current),
            "operations": [{"op": "update", "id": "E1S01", "fields": {"note": "stale"}}],
        }
    )
    pm.save_script("demo", current, "episode_1_copy.json")
    original_path = project_dir / "scripts" / "episode_1.json"
    rebound_path = project_dir / "scripts" / "episode_1_copy.json"
    before_original = original_path.read_bytes()
    before_rebound = rebound_path.read_bytes()

    result = service.execute("demo", command)

    assert result.success is False
    assert result.script == "episode_1.json"
    assert result.problems[0].code == "revision_conflict"
    assert result.problems[0].reason == "script_binding_changed"
    assert original_path.read_bytes() == before_original
    assert rebound_path.read_bytes() == before_rebound


def test_route_mismatched_legacy_script_remains_editable(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, _project_dir = editor
    pm.update_project("demo", lambda project: project.update({"generation_mode": "reference_video"}))

    result = service.execute(
        "demo",
        _command(pm, [{"op": "update", "id": "E1S01", "fields": {"note": "仍可修复"}}]),
    )

    assert result.success is True
    assert pm.load_script("demo", "episode_1.json")["segments"][0]["note"] == "仍可修复"


class _FailingManifestAdapter:
    def __init__(self, project_dir: Path):
        self._delegate = ProjectArtifactManifestAdapter(project_dir)

    def inspect_artifact(self, artifact_path: str) -> ArtifactObservation:
        return self._delegate.inspect_artifact(artifact_path)

    def get_entry(self, key: ArtifactKey):
        return self._delegate.get_entry(key)

    def put_entry(self, key: ArtifactKey, entry) -> bool:
        raise ArtifactManifestError("injected manifest write failure")

    def delete_entry(self, key: ArtifactKey) -> bool:
        return self._delegate.delete_entry(key)


class _WriteThenFailManifestAdapter(_FailingManifestAdapter):
    def __init__(self, project_dir: Path):
        super().__init__(project_dir)
        self._failed = False

    def put_entry(self, key: ArtifactKey, entry) -> bool:
        changed = self._delegate.put_entry(key, entry)
        if not self._failed:
            self._failed = True
            raise ArtifactManifestError("injected failure after manifest replacement")
        return changed


def test_manifest_write_failure_restores_script_and_project_bytes(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, _service, project_dir = editor
    service = ScriptBatchEditor(pm, manifest_adapter_factory=_FailingManifestAdapter)
    script_path = project_dir / "scripts" / "episode_1.json"
    project_path = project_dir / "project.json"
    before_script = script_path.read_bytes()
    before_project = project_path.read_bytes()

    result = service.execute(
        "demo",
        _command(pm, [{"op": "update", "id": "E1S01", "fields": {"note": "must roll back"}}]),
    )

    assert result.success is False
    assert result.problems[0].code == "commit_failed"
    assert script_path.read_bytes() == before_script
    assert project_path.read_bytes() == before_project
    assert not (project_dir / ".arcreel_artifacts.json").exists()


def test_manifest_post_replace_failure_restores_all_three_stores(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, _service, project_dir = editor
    service = ScriptBatchEditor(pm, manifest_adapter_factory=_WriteThenFailManifestAdapter)
    script_path = project_dir / "scripts" / "episode_1.json"
    project_path = project_dir / "project.json"
    before_script = script_path.read_bytes()
    before_project = project_path.read_bytes()

    result = service.execute(
        "demo",
        _command(pm, [{"op": "update", "id": "E1S01", "fields": {"note": "must roll back"}}]),
    )

    assert result.success is False
    assert result.problems[0].code == "commit_failed"
    assert script_path.read_bytes() == before_script
    assert project_path.read_bytes() == before_project
    assert not (project_dir / ".arcreel_artifacts.json").exists()


def test_corrupt_manifest_is_rejected_during_preflight_without_writes(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    script_path = project_dir / "scripts" / "episode_1.json"
    project_path = project_dir / "project.json"
    before_script = script_path.read_bytes()
    before_project = project_path.read_bytes()
    (project_dir / ".arcreel_artifacts.json").write_text("{broken", encoding="utf-8")

    result = service.execute(
        "demo",
        _command(pm, [{"op": "update", "id": "E1S01", "fields": {"note": "rejected"}}]),
    )

    assert result.success is False
    assert result.problems[0].code == "manifest_invalid"
    assert script_path.read_bytes() == before_script
    assert project_path.read_bytes() == before_project
    assert (project_dir / ".arcreel_artifacts.json").read_text(encoding="utf-8") == "{broken"


def test_reference_failure_maps_numeric_path_and_responsible_operation(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    before = (project_dir / "scripts" / "episode_1.json").read_bytes()

    result = service.execute(
        "demo",
        _command(
            pm,
            [{"op": "update", "id": "E1S01", "fields": {"characters_in_segment": ["missing"]}}],
        ),
    )

    assert result.success is False
    problem = result.problems[0]
    assert problem.code == "references_invalid"
    assert problem.operation_index == 0
    assert problem.unit_id == "E1S01"
    assert problem.locations[0].path[:2] == ("segments", 0)
    assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before


def test_reference_failure_is_attributed_to_the_operation_that_changed_the_field(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    before = (project_dir / "scripts" / "episode_1.json").read_bytes()

    result = service.execute(
        "demo",
        _command(
            pm,
            [
                {"op": "update", "id": "E1S01", "fields": {"characters_in_segment": ["missing"]}},
                {"op": "update", "id": "E1S01", "fields": {"note": "later unrelated edit"}},
            ],
        ),
    )

    assert result.success is False
    problem = result.problems[0]
    assert problem.code == "references_invalid"
    assert problem.operation_index == 0
    assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before


def test_unrelated_video_unit_edit_does_not_reject_unmarked_legacy_mixed_speech(tmp_path: Path) -> None:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="narration")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.update_project("demo", lambda project: project.update({"generation_mode": "reference_video"}))
    pm.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
    script = {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "@[角色A]：{快走。}\n{风吹过旷野。}"}],
                "references": [{"type": "character", "name": "角色A"}],
                "duration_seconds": 8,
                "generated_assets": {"video_clip": "videos/E1U1.mp4", "status": "completed"},
            }
        ],
    }
    pm.save_script("demo", script, "episode_1.json")
    service = ScriptBatchEditor(pm)

    result = service.execute(
        "demo",
        _command(pm, [{"op": "update", "id": "E1U1", "fields": {"note": "保留历史媒体"}}]),
    )

    assert result.success is True
    saved = pm.load_script("demo", "episode_1.json")["video_units"][0]
    assert saved["note"] == "保留历史媒体"
    assert "needs_replan" not in saved
    assert saved["generated_assets"] == {
        "video_clip": "videos/E1U1.mp4",
        "status": "completed",
    }


def test_malformed_video_unit_shots_returns_structured_failure_without_writes(tmp_path: Path) -> None:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="narration")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.update_project("demo", lambda project: project.update({"generation_mode": "reference_video"}))
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "video_units": [
                {
                    "unit_id": "E1U1",
                    "shots": [{"text": "{风吹过旷野。}"}],
                    "references": [],
                    "duration_seconds": 8,
                    "generated_assets": {},
                }
            ],
        },
        "episode_1.json",
    )
    service = ScriptBatchEditor(pm)
    script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    before = script_path.read_bytes()

    result = service.execute(
        "demo",
        _command(pm, [{"op": "update", "id": "E1U1", "fields": {"shots": 123}}]),
    )

    assert result.success is False
    problem = result.problems[0]
    assert problem.code == "parse_failed"
    assert problem.operation_index == 0
    assert problem.unit_id == "E1U1"
    assert problem.locations[0].path == ("shots",)
    assert script_path.read_bytes() == before


def test_remove_then_reinsert_same_id_preserves_anchor_media(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, _project_dir = editor
    with pm.locked_script("demo", "episode_1.json", validate=False) as script:
        script["segments"][0]["generated_assets"] = {
            "video_clip": "videos/E1S01.mp4",
            "status": "completed",
        }
        script["segments"][0]["end_frame_image"] = "end_frames/E1S01.png"
    replacement = _segment("E1S01", text="拆分后的锚点")

    result = service.execute(
        "demo",
        _command(
            pm,
            [
                {"op": "remove", "id": "E1S01"},
                {"op": "insert_after", "after_id": None, "item": replacement},
            ],
        ),
    )

    assert result.success is True
    anchor = pm.load_script("demo", "episode_1.json")["segments"][0]
    assert anchor["generated_assets"] == {
        "video_clip": "videos/E1S01.mp4",
        "status": "completed",
    }
    assert anchor["end_frame_image"] == "end_frames/E1S01.png"


def test_structural_edit_preserves_existing_paid_media(
    editor: tuple[ProjectManager, ScriptBatchEditor, Path],
) -> None:
    pm, service, project_dir = editor
    media_path = project_dir / "videos" / "E1S01.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"paid-media")
    with pm.locked_script("demo", "episode_1.json", validate=False) as script:
        script["segments"][0]["generated_assets"] = {"video_clip": "videos/E1S01.mp4", "status": "completed"}

    result = service.execute(
        "demo",
        _command(pm, [{"op": "update", "id": "E1S01", "fields": {"video_prompt.action": "回头"}}]),
    )

    assert result.success is True
    assert media_path.read_bytes() == b"paid-media"
    assert pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"] == {
        "video_clip": "videos/E1S01.mp4",
        "status": "completed",
    }
