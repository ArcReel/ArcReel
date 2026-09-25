"""分镜图任务执行：引用衍生、商品与上一分镜图的分镜图生成后判 current；生成输入不成立时在付费前失败。"""

import json

import pytest

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.generation.task_failure import parse_failure, render_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.i18n import _, render_generation_input_error
from lib.infra.api_errors import BadRequestError
from lib.project.project_manager import ProjectManager
from server.services.tasks import generation_tasks
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    ad_pm,
    fake_resolve_ctx,
    persist_active_fake_project,
    prepare_files,
    register_stale_visual_claim,
    seed_current_storyboard,
)

ITEM_ID = "E1S02"
DERIVATIVE_SHEET = "characters/derivatives/Alice/劲装.png"


def _project_with_derivative_product_and_previous(project_path):
    (project_path / "products" / "保温杯.png").write_bytes(b"product-sheet")
    (project_path / DERIVATIVE_SHEET).parent.mkdir(parents=True, exist_ok=True)
    (project_path / DERIVATIVE_SHEET).write_bytes(b"derivative-sheet")
    fixture = ad_pm(project_path, with_sheet=True)
    fixture.project["characters"]["Alice"]["derivatives"] = {
        "劲装": {"description": "换上劲装", "character_sheet": DERIVATIVE_SHEET}
    }
    fixture.script["shots"][1]["characters_in_shot"] = ["Alice/劲装"]
    fixture.script["shots"][0]["generated_assets"] = {}
    seed_current_storyboard(fixture, "E1S01")
    persist_active_fake_project(fixture)
    register_stale_visual_claim(project_path, ArtifactKey.asset_sheet("character", "Alice/劲装"), DERIVATIVE_SHEET)
    return fixture


def _patch(monkeypatch, pm, generator):
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator))
    monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *_a, **_kw: None)


async def test_generated_storyboard_with_derivative_product_and_previous_is_current(tmp_path, monkeypatch):
    project_path = prepare_files(tmp_path)
    _project_with_derivative_product_and_previous(project_path)
    pm = ProjectManager(tmp_path)
    generator = FakeGenerator(project_path)
    _patch(monkeypatch, pm, generator)

    await generation_tasks.execute_storyboard_task(
        "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": "queued prompt"}
    )

    sent = [reference["image"].name for reference in generator.image_calls[0]["reference_images"]]
    assert sent == ["0000-保温杯.png", "0001-保温杯_1.jpg", "0002-劲装.png", "0003-祠堂.png", "0004-scene_E1S01.png"]
    resolver = active_artifact_currency_resolver(project_path, pm.load_project("demo"))
    comparison = resolver.compare(
        ArtifactKey.episode_storyboard(1, ITEM_ID), artifact_path=f"storyboards/scene_{ITEM_ID}.png"
    )
    assert comparison.status is ArtifactStatus.CURRENT


async def test_clamping_only_trims_what_is_sent(tmp_path, monkeypatch):
    project_path = prepare_files(tmp_path)
    _project_with_derivative_product_and_previous(project_path)
    pm = ProjectManager(tmp_path)
    generator = FakeGenerator(project_path)
    _patch(monkeypatch, pm, generator)
    monkeypatch.setattr(
        generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator, image_max_reference_images=2)
    )

    result = await generation_tasks.execute_storyboard_task(
        "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": "queued prompt"}
    )

    assert len(generator.image_calls[0]["reference_images"]) == 2
    assert [warning["key"] for warning in result["warnings"]] == ["ref_too_many_images"]
    resolver = active_artifact_currency_resolver(project_path, pm.load_project("demo"))
    comparison = resolver.compare(
        ArtifactKey.episode_storyboard(1, ITEM_ID), artifact_path=f"storyboards/scene_{ITEM_ID}.png"
    )
    assert comparison.status is ArtifactStatus.CURRENT


async def test_every_gap_is_reported_before_the_provider_is_resolved(tmp_path, monkeypatch):
    project_path = prepare_files(tmp_path)
    fixture = _project_with_derivative_product_and_previous(project_path)
    fixture.script["shots"][1]["characters_in_shot"] = ["Alice/劲装", "Bob"]
    persist_active_fake_project(fixture)
    (project_path / "products" / "refs" / "保温杯_1.jpg").unlink()
    (project_path / DERIVATIVE_SHEET).unlink()
    pm = ProjectManager(tmp_path)
    lane_requests: list[dict] = []
    generator = FakeGenerator(project_path)
    _patch(monkeypatch, pm, generator)
    monkeypatch.setattr(
        generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator, seen_lane_requests=lane_requests)
    )

    with pytest.raises(BadRequestError) as refused:
        await generation_tasks.execute_storyboard_task(
            "demo", ITEM_ID, {"script_file": "episode_1.json", "prompt": "queued prompt"}
        )

    assert refused.value.key == "asset_original_missing"
    assert refused.value.params["missing_text"] == "product: 保温杯, character: Alice/劲装, Bob"
    assert refused.value.params["gaps"] == [
        {"code": "asset_original_missing", "asset_type": "product", "name": "保温杯"},
        {"code": "reference_asset_missing", "asset_type": "character", "name": "Alice/劲装"},
        {"code": "reference_asset_unregistered", "asset_type": "character", "name": "Bob"},
    ]
    stored = encode_task_failure_message(refused.value)
    assert parse_failure(stored) == ("asset_original_missing", json.loads(json.dumps(refused.value.params)))

    def translate(key, **params):
        return _(key, locale="zh", **params)

    detail = render_generation_input_error(refused.value.key, refused.value.params, translate)
    assert detail == render_failure(stored, translate)
    assert "原图读不到：product: 保温杯" in detail
    assert "参考素材缺失或文件不可用：character: Alice/劲装" in detail
    assert "未登记的资产名：Bob" in detail
    assert lane_requests == []
    assert generator.image_calls == []
