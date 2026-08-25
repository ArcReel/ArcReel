"""Web 与 Agent 共用的整批准入判定适配层：分镜图生视频与参考生视频的当前状态判定。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.batch_admission import BatchAdmissionDecision
from lib.generation_result import GenerationAction, GenerationProblemCode, GenerationSelectionMode
from lib.narration_delivery import (
    POST_PRODUCTION,
    USE_TTS,
    NarrationDeliveryPreparation,
    NarrationDeliveryProblem,
    NarrationTtsStatus,
    prepare_narrated_video_duration,
)
from lib.reference_video.request_projection import ReferenceRequestOptions
from server.services import video_batch_admission as admission_mod
from server.services.video_batch_admission import admit_reference_video_batch, admit_storyboard_video_batch


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
    )

    assert admission.decision is BatchAdmissionDecision.ADMITTED
    assert admission.unit_ids == ("E1S01", "E1S02")
    assert probes == ["active_tasks"]


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


async def test_extra_tickets_join_the_same_verdict(monkeypatch, tmp_path: Path):
    """调用方在准入前就判死的目标（不存在的 ID、坏 unit）与本批共用一个结论。"""

    from lib.batch_admission import refused_ticket

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
