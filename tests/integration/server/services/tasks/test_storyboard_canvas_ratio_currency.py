"""分镜图画布比例：执行器登记的依据与目标态规划器重建的依据走同一条项目比例规则，生成后判 current。"""

import json

import pytest

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.project.project_manager import ProjectManager
from server.services.tasks import generation_tasks
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    fake_resolve_ctx,
    persist_active_fake_project,
    prepare_files,
    register_asset_sheet_claims,
)

ITEM_ID = "E1S02"
ARTIFACT_PATH = f"storyboards/scene_{ITEM_ID}.png"


def _persist_with_ratio(pm: _FakePM, *, content_mode: str, aspect_ratio: object) -> None:
    """持久化替身项目后改写创作类型与画布比例；``aspect_ratio=None`` 表示项目缺该字段。"""
    persist_active_fake_project(pm)
    pm.project["content_mode"] = content_mode
    if aspect_ratio is None:
        pm.project.pop("aspect_ratio", None)
    else:
        pm.project["aspect_ratio"] = aspect_ratio
    (pm.project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    ("content_mode", "aspect_ratio", "expected_ratio"),
    [
        pytest.param("drama", None, "16:9", id="drama-missing-ratio"),
        pytest.param("narration", {"storyboards": "4:3", "videos": "9:16"}, "4:3", id="per-resource-ratio"),
        pytest.param("narration", {"videos": "16:9"}, "9:16", id="per-resource-ratio-without-storyboards"),
    ],
)
async def test_generated_storyboard_is_current_against_the_target_state(
    tmp_path, monkeypatch, content_mode, aspect_ratio, expected_ratio
):
    project_path = prepare_files(tmp_path)
    fixture = _FakePM(project_path)
    _persist_with_ratio(fixture, content_mode=content_mode, aspect_ratio=aspect_ratio)
    register_asset_sheet_claims(fixture)
    pm = ProjectManager(tmp_path / "projects")
    generator = FakeGenerator(project_path)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator))
    monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *_a, **_kw: None)

    await generation_tasks.execute_storyboard_task(
        "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": "queued prompt"}
    )

    assert generator.image_calls[0]["aspect_ratio"] == expected_ratio
    resolver = active_artifact_currency_resolver(project_path, pm.load_project("demo"))
    comparison = resolver.compare(ArtifactKey.episode_storyboard(1, ITEM_ID), artifact_path=ARTIFACT_PATH)
    assert comparison.status is ArtifactStatus.CURRENT
