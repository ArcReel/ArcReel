"""分镜图按实际图像通道检查全部参考图，验证拒绝点、请求内容和产物依据。"""

import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.backends.image_backends.grok import GrokImageBackend
from lib.backends.image_backends.openrouter import OpenRouterImageBackend
from lib.backends.image_backends.vidu import ViduImageBackend
from lib.backends.image_errors import ImageCapabilityError
from server.services.tasks import generation_context, generation_tasks
from tests.fakes import (
    bind_session_factory,
    hook_claim_recheck,
    use_backend_assembler,
    use_generation_context,
    use_project_manager,
)
from tests.http_capture import capture_http
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    fake_resolve_ctx,
    persist_active_fake_project,
    pm_with_eight_references,
    prepare_files,
)

ITEM_ID = "E1S02"
STORYBOARD_PAYLOAD = {"script_file": "episode_1.json", "prompt": "queued prompt"}
#: fake_resolve_ctx 默认 image lane 的 backend_model；超限错误报的是它，不是 registry 里的 provider 名。
FAKE_BACKEND_MODEL = "gpt-image-2"


def _patch_execution(monkeypatch, pm: _FakePM, generator: FakeGenerator, *, limit: int | None) -> None:
    use_project_manager(monkeypatch, pm)
    use_generation_context(monkeypatch, fake_resolve_ctx(generator, image_max_reference_images=limit))


async def _run_storyboard(generator: FakeGenerator) -> tuple[dict, dict]:
    """返回 ``(任务结果, 供应商收到的调用)``。"""
    result = await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)
    return result, generator.image_calls[0]


class TestStoryboardReferenceLimit:
    async def test_over_limit_references_block_before_submission(self, tmp_path, monkeypatch):
        """上限 7：报告第 8 张的逻辑名，并在供应商调用前拒绝。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)

        with pytest.raises(ImageCapabilityError) as caught:
            await _run_storyboard(generator)
        assert caught.value.params == {"model": FAKE_BACKEND_MODEL, "limit": 7, "count": 8, "names": "玉佩"}
        assert not generator.image_calls

    async def test_at_limit_storyboard_is_current_against_the_target_state(self, tmp_path, monkeypatch):
        """上限刚好覆盖装配集时，规划器重建的依据与执行生成的产物一致。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=8)

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

        result, call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 8
        assert "图7为场景参考图；图8为道具参考图。" in call["prompt"]
        assert "图1握着图8立在图7门口" in call["prompt"]
        # 没丢图就没有 warning：结果不带该键，任务列表不会给用户空提示
        assert "warnings" not in result

    async def test_backend_without_a_declared_limit_never_clamps(self, tmp_path, monkeypatch):
        """条目与通道都没有声明上限（未收录）：不裁剪，与上限充裕时同一文本。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=None)

        _result, call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 8
        assert "图8为道具参考图。" in call["prompt"]

    async def test_last_reference_changing_before_submit_still_aborts(self, tmp_path, monkeypatch):
        """最后一张道具图是产物依据的输入：它的登记在提交前被删，复核照旧拦下，供应商未收到提交。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=8)
        hook_claim_recheck(
            monkeypatch,
            before=lambda: ProjectArtifactManifestAdapter(project_path).delete_entry(
                ArtifactKey.asset_sheet("prop", "玉佩")
            ),
        )

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        assert generator.image_calls == []

    async def test_a_scene_reference_changing_before_submit_still_aborts(self, tmp_path, monkeypatch):
        """仍随请求发出的场景图（图7）在提交前被删登记：复核照旧拦下，供应商未收到提交。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=8)
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
        bind_session_factory(monkeypatch, db_factory)
        generation_context.reset_backend_cache_for_tests()

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            assert (provider_id, media_type) == ("vidu", "image")
            return ViduImageBackend(api_key="k", model=model_id, base_url="https://api.vidu.com/ent/v2")

        use_backend_assembler(monkeypatch, _assemble)
        yield
        generation_context.reset_backend_cache_for_tests()

    async def test_vidu_limit_blocks_before_any_submission(self, tmp_path, monkeypatch, vidu_lane, caplog):
        """检查真实通道声明驱动的拒绝和零次提交。"""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        pm.project["image_provider_i2i"] = "vidu/viduq2"
        (project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")
        use_project_manager(monkeypatch, pm)

        with capture_http() as router:
            route = router.post("https://api.vidu.com/ent/v2/reference2image").mock(
                return_value=httpx.Response(400, json={"code": 1013, "message": "nope"})
            )
            with pytest.raises(ImageCapabilityError, match="image_reference_images_exceeded"):
                await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)
            assert not route.called


class TestGrokLaneLimit:
    """真实 Grok 通道声明上限 5。"""

    @pytest.fixture
    async def grok_lane(self, db_factory, monkeypatch):
        bind_session_factory(monkeypatch, db_factory)
        generation_context.reset_backend_cache_for_tests()
        client = MagicMock()
        client.image.sample = AsyncMock(
            return_value=SimpleNamespace(respect_moderation=True, url="https://grok.test/out.png")
        )

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            assert (provider_id, media_type) == ("grok", "image")
            with patch("lib.backends.image_backends.grok.create_grok_client", return_value=client):
                return GrokImageBackend(api_key="k", model=model_id)

        use_backend_assembler(monkeypatch, _assemble)
        yield client
        generation_context.reset_backend_cache_for_tests()

    async def test_grok_limit_blocks_before_any_submission(self, tmp_path, monkeypatch, grok_lane):
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        pm.project["image_provider_i2i"] = "grok/grok-imagine-image"
        (project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")
        use_project_manager(monkeypatch, pm)
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")

        with capture_http() as router:
            router.get("https://grok.test/out.png").mock(return_value=httpx.Response(200, content=buffer.getvalue()))
            with pytest.raises(ImageCapabilityError) as caught:
                await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)
            assert caught.value.params["limit"] == 5
            grok_lane.image.sample.assert_not_awaited()


class TestOpenRouterCatalogLimit:
    """真实 OpenRouter backend：上限按模型目录声明的 input_references 给出，编排层按该值拒绝超限请求。"""

    MODEL = "vendor/three-refs"
    BASE_URL = "https://openrouter.ai/api/v1"

    @pytest.fixture
    async def openrouter_lane(self, db_factory, monkeypatch):
        bind_session_factory(monkeypatch, db_factory)
        generation_context.reset_backend_cache_for_tests()

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            assert media_type == "image"
            return OpenRouterImageBackend(api_key="k", model=self.MODEL, base_url=self.BASE_URL)

        use_backend_assembler(monkeypatch, _assemble)
        yield
        generation_context.reset_backend_cache_for_tests()

    async def test_three_reference_model_rejects_overflow_before_submission(
        self, tmp_path, monkeypatch, openrouter_lane
    ):
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        pm.project["image_provider_i2i"] = "vidu/viduq2"
        (project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")
        use_project_manager(monkeypatch, pm)
        catalog = {
            "data": [
                {
                    "id": self.MODEL,
                    "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 3}},
                }
            ]
        }
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
        image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

        with capture_http() as router:
            models = router.get(f"{self.BASE_URL}/images/models").mock(return_value=httpx.Response(200, json=catalog))
            route = router.post(f"{self.BASE_URL}/images").mock(
                return_value=httpx.Response(200, json={"data": [{"b64_json": image_b64}]})
            )
            with pytest.raises(ImageCapabilityError) as caught:
                await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)
            assert caught.value.params["limit"] == 3
            assert not route.called
            assert models.call_count == 1
