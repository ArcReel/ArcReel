"""Reference request projection contract across public consumers."""

from typing import Any, cast

import pytest
from fastapi import HTTPException

from lib.config.resolver import ConfigResolver
from lib.generation_queue import reference_projection_for_queued_task
from lib.reference_video.request_projection import USE_TTS, ReferenceRequestOptions
from server.agent_runtime.sdk_tools import enqueue_videos
from server.agent_runtime.sdk_tools._context import ToolContext
from server.auth import CurrentUserInfo
from server.routers import reference_videos
from server.services.cost_estimation import CostEstimationService
from tests.fakes import FakeReferenceCapabilityProjection, fake_reference_request_projector


@pytest.mark.integration
async def test_reference_projection_contract_stays_aligned_across_public_consumers(
    db_factory,
    monkeypatch,
    tmp_path,
):
    """Quote, Web, Agent, and queue expose the same current-state projection."""

    capabilities = FakeReferenceCapabilityProjection(
        durations=(4, 8, 12),
        provider_id="fake",
        model_id="fake-model",
        max_reference_images=1,
    )
    unit: dict[str, Any] = {
        "unit_id": "E1U1",
        "shots": [{"text": "镜头"}],
        "references": [
            {"type": "character", "name": "甲"},
            {"type": "character", "name": "乙"},
            {"type": "character", "name": "丙"},
        ],
        "duration_seconds": 5,
        "transition_to_next": "cut",
        "generated_assets": {},
    }
    script: dict[str, Any] = {
        "episode": 1,
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "video_units": [unit],
    }
    project: dict[str, Any] = {
        "title": "Narration",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "characters": {
            "甲": {"character_sheet": "characters/a.png"},
            "乙": {"character_sheet": "characters/b.png"},
            "丙": {"character_sheet": "characters/missing.png"},
        },
        "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
    }
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters/a.png").write_bytes(b"a")
    (tmp_path / "characters/b.png").write_bytes(b"b")
    options = ReferenceRequestOptions(narration_delivery=USE_TTS, narration_duration_floor=9.5)

    project_current = fake_reference_request_projector(capabilities=capabilities)

    class _ProjectManager:
        def load_project(self, project_name):
            assert project_name == "demo"
            return project

        def load_script(self, project_name, script_file):
            assert (project_name, script_file) == ("demo", "ep1.json")
            return script

        def get_project_path(self, project_name):
            assert project_name == "demo"
            return tmp_path

    pm = _ProjectManager()
    monkeypatch.setattr("server.services.cost_estimation.ConfigReferenceCapabilityProjection", lambda _r: capabilities)
    monkeypatch.setattr(reference_videos, "get_project_manager", lambda: pm)
    monkeypatch.setattr(reference_videos, "project_reference_unit_request", project_current)
    monkeypatch.setattr(enqueue_videos, "project_reference_unit_request", project_current)
    monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: pm)
    monkeypatch.setattr(
        "lib.reference_video.request_projection.project_reference_unit_request",
        project_current,
    )
    service = CostEstimationService(ConfigResolver(db_factory), db_factory, project_path=tmp_path)

    async def observe(expected_input: float, expected_slot: int) -> None:
        quote = await service.compute(
            project,
            {"ep1.json": script},
            project_name="demo",
            reference_request_options={"E1U1": options},
        )
        quote_projection = quote["episodes"][0]["segments"][0]["request_projection"]

        with pytest.raises(HTTPException) as web_precheck_blocked:
            await reference_videos.precheck_unit_duration(
                project_name="demo",
                episode=1,
                unit_id="E1U1",
                _t=lambda key, **_params: key,
                narration_delivery=USE_TTS,
                narration_duration_floor=9.5,
            )
        with pytest.raises(HTTPException) as web_generate_blocked:
            await reference_videos.generate_unit(
                project_name="demo",
                episode=1,
                unit_id="E1U1",
                user=CurrentUserInfo(id="u1", sub="test", role="admin"),
                _t=lambda key, **_params: key,
                req=reference_videos.GenerateUnitRequest(
                    narration_delivery=USE_TTS,
                    narration_duration_floor=9.5,
                ),
            )

        agent_response = await enqueue_videos.generate_video_episode_tool(
            ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)  # type: ignore[arg-type]
        ).handler(
            {
                "script": "ep1.json",
                "narration_delivery": USE_TTS,
                "narration_duration_floor": 9.5,
            }
        )
        queue_projection = await reference_projection_for_queued_task(
            project=project,
            project_name="demo",
            payload={"script_file": "ep1.json", "reference_request_options": options.to_payload()},
            resource_id="E1U1",
        )
        assert queue_projection is not None

        expected_facts = ("r2v", "fake", "fake-model", expected_input, expected_slot)
        assert (
            quote_projection["capability"],
            quote_projection["provider_id"],
            quote_projection["model_id"],
            quote_projection["duration_input"],
            quote_projection["request_duration"],
        ) == expected_facts
        precheck_detail = cast(dict[str, object], web_precheck_blocked.value.detail)
        generate_detail = cast(dict[str, object], web_generate_blocked.value.detail)
        agent_projection = cast(dict[str, object], agent_response["request_projection"])
        for projection in (precheck_detail, generate_detail, agent_projection):
            assert (
                projection["hydrated_capability"],
                projection["provider_id"],
                projection["model_id"],
                projection["duration_input"],
                projection["request_duration"],
            ) == expected_facts
        assert (
            queue_projection.hydrated_capability,
            queue_projection.provider_id,
            queue_projection.model_id,
            queue_projection.duration_input,
            queue_projection.request_duration.seconds if queue_projection.request_duration else None,
        ) == expected_facts

        expected_codes = [
            "reference_asset_missing",
            "reference_images_clamped",
            "reference_duration_confirmation_required",
        ]
        for projection in (quote_projection, precheck_detail, generate_detail, agent_projection):
            problems = cast(list[dict[str, object]], projection["problems"])
            assert [problem["code"] for problem in problems] == expected_codes
        assert [problem.code for problem in queue_projection.problems] == expected_codes

    await observe(9.5, 12)
    unit["duration_seconds"] = 13
    await observe(13, 12)
