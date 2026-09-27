"""Web 与 Agent 共用的整批准入判定适配层：分镜图生视频与参考生视频的当前状态判定。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.generation.batch_admission import BatchAdmissionDecision
from lib.generation.generation_result import GenerationAction, GenerationProblemCode, GenerationSelectionMode
from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.reference_video.request_projection import ReferenceRequestOptions
from lib.script.reference_video.unit_capabilities import evaluate_reference_unit_capabilities
from lib.speech.narration_delivery import (
    POST_PRODUCTION,
    USE_TTS,
    NarrationDeliveryPreparation,
    NarrationDeliveryProblem,
    NarrationTtsStatus,
    prepare_narrated_video_duration,
)
from server.services.admission import video_batch_admission as admission_mod
from server.services.admission.video_batch_admission import admit_reference_video_batch, admit_storyboard_video_batch
from server.services.tasks.video_caps import reference_request_facts_lookup
from tests.factories import activate_reference_project, make_video_request_facts


def _script() -> dict[str, Any]:
    return {"episode": 1, "content_mode": "narration", "segments": []}


def _stub_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    active: list[dict[str, Any]] | None = None,
    active_tts: frozenset[str] = frozenset(),
) -> list[str]:
    probes: list[str] = []

    async def _active(**_kwargs):
        probes.append("active_tasks")
        return list(active or [])

    async def _tts(**_kwargs):
        probes.append("active_tts")
        return active_tts

    monkeypatch.setattr(admission_mod, "get_active_tasks_for_resources", _active)
    monkeypatch.setattr(admission_mod, "active_tts_resource_ids", _tts)
    return probes


def _preparation(*, problems=(), tts_status=NarrationTtsStatus.CURRENT, actual=9.5):
    narration = NarrationDeliveryPreparation(
        delivery=USE_TTS,
        unit_id="E1S01",
        speech_mode=None,
        tts_status=tts_status,
        artifact_path="audio/segment_E1S01.wav",
        basis_digest="basis",
        actual_duration_seconds=actual,
        problems=problems,
    )
    return prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=None,
    )


async def test_storyboard_post_production_admits_without_consulting_tts(monkeypatch, tmp_path: Path):
    """后期配音在分镜图生视频没有 TTS 输入可查，唯一还生效的整批闸门是在途任务冲突。"""

    probes = _stub_state(monkeypatch)

    admission = await admit_storyboard_video_batch(
        project_name="demo",
        project={},
        project_path=tmp_path,
        script=_script(),
        script_file="episode_1.json",
        items=[("E1S01", {"duration_seconds": 4}, "一个镜头"), ("E1S02", {}, "另一个镜头")],
        request_options=ReferenceRequestOptions(narration_delivery=POST_PRODUCTION),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        video_request_facts=make_video_request_facts(),
    )

    assert admission.decision is BatchAdmissionDecision.ADMITTED
    assert admission.unit_ids == ("E1S01", "E1S02")
    assert probes == ["active_tasks"]


async def test_storyboard_facts_failure_blocks_each_target_with_code_params_and_action(monkeypatch, tmp_path: Path):
    """分镜路线只有一个桶：事实失败时每个目标都带同一问题码、参数与修复指引，不塌成一句通用错误。"""
    _stub_state(monkeypatch)
    failure = VideoRequestFactsFailure(
        "video_supported_durations_incompatible",
        (("provider", "gemini-aistudio"), ("model", "veo-3.1"), ("resolution", "1080p"), ("capability", "i2v")),
    )
    admission = await admit_storyboard_video_batch(
        project_name="demo",
        project={},
        project_path=tmp_path,
        script=_script(),
        script_file="episode_1.json",
        items=[("E1S01", {}, "bad"), ("E1S02", {}, "also bad")],
        request_options=ReferenceRequestOptions(narration_delivery=POST_PRODUCTION),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        video_request_facts=failure,
    )
    assert [ticket.unit_id for ticket in admission.tickets] == ["E1S01", "E1S02"]
    for ticket in admission.tickets:
        assert [(problem.code, problem.action, problem.params) for problem in ticket.problems] == [
            (failure.code, GenerationAction.CONFIGURE_PROVIDER, failure.parameters())
        ]


async def test_storyboard_use_tts_reports_each_units_delivery_problem(monkeypatch, tmp_path: Path):
    _stub_state(monkeypatch)

    async def _prepare(**_kwargs):
        return _preparation(
            problems=(
                NarrationDeliveryProblem(
                    code="tts_missing",
                    reason="tts_audio_missing",
                    action="generate_tts",
                    locations=(),
                ),
            ),
            tts_status=NarrationTtsStatus.MISSING,
            actual=None,
        )

    monkeypatch.setattr(admission_mod, "prepare_current_storyboard_narrated_video_duration", _prepare)

    admission = await admit_storyboard_video_batch(
        project_name="demo",
        project={},
        project_path=tmp_path,
        script=_script(),
        script_file="episode_1.json",
        items=[("E1S01", {"duration_seconds": 4}, "一个镜头")],
        request_options=ReferenceRequestOptions(narration_delivery=USE_TTS),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        video_request_facts=make_video_request_facts(),
    )

    assert admission.decision is BatchAdmissionDecision.BLOCKED
    problem = admission.tickets[0].problems[0]
    assert problem.code == "tts_missing"
    assert problem.action is GenerationAction.GENERATE_TTS


async def test_an_active_task_conflicts_before_anything_is_projected(monkeypatch, tmp_path: Path):
    """在途任务是整批的前置冲突：占用中的 unit 不再解析，也不让整批入队。"""

    _stub_state(monkeypatch, active=[{"resource_id": "E1U1", "id": "task-1", "status": "queued"}])
    projected: list[str] = []

    async def _project(**kwargs):
        projected.append(kwargs["unit"]["unit_id"])
        raise AssertionError("occupied units must not be projected")

    async def _options(*, options, **_kwargs):
        return options

    monkeypatch.setattr(admission_mod, "project_reference_unit_request", _project)
    monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _options)

    admission = await admit_reference_video_batch(
        project_name="demo",
        project={},
        project_path=tmp_path,
        script={"video_units": []},
        script_file="episode_1.json",
        units=[{"unit_id": "E1U1", "text": "镜头"}],
        request_options=ReferenceRequestOptions(),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
    )

    assert projected == []
    assert admission.decision is BatchAdmissionDecision.BLOCKED
    problem = admission.tickets[0].problems[0]
    assert problem.code == GenerationProblemCode.ACTIVE_TASK_CONFLICT
    assert problem.action is GenerationAction.WAIT_FOR_TASK
    assert problem.params["task_id"] == "task-1"


async def test_a_unit_that_cannot_be_enqueued_is_refused_with_its_own_code(monkeypatch, tmp_path: Path):
    _stub_state(monkeypatch)

    async def _project(**_kwargs):
        raise AssertionError("unenqueueable units must not be projected")

    monkeypatch.setattr(admission_mod, "project_reference_unit_request", _project)

    def _reject(_unit):
        raise ValueError("正文为空")

    admission = await admit_reference_video_batch(
        project_name="demo",
        project={},
        project_path=tmp_path,
        script={"video_units": []},
        script_file="episode_1.json",
        units=[{"unit_id": "E1U1"}],
        request_options=ReferenceRequestOptions(),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        spec_check=_reject,
    )

    assert admission.decision is BatchAdmissionDecision.BLOCKED
    assert admission.tickets[0].problems[0].code == GenerationProblemCode.UNIT_REQUEST_INVALID


async def test_text_only_unit_on_image_only_model_blocks_the_whole_batch(monkeypatch, tmp_path: Path):
    from tests.fakes import fake_reference_request_projector

    _stub_state(monkeypatch)

    async def _options(*, options, **_kwargs):
        return options

    monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _options)
    monkeypatch.setattr(
        admission_mod,
        "project_reference_unit_request",
        fake_reference_request_projector(durations=(3,), text_to_video=False),
    )

    admission = await admit_reference_video_batch(
        project_name="demo",
        project={},
        project_path=tmp_path,
        script={"video_units": []},
        script_file="episode_1.json",
        units=[
            {"unit_id": "E1U1", "text": "空镜头一", "duration_seconds": 3},
            {"unit_id": "E1U2", "text": "空镜头二", "duration_seconds": 3},
        ],
        request_options=ReferenceRequestOptions(),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
    )

    assert admission.decision is BatchAdmissionDecision.BLOCKED
    assert [ticket.problems[0].code for ticket in admission.tickets] == [
        "video_capability_missing_t2v",
        "video_capability_missing_t2v",
    ]


async def test_reference_facts_failure_keeps_action_for_each_unit(monkeypatch, tmp_path: Path, set_video_request_facts):
    _stub_state(monkeypatch)
    failure = VideoRequestFactsFailure(
        "video_capability_reference_unavailable", (("provider", "ark"), ("model", "removed"))
    )
    set_video_request_facts(failure)
    project = {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION}
    (tmp_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    admission = await admit_reference_video_batch(
        project_name="demo",
        project=project,
        project_path=tmp_path,
        script={"video_units": []},
        script_file="episode_1.json",
        units=[
            {"unit_id": "E1U1", "text": "空镜头一", "duration_seconds": 4},
            {"unit_id": "E1U2", "text": "空镜头二", "duration_seconds": 4},
        ],
        request_options=ReferenceRequestOptions(),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
    )
    assert [ticket.unit_id for ticket in admission.tickets] == ["E1U1", "E1U2"]
    for ticket in admission.tickets:
        assert [(p.code, p.action) for p in ticket.problems] == [(failure.code, GenerationAction.CONFIGURE_PROVIDER)]


async def test_reference_bucket_failure_blocks_only_its_units_and_withholds_the_healthy_bucket(
    monkeypatch, tmp_path: Path
):
    """i2v 桶解析不出、r2v 桶健康：无图单元带自己的问题码阻断，有图单元本身通过，整批只因前者受阻。"""
    from tests.fakes import fake_reference_request_facts, fake_reference_request_projector

    _stub_state(monkeypatch)
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "a.png").write_bytes(b"\x89PNG")
    project = {"generation_mode": "reference_video", "characters": {"阿离": {"character_sheet": "characters/a.png"}}}
    failure = VideoRequestFactsFailure(
        "reference_supported_durations_incompatible",
        (("provider", "gemini-aistudio"), ("model", "veo-3.1"), ("resolution", "4k")),
    )

    async def _options(*, options, **_kwargs):
        return options

    monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _options)
    monkeypatch.setattr(
        admission_mod,
        "project_reference_unit_request",
        fake_reference_request_projector(
            request_facts=fake_reference_request_facts(durations=(4,), failures={"i2v": failure})
        ),
    )

    admission = await admit_reference_video_batch(
        project_name="demo",
        project=project,
        project_path=tmp_path,
        script={"video_units": []},
        script_file="episode_1.json",
        units=[
            {"unit_id": "E1U1", "text": "@[阿离] 走入画面。", "duration_seconds": 4},
            {"unit_id": "E1U2", "text": "空镜头。", "duration_seconds": 4},
        ],
        request_options=ReferenceRequestOptions(),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
    )

    assert admission.decision is BatchAdmissionDecision.BLOCKED
    tickets = {ticket.unit_id: ticket for ticket in admission.tickets}
    assert tickets["E1U1"].admitted
    assert tickets["E1U1"].problems == ()
    assert [(p.code, p.action, p.params) for p in tickets["E1U2"].problems] == [
        (failure.code, GenerationAction.CONFIGURE_PROVIDER, {"capability": "i2v", **failure.parameters()})
    ]
    withheld = admission.withheld_problem_for(tickets["E1U1"])
    assert withheld is not None
    assert (withheld.code, withheld.params) == (
        GenerationProblemCode.BATCH_ADMISSION_WITHHELD,
        {"blocked_unit_ids": ["E1U2"]},
    )
    assert admission.withheld_problem_for(tickets["E1U2"]) is None


async def test_extra_tickets_join_the_same_verdict(monkeypatch, tmp_path: Path):
    """调用方在准入前就判死的目标（不存在的 ID、坏 unit）与本批共用一个结论。"""

    from lib.generation.batch_admission import refused_ticket

    _stub_state(monkeypatch)

    async def _project(**kwargs):
        class _Projection:
            unit_id = kwargs["unit"]["unit_id"]
            blocking_problems: tuple[object, ...] = ()
            cost = None
            planned_duration = 4
            request_duration = None
            current_visual_duration = None

            def to_advisory_payload(self):
                return {"allowed": True, "unit_id": self.unit_id, "problems": []}

        return _Projection()

    async def _options(*, options, **_kwargs):
        return options

    monkeypatch.setattr(admission_mod, "project_reference_unit_request", _project)
    monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _options)

    admission = await admit_reference_video_batch(
        project_name="demo",
        project={},
        project_path=tmp_path,
        script={"video_units": []},
        script_file="episode_1.json",
        units=[{"unit_id": "E1U1", "text": "镜头"}],
        request_options=ReferenceRequestOptions(),
        operation="generate_videos",
        selection=GenerationSelectionMode.EXPLICIT,
        extra_tickets=[
            refused_ticket(
                "E9U9",
                code=GenerationProblemCode.UNIT_NOT_FOUND,
                detail="unit E9U9 不在 video_units 中",
                action=GenerationAction.FIX_INPUT,
            )
        ],
    )

    assert admission.decision is BatchAdmissionDecision.BLOCKED
    assert admission.unit_ids == ("E9U9", "E1U1")


def _activated_project_with_unclaimed_sheet(tmp_path: Path) -> dict[str, Any]:
    """已激活产物清单的项目：张三的图由补录认领；李四的图在盘上、路径已登记，但清单从未认领。"""
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "张三.png").write_bytes(b"image")
    project = activate_reference_project(
        tmp_path, {"characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}}}
    )
    project["characters"]["李四"] = {"description": "y", "character_sheet": "characters/李四.png"}
    (tmp_path / "characters" / "李四.png").write_bytes(b"image")
    (tmp_path / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return project


async def test_admission_buckets_an_unclaimed_sheet_like_the_canvas_and_blocks(
    monkeypatch, tmp_path: Path, set_video_request_facts
):
    """整批准入与画布逐单元结论同判据：图在盘上但清单未认领的单元两侧都落 i2v，准入阻断而非按 r2v 放行。"""
    _stub_state(monkeypatch)

    async def _options(*, options, **_kwargs):
        return options

    monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _options)
    set_video_request_facts(
        {
            "i2v": make_video_request_facts(
                route="reference_video", generation_type="i2v", supported_durations=(5, 10), allowed_durations=(5, 10)
            ),
            "r2v": make_video_request_facts(
                route="reference_video", generation_type="r2v", supported_durations=(9,), allowed_durations=(9,)
            ),
        }
    )
    project = _activated_project_with_unclaimed_sheet(tmp_path)
    units = [
        {"unit_id": "E1U1", "text": "镜头1：@[张三] 推门", "duration_seconds": 9},
        {"unit_id": "E1U2", "text": "镜头2：@[李四] 回头", "duration_seconds": 9},
    ]
    script = {"episode": 1, "generation_mode": "reference_video", "video_units": units}

    admission = await admit_reference_video_batch(
        project_name="demo",
        project=project,
        project_path=tmp_path,
        script=script,
        script_file="scripts/episode_1.json",
        units=units,
        request_options=ReferenceRequestOptions(),
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        confirmed_request_durations={"E1U1": 9, "E1U2": 9},
    )
    capabilities = await evaluate_reference_unit_capabilities(
        project, tmp_path, units, request_facts=reference_request_facts_lookup(project)
    )

    assert admission.decision is BatchAdmissionDecision.BLOCKED
    claimed, unclaimed = admission.tickets
    assert (claimed.projection["hydrated_capability"], claimed.problems) == ("r2v", ())
    assert unclaimed.projection["hydrated_capability"] == "i2v"
    assert [problem.code for problem in unclaimed.problems][:2] == [
        "reference_asset_missing",
        "reference_capability_changed",
    ]
    assert [capability.generation_type for capability in capabilities] == ["r2v", "i2v"]
