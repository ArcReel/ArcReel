"""分镜图参考图按图像后端上限裁剪后再编号：实发张数与声明行一致，依据仍按完整装配集登记。"""

import json
import logging
from pathlib import Path

import httpx
import pytest

from lib.artifact_activation import active_artifact_currency_resolver
from lib.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.image_backends.vidu import ViduImageBackend
from lib.visual_artifact_provenance import VisualReference, build_storyboard_image_visual_basis
from server.services import generation_context, generation_tasks
from tests.fakes import hook_claim_recheck
from tests.http_capture import capture_http
from tests.integration.server.services.generation_tasks_support import (
    EIGHT_REFERENCE_CHARACTERS,
    FakeGenerator,
    _FakePM,
    fake_resolve_ctx,
    persist_active_fake_project,
    pm_with_eight_references,
    prepare_files,
)

ITEM_ID = "E1S02"
STORYBOARD_PAYLOAD = {"script_file": "episode_1.json", "prompt": "queued prompt"}
CLAMPED_PROMPT = (
    "Visual style: cinematic\n\n"
    "Style: Anime\n"
    "Reference_Images: 图1、图2、图3、图4、图5、图6为角色参考图；图7为场景参考图。\n"
    "Scene: 图1握着玉佩立在图7门口\n"
    "Composition:\n  shot_type: Medium Shot\n  lighting: 暖光\n  ambiance: 薄雾\n"
    "Avoid: 水印、多余文字、Logo"
)


def _expected_basis(project_path: Path, pm: _FakePM):
    """完整装配集对应的 basis：6 张角色 sheet + 场景 sheet + 道具 sheet，与目标态规划器按脚本条目重建的一致。"""
    references = [
        VisualReference(
            path=project_path / "characters" / f"{name}.png",
            role="asset_sheet",
            logical_type="character",
            logical_id=name,
            kind="sheet",
        )
        for name in ["Alice", *EIGHT_REFERENCE_CHARACTERS]
    ]
    references.append(
        VisualReference(
            path=project_path / "scenes" / "祠堂.png",
            role="asset_sheet",
            logical_type="scene",
            logical_id="祠堂",
            kind="sheet",
        )
    )
    references.append(
        VisualReference(
            path=project_path / "props" / "玉佩.png",
            role="asset_sheet",
            logical_type="prop",
            logical_id="玉佩",
            kind="sheet",
        )
    )
    return build_storyboard_image_visual_basis(
        resource_id=ITEM_ID,
        image_prompt=pm.script["segments"][1]["image_prompt"],
        style="Anime",
        style_description="cinematic",
        aspect_ratio="9:16",
        references=references,
    )


def _patch_execution(monkeypatch, pm: _FakePM, generator: FakeGenerator, *, limit: int) -> None:
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(
        generation_tasks,
        "resolve_generation_context",
        fake_resolve_ctx(generator, image_max_reference_images=limit),
    )
    monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *_a, **_kw: None)


async def _run_storyboard(generator: FakeGenerator) -> dict:
    await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)
    return generator.image_calls[0]


class TestStoryboardReferenceClamping:
    async def test_over_limit_references_are_dropped_before_numbering(self, tmp_path, monkeypatch):
        """Vidu 口径（上限 7）：只发 7 张，声明行止于图7，第 8 张的引用退回裸名。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)

        call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 7
        # 登记进清单的 basis 仍记完整的 8 张：上限是供应商属性，不进 basis，被去尾的道具仍是该产物的依据
        entry = ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_storyboard(1, ITEM_ID))
        assert entry is not None
        assert entry.basis_digest == _expected_basis(project_path, pm).digest
        assert call["prompt"] == CLAMPED_PROMPT

    async def test_clamped_storyboard_is_current_against_the_target_state(self, tmp_path, monkeypatch):
        """裁剪只影响实发与编号：登记后的分镜图按规划器重建的目标态比对仍是 current，不因裁剪被判 stale。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)

        await _run_storyboard(generator)

        # 替身 PM 不把分镜图指针写回磁盘剧本：按正式提交后的形态持久化，规划器才有条目可重建
        pm.script["segments"][1]["generated_assets"] = {"storyboard_image": f"storyboards/scene_{ITEM_ID}.png"}
        persist_active_fake_project(pm, register_script=False)
        resolver = active_artifact_currency_resolver(project_path, pm.load_project("demo"))
        comparison = resolver.compare(
            ArtifactKey.episode_storyboard(1, ITEM_ID), artifact_path=f"storyboards/scene_{ITEM_ID}.png"
        )
        assert comparison.status is ArtifactStatus.CURRENT

    async def test_limit_above_the_assembled_count_changes_nothing(self, tmp_path, monkeypatch):
        """OpenAI 口径（上限 16）：8 张全发，编号覆盖到图8。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=16)

        call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 8
        assert "图7为场景参考图；图8为道具参考图。" in call["prompt"]
        assert "图1握着图8立在图7门口" in call["prompt"]

    async def test_backend_without_a_declared_limit_never_clamps(self, tmp_path, monkeypatch):
        """未声明上限的后端（0）：不裁剪，与上限充裕时同一文本。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=0)

        call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 8
        assert "图8为道具参考图。" in call["prompt"]

    async def test_a_dropped_reference_changing_before_submit_still_aborts(self, tmp_path, monkeypatch):
        """被去尾的道具图仍是产物依据的输入：它的登记在提交前被删，复核照旧拦下，供应商未收到提交。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)
        hook_claim_recheck(
            monkeypatch,
            before=lambda: ProjectArtifactManifestAdapter(project_path).delete_entry(
                ArtifactKey.asset_sheet("prop", "玉佩")
            ),
        )

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        assert generator.image_calls == []

    async def test_a_kept_reference_changing_before_submit_still_aborts(self, tmp_path, monkeypatch):
        """仍随请求发出的场景图（图7）在提交前被删登记：复核照旧拦下，供应商未收到提交。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)
        hook_claim_recheck(
            monkeypatch,
            before=lambda: ProjectArtifactManifestAdapter(project_path).delete_entry(
                ArtifactKey.asset_sheet("scene", "祠堂")
            ),
        )

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        assert generator.image_calls == []


class TestLimitComesFromTheResolvedBackend:
    """不替换 image lane：真实 resolve_generation_context 构造真实 Vidu backend，上限由它声明。"""

    @pytest.fixture
    async def vidu_lane(self, db_factory, monkeypatch):
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)
        generation_context.invalidate_backend_cache()
        generation_context._backend_cache._locks.clear()

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            assert (provider_id, media_type) == ("vidu", "image")
            return ViduImageBackend(api_key="k", model=model_id, base_url="https://api.vidu.com/ent/v2")

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        yield
        generation_context.invalidate_backend_cache()
        generation_context._backend_cache._locks.clear()

    async def test_vidu_receives_seven_images_and_a_prompt_numbered_to_match(
        self, tmp_path, monkeypatch, vidu_lane, caplog
    ):
        """建任务请求以 400 短路（后端折成 ProviderRejectedError），只看真实发出的请求体。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        pm.project["image_provider_i2i"] = "vidu/viduq2"
        (project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_context, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *_a, **_kw: None)

        with capture_http() as router:
            route = router.post("https://api.vidu.com/ent/v2/reference2image").mock(
                return_value=httpx.Response(400, json={"code": 1013, "message": "nope"})
            )
            with caplog.at_level(logging.WARNING), pytest.raises(httpx.HTTPStatusError):
                await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        body = json.loads(route.calls.last.request.content)
        assert len(body["images"]) == 7
        assert body["prompt"] == CLAMPED_PROMPT
        # 裁剪发生在编排层，Vidu 自己的兜底截断在正常路径下不再触发
        assert "超过 backend=viduq2 上限 7" in caplog.text
        assert "Vidu 参考图数量" not in caplog.text
