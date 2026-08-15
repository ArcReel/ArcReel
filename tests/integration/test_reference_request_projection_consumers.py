"""Reference request projection contract across public consumers."""

from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from lib.config.resolver import ConfigResolver
from lib.generation_queue import reference_projection_for_queued_task
from lib.reference_video.request_projection import USE_TTS, ReferenceRequestOptions
from server.agent_runtime.sdk_tools import enqueue_videos
from server.agent_runtime.sdk_tools._context import ToolContext
from server.auth import CurrentUserInfo
from server.routers import reference_videos
from server.services.cost_estimation import CostEstimationService, VideoRequestQuote
from tests.fakes import FakeReferenceCapabilityProjection, fake_reference_request_projector


@pytest.mark.integration
async def test_reference_projection_contract_stays_aligned_across_public_consumers(
    db_factory,
    monkeypatch,
    tmp_path,
):
    """Public request consumers agree; queue routing keeps only current visual capability facts."""

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
    options = ReferenceRequestOptions(narration_delivery=USE_TTS)

    project_current = fake_reference_request_projector(capabilities=capabilities)

    async def project_current_with_tts(**kwargs):
        request_options = kwargs.get("options") or ReferenceRequestOptions()
        kwargs["options"] = replace(request_options, current_tts_duration_seconds=9.5)
        return await project_current(**kwargs)

    async def materialize_current_tts(**kwargs):
        return replace(kwargs["options"], current_tts_duration_seconds=9.5)

    async def no_active_tasks(**_kwargs):
        return []

    async def quote_current(facts, _session_factory):
        return VideoRequestQuote(
            amount=1.2,
            currency="USD",
            provider_id=facts.provider_id,
            model_id=facts.model_id,
            request_duration_seconds=facts.duration_seconds,
        )

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
    monkeypatch.setattr(reference_videos, "project_reference_unit_request", project_current_with_tts)
    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request", project_current_with_tts
    )
    monkeypatch.setattr(reference_videos, "prepare_current_reference_video_request_options", materialize_current_tts)
    monkeypatch.setattr(
        "server.services.video_batch_admission.prepare_current_reference_video_request_options", materialize_current_tts
    )
    monkeypatch.setattr(reference_videos, "tts_task_in_progress", AsyncMock(return_value=False))
    monkeypatch.setattr("server.services.video_batch_admission.get_active_tasks_for_resources", no_active_tasks)
    monkeypatch.setattr(
        "server.services.video_batch_admission.active_tts_resource_ids", AsyncMock(return_value=frozenset())
    )
    monkeypatch.setattr(reference_videos, "quote_video_request", quote_current)
    monkeypatch.setattr("server.services.video_batch_admission.quote_video_request", quote_current)
    monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: pm)
    monkeypatch.setattr(
        "lib.reference_video.request_projection.project_reference_unit_request",
        project_current_with_tts,
    )
    monkeypatch.setattr(
        "server.services.cost_estimation.prepare_current_reference_video_request_options",
        materialize_current_tts,
    )
    service = CostEstimationService(ConfigResolver(db_factory), db_factory, project_path=tmp_path)

    async def observe(expected_input: float, expected_slot: int) -> None:
        def unexpected_global_queue():
            raise AssertionError("cost projection must use its injected database")

        with monkeypatch.context() as isolated:
            isolated.setattr(
                "server.services.narration_delivery_tasks.get_generation_queue",
                unexpected_global_queue,
            )
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
                ),
            )

        agent_response = await enqueue_videos.generate_video_scene_tool(
            ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)  # type: ignore[arg-type]
        ).handler(
            {
                "script": "ep1.json",
                "scene_id": "E1U1",
                "narration_delivery": USE_TTS,
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
        agent_projection = cast(dict[str, object], agent_response["request_projections"][0])
        for projection in (precheck_detail, generate_detail, agent_projection):
            assert (
                projection["hydrated_capability"],
                projection["provider_id"],
                projection["model_id"],
                projection["duration_input"],
                projection["request_duration"],
            ) == expected_facts
        queue_duration_input = unit["duration_seconds"]
        queue_request_duration = 8 if queue_duration_input == 5 else 12
        assert (
            queue_projection.hydrated_capability,
            queue_projection.provider_id,
            queue_projection.model_id,
            queue_projection.duration_input,
            queue_projection.request_duration.seconds if queue_projection.request_duration else None,
        ) == ("r2v", "fake", "fake-model", queue_duration_input, queue_request_duration)

        duration_code = "needs_replan" if expected_input > expected_slot else "reference_duration_confirmation_required"
        expected_codes = [
            "reference_asset_missing",
            "reference_images_clamped",
            duration_code,
        ]
        for projection in (quote_projection, precheck_detail, generate_detail, agent_projection):
            problems = cast(list[dict[str, object]], projection["problems"])
            assert [problem["code"] for problem in problems] == expected_codes
        assert [problem.code for problem in queue_projection.problems] == expected_codes

    await observe(9.5, 12)
    unit["duration_seconds"] = 13
    await observe(13, 12)


@pytest.mark.integration
async def test_malformed_references_block_all_public_consumers_without_queue_or_cost(
    db_factory,
    monkeypatch,
    tmp_path,
) -> None:
    capabilities = FakeReferenceCapabilityProjection(
        durations=(4, 8, 12),
        provider_id="fake",
        model_id="fake-model",
        max_reference_images=9,
    )
    unit: dict[str, Any] = {
        "unit_id": "E1U1",
        "shots": [{"text": "镜头"}],
        "references": [{"type": "character", "name": "甲"}, "bad-entry"],
        "duration_seconds": 4,
        "generated_assets": {},
    }
    script: dict[str, Any] = {
        "episode": 1,
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "video_units": [unit],
    }
    project: dict[str, Any] = {
        "generation_mode": "reference_video",
        "characters": {"甲": {"character_sheet": "characters/a.png"}},
        "episodes": [{"episode": 1, "script_file": "ep1.json"}],
    }
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "a.png").write_bytes(b"a")
    project_current = fake_reference_request_projector(capabilities=capabilities)

    class _ProjectManager:
        def load_project(self, _project_name):
            return project

        def load_script(self, _project_name, _script_file):
            return script

        def get_project_path(self, _project_name):
            return tmp_path

    pm = _ProjectManager()
    monkeypatch.setattr("server.services.cost_estimation.ConfigReferenceCapabilityProjection", lambda _r: capabilities)
    monkeypatch.setattr(reference_videos, "get_project_manager", lambda: pm)
    monkeypatch.setattr(reference_videos, "project_reference_unit_request", project_current)
    monkeypatch.setattr("server.services.video_batch_admission.project_reference_unit_request", project_current)
    monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: pm)
    monkeypatch.setattr("lib.reference_video.request_projection.project_reference_unit_request", project_current)

    enqueued: list[dict[str, Any]] = []

    class _Queue:
        async def enqueue_task(self, **kwargs):
            enqueued.append(kwargs)
            return {"task_id": "unexpected"}

    monkeypatch.setattr(reference_videos, "get_generation_queue", lambda: _Queue())

    with pytest.raises(HTTPException) as web_blocked:
        await reference_videos.generate_unit(
            project_name="demo",
            episode=1,
            unit_id="E1U1",
            user=CurrentUserInfo(id="u1", sub="test", role="admin"),
            _t=lambda key, **_params: key,
        )
    agent_response = await enqueue_videos.generate_video_episode_tool(
        ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)  # type: ignore[arg-type]
    ).handler({"script": "ep1.json"})
    service = CostEstimationService(ConfigResolver(db_factory), db_factory, project_path=tmp_path)
    quote = await service.compute(project, {"ep1.json": script}, project_name="demo")
    queue_projection = await reference_projection_for_queued_task(
        project=project,
        project_name="demo",
        payload={"script_file": "ep1.json"},
        resource_id="E1U1",
    )

    assert enqueued == []
    assert agent_response["is_error"] is True
    assert queue_projection is not None and queue_projection.blocking_problems
    web_codes = [problem["code"] for problem in cast(dict, web_blocked.value.detail)["problems"]]
    agent_codes = [problem["code"] for problem in agent_response["request_projections"][0]["problems"]]
    quote_projection = quote["episodes"][0]["segments"][0]["request_projection"]
    quote_codes = [problem["code"] for problem in quote_projection["problems"]]
    queue_codes = [problem.code for problem in queue_projection.problems]
    assert web_codes == agent_codes == quote_codes == queue_codes == ["reference_declaration_invalid"]
    assert quote["episodes"][0]["segments"][0]["estimate"]["video"] == {}
