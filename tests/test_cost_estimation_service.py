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
    cost_amount: float | None = None,
    currency: str | None = None,
) -> None:
    """直连 UsageRepository 写入一条已完成调用记录（等价于旧 UsageTracker 的种子写法）。

    ``cost_amount`` 非空时绕过按 model 定价表的自动计算，直接结算为该值——用于不依赖
    具体供应商定价配置、只关心分摊/聚合逻辑的用例（如 unit 费用按镜头摊回）。
    """
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
            settlement=SettlementInput(usage_tokens=usage_tokens, cost_amount=cost_amount, currency=currency),
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


def _make_reference_video_script(episode: int, content_mode: str, unit_specs: list[tuple[str, int]]) -> dict:
    """Helper to create a narration/drama + reference_video episode script dict (video_units[])."""
    units = []
    for unit_id, duration in unit_specs:
        units.append(
            {
                "unit_id": unit_id,
                "shots": [{"duration": duration, "text": "t"}],
                "references": [],
                "duration_seconds": duration,
                "duration_override": False,
                "transition_to_next": "cut",
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
        )
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": content_mode,
        "generation_mode": "reference_video",
        "duration_seconds": sum(d for _, d in unit_specs),
        "novel": {"title": "t", "chapter": "c"},
        "video_units": units,
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
        assert "unassigned" not in result["episodes"][0]["totals"]["actual"]
        assert "unassigned" not in result["project_totals"]["actual"]

    @pytest.mark.integration
    async def test_duplicate_unit_id_does_not_double_count_actual_cost(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "duplicate-unit",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=1.25,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {
            "ep1.json": _make_reference_video_script(
                1,
                "narration",
                [("E1U1", 5), ("E1U1", 5)],
            )
        }

        result = await service.compute(project_data, scripts, project_name="duplicate-unit")

        assert result["episodes"][0]["totals"]["actual"]["video"]["USD"] == pytest.approx(1.25)
        assert result["project_totals"]["actual"]["video"]["USD"] == pytest.approx(1.25)

    @pytest.mark.integration
    async def test_deleted_unit_actual_cost_is_reconciled_as_unassigned(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "deleted-unit",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=1.25,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U2", 5)])}

        result = await service.compute(project_data, scripts, project_name="deleted-unit")

        episode = result["episodes"][0]
        assert episode["totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.25)
        assert result["project_totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.25)

    @pytest.mark.integration
    async def test_replaced_script_reconciles_all_historical_actual_costs(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        for unit_id, amount, call_type in (
            ("E1U1", 0.4, "image"),
            ("E1U2", 1.6, "video"),
            ("E1U3", 0.2, "audio"),
        ):
            await _seed_call(
                db_factory,
                "replaced-script",
                call_type,
                "historical-model",
                segment_id=unit_id,
                cost_amount=amount,
                currency="USD",
            )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U9", 5)])}

        result = await service.compute(project_data, scripts, project_name="replaced-script")

        episode = result["episodes"][0]
        assert episode["totals"]["actual"]["unassigned"]["USD"] == pytest.approx(2.2)
        assert result["project_totals"]["actual"]["unassigned"]["USD"] == pytest.approx(2.2)

    @pytest.mark.integration
    async def test_missing_script_episode_still_gets_history_row(self, db_factory):
        """剧本文件缺失的集不进入估算，但它的历史支出仍要落在该集行上。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "missing-script",
            "video",
            "historical-model",
            segment_id="E2U1",
            cost_amount=1.5,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [
                {"episode": 1, "title": "Ep1", "script_file": "ep1.json"},
                {"episode": 2, "title": "Ep2", "script_file": "ep2.json"},
            ],
        }
        # ep2.json 不在 scripts 里：模拟剧本文件已丢失、集元数据仍在 project.json 的状态
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        result = await service.compute(project_data, scripts, project_name="missing-script")

        episodes = result["episodes"]
        assert [ep["episode"] for ep in episodes] == [1, 2]
        assert episodes[1]["title"] == "Ep2"
        assert episodes[1]["segments"] == []
        assert episodes[1]["totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.5)
        assert result["project_totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.5)

    @pytest.mark.integration
    async def test_null_segment_history_counts_as_unassigned(self, db_factory):
        """segment_id 为空的历史 video/audio 与无法归类的图，同样是真实支出。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "null-segment",
            "video",
            "historical-model",
            segment_id=None,
            cost_amount=2.0,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "null-segment",
            "audio",
            "historical-model",
            segment_id=None,
            cost_amount=0.5,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "null-segment",
            "image",
            "historical-model",
            segment_id=None,
            output_path="misc/legacy.png",
            cost_amount=0.3,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "null-segment",
            "image",
            "historical-model",
            segment_id=None,
            output_path="characters/hero.png",
            cost_amount=0.7,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        result = await service.compute(project_data, scripts, project_name="null-segment")

        project_actual = result["project_totals"]["actual"]
        # 资产图按类型单列，其余（other 图 + video + audio）归入未归属
        assert project_actual["characters"]["USD"] == pytest.approx(0.7)
        assert project_actual["unassigned"]["USD"] == pytest.approx(2.8)
        assert "unassigned" not in result["episodes"][0]["totals"]["actual"]

    @pytest.mark.integration
    async def test_unit_id_named_like_project_sentinel_is_not_double_counted(self, db_factory):
        """剧本用哨兵键字面量作 unit_id 时，它的支出只算一次（哨兵键不与真实 ID 共用取值）。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "sentinel-unit",
            "video",
            "historical-model",
            segment_id="__project__",
            cost_amount=3.0,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("__project__", 5)])}

        result = await service.compute(project_data, scripts, project_name="sentinel-unit")

        project_actual = result["project_totals"]["actual"]
        assert project_actual["video"]["USD"] == pytest.approx(3.0)
        assert "unassigned" not in project_actual

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

    @pytest.mark.integration
    async def test_claimed_key_keeps_unconsumed_cost_types_as_unassigned(self, db_factory):
        """认领粒度到 (记账 key, 类型)：宫格 key 上只消费 image，同 key 的 video 仍须计入未归属。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_mixed"
        seg_ids = ["E1S001", "E1S002"]
        await _seed_call(
            db_factory,
            "mixed-key",
            "image",
            "historical-model",
            segment_id=grid_id,
            cost_amount=0.6,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "mixed-key",
            "video",
            "historical-model",
            segment_id=grid_id,
            cost_amount=1.4,
            currency="USD",
        )

        overrides = [{"grid_id": grid_id, "grid_cell_index": i} for i in range(2)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "grid",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6, 6], generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="mixed-key")

        project_actual = result["project_totals"]["actual"]
        assert project_actual["image"]["USD"] == pytest.approx(0.6)
        assert project_actual["unassigned"]["USD"] == pytest.approx(1.4)

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
        """ad + 参考生视频路径跳过分镜步骤：不产生分镜图估值，视频估值按 unit 计费后摊回镜头。

        两个镜头（4s + 6s）未受供应商时长上限约束，派生分组合并为同一个 reference_unit——
        计费颗粒度与实际生成一致（``execute_reference_video_task`` 按 unit 整体送 provider，
        不是逐镜头分别计费），但输出仍按镜头 ID 分条，前端才索引得到。
        """
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
        assert [seg["segment_id"] for seg in segments] == ["E1S1", "E1S2"]
        assert [seg["duration_seconds"] for seg in segments] == [4, 6]
        for seg in segments:
            assert seg["estimate"]["image"] == {}
            assert seg["estimate"]["video"]
        assert result["project_totals"]["estimate"].get("image", {}) == {}
        assert result["project_totals"]["estimate"]["video"]

    async def test_ad_reference_video_apportions_unit_cost_across_shots(self, db_factory, monkeypatch):
        """unit 费用均摊到成员镜头，除不尽时余数补末镜，合计与 unit 原值分文不差。

        3 个镜头分摊一笔按 8s 计的 unit 费用（USD 1.6 / 3 除不尽）：前两镜各取 6 位小数的
        均值，末镜吃下余数，三者之和须等于按 unit 整体计费的原值——否则镜头数越多，
        用户看到的项目总价偏离真实计费越远。
        """
        from server.services import cost_estimation as cost_estimation_module
        from server.services.reference_video_tasks import ProjectDurationContext

        async def _fake_ctx(project):
            return ProjectDurationContext(
                supported_durations=(8,), resolution=None, provider_id="veo", model_name="veo-3.1"
            )

        monkeypatch.setattr(cost_estimation_module, "resolve_project_duration_context", _fake_ctx)

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1", "E1S2", "E1S3"], [2, 2, 2])}

        result = await service.compute(project_data, scripts, project_name="ad-ref-split")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1S1", "E1S2", "E1S3"]
        currency = next(iter(segments[0]["estimate"]["video"]))
        shares = [seg["estimate"]["video"][currency] for seg in segments]
        assert shares[0] == shares[1], "除不尽时只有末镜与其它镜头不同"
        assert shares[2] != shares[0], "余数应落在末镜上，否则合计会缺一截"
        # 分摊后的合计 == 集合计 == 按 unit 整体计费的原值
        ep_total = result["episodes"][0]["totals"]["estimate"]["video"][currency]
        assert round(sum(shares), 6) == ep_total
        assert ep_total == result["project_totals"]["estimate"]["video"][currency]

    async def test_ad_reference_video_apportions_actual_cost_across_shots(self, db_factory):
        """实际费用同样按 unit 分摊：usage 记录的 segment_id 是 unit ID，不是镜头 ID。

        ``act_by_shot`` 这条链路此前零覆盖——只断言 estimate 侧无法区分「actual 按 unit
        正确分摊」与「actual_by_segment.get(unit_id) 查询本身有误、恒为空」。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "ad-ref-actual",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=1.6,
            currency="USD",
        )

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1", "E1S2", "E1S3"], [2, 2, 2])}

        result = await service.compute(project_data, scripts, project_name="ad-ref-actual")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1S1", "E1S2", "E1S3"]
        shares = [seg["actual"]["video"]["USD"] for seg in segments]
        assert all(shares), "actual_by_segment.get(unit_id) 查不到时分摊结果会全是空 dict"
        assert round(sum(shares), 6) == 1.6
        assert result["episodes"][0]["totals"]["actual"]["video"]["USD"] == pytest.approx(1.6)
        assert result["project_totals"]["actual"]["video"]["USD"] == pytest.approx(1.6)

    async def test_ad_reference_video_merges_shot_level_legacy_video_actual(self, db_factory):
        """切换到 reference_video 前，某镜头已在 storyboard 模式产生过视频实付（按 shot_id
        记账），切换后与 unit 分摊额是两笔独立支出，须相加而非互相替换。

        只对其中一个镜头（E1S2）播历史视频费用，断言只有它的 actual.video 比其余镜头
        多出这一截，其余镜头仍只有 unit 分摊份额——防止实现退化成「谁覆盖谁」。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "ad-ref-legacy-video",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=1.5,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "ad-ref-legacy-video",
            "video",
            "sora-2",
            provider="openai",
            segment_id="E1S2",
            cost_amount=0.4,
            currency="USD",
        )

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1", "E1S2", "E1S3"], [2, 2, 2])}

        result = await service.compute(project_data, scripts, project_name="ad-ref-legacy-video")

        segments = {seg["segment_id"]: seg for seg in result["episodes"][0]["segments"]}
        apportioned_share = segments["E1S1"]["actual"]["video"]["USD"]
        assert segments["E1S3"]["actual"]["video"]["USD"] == pytest.approx(apportioned_share)
        assert segments["E1S2"]["actual"]["video"]["USD"] == pytest.approx(apportioned_share + 0.4)
        # 集/项目合计须把这笔历史支出也算进去，不能因为它挂在 shot_id 下就被漏计
        ep_total = result["episodes"][0]["totals"]["actual"]["video"]["USD"]
        assert ep_total == pytest.approx(1.5 + 0.4)
        assert result["project_totals"]["actual"]["video"]["USD"] == pytest.approx(1.5 + 0.4)

    async def test_ad_reference_video_estimate_uses_rounded_up_unit_duration(self, db_factory, monkeypatch):
        """取档向上的 unit：预估金额按取档后的秒数（8s）计，而非剧本原始总时长（5s）。

        金额必须一起断言：只断言 ``duration_seconds`` 无法区分「按 8s 计价」与「秒数传对了
        但定价查不到、估值静默为空」——后者在本函数里被吞成 debug 日志。
        """
        from server.services import cost_estimation as cost_estimation_module
        from server.services.reference_video_tasks import ProjectDurationContext

        def _patch_ctx(durations: tuple[int, ...]):
            async def _fake_ctx(project):
                return ProjectDurationContext(
                    supported_durations=durations, resolution=None, provider_id="veo", model_name="veo-3.1"
                )

            monkeypatch.setattr(cost_estimation_module, "resolve_project_duration_context", _fake_ctx)

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1"], [5])}

        _patch_ctx((8,))
        rounded = await service.compute(project_data, scripts, project_name="ad-ref-round")
        # 对照组：无档位约束（unconstrained）时按剧本原始 5s 计，验证金额确实随取档后的秒数走
        _patch_ctx(())
        unconstrained = await service.compute(project_data, scripts, project_name="ad-ref-round")

        seg = rounded["episodes"][0]["segments"][0]
        base_seg = unconstrained["episodes"][0]["segments"][0]
        # 单镜头 unit：展示的是镜头自身编排时长（取档发生在 unit 级），两次都是剧本原值 5s
        assert seg["segment_id"] == "E1S1"
        assert seg["duration_seconds"] == 5
        assert base_seg["duration_seconds"] == 5
        assert seg["estimate"]["video"], "取档后仍应算出视频估值，空 dict 说明定价路径被静默吞掉"
        assert base_seg["estimate"]["video"]
        # 视频按秒计价，取档到 8s 的估值须严格高于按剧本 5s 的估值（低估用户实付正是本票要修的缺陷）
        assert sum(seg["estimate"]["video"].values()) > sum(base_seg["estimate"]["video"].values())
        assert seg["estimate"]["video"] == rounded["episodes"][0]["totals"]["estimate"]["video"]

    async def test_ad_reference_video_fallback_derivation_applies_max_unit_duration(self, db_factory, monkeypatch):
        """剧本尚未派生 reference_units 时，估算现推的分组与执行侧同受供应商时长上限约束。

        不施加上限会把本该分成多个 unit 的镜头并成一个，取档命中最大档位后按一次计费，
        总估值成倍偏低。上限取自同一份能力解析（``ProjectDurationContext.max_duration``），
        不额外触发 IO。
        """
        from server.services import cost_estimation as cost_estimation_module
        from server.services.reference_video_tasks import ProjectDurationContext

        async def _fake_ctx(project):
            return ProjectDurationContext(
                supported_durations=(8,),
                resolution=None,
                provider_id="veo",
                model_name="veo-3.1",
                max_duration=8,
            )

        monkeypatch.setattr(cost_estimation_module, "resolve_project_duration_context", _fake_ctx)

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        # 三镜头共 18s，8s 上限下派生为 3 个 unit（6+6 超限，故逐镜一组）；无上限时会并成 1 个
        scripts = {"ep1.json": _make_ad_script(["E1S1", "E1S2", "E1S3"], [6, 6, 6])}

        result = await service.compute(project_data, scripts, project_name="ad-ref-maxdur")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1S1", "E1S2", "E1S3"]
        # 三个单镜头 unit 各按 8s 档位计费一次；若分组不受上限约束会并成 1 个 unit、
        # 只计一次 8s（18s 超出最大档位取 DOWN），总额恰好是这里的三分之一
        currency = next(iter(segments[0]["estimate"]["video"]))
        per_unit = segments[0]["estimate"]["video"][currency]
        assert [seg["estimate"]["video"][currency] for seg in segments] == [per_unit] * 3
        assert result["episodes"][0]["totals"]["estimate"]["video"][currency] == round(per_unit * 3, 6)

    @pytest.mark.integration
    async def test_narration_reference_video_produces_nonzero_video_estimate(self, db_factory):
        """narration + reference_video 集的视频估值不应恒为 0（`get_storyboard_items` 对该
        generation_mode 恒返回空列表，之前的估算循环遍历它，等于永远算不出视频费用）。

        unit 本身就是展示颗粒度（``Shot`` 无独立 ID），故 segment_id 直接是 unit_id，
        不像 ad 路径那样需要摊回成员镜头。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])}

        result = await service.compute(project_data, scripts, project_name="narration-ref")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2"]
        assert [seg["duration_seconds"] for seg in segments] == [6, 8]
        for seg in segments:
            assert seg["estimate"]["image"] == {}
            assert seg["estimate"]["audio"] == {}
            assert seg["estimate"]["video"]
        assert result["project_totals"]["estimate"].get("image", {}) == {}
        assert result["project_totals"]["estimate"]["video"]

    @pytest.mark.integration
    async def test_drama_reference_video_actual_cost_matches_unit_id(self, db_factory):
        """actual 侧直接按 unit_id 匹配，不需要摊分——reference_videos 的记账 resource_id
        即 unit_id，与本路径输出的 segment_id 同一 identity。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "drama-ref-actual",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=0.8,
            currency="USD",
        )

        project_data = {
            "title": "Drama",
            "content_mode": "drama",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "drama", [("E1U1", 8)])}

        result = await service.compute(project_data, scripts, project_name="drama-ref-actual")

        segments = result["episodes"][0]["segments"]
        assert segments[0]["segment_id"] == "E1U1"
        assert segments[0]["actual"]["video"]["USD"] == pytest.approx(0.8)
        assert result["episodes"][0]["totals"]["actual"]["video"]["USD"] == pytest.approx(0.8)
        assert result["project_totals"]["actual"]["video"]["USD"] == pytest.approx(0.8)

    @pytest.mark.integration
    async def test_narration_reference_video_estimate_uses_rounded_up_unit_duration(self, db_factory, monkeypatch):
        """取档向上的 unit：预估金额按取档后的秒数（8s）计，而非剧本原始总时长（5s）。"""
        from server.services import cost_estimation as cost_estimation_module
        from server.services.reference_video_tasks import ProjectDurationContext

        def _patch_ctx(durations: tuple[int, ...]):
            async def _fake_ctx(project):
                return ProjectDurationContext(
                    supported_durations=durations, resolution=None, provider_id="veo", model_name="veo-3.1"
                )

            monkeypatch.setattr(cost_estimation_module, "resolve_project_duration_context", _fake_ctx)

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        _patch_ctx((8,))
        rounded = await service.compute(project_data, scripts, project_name="narration-ref-round")
        _patch_ctx(())
        unconstrained = await service.compute(project_data, scripts, project_name="narration-ref-round")

        seg = rounded["episodes"][0]["segments"][0]
        base_seg = unconstrained["episodes"][0]["segments"][0]
        assert seg["segment_id"] == "E1U1"
        assert seg["duration_seconds"] == 5
        assert base_seg["duration_seconds"] == 5
        assert seg["estimate"]["video"]
        assert base_seg["estimate"]["video"]
        assert sum(seg["estimate"]["video"].values()) > sum(base_seg["estimate"]["video"].values())
        assert seg["estimate"]["video"] == rounded["episodes"][0]["totals"]["estimate"]["video"]

    @pytest.mark.integration
    async def test_narration_reference_video_falls_back_when_script_still_storyboard(self, db_factory):
        """项目已切到 reference_video、该集剧本还是分镜骨架时，估算沿用分镜路径而非归零。

        narration/drama 的生成侧按剧本自身的 generation_mode 戳判定，此状态下实际入队的
        仍是分镜任务；若估算只看项目级戳就会找不到 video_units 而给出一份全零预估。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {
            "ep1.json": {
                "episode": 1,
                "title": "Episode 1",
                "content_mode": "narration",
                "duration_seconds": 10,
                "novel": {"title": "t", "chapter": "c"},
                "segments": [
                    {"segment_id": "E1S1", "duration": 5, "narration": "n1", "visual_prompt": "v1"},
                    {"segment_id": "E1S2", "duration": 5, "narration": "n2", "visual_prompt": "v2"},
                ],
            }
        }

        result = await service.compute(project_data, scripts, project_name="narration-transitional")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1S1", "E1S2"]
        assert result["project_totals"]["estimate"]["image"]
        assert result["project_totals"]["estimate"]["video"]

    @pytest.mark.integration
    async def test_narration_reference_video_falls_back_when_video_units_stale(self, db_factory):
        """剧本残留切换前/迁移中间态的 ``video_units``、自身戳非 reference_video 时，
        估算仍按分镜路径走，不因 units 非空而误判为 unit 路径。

        与 ``test_narration_reference_video_falls_back_when_script_still_storyboard`` 的区别：
        该用例的剧本没有 video_units；本用例剧本残留了非空 video_units，验证判定看的是剧本
        自身的 ``generation_mode`` 戳而非 ``bool(video_units)``。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6)])
        script.pop("generation_mode")
        script["segments"] = [
            {"segment_id": "E1S1", "duration_seconds": 5, "narration": "n1", "visual_prompt": "v1"},
        ]
        scripts = {"ep1.json": script}

        result = await service.compute(project_data, scripts, project_name="narration-stale-units")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1S1"]
        assert result["project_totals"]["estimate"]["image"]
        assert result["project_totals"]["estimate"]["video"]

    @pytest.mark.integration
    async def test_narration_reference_video_estimate_follows_script_stamp_over_effective_mode(self, db_factory):
        """项目级 ``generation_mode`` 事后回退到 storyboard，但该集剧本仍保留切换前的
        ``reference_video`` 戳时，估算须跟随剧本戳走 unit 路径，不因项目级戳回退而误判回落
        分镜——实际入队（``_is_reference_script``）只认剧本自身的戳，从不读 ``effective_mode``。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 6)])}

        result = await service.compute(project_data, scripts, project_name="narration-reverted-project-mode")

        segments = result["episodes"][0]["segments"]
        assert segments[0]["segment_id"] == "E1U1"
        assert segments[0]["estimate"]["video"]
        assert result["project_totals"]["estimate"]["video"]

    @pytest.mark.integration
    async def test_narration_reference_video_estimate_skips_unenqueueable_units(self, db_factory):
        """没有 shots 或拼接文本为空的 unit 不可入队（``enqueue_videos.py::_reference_unit_spec``
        对没有 shots 的 unit 直接拒绝，``TaskSpec.from_request`` 对空提示词同样拒绝），这类 unit
        不产生新预估——但 unit 整条仍要保留在结果里、纳入汇总：不可入队只影响能否产生新预估，
        不影响该 unit 是否曾经成功生成过。已有实付的 unit（曾成功生成、之后被编辑成空 shots）
        其历史支出不能因此从合计里消失。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6)])
        script["video_units"].append(
            {
                "unit_id": "E1U2",
                "shots": [],
                "references": [],
                "duration_seconds": 5,
                "duration_override": False,
                "transition_to_next": "cut",
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
        )
        script["video_units"].append(
            {
                "unit_id": "E1U3",
                "shots": [{"duration": 5, "text": "   "}],
                "references": [],
                "duration_seconds": 5,
                "duration_override": False,
                "transition_to_next": "cut",
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
        )
        scripts = {"ep1.json": script}
        await _seed_call(
            db_factory,
            "narration-unenqueueable-units",
            "video",
            "veo-3.1-lite-generate-preview",
            segment_id="E1U2",
            cost_amount=1.23,
            currency="USD",
        )

        result = await service.compute(project_data, scripts, project_name="narration-unenqueueable-units")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2", "E1U3"]
        by_id = {seg["segment_id"]: seg for seg in segments}
        assert by_id["E1U2"]["estimate"]["video"] == {}
        assert by_id["E1U2"]["actual"]["video"] == {"USD": 1.23}
        assert by_id["E1U3"]["estimate"]["video"] == {}
        assert result["project_totals"]["actual"]["video"] == {"USD": 1.23}

    @pytest.mark.integration
    async def test_narration_reference_video_estimate_skips_unit_with_malformed_duration(self, db_factory):
        """agent/外部编辑过的剧本可能写入非数值 ``duration_seconds``（字符串、list、dict 等）。
        SDK 侧入队预检（``enqueue_videos.py``）对每个 unit 单独 catch ``ValueError`` 跳过，
        估算须跟随同一容错口径——一个 unit 的脏时长不能让整个项目估算 500，拖累其余正常集，
        其余正常 unit 仍要继续产生预估。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8), ("E1U3", 8)])
        script["video_units"][0]["duration_seconds"] = "bad"
        script["video_units"][1]["duration_seconds"] = ["not", "a", "number"]

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-bad-duration")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2", "E1U3"]
        assert segments[0]["estimate"]["video"] == {}
        assert segments[1]["estimate"]["video"] == {}
        assert segments[2]["estimate"]["video"]

    @pytest.mark.integration
    async def test_narration_reference_video_estimate_skips_unit_with_non_list_shots(self, db_factory):
        """agent/外部编辑过的剧本可能把 ``shots`` 裸写成非 list 的 truthy 值（如 ``true``/``1``）。
        ``assemble_shots_text`` 对非 list 输入遍历会抛 ``TypeError``，该异常发生在时长解析
        之前，必须在拼接前先做类型检查，否则同样会让整个项目估算 500。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])
        script["video_units"][0]["shots"] = True

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-non-list-shots")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2"]
        assert segments[0]["estimate"]["video"] == {}
        assert segments[1]["estimate"]["video"]

    @pytest.mark.integration
    async def test_narration_reference_video_estimate_rejects_non_string_unit_id(self, db_factory):
        """agent/外部编辑过的剧本可能把 ``unit_id`` 裸写成非字符串 truthy 值（如数字/布尔）。
        入队执行时（``execute_reference_video_task``）按字符串 resource_id 与剧本原始
        （未转型）值比较定位 unit，类型不等会导致 "unit not found"；估算侧若把该值
        str() 强转后正常计费，会展示一笔实际跑不起来的费用，必须原本就是字符串才计价。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])
        script["video_units"][0]["unit_id"] = 1

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-non-str-unit-id")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U2"]
        assert segments[0]["estimate"]["video"]

    @pytest.mark.integration
    async def test_narration_reference_video_estimate_handles_token_priced_video_model(self, db_factory):
        """按 token 计费的视频模型（Ark/Seedance）也要能算出非零视频预估。

        ``_estimate_unit_video_cost`` 只传 ``duration_seconds``，若不换算 usage_tokens，
        ``PerTokenVideo`` 定价形状会因 ``usage_tokens`` 缺失恒算出 0。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_backend": "ark",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 6)])}

        result = await service.compute(project_data, scripts, project_name="narration-ark-token")

        segments = result["episodes"][0]["segments"]
        assert segments[0]["segment_id"] == "E1U1"
        assert segments[0]["estimate"]["video"]
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

    @pytest.mark.integration
    @pytest.mark.parametrize(
        ("video_backend", "configured_generate_audio", "expected_usd"),
        [
            # AI Studio 无 audio-off 档，供应商恒按含音价出账：无论开关状态，预估都须落在
            # veo-3.1-lite-generate-preview 1080p 的含音价 0.08 USD/s。
            ("gemini-aistudio", True, 0.08),
            ("gemini-aistudio", False, 0.08),
            # Vertex 有独立的 audio-off 档，不受 AI Studio 修正影响，预估随开关走
            # （veo-3.1-fast-generate-001 1080p：含音 0.12 / 无音 0.10 USD/s）。
            ("gemini-vertex", True, 0.12),
            ("gemini-vertex", False, 0.10),
        ],
    )
    async def test_video_estimate_generate_audio_by_provider(
        self, db_factory, monkeypatch, video_backend, configured_generate_audio, expected_usd
    ):
        """费用预估在所有 (provider, audio 开关) 组合下与供应商实际出账口径一致。

        经 ``ConfigResolver.video_generate_audio`` 直接 monkeypatch 配置开关的解析结果，
        绕开该方法内部对项目文件是否存在于磁盘的依赖（预估路径本不该受此约束）。
        """

        async def _fake_video_generate_audio(self, project_name=None):
            return configured_generate_audio

        monkeypatch.setattr(ConfigResolver, "video_generate_audio", _fake_video_generate_audio)

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "video_backend": video_backend,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [1])}

        result = await service.compute(project_data, scripts, project_name="test")

        seg = result["episodes"][0]["segments"][0]
        assert seg["estimate"]["video"]["USD"] == pytest.approx(expected_usd)

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
