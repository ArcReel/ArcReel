"""Tests for CostEstimationService."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.resolver import ConfigResolver
from lib.db.base import Base
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.providers import PROVIDER_GEMINI
from server.services.cost_estimation import CostEstimationService


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_call(
    db_factory,
    project_name: str,
    call_type: str,
    model: str,
    *,
    provider: str = PROVIDER_GEMINI,
    resolution: str | None = None,
    segment_id: str | None = None,
    output_path: str | None = None,
    usage_tokens: int | None = None,
) -> None:
    """直连 UsageRepository 写入一条已完成调用记录（等价于旧 UsageTracker 的种子写法）。"""
    async with db_factory() as session:
        repo = UsageRepository(session)
        cid = await repo.start_call(
            project_name=project_name,
            call_type=call_type,
            model=model,
            provider=provider,
            resolution=resolution,
            segment_id=segment_id,
        )
        await repo.finish_call(
            cid,
            status="success",
            settlement=SettlementInput(usage_tokens=usage_tokens),
            output_path=output_path,
        )


def _make_script(
    episode: int,
    segment_ids: list[str],
    durations: list[int],
    generated_assets_overrides: list[dict] | None = None,
) -> dict:
    """Helper to create a narration episode script dict."""
    default_assets = {"storyboard_image": None, "video_clip": None, "status": "pending"}
    segments = []
    for i, (sid, dur) in enumerate(zip(segment_ids, durations)):
        assets = {**default_assets}
        if generated_assets_overrides and i < len(generated_assets_overrides):
            assets.update(generated_assets_overrides[i])
        segments.append(
            {
                "segment_id": sid,
                "episode": episode,
                "duration_seconds": dur,
                "segment_break": False,
                "novel_text": "text",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "medium", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "aa"},
                "transition_to_next": "cut",
                "generated_assets": assets,
            }
        )
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": "narration",
        "duration_seconds": sum(durations),
        "summary": "test",
        "novel": {"title": "t", "chapter": "c"},
        "segments": segments,
    }


def _make_ad_script(shot_ids: list[str], durations: list[int]) -> dict:
    """Helper to create an ad episode script dict (平铺 shots[])."""
    shots = []
    for sid, dur in zip(shot_ids, durations, strict=True):
        shots.append(
            {
                "shot_id": sid,
                "section": "hook",
                "duration_seconds": dur,
                "voiceover_text": "口播文案" * 10,
                "products_in_shot": [],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "medium", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "aa"},
                "transition_to_next": "cut",
                "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
            }
        )
    return {
        "episode": 1,
        "title": "Ad",
        "content_mode": "ad",
        "duration_seconds": sum(durations),
        "novel": {"title": "t", "chapter": "c"},
        "shots": shots,
    }


class TestCostEstimationService:
    async def test_estimate_single_episode(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S002"], [6, 8])}

        result = await service.compute(project_data, scripts, project_name="test")

        assert len(result["episodes"]) == 1
        ep = result["episodes"][0]
        assert len(ep["segments"]) == 2
        for seg in ep["segments"]:
            assert "image" in seg["estimate"]
            assert "video" in seg["estimate"]
            for cost in seg["estimate"].values():
                assert isinstance(cost, dict)
                assert all(isinstance(v, (int, float)) for v in cost.values())

    async def test_actual_costs_included(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image-preview",
            resolution="1K",
            segment_id="E1S001",
            output_path="a.png",
        )

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="proj")

        seg = result["episodes"][0]["segments"][0]
        assert seg["actual"]["image"]["USD"] == pytest.approx(0.067)

    async def test_grid_actual_costs_apportioned_to_scenes(self, db_factory):
        """Grid actual cost should be split evenly among scenes sharing the grid_id."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_abc123"
        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]  # 9 scenes

        # Record grid image API call
        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image-preview",
            resolution="2K",
            segment_id=grid_id,
            output_path="g.png",
        )

        # All 9 scenes reference the same grid_id
        overrides = [{"grid_id": grid_id, "grid_cell_index": i} for i in range(9)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "grid",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj")

        # Each scene should get 1/9 of the grid cost
        expected_per_scene = round(0.101 / 9, 6)
        for seg in result["episodes"][0]["segments"]:
            assert seg["actual"]["image"]["USD"] == pytest.approx(expected_per_scene, abs=1e-5)

        # Episode total should equal the full grid cost
        ep_total_image = result["episodes"][0]["totals"]["actual"].get("image", {})
        assert ep_total_image.get("USD", 0) == pytest.approx(0.101, abs=1e-4)

        # Project totals should NOT have a separate "grid" bucket
        assert "grid" not in result["project_totals"]["actual"]
        # But should have the cost under "image"
        assert result["project_totals"]["actual"]["image"]["USD"] == pytest.approx(0.101, abs=1e-4)

    async def test_grid_partial_generation_some_without_grid_id(self, db_factory):
        """Scenes without grid_id should have empty actual image cost."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_partial"
        seg_ids = [f"E1S{i:03d}" for i in range(1, 6)]  # 5 scenes

        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image-preview",
            resolution="2K",
            segment_id=grid_id,
            output_path="g.png",
        )

        # Only first 3 scenes have grid_id
        overrides = [
            {"grid_id": grid_id, "grid_cell_index": 0},
            {"grid_id": grid_id, "grid_cell_index": 1},
            {"grid_id": grid_id, "grid_cell_index": 2},
            {},  # no grid_id
            {},  # no grid_id
        ]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "grid",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 5, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj")

        segments = result["episodes"][0]["segments"]
        expected = round(0.101 / 3, 6)
        for seg in segments[:3]:
            assert seg["actual"]["image"]["USD"] == pytest.approx(expected, abs=1e-5)
        for seg in segments[3:]:
            assert seg["actual"]["image"] == {}

    async def test_single_mode_unaffected_by_grid_logic(self, db_factory):
        """Single generation mode should be completely unaffected by grid apportionment."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image-preview",
            resolution="1K",
            segment_id="E1S001",
            output_path="a.png",
        )

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "single",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S002"], [6, 8])}

        result = await service.compute(project_data, scripts, project_name="proj")

        seg1 = result["episodes"][0]["segments"][0]
        assert seg1["actual"]["image"]["USD"] == pytest.approx(0.067)
        seg2 = result["episodes"][0]["segments"][1]
        assert seg2["actual"]["image"] == {}

    async def test_project_level_actual_split_by_asset_type(self, db_factory):
        """project-level image 成本应按 output_path 前缀拆分为 characters/scenes/props 三项。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        # 3 条 project-level image 调用，分别落在 characters / scenes / props
        for sub in ("characters", "scenes", "props"):
            await _seed_call(
                db_factory,
                "proj",
                "image",
                "gemini-3.1-flash-image-preview",
                resolution="1K",
                output_path=f"projects/proj/{sub}/a.png",
            )

        result = await service.compute(
            {"title": "T", "content_mode": "narration", "episodes": []},
            {},
            project_name="proj",
        )
        actual = result["project_totals"]["actual"]

        assert "characters" in actual and actual["characters"].get("USD", 0) > 0
        assert "scenes" in actual and actual["scenes"].get("USD", 0) > 0
        assert "props" in actual and actual["props"].get("USD", 0) > 0
        # 旧 key 不应出现
        assert "character_and_clue" not in actual

    async def test_dirty_script_skipped_with_warning(self, db_factory, caplog):
        """单集脏脚本(segments=null)不应让整个项目费用估算 5xx;脏集降级为 0 segments
        + warning,其他正常集仍参与估算。"""
        import logging

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [
                {"episode": 1, "title": "Ep1", "script_file": "ep1.json"},
                {"episode": 2, "title": "Ep2-dirty", "script_file": "ep2.json"},
                {"episode": 3, "title": "Ep3", "script_file": "ep3.json"},
            ],
        }
        # ep2 segments 是 null(脏数据)→ get_storyboard_items 抛 ScriptEditError
        dirty_script = {
            "episode": 2,
            "title": "Dirty",
            "content_mode": "narration",
            "summary": "t",
            "novel": {"title": "t", "chapter": "c"},
            "segments": None,  # 脏数据
        }
        scripts = {
            "ep1.json": _make_script(1, ["E1S001"], [6]),
            "ep2.json": dirty_script,
            "ep3.json": _make_script(3, ["E3S001"], [8]),
        }

        with caplog.at_level(logging.WARNING, logger="server.services.cost_estimation"):
            result = await service.compute(project_data, scripts, project_name="test")

        # 正常集 ep1 / ep3 都参与估算,脏集 ep2 仍出现但 segments 为空
        assert len(result["episodes"]) == 3
        eps_by_episode = {ep["episode"]: ep for ep in result["episodes"]}
        assert len(eps_by_episode[1]["segments"]) == 1
        assert len(eps_by_episode[2]["segments"]) == 0
        assert len(eps_by_episode[3]["segments"]) == 1

        # warning 显式标出哪一集被跳过
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ep2.json" in m for m in warnings), warnings

    async def test_audio_estimate_per_segment_by_characters(self, db_factory):
        """旁白配音预估 = novel_text 字符数 × 按字符费率；models 含 audio 条目。"""
        from lib.config.service import ConfigService

        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        script = _make_script(1, ["E1S001", "E1S002"], [6, 8])
        script["segments"][0]["novel_text"] = "字" * 100
        script["segments"][1]["novel_text"] = ""
        scripts = {"ep1.json": script}

        result = await service.compute(project_data, scripts, project_name="test")

        assert result["models"]["audio"] == {"provider": "dashscope", "model": "qwen3-tts-flash"}
        segments = result["episodes"][0]["segments"]
        # qwen3-tts-flash 按 ¥0.8/万字符：100 字 → 0.008 CNY
        assert segments[0]["estimate"]["audio"]["CNY"] == pytest.approx(0.008)
        # 无原文的段不产生旁白预估
        assert segments[1]["estimate"]["audio"] == {}
        # 集/项目两级合计纳入 audio
        assert result["episodes"][0]["totals"]["estimate"]["audio"]["CNY"] == pytest.approx(0.008)
        assert result["project_totals"]["estimate"]["audio"]["CNY"] == pytest.approx(0.008)

    async def test_audio_actual_costs_included(self, db_factory):
        """旁白实际费用按 segment 聚合进 actual.audio。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "proj",
            "audio",
            "qwen3-tts-flash",
            provider="dashscope",
            segment_id="E1S001",
            output_path="a.wav",
            usage_tokens=100,
        )

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="proj")

        seg = result["episodes"][0]["segments"][0]
        assert seg["actual"]["audio"]["CNY"] == pytest.approx(0.008)
        assert result["project_totals"]["actual"]["audio"]["CNY"] == pytest.approx(0.008)

    async def test_ad_storyboard_estimates_per_shot(self, db_factory):
        """ad 项目（分镜路径）：逐镜头返回分镜图 + 视频估值，聚合进集/项目两级合计。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "storyboard",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1", "E1S2"], [4, 6])}

        result = await service.compute(project_data, scripts, project_name="ad-proj")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1S1", "E1S2"]
        for seg in segments:
            assert seg["estimate"]["image"], seg
            assert seg["estimate"]["video"], seg
        # 视频估值随镜头时长变化（单镜头级估值非整集平摊）
        assert segments[0]["estimate"]["video"] != segments[1]["estimate"]["video"]
        assert result["episodes"][0]["totals"]["estimate"]["image"]
        assert result["project_totals"]["estimate"]["video"]

    async def test_ad_voiceover_does_not_produce_audio_estimate(self, db_factory):
        """ad 镜头口播文案不产生旁白配音预估（本期草稿导出后在剪映配音）。"""
        from lib.config.service import ConfigService

        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1"], [4])}

        result = await service.compute(project_data, scripts, project_name="ad-proj")

        assert result["episodes"][0]["segments"][0]["estimate"]["audio"] == {}

    async def test_ad_reference_video_skips_image_estimate(self, db_factory):
        """ad + 参考生视频路径跳过分镜步骤：不产生分镜图估值，视频估值保留。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1", "E1S2"], [4, 6])}

        result = await service.compute(project_data, scripts, project_name="ad-ref")

        segments = result["episodes"][0]["segments"]
        assert len(segments) == 2
        for seg in segments:
            assert seg["estimate"]["image"] == {}, seg
            assert seg["estimate"]["video"], seg
        assert result["project_totals"]["estimate"].get("image", {}) == {}
        assert result["project_totals"]["estimate"]["video"]

    async def test_empty_episodes(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        result = await service.compute(
            {"title": "T", "content_mode": "narration", "episodes": []}, {}, project_name="p"
        )

        assert result["episodes"] == []
        assert result["project_totals"]["estimate"] == {}

    async def test_cost_estimation_uses_t2i_default_when_split_fields_present(self, db_factory):
        """project 仅有 image_provider_t2i 时，cost estimation 用此值估算（T2I 是 cost estimation 锚点）。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "openai/gpt-image-1",
            "image_provider_i2i": "openai/gpt-image-1-edit",
            "episodes": [],
        }

        result = await service.compute(project_data, {}, project_name="test_split")

        # T2I field should be the canonical image cost estimation anchor
        assert result["models"]["image"]["provider"] == "openai"
        assert result["models"]["image"]["model"] == "gpt-image-1"

    async def test_cost_estimation_no_image_provider_falls_back_to_resolver(self, db_factory):
        """project 没有 image_provider_t2i 时，cost_estimation 不再自行 fallback I2I 或 legacy
        （legacy 由 ProjectManager.load_project 的 lazy upgrade 处理；I2I 和 T2I 是正交能力槽，
        互替会算到错误价目）。无 T2I 字段则使用 resolver 默认值。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            # 仅有 i2i 与 legacy 字段：cost_estimation 应忽略，落到 resolver 默认值
            "image_provider_i2i": "openai/gpt-image-1-edit",
            "image_backend": "gemini/gemini-2.0-flash-preview-image-generation",
            "episodes": [],
        }

        result = await service.compute(project_data, {}, project_name="test_no_t2i")

        # 正向锁定：项目无 T2I 字段时走 resolver；空 DB 没有任何 image provider，
        # cost_estimation 走 except 分支返回 ("unknown", "unknown")。
        # 这个契约同时排除掉 i2i 槽（gpt-image-1-edit）和 legacy（gemini-2.0-...）。
        assert result["models"]["image"]["provider"] == "unknown"
        assert result["models"]["image"]["model"] == "unknown"

    async def test_cost_estimation_resolve_resolution_exception_degrades_gracefully(self, db_factory, monkeypatch):
        """resolve_resolution 抛异常时预估整体降级而非中断，与 image/video/audio 三处 except 兜底同构。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        async def _raise(self, project, provider_id, model_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(ConfigResolver, "resolve_resolution", _raise)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [],
        }

        result = await service.compute(project_data, {}, project_name="test_resolution_exc")

        # compute() 不因 resolve_resolution 异常而中断，其余字段照常返回
        assert result["models"]["video"]["provider"] == "unknown"

    async def test_custom_provider_estimates_use_db_prices(self, db_factory):
        """自定义供应商预估：image/video/audio 单价来自 DB（与实际记账同源），估值按配置价格非零。"""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "img",
                        "display_name": "Img",
                        "endpoint": "openai-images",
                        "price_unit": "image",
                        "price_input": 0.05,
                        "currency": "USD",
                    },
                    {
                        "model_id": "vid",
                        "display_name": "Vid",
                        "endpoint": "openai-video",
                        "price_unit": "second",
                        "price_input": 0.10,
                        "currency": "USD",
                    },
                    {
                        "model_id": "aud",
                        "display_name": "Aud",
                        "endpoint": "openai-tts",
                        "price_unit": "character",
                        "price_input": 2.0,
                        "currency": "CNY",
                    },
                ],
            )
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "custom-1/img",
            "video_backend": "custom-1/vid",
            "audio_backend": "custom-1/aud",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        script = _make_script(1, ["E1S001"], [6])
        script["segments"][0]["novel_text"] = "字" * 10000  # 1 万字符
        scripts = {"ep1.json": script}

        result = await service.compute(project_data, scripts, project_name="test-custom")

        assert result["models"]["image"] == {"provider": "custom-1", "model": "img"}
        seg = result["episodes"][0]["segments"][0]
        # image：自定义供应商按张计费，flat 0.05 USD（不随 1K/2K 变化）
        assert seg["estimate"]["image"]["USD"] == pytest.approx(0.05)
        # video：时长 6s × 0.10 = 0.60 USD
        assert seg["estimate"]["video"]["USD"] == pytest.approx(0.60)
        # audio：10000 字符 / 10000 × 2.0 = 2.0 CNY
        assert seg["estimate"]["audio"]["CNY"] == pytest.approx(2.0)
        # 集/项目两级合计同步纳入
        assert result["project_totals"]["estimate"]["video"]["USD"] == pytest.approx(0.60)

    async def test_custom_provider_grid_estimate_uses_db_price(self, db_factory):
        """grid 模式下自定义供应商图片单价同样贯通（2K grid 单价 = DB flat 价）。"""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "img",
                        "display_name": "Img",
                        "endpoint": "openai-images",
                        "price_unit": "image",
                        "price_input": 0.09,
                        "currency": "USD",
                    },
                ],
            )
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]  # 9 scenes → 1 张 grid_9
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "grid",
            "image_provider_t2i": "custom-1/img",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9)}

        result = await service.compute(project_data, scripts, project_name="test-grid-custom")

        # 9 格拼成 1 张 grid，flat 0.09 USD 摊到 9 格 → 每格 0.01 USD
        segments = result["episodes"][0]["segments"]
        for seg in segments:
            assert seg["estimate"]["image"]["USD"] == pytest.approx(round(0.09 / 9, 6))
        # 集合计 = 满张单价 0.09 USD
        assert result["episodes"][0]["totals"]["estimate"]["image"]["USD"] == pytest.approx(0.09, abs=1e-4)

    async def test_custom_provider_without_price_degrades_to_zero(self, db_factory):
        """自定义供应商查无价格模型：预估降级为 0（记 debug 日志、不抛错），与现状降级口径一致。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "custom-99/ghost",  # DB 无此供应商/模型
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="test-noprice")

        # 断言解析到的仍是该自定义 provider/model，排除 resolver 回落 unknown 导致的同结果假阳性
        assert result["models"]["image"] == {"provider": "custom-99", "model": "ghost"}
        # 缺价 → calculate_cost 返回 0，_add_cost 过滤，image 估值为空且未抛错
        seg = result["episodes"][0]["segments"][0]
        assert seg["estimate"]["image"] == {}

    async def test_custom_provider_malformed_id_degrades_to_zero(self, db_factory):
        """畸形 custom- provider id（非数字后缀）：parse_provider_id 的 ValueError 需降级为 0，不抛错。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "custom-abc/ghost",  # 写入侧校验只查前缀，后缀非数字仍可能入库
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="test-malformed-id")

        # 断言解析到的仍是该畸形 provider/model，排除 resolver 回落 unknown 导致的同结果假阳性
        assert result["models"]["image"] == {"provider": "custom-abc", "model": "ghost"}
        seg = result["episodes"][0]["segments"][0]
        assert seg["estimate"]["image"] == {}
