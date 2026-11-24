"""分镜首尾帧路径的费用估算集成测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from arcreel_catalog_core import DurationConstraints
from lib.billing.multipliers import PROVIDER_MULTIPLIER_KEY
from lib.catalog import query as catalog
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository
from lib.config.resolver import ConfigResolver
from server.services.admission.cost_estimation import CostEstimationService
from tests.catalog_support import install_catalog, with_entry_changes, with_entry_facts


@pytest.fixture(autouse=True)
def _active_veo_model(monkeypatch):
    data = with_entry_changes(
        catalog.catalog_data(),
        "gemini-aistudio",
        "veo-3.1-generate-preview",
        discontinued=False,
        shutdown_date=None,
    )
    install_catalog(monkeypatch, data)


def _make_script(episode: int, segment_ids: list[str], durations: list[int]) -> dict:
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": "narration",
        "duration_seconds": sum(durations),
        "summary": "test",
        "novel": {"title": "t", "chapter": "c"},
        "segments": [
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
                    "composition": {"shot_type": "w", "lighting": "n", "ambiance": "c"},
                },
                "video_prompt": {"action": "a", "camera_motion": "s"},
                "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
            }
            for sid, dur in zip(segment_ids, durations, strict=True)
        ],
    }


async def test_storyboard_estimate_reports_end_frame_duration_failure(db_factory, monkeypatch):
    install_catalog(
        monkeypatch,
        with_entry_facts(
            catalog.catalog_data(),
            "gemini-aistudio",
            "veo-3.1-generate-preview",
            duration_constraints=DurationConstraints(with_end_frame=(4, 6)),
        ),
    )
    project = {
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
        "episodes": [{"episode": 1, "script_file": "ep1.json"}],
    }
    script = _make_script(1, ["E1S001", "E1S002"], [8, 8])
    script["segments"][0]["end_frame_image"] = "end_frames/end.png"
    result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
        project, {"ep1.json": script}, project_name="end-frame-cost"
    )
    blocked, normal = result["episodes"][0]["segments"]
    assert blocked["estimate"]["video"] == {}
    assert blocked["request_projection"]["problems"][0]["code"] == "video_duration_end_frame_not_supported"
    assert normal["estimate"]["video"]
    assert "request_projection" not in normal


async def test_storyboard_estimate_uses_end_frame_first_allowed_duration_when_unset(db_factory, monkeypatch):
    """分镜与项目都未设时长时，带尾帧分镜按尾帧请求事实的首个档位估价，不套用固定 8 秒误报越界。"""
    install_catalog(
        monkeypatch,
        with_entry_facts(
            catalog.catalog_data(),
            "gemini-aistudio",
            "veo-3.1-generate-preview",
            duration_constraints=DurationConstraints(with_end_frame=(4, 6)),
        ),
    )
    project = {
        "content_mode": "ad",
        "generation_mode": "storyboard",
        "target_duration": 15,
        "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
        "episodes": [{"episode": 1, "script_file": "ep1.json"}],
    }
    script = _make_script(1, ["E1S001"], [8])
    del script["segments"][0]["duration_seconds"]
    script["segments"][0]["end_frame_image"] = "end_frames/end.png"
    result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
        project, {"ep1.json": script}, project_name="end-frame-unset-duration"
    )
    seg = result["episodes"][0]["segments"][0]
    assert seg["duration_seconds"] == 4
    assert "request_projection" not in seg
    assert seg["estimate"]["video"]


async def test_storyboard_end_frame_estimate_pins_same_model_identity_as_price_source(db_factory):
    """全局默认视频模型在估算中途切换时，尾帧请求事实与价格来源仍固定在同一模型身份上。"""
    async with db_factory() as session:
        await SystemSettingRepository(session).set("default_video_backend", "gemini-aistudio/veo-3.1-generate-preview")
        await ProviderConfigRepository(session).set("gemini-aistudio", PROVIDER_MULTIPLIER_KEY, "2.0")
        await ProviderConfigRepository(session).set("kling", PROVIDER_MULTIPLIER_KEY, "1.0")
        await session.commit()

    flipped = False

    @asynccontextmanager
    async def _pricing_session_factory():
        nonlocal flipped
        async with db_factory() as session:
            yield session
        if not flipped:
            flipped = True
            async with db_factory() as writer:
                await SystemSettingRepository(writer).set("default_video_backend", "kling/kling-v3")
                await writer.commit()

    project = {
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "episodes": [{"episode": 1, "script_file": "ep1.json"}],
    }
    script = _make_script(1, ["E1S001", "E1S002"], [6, 6])
    script["segments"][0]["end_frame_image"] = "end_frames/end.png"

    result = await CostEstimationService(ConfigResolver(db_factory), _pricing_session_factory).compute(
        project, {"ep1.json": script}, project_name="end-frame-race"
    )

    end_seg, normal_seg = result["episodes"][0]["segments"]
    assert flipped is True
    async with db_factory() as reader:
        assert await SystemSettingRepository(reader).get("default_video_backend") == "kling/kling-v3"
    assert end_seg["estimate"]["video"] == {"USD": pytest.approx(4.8)}
    assert normal_seg["estimate"]["video"] == {"USD": pytest.approx(4.8)}
