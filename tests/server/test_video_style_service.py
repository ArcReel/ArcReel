from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.project_manager import ProjectManager
from lib.text_backends.base import TEXT_TASK_TIERS, TextGenerationResult, TextTaskTier, TextTaskType
from lib.video_style import UnifiedVideoStyleDraft, UnifiedVideoStylePatch
from server.services.video_style import VideoStyleService

pytestmark = pytest.mark.unit


def _project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo",
        "景泰蓝",
        "写实",
        "narration",
        extras={
            "generation_mode": "reference_video",
            "source_language": "zh",
            "style_description": "暖色自然光，传统工艺纪录片",
        },
    )
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "景泰蓝",
            "content_mode": "narration",
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "duration_seconds": 8,
                    "text": "微距展示铜丝弯折、镊子轻碰与釉料颗粒倾倒。",
                }
            ],
        },
        "episode_1.json",
        validate=False,
    )
    return pm


class _Generator:
    def __init__(self, response: UnifiedVideoStyleDraft) -> None:
        self.response = response
        self.requests: list[Any] = []

    async def generate(self, request: Any, *, project_name: str) -> TextGenerationResult:
        self.requests.append((request, project_name))
        return TextGenerationResult(
            text=json.dumps(self.response.model_dump(), ensure_ascii=False),
            provider="test",
            model="simple",
        )


async def test_ensure_analyzes_once_and_persists_agent_source(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    generator = _Generator(
        UnifiedVideoStyleDraft(
            prompt="写实工艺纪录片，以微距特写和缓慢稳定移动为主，使用长镜头，突出铜丝、镊子和釉料颗粒声。",
        )
    )

    async def _factory(_project_name: str) -> _Generator:
        return generator

    service = VideoStyleService(pm, generator_factory=_factory)
    first, created = await service.ensure("demo", preferred_episode=1)
    second, created_again = await service.ensure("demo", preferred_episode=1)

    assert created is True
    assert created_again is False
    assert second == first
    assert first.source == "agent"
    assert "微距特写" in first.prompt
    assert len(generator.requests) == 1
    request, project_name = generator.requests[0]
    assert project_name == "demo"
    assert request.response_schema is UnifiedVideoStyleDraft
    assert "铜丝弯折" in request.prompt
    assert pm.load_project_readonly("demo")["video_style"]["source"] == "agent"


def test_update_edits_the_same_object_and_marks_user_source(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    service = VideoStyleService(pm)

    initial = service.update(
        "demo",
        UnifiedVideoStylePatch(prompt="固定微距，以 ASMR 材质声为主，不使用背景音乐。"),
    )
    updated = service.update("demo", UnifiedVideoStylePatch(prompt="固定微距与长镜头，突出 ASMR 材质声。"))

    assert initial.source == "user"
    assert updated.source == "user"
    assert updated.prompt == "固定微距与长镜头，突出 ASMR 材质声。"
    assert "video_style" in pm.load_project_readonly("demo")


def test_video_style_analysis_is_a_simple_text_task() -> None:
    assert TEXT_TASK_TIERS[TextTaskType.VIDEO_STYLE_ANALYSIS] is TextTaskTier.SIMPLE
