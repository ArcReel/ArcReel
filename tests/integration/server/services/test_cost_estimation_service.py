"""Tests for CostEstimationService."""

from unittest.mock import AsyncMock

import pytest

from lib.config.resolver import ConfigResolver
from lib.cost_calculator import cost_calculator
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.narration_delivery import VideoRequestCostFacts
from lib.providers import PROVIDER_GEMINI
from lib.reference_video.request_projection import (
    USE_TTS,
    ProviderProjectionCandidate,
    ReferenceRequestOptions,
)
from server.services import reference_video_tasks
from server.services.cost_estimation import CostEstimationService, quote_video_request


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
    """Helper to create a reference_video episode script dict (video_units[])."""
    units = []
    for unit_id, duration in unit_specs:
        units.append(
            {
                "unit_id": unit_id,
                "text": "t",
                "duration_seconds": duration,
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
    async def test_shared_video_quote_exposes_exact_amount_currency_and_request_coordinates(self, db_factory):
        quote = await quote_video_request(
            VideoRequestCostFacts(
                provider_id="openai",
                model_id="sora-2",
                resolution="720p",
                duration_seconds=8,
                generate_audio=True,
            ),
            db_factory,
        )

        assert quote is not None
        assert quote.to_payload() == {
            "amount": pytest.approx(0.8),
            "currency": "USD",
            "provider_id": "openai",
            "model_id": "sora-2",
            "request_duration_seconds": 8,
        }

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
            "generation_mode": "storyboard",
            "grid_storyboard": True,
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

    async def test_grid_duplicate_ids_each_claim_own_share(self, db_factory):
        """一张宫格覆盖两个共用同一 ID 的条目（ADR 0053 明确接受的受支持状态）：

        两条目应各拿自己那一份均摊份额，而不是把宫格实付重复计入合计。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_dup"
        await _seed_call(
            db_factory,
            "proj-dup",
            "image",
            "historical-model",
            segment_id=grid_id,
            cost_amount=1.0,
            currency="USD",
        )

        overrides = [
            {"grid_id": grid_id, "grid_cell_index": 0},
            {"grid_id": grid_id, "grid_cell_index": 1},
        ]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S001"], [6, 6], generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj-dup")

        segments = result["episodes"][0]["segments"]
        assert len(segments) == 2
        for seg in segments:
            assert seg["segment_id"] == "E1S001"
            assert seg["actual"]["image"]["USD"] == pytest.approx(0.5)

        ep_total_image = result["episodes"][0]["totals"]["actual"].get("image", {})
        assert ep_total_image.get("USD", 0) == pytest.approx(1.0)
        assert result["project_totals"]["actual"]["image"]["USD"] == pytest.approx(1.0)

    async def test_grid_duplicate_ids_across_multiple_grids(self, db_factory):
        """同一 ID 既在一张宫格内出现 3 次、又跨到另一张宫格：每个条目只拿所属宫格的那一份。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        for grid_id, amount in (("grid_a", 0.9), ("grid_b", 1.0)):
            await _seed_call(
                db_factory,
                "proj-multi",
                "image",
                "historical-model",
                segment_id=grid_id,
                cost_amount=amount,
                currency="USD",
            )

        overrides = [
            {"grid_id": "grid_a", "grid_cell_index": 0},
            {"grid_id": "grid_a", "grid_cell_index": 1},
            {"grid_id": "grid_a", "grid_cell_index": 2},
            {"grid_id": "grid_b", "grid_cell_index": 0},
            {"grid_id": "grid_b", "grid_cell_index": 1},
        ]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        seg_ids = ["E1S001", "E1S001", "E1S001", "E1S001", "E1S002"]
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 5, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj-multi")

        per_scene_costs = [seg["actual"]["image"]["USD"] for seg in result["episodes"][0]["segments"]]
        assert per_scene_costs == pytest.approx([0.3, 0.3, 0.3, 0.5, 0.5])
        assert result["project_totals"]["actual"]["image"]["USD"] == pytest.approx(1.9)

    async def test_grid_actual_split_remainder_sums_exactly(self, db_factory):
        """除不尽的宫格实付（USD 0.101 均摊 9 份）分摊后，各份之和须与冻结实付分文不差。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_remainder"
        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]  # 9 scenes

        await _seed_call(
            db_factory,
            "proj-rem",
            "image",
            "historical-model",
            segment_id=grid_id,
            cost_amount=0.101,
            currency="USD",
        )

        overrides = [{"grid_id": grid_id, "grid_cell_index": i} for i in range(9)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj-rem")

        per_scene_costs = [seg["actual"]["image"]["USD"] for seg in result["episodes"][0]["segments"]]
        assert sum(per_scene_costs) == pytest.approx(0.101, abs=1e-9)

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
            "generation_mode": "storyboard",
            "grid_storyboard": True,
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
            "generation_mode": "storyboard",
            "grid_storyboard": True,
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

    async def test_grid_estimate_count_follows_4k_gate(self, db_factory, monkeypatch):
        """估算的宫格张数按 4K 门控走同一条阶梯：12 场景一组，4K 下一张 4×4 装下，
        非 4K 下封顶 3×3 要切两张，估算总价相应翻倍。

        用按张定价（与分辨率无关）的自定义供应商，把张数变化与单价随档位变化的影响隔开。
        """
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        from server.services import cost_estimation as ce

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

        seg_ids = [f"E1S{i:03d}" for i in range(1, 13)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "image_provider_t2i": "custom-1/img",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 12)}

        async def _estimated_image_total(resolution: str | None) -> float:
            async def _resolution(_r, _project):
                return resolution

            monkeypatch.setattr(ce, "resolve_image_resolution", _resolution)
            service = CostEstimationService(ConfigResolver(db_factory), db_factory)
            result = await service.compute(project_data, scripts, project_name="proj")
            return sum(seg["estimate"]["image"]["USD"] for seg in result["episodes"][0]["segments"])

        total_4k = await _estimated_image_total("4K")
        assert total_4k > 0
        # 未配置分辨率（None）与 2K 同样落在门控内
        assert await _estimated_image_total("2K") == pytest.approx(total_4k * 2, rel=1e-4)  # 每条份额各自 round(…, 6)
        assert await _estimated_image_total(None) == pytest.approx(total_4k * 2, rel=1e-4)  # 每条份额各自 round(…, 6)

    async def test_grid_estimate_prices_at_resolved_resolution(self, db_factory, monkeypatch):
        """宫格图按执行期生效的分辨率档计价：4K 项目按 4K 单价，未配置回落保底档。

        9 场景在任何档位下都只切一张 grid_9，张数不变，差异只来自单价。
        """
        from lib.grid.layout import GRID_FALLBACK_RESOLUTION
        from server.services import cost_estimation as ce

        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            # 显式钉住按分辨率分档定价的型号：测试要验的是「计价档随解析结果走」，
            # 不该依赖全局默认图片模型恰好是分档定价的
            "image_provider_t2i": "gemini-aistudio/gemini-3.1-flash-image-preview",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9)}

        async def _estimated_image_total(resolution: str | None) -> float:
            async def _resolution(_r, _project):
                return resolution

            monkeypatch.setattr(ce, "resolve_image_resolution", _resolution)
            service = CostEstimationService(ConfigResolver(db_factory), db_factory)
            result = await service.compute(project_data, scripts, project_name="proj")
            return sum(seg["estimate"]["image"]["USD"] for seg in result["episodes"][0]["segments"])

        total_2k = await _estimated_image_total("2K")
        total_4k = await _estimated_image_total("4K")
        assert total_2k > 0
        # 该型号 4K 单价高于 2K，估算须随之上浮而非恒按 2K
        assert total_4k > total_2k
        # 未配置分辨率时按保底档计价，与执行期下发的档位同源
        assert await _estimated_image_total(None) == pytest.approx(
            await _estimated_image_total(GRID_FALLBACK_RESOLUTION)
        )

    async def test_plain_storyboard_estimate_prices_at_resolved_resolution(self, db_factory, monkeypatch):
        """普通（非宫格）分镜图同样按执行期生效的分辨率档计价，未配置时按保底档。"""
        from server.services import cost_estimation as ce
        from server.services.cost_estimation import _IMAGE_PRICING_FALLBACK_RESOLUTION

        seg_ids = [f"E1S{i:03d}" for i in range(1, 4)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            # 按分辨率分档定价的型号，档位差异才可观测
            "image_provider_t2i": "gemini-aistudio/gemini-3.1-flash-image-preview",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 3)}

        async def _estimated_image_total(resolution: str | None) -> float:
            async def _resolution(_r, _project):
                return resolution

            monkeypatch.setattr(ce, "resolve_image_resolution", _resolution)
            service = CostEstimationService(ConfigResolver(db_factory), db_factory)
            result = await service.compute(project_data, scripts, project_name="proj")
            return sum(seg["estimate"]["image"]["USD"] for seg in result["episodes"][0]["segments"])

        total_fallback = await _estimated_image_total(_IMAGE_PRICING_FALLBACK_RESOLUTION)
        assert total_fallback > 0
        # 该型号 4K 单价高于保底档，估算须随之上浮而非恒按保底档
        assert await _estimated_image_total("4K") > total_fallback
        # 解析失败（未配置图像供应商）按保底档计价，不抛错
        assert await _estimated_image_total(None) == pytest.approx(total_fallback)

    async def test_grid_estimate_duplicate_ids_across_groups_each_correct(self, db_factory):
        """同 ID 条目落在不同分组时各自展示本组的均摊估算，不被后写的分组覆盖。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        # 前 9 条一组（grid_9，每条摊 1/9 张），第 10 条 segment_break 另起一组（grid_4，独占一张）
        seg_ids = [f"E1S{i:03d}" for i in range(1, 9)] + ["DUP", "DUP"]
        script = _make_script(1, seg_ids, [6] * 10)
        script["segments"][9]["segment_break"] = True

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }

        result = await service.compute(project_data, {"ep1.json": script}, project_name="proj")
        segments = result["episodes"][0]["segments"]

        unit = segments[9]["estimate"]["image"]["USD"]  # 独占一张宫格 → 满张单价
        assert unit > 0
        # 含末位与第 10 条同名的第 9 条：按 ID 建 key 时它会被后写的第二组覆盖成满张单价
        for seg in segments[:9]:
            assert seg["estimate"]["image"]["USD"] == pytest.approx(round(unit / 9, 6))

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
        """ad 项目（分镜图生视频路径）：逐个分镜返回分镜图 + 视频估值，聚合进集/项目两级合计。"""
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
        # 视频估值随分镜时长变化（单个分镜级估值非整集平摊）
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

    async def test_ad_reference_video_estimates_self_contained_units(self, db_factory):
        """广告/短片的参考生视频按 video_units 计费展示，并跳过分镜图与独立音频估值。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {
            "ep1.json": _make_reference_video_script(
                1,
                "ad",
                [("E1U1", 4), ("E1U2", 6)],
            )
        }

        result = await service.compute(project_data, scripts, project_name="ad-ref")

        segments = result["episodes"][0]["segments"]
        assert [segment["segment_id"] for segment in segments] == ["E1U1", "E1U2"]
        assert [segment["duration_seconds"] for segment in segments] == [4, 6]
        for segment in segments:
            assert segment["estimate"]["image"] == {}
            assert segment["estimate"]["audio"] == {}
            assert segment["estimate"]["video"]
        assert result["project_totals"]["estimate"].get("image", {}) == {}
        assert result["project_totals"]["estimate"]["video"]

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
            "video_provider_i2v": "kling/kling-v3",
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

    async def test_narration_reference_video_estimate_uses_rounded_up_unit_duration(self, db_factory, monkeypatch):
        """取档向上的 unit：预估金额按取档后的秒数（8s）计，而非剧本原始总时长（5s）。"""
        priced_durations: list[int | None] = []
        original = cost_calculator.calculate_cost

        def _spy(provider, params, **kwargs):
            if params.call_type == "video":
                priced_durations.append(params.duration_seconds)
            return original(provider, params, **kwargs)

        monkeypatch.setattr(cost_calculator, "calculate_cost", _spy)

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        rounded = await service.compute(project_data, scripts, project_name="narration-ref-round")

        seg = rounded["episodes"][0]["segments"][0]
        assert seg["segment_id"] == "E1U1"
        assert seg["duration_seconds"] == 5
        assert seg["estimate"]["video"]
        assert seg["request_projection"]["request_duration"] == 8
        assert priced_durations == [8]
        assert seg["estimate"]["video"] == rounded["episodes"][0]["totals"]["estimate"]["video"]

    async def test_reference_video_quote_accepts_server_materialized_tts_duration(self, db_factory, monkeypatch):
        class _TtsFloorCapabilities:
            def __init__(self, _resolver):
                pass

            async def resolve_candidate(self, _project, capability):
                return ProviderProjectionCandidate(
                    capability=capability,
                    provider_id="kling",
                    model_id="kling-v3",
                    supported_durations=(4, 8, 12),
                    max_reference_images=4,
                    resolution="1080p",
                    generate_audio=True,
                    requested_generate_audio=True,
                    has_audio_track=True,
                    audio_switch_controllable=True,
                )

        monkeypatch.setattr(
            "server.services.cost_estimation.ConfigReferenceCapabilityProjection",
            _TtsFloorCapabilities,
        )
        monkeypatch.setattr(
            "server.services.cost_estimation.active_tts_resource_ids",
            AsyncMock(return_value=frozenset()),
        )
        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        result = await service.compute(
            project_data,
            scripts,
            project_name="narration-ref-tts-floor",
            reference_request_options={
                "E1U1": ReferenceRequestOptions(
                    narration_delivery=USE_TTS,
                    current_tts_duration_seconds=9.5,
                )
            },
        )

        projection = result["episodes"][0]["segments"][0]["request_projection"]
        assert projection["duration_input"] == 9.5
        assert projection["request_duration"] == 12
        assert projection["problems"][0]["code"] == "reference_duration_confirmation_required"

    async def test_reference_video_tts_quote_uses_current_visual_tier_for_zero_or_incremental_cost(
        self, db_factory, monkeypatch
    ):
        class _SoraCapabilities:
            def __init__(self, _resolver):
                pass

            async def resolve_candidate(self, _project, capability):
                return ProviderProjectionCandidate(
                    capability=capability,
                    provider_id="openai",
                    model_id="sora-2",
                    supported_durations=(4, 8, 12),
                    max_reference_images=4,
                    resolution="720p",
                    generate_audio=True,
                    requested_generate_audio=True,
                    has_audio_track=True,
                    audio_switch_controllable=True,
                )

        monkeypatch.setattr(
            "server.services.cost_estimation.ConfigReferenceCapabilityProjection",
            _SoraCapabilities,
        )
        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "openai/sora-2",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 4)])}

        reused = await service.compute(
            project_data,
            scripts,
            project_name="narration-ref-reused-quote",
            reference_request_options={
                "E1U1": ReferenceRequestOptions(
                    narration_delivery=USE_TTS,
                    current_tts_duration_seconds=8.0,
                    current_visual_duration_seconds=8,
                    current_reusable_visual_duration_seconds=8,
                )
            },
        )
        regenerated = await service.compute(
            project_data,
            scripts,
            project_name="narration-ref-regenerated-quote",
            reference_request_options={
                "E1U1": ReferenceRequestOptions(
                    narration_delivery=USE_TTS,
                    current_tts_duration_seconds=8.0,
                    current_visual_duration_seconds=4,
                )
            },
        )

        reused_segment = reused["episodes"][0]["segments"][0]
        regenerated_segment = regenerated["episodes"][0]["segments"][0]
        assert reused_segment["estimate"]["video"] == {}
        assert reused_segment["request_projection"]["request_cost"] == {
            "amount": 0.0,
            "currency": "USD",
            "provider_id": "openai",
            "model_id": "sora-2",
            "request_duration_seconds": 8,
        }
        assert regenerated_segment["estimate"]["video"] == {"USD": pytest.approx(0.8)}
        assert regenerated_segment["request_projection"]["request_cost"] == {
            "amount": pytest.approx(0.8),
            "currency": "USD",
            "provider_id": "openai",
            "model_id": "sora-2",
            "request_duration_seconds": 8,
        }
        assert regenerated_segment["request_projection"]["problems"][0]["code"] == (
            "reference_duration_confirmation_required"
        )

        def _quote_unavailable(*_args, **_kwargs):
            raise ValueError("price unavailable")

        monkeypatch.setattr(
            "server.services.cost_estimation.quote_video_request_from_price",
            _quote_unavailable,
        )
        unavailable = await service.compute(
            project_data,
            scripts,
            project_name="narration-ref-unavailable-quote",
            reference_request_options={
                "E1U1": ReferenceRequestOptions(
                    narration_delivery=USE_TTS,
                    current_tts_duration_seconds=8.0,
                    current_visual_duration_seconds=4,
                )
            },
        )
        unavailable_segment = unavailable["episodes"][0]["segments"][0]
        assert unavailable_segment["estimate"]["video"] == {}
        assert unavailable_segment["request_projection"]["allowed"] is False
        assert [problem["code"] for problem in unavailable_segment["request_projection"]["problems"]] == [
            "reference_duration_confirmation_required",
            "video_request_cost_unavailable",
        ]

    async def test_reference_video_estimate_blocks_when_duration_metadata_is_empty(self, db_factory, monkeypatch):
        class _MissingDurationCapabilities:
            def __init__(self, _resolver):
                pass

            async def resolve_candidate(self, _project, capability):
                return ProviderProjectionCandidate(
                    capability=capability,
                    provider_id="kling",
                    model_id="kling-v3",
                    supported_durations=(),
                    max_reference_images=4,
                    resolution="1080p",
                    generate_audio=True,
                    requested_generate_audio=True,
                    has_audio_track=True,
                    audio_switch_controllable=True,
                )

        monkeypatch.setattr(
            "server.services.cost_estimation.ConfigReferenceCapabilityProjection",
            _MissingDurationCapabilities,
        )
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        result = await service.compute(project_data, scripts, project_name="missing-duration-metadata")

        segment = result["episodes"][0]["segments"][0]
        assert segment["estimate"]["video"] == {}
        assert segment["request_projection"]["request_duration"] is None
        assert segment["request_projection"]["problems"] == [
            {
                "code": "reference_supported_durations_missing",
                "blocking": True,
                "unit_id": "E1U1",
                "locations": [{"path": ["duration_seconds"], "line": None}],
                "params": {"provider": "kling", "model": "kling-v3"},
                "action": "configure_video_model",
            }
        ]

    async def test_reference_route_gives_no_estimate_for_mismatched_storyboard_script(self, db_factory):
        """参考生视频项目下的失配剧本（分镜骨架）不产生预估：该集按当前生成模式根本不能生成。

        估算与执行同轴——生成侧对这类存量混排集直接拒绝并要求重拆，估算这边照实给零，
        不去替它假想一条分镜路径。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
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

        result = await service.compute(project_data, scripts, project_name="narration-mismatched")

        assert result["episodes"][0]["segments"] == []
        assert not result["project_totals"]["estimate"]["video"]

    async def test_reference_route_estimates_units_ignoring_residual_segments(self, db_factory):
        """参考生视频项目按 units 估算，剧本里残留的 segments 不参与——生成模式定路径，形状不投票。"""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6)])
        script["segments"] = [
            {"segment_id": "E1S1", "duration_seconds": 5, "narration": "n1", "visual_prompt": "v1"},
        ]
        scripts = {"ep1.json": script}

        result = await service.compute(project_data, scripts, project_name="narration-residual-segments")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1"]
        assert result["project_totals"]["estimate"]["video"]

    async def test_storyboard_route_gives_no_estimate_for_mismatched_unit_script(self, db_factory):
        """分镜图生视频项目下的失配剧本（video_units 骨架）不产生预估：估算只认项目生成模式。"""
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

        result = await service.compute(project_data, scripts, project_name="narration-mismatched-units")

        assert result["episodes"][0]["segments"] == []
        assert not result["project_totals"]["estimate"]["video"]

    async def test_narration_reference_video_estimate_skips_unenqueueable_units(self, db_factory):
        """正文为空或只有空白的 unit 不可入队（``enqueue_videos.py::_reference_unit_spec``
        对空正文直接拒绝，``TaskSpec.from_request`` 对空提示词同样拒绝），这类 unit
        不产生新预估——但 unit 整条仍要保留在结果里、纳入汇总：不可入队只影响能否产生新预估，
        不影响该 unit 是否曾经成功生成过。已有实付的 unit（曾成功生成、之后被编辑成空正文）
        其历史支出不能因此从合计里消失。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6)])
        script["video_units"].append(
            {
                "unit_id": "E1U2",
                "text": "",
                "needs_replan": True,
                "duration_seconds": 5,
                "transition_to_next": "cut",
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
        )
        script["video_units"].append(
            {
                "unit_id": "E1U3",
                "text": "   ",
                "duration_seconds": 5,
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

    async def test_reference_video_estimate_skips_replan_unit_but_keeps_actual(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "ad", [("E1U1", 6)])
        script["video_units"][0]["needs_replan"] = True
        await _seed_call(
            db_factory,
            "ad-replan-cost",
            "video",
            "veo-3.1-lite-generate-preview",
            segment_id="E1U1",
            cost_amount=1.23,
            currency="USD",
        )

        result = await service.compute(project_data, {"ep1.json": script}, project_name="ad-replan-cost")

        segment = result["episodes"][0]["segments"][0]
        assert segment["estimate"]["video"] == {}
        assert segment["actual"]["video"] == {"USD": 1.23}
        assert result["project_totals"]["actual"]["video"] == {"USD": 1.23}

    async def test_narration_reference_video_estimate_skips_unit_with_malformed_duration(self, db_factory):
        """Agent/外部编辑过的剧本可能写入非数值 ``duration_seconds``（字符串、list、dict 等）。
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
            "video_provider_i2v": "kling/kling-v3",
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

    async def test_narration_reference_video_estimate_skips_unit_with_non_string_text(self, db_factory):
        """Agent/外部编辑过的剧本可能把 ``text`` 裸写成非字符串的 truthy 值（如 ``true``/``1``）。
        该值参与提示词拼接会抛 ``TypeError``，必须先做类型检查，否则单条脏数据会让整个项目
        估算 500。
        """
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])
        script["video_units"][0]["text"] = True

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-non-string-text")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2"]
        assert segments[0]["estimate"]["video"] == {}
        assert segments[1]["estimate"]["video"]

    async def test_narration_reference_video_estimate_rejects_non_string_unit_id(self, db_factory):
        """Agent/外部编辑过的剧本可能把 ``unit_id`` 裸写成非字符串 truthy 值（如数字/布尔）。
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
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])
        script["video_units"][0]["unit_id"] = 1

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-non-str-unit-id")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U2"]
        assert segments[0]["estimate"]["video"]

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
        self, db_factory, video_backend, configured_generate_audio, expected_usd
    ):
        """费用预估在所有 (provider, audio 开关) 组合下与供应商实际出账口径一致。

        直接在已加载 project dict 中给出项目开关，验证能力解析不二次依赖磁盘项目文件。
        """

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "video_backend": video_backend,
            "video_generate_audio": configured_generate_audio,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [1])}

        result = await service.compute(project_data, scripts, project_name="test")

        seg = result["episodes"][0]["segments"][0]
        assert seg["estimate"]["video"]["USD"] == pytest.approx(expected_usd)

    async def test_kling_reference_model_estimate_uses_effective_silent_tier(self, db_factory):
        """参考能力 model 不产出人声；即使项目开关为真，预估也应与 backend 结算的静音档一致。"""
        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "video_backend": "kling/kling-video-o1",
            "video_generate_audio": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }

        result = await service.compute(
            project_data, {"ep1.json": _make_script(1, ["E1S001"], [5])}, project_name="test"
        )

        # kling-video-o1 默认 std：静音 ¥0.6/s；错误沿用项目开关会走有声 ¥0.8/s。
        assert result["episodes"][0]["segments"][0]["estimate"]["video"]["CNY"] == pytest.approx(3.0)

    async def test_disabled_custom_video_model_estimate_reports_unknown(self, db_factory):
        """估算模型身份与执行同口径：项目模型被禁用后按任务类型桶解析闸算悬空引用，不改按该供应商
        默认启用模型出价——那个模型用户没选过，执行期也不会用它（``docs/adr/0054``）。"""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "disabled",
                        "display_name": "Disabled",
                        "endpoint": "openai-video",
                        "is_enabled": False,
                        "price_unit": "second",
                        "price_input": 9.0,
                        "currency": "USD",
                    },
                    {
                        "model_id": "runtime",
                        "display_name": "Runtime",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                        "is_default": True,
                        "price_unit": "second",
                        "price_input": 0.1,
                        "currency": "USD",
                    },
                ],
            )
            await session.commit()

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "video_backend": "custom-1/disabled",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }

        result = await service.compute(
            project_data, {"ep1.json": _make_script(1, ["E1S001"], [6])}, project_name="test-custom-fallback"
        )

        assert result["models"]["video"] == {"provider": "unknown", "model": "unknown"}
        # 身份解析不出时退到通用目录价，既不按被禁用模型（9.0/s → 54.0）也不按该供应商默认
        # 启用模型（0.1/s → 0.6）的 DB 单价出数
        video_estimate = result["episodes"][0]["segments"][0]["estimate"]["video"]
        assert video_estimate.get("USD") != pytest.approx(0.6)
        assert video_estimate.get("USD") != pytest.approx(54.0)

    @pytest.mark.parametrize(
        ("generation_mode", "expected_model"),
        [("storyboard", "kling-v3"), ("reference_video", "kling-v3-omni")],
    )
    async def test_estimate_resolves_video_model_by_generation_mode_bucket(
        self, db_factory, generation_mode, expected_model
    ):
        """估算按 generation_mode 定桶取模型，与执行扣费同源：切模式即换到另一个桶的价目。"""
        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": generation_mode,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "kling/kling-v3-omni",
            "episodes": [],
        }

        result = await service.compute(project_data, {}, project_name="test-video-bucket")

        assert result["models"]["video"] == {"provider": "kling", "model": expected_model}

    async def test_unit_duration_slots_come_from_the_r2v_bucket_model(self, db_factory, monkeypatch):
        """有参考图 unit 的取档与算价读同一个模型：两者都落 r2v 桶。

        若取档误用 i2v 桶，5 秒的 unit 会按 kling 的 [5, 10] 停在 5 秒，再按 r2v 桶 Veo 的单价
        算钱；而执行期按 Veo 的档位（未配分辨率走 1080p 兜底，只接受 8 秒）申请 8 秒——估算量
        与扣费量对不上。
        """
        priced: list[tuple[str | None, int | None]] = []
        original = cost_calculator.calculate_cost

        def _spy(provider, params, **kwargs):
            if params.call_type == "video":
                priced.append((params.model, params.duration_seconds))
            return original(provider, params, **kwargs)

        monkeypatch.setattr(cost_calculator, "calculate_cost", _spy)

        # 取档解析走全局 session factory（真实部署的库），测试库换成 db_factory 后照常做真实
        # 桶解析——被观察的是它拿到哪个模型的档位，不是它怎么连库。
        async def _caps_from_test_db(project, *, degraded_to, capability=None, episode=None):
            return await ConfigResolver(db_factory).video_capabilities_for_project(project, capability=capability)

        monkeypatch.setattr(reference_video_tasks, "project_video_caps", _caps_from_test_db)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "gemini-aistudio/veo-3.1-generate-preview",
            "characters": {"A": {"name": "A"}},
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}
        scripts["ep1.json"]["video_units"][0]["text"] = "@[A] 走进房间"

        await service.compute(project_data, scripts, project_name="r2v-duration-slots")

        assert priced == [("veo-3.1-generate-preview", 8)]

    async def test_degenerate_unit_prices_by_i2v_bucket_model(self, db_factory, monkeypatch):
        """无参考图退化 unit 降级到 i2v 桶：取档与算价都读 i2v 桶模型。

        执行侧对空参考镜头按 i2v 桶解析模型（不送入拒空参考的 r2v 模型），估算若仍按 r2v 桶
        Veo 的档位（只接受 8 秒）与单价出数，会与实际扣费的 kling 5 秒对不上。
        """
        priced: list[tuple[str | None, int | None]] = []
        original = cost_calculator.calculate_cost

        def _spy(provider, params, **kwargs):
            if params.call_type == "video":
                priced.append((params.model, params.duration_seconds))
            return original(provider, params, **kwargs)

        monkeypatch.setattr(cost_calculator, "calculate_cost", _spy)

        async def _caps_from_test_db(project, *, degraded_to, capability=None, episode=None):
            return await ConfigResolver(db_factory).video_capabilities_for_project(project, capability=capability)

        monkeypatch.setattr(reference_video_tasks, "project_video_caps", _caps_from_test_db)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "gemini-aistudio/veo-3.1-generate-preview",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        await service.compute(project_data, scripts, project_name="i2v-degenerate-unit")

        assert priced == [("kling-v3", 5)]

    async def test_all_episodes_priced_by_the_project_route_bucket(self, db_factory, monkeypatch):
        """全项目同一种生成模式、同一个桶：算价不逐集分歧，也不被某集的剧本形状带偏。

        项目生成模式是 storyboard，ep2 是失配的 video_units 骨架——它不产生预估（生成侧会拒绝
        并要求重拆），更不会把估算拽去 r2v 桶。
        """
        priced_models: list[str | None] = []
        original = cost_calculator.calculate_cost

        def _spy(provider, params, **kwargs):
            if params.call_type == "video":
                priced_models.append(params.model)
            return original(provider, params, **kwargs)

        monkeypatch.setattr(cost_calculator, "calculate_cost", _spy)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "kling/kling-v3-omni",
            "episodes": [
                {"episode": 1, "title": "", "script_file": "ep1.json"},
                {"episode": 2, "title": "", "script_file": "ep2.json"},
            ],
        }
        scripts = {
            "ep1.json": _make_script(1, ["E1S001"], [6]),
            "ep2.json": _make_reference_video_script(2, "narration", [("E2U1", 6)]),
        }

        result = await service.compute(project_data, scripts, project_name="per-episode-bucket")

        # 只有骨架与生成模式相符的 ep1 产生预估，且按项目生成模式的 i2v 桶算价。
        assert priced_models == ["kling-v3"]
        assert result["episodes"][1]["segments"] == []
        assert result["models"]["video"] == {"provider": "kling", "model": "kling-v3"}

    @pytest.mark.parametrize(
        ("text", "expected_model"),
        [("@[A] 走进房间", "kling-v3-omni"), ("空镜头", "kling-v3")],
    )
    async def test_ad_reference_route_prices_by_unit_reference_bucket(
        self, db_factory, monkeypatch, text, expected_model
    ):
        """ad 参考生视频按 unit 当前实际可用参考图分桶算价：有图 → r2v，无图 → i2v。

        参考生视频的集实际入队参考生视频任务；执行侧对无参考图视频单元按 i2v 桶降级解析模型，
        算价须跟着同一口径分桶。
        """
        priced_models: list[str | None] = []
        original = cost_calculator.calculate_cost

        def _spy(provider, params, **kwargs):
            if params.call_type == "video":
                priced_models.append(params.model)
            return original(provider, params, **kwargs)

        monkeypatch.setattr(cost_calculator, "calculate_cost", _spy)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "kling/kling-v3-omni",
            "characters": {"A": {"name": "A"}},
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "ad", [("E1U1", 6)])
        script["video_units"][0]["text"] = text
        scripts = {"ep1.json": script}

        await service.compute(project_data, scripts, project_name="ad-reference-route-bucket")

        assert priced_models == [expected_model]

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
            "generation_mode": "storyboard",
            "grid_storyboard": True,
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

    async def test_grid_estimate_counts_every_chunk_of_oversized_group(self, db_factory):
        """超过单张格数上限的分组按切块后的张数计价，与入队实际产出的宫格张数一致。"""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        from lib.grid.layout import plan_grid_chunks

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

        seg_ids = [f"E1S{i:03d}" for i in range(1, 13)]  # 12 scenes，非 4K 上限 9 → 切 2 张
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "aspect_ratio": "9:16",
            "image_provider_t2i": "custom-1/img",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 12)}

        result = await service.compute(project_data, scripts, project_name="test-grid-oversized")

        expected_grids = len(plan_grid_chunks(seg_ids, "9:16", allow_large_grid=False))
        assert expected_grids == 2
        assert result["episodes"][0]["totals"]["estimate"]["image"]["USD"] == pytest.approx(
            0.09 * expected_grids, abs=1e-4
        )

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

        # 断言解析到的仍是该自定义供应商/模型，排除 resolver 回落 unknown 导致的同结果假阳性
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
