"""Tests for enqueue_videos."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifact_manifest import ArtifactStatus
from lib.generation_queue import GenerationQueue
from lib.generation_result import GenerationBatchResult
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.resource_paths import resource_relative_path
from lib.script_skeleton import SkeletonRouteMismatchError
from lib.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from server.media_tools import videos as enqueue_videos_mod
from server.media_tools.context import ToolContext
from server.media_tools.videos import generate_videos_tool
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _CLAIMED_BASIS_DIGEST,
    _activate_unbound_project,
    _call,
    _fake_caps_resolver,
    _fake_reference_projection,
    _fake_scene_batch,
    _generation_result,
    _reference_video_script,
    _use_reference_route,
    _videos_tool_for_scope,
)
from tests.speech_contract_cases import SPEECH_CONTRACT_CASES, SpeechContractCase


def _episode_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "episode")


def _scene_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "scene")


def _all_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "all")


def _selected_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "selected")


def _select_manual_video(
    project_path: Path,
    *,
    resource_type: str,
    resource_id: str,
    content: bytes,
) -> str:
    artifact_path = resource_relative_path(resource_type, resource_id)
    staged = project_path / f".{resource_type}-{resource_id}.upload.mp4"
    staged.write_bytes(content)
    VersionManager(project_path).commit_staged_version(
        resource_type,
        resource_id,
        "",
        staged_file=staged,
        current_file=project_path / artifact_path,
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )
    return artifact_path


class _MissingEverythingResolver:
    """An active Manifest that never admits a formal artifact as usable."""

    def compare(self, key, *, artifact_path=None):
        from lib.artifact_manifest import ArtifactComparison

        return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path or "")


def _activated_project(project_dir: Path, storyboard_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """构造一个已迁移到当前 schema 的项目，并把点名的分镜图登记进产物清单。

    直接调用入队构造函数的用例不经 pm，项目形态得在这里补齐：清单是读取已生成产物的
    唯一口径，没有登记的分镜图不能作为视频输入。
    """

    from lib.artifact_manifest import (
        ArtifactKey,
        ArtifactManifest,
        ArtifactManifestEntry,
        ProjectArtifactManifestAdapter,
    )

    project: dict[str, Any] = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project_dir))
    for resource_id, artifact_path in (storyboard_ids or {}).items():
        manifest.register_entry_transactionally(
            ArtifactKey.episode_storyboard(1, resource_id),
            ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=_CLAIMED_BASIS_DIGEST),
        )
    return project


def _blocked_problems_of(result: GenerationBatchResult) -> dict[str, tuple[str, str]]:
    """Map each problem-carrying unit of a finished result to its ``(code, action)`` pair."""

    return {
        item.unit_id: (item.problem.code, item.problem.action.value)
        for item in result.items
        if item.problem is not None
    }


def _refused_problems(refused: list[Any]) -> dict[str, tuple[str, str]]:
    """Map each refused ticket's unit ID to its ``(code, action)`` pair."""

    return {ticket.unit_id: (problem.code, problem.action.value) for ticket in refused for problem in ticket.problems}


# ---------------------------------------------------------------------------
# enqueue_videos
# ---------------------------------------------------------------------------


async def test_generate_videos_episode_scope_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import videos as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        for spec in specs:
            br = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"videos/scene_{spec.resource_id}.mp4"},
            )
            if on_success:
                on_success(br)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True


async def test_generate_videos_episode_scope_declares_the_missing_only_selection_it_performs(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """整集生成从不强制重生：已有可用片段一律复用，所以选择模式如实报 missing-only。"""
    from server.media_tools import videos as mod

    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S01.png"
    }
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert result.selection.value == "missing_only"
    assert result.succeeded == ["E1S01"]


async def test_generate_videos_episode_scope_skips_current_clip(fake_ctx: ToolContext, monkeypatch) -> None:
    """整集调用复用仍是 current 的旧片段。"""
    from lib.artifact_manifest import ArtifactComparison
    from server.media_tools import videos as mod

    project = fake_ctx.pm.project_payload  # type: ignore[attr-defined]
    project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S01.png",
        "video_clip": "videos/scene_E1S01.mp4",
    }

    class _CurrentCurrency:
        def compare(self, key, *, artifact_path):
            return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=artifact_path)

        def resolve_usable_entry(self, key, *, artifact_path):
            from lib.artifact_manifest import ArtifactManifestEntry

            return ArtifactManifestEntry(artifact_path=artifact_path, basis_digest="selected")

        def compare_frozen_entry(self, key, entry):
            return self.compare(key, artifact_path=entry.artifact_path)

        def artifact_content_digest(self, artifact_path):
            return "0" * 64

    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _CurrentCurrency())
    enqueued: list[str] = []

    async def _batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _batch)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert enqueued == []
    assert result.succeeded == []
    assert [entry.unit_id for entry in result.skipped] == ["E1S01"]


async def test_generate_videos_episode_scope_blocks_a_clip_whose_manifest_state_is_unreadable(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """整集调用里某片段的 Manifest 比对抛错（BLOCKED）时必须报 blocked，不能落入
    「既不可复用也不算 blocked」的空档而被当作缺失去付费重生——不可读不等于没有。"""
    from lib.artifact_manifest import ArtifactComparison
    from server.media_tools import videos as mod

    project = fake_ctx.pm.project_payload  # type: ignore[attr-defined]
    project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1  # type: ignore[attr-defined]
    segments = fake_ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
    segments[0]["generated_assets"] = {
        "storyboard_image": "storyboards/scene_E1S01.png",
        "video_clip": "videos/scene_E1S01.mp4",
    }

    class _Resolver:
        def compare(self, key, *, artifact_path):
            if artifact_path == "videos/scene_E1S01.mp4":
                raise RuntimeError("manifest sidecar unreadable")
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

        def resolve_usable_entry(self, key, *, artifact_path):
            raise RuntimeError("manifest sidecar unreadable")

        def compare_frozen_entry(self, key, entry):
            return self.compare(key, artifact_path=entry.artifact_path)

        def artifact_content_digest(self, artifact_path):
            return "0" * 64

    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _Resolver())
    enqueued: list[str] = []

    async def _batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _batch)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    result = _generation_result(out)
    assert enqueued == []
    assert result.succeeded == []
    assert result.blocked == ["E1S01"]
    blocked_item = next(item for item in result.items if item.unit_id == "E1S01")
    assert blocked_item.problem is not None
    assert blocked_item.problem.code == "generation_artifact_state_unavailable"


async def test_generate_videos_episode_scope_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from lib.generation_queue_client import TaskSpec
    from server.media_tools import videos as mod

    _activate_unbound_project(fake_ctx)
    spec = TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id="E1S01",
        prompt="test",
        script_file="episode_1.json",
    )

    def fake_build_specs(**_kwargs):
        return [spec], []

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "build_storyboard_video_specs", fake_build_specs)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    enqueue.assert_not_awaited()


async def test_generate_videos_episode_scope_resolves_episode_from_canonical_filename(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    """剧集身份可由规范文件名解析，但不自带 episode 字段的剧本读不出产物状态。

    身份解析按规范文件名兜底，这一批确实是按第 2 集构造的；而产物清单只认自带 episode
    字段、与账本绑定一致的剧本，该集分镜图的状态因此不可读，整批停在建任务之前。
    """
    from server.media_tools import videos as mod
    from server.services import video_batch_admission as admission_mod

    fake_ctx.pm.script_payload.pop("episode")  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "episodes": [{"episode": 2, "script_file": "scripts/episode_2.json"}],
        }
    )
    captured: dict[str, int] = {}
    build_video_specs = admission_mod.build_storyboard_video_specs

    def _capture_episode(**kwargs):
        captured["episode"] = kwargs["episode"]
        return build_video_specs(**kwargs)

    async def _batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        for spec in specs:
            if on_success is not None:
                on_success(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id="t1",
                        status="succeeded",
                        result={"file_path": f"videos/scene_{spec.resource_id}.mp4"},
                    )
                )
        return [], []

    monkeypatch.setattr(mod, "build_storyboard_video_specs", _capture_episode)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _batch)
    out = await _call(_episode_scope(fake_ctx), {"script": "episode_2.json"})

    assert captured == {"episode": 2}
    result = _generation_result(out)
    assert result.blocked == ["E1S01"]
    assert _blocked_problems_of(result) == {"E1S01": ("generation_unit_input_unusable", "generate_dependency")}


async def test_generate_videos_episode_scope_non_dict_generated_assets_does_not_abort_batch(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """整集入队先按 generated_assets.video_clip 过滤已完成条目。容器被外部编辑损坏为非 dict
    时该过滤须按「未生成」处理，而不是在 pending 过滤阶段就抛未处理 AttributeError；随后该条目
    以自己的问题码拦住整批，本次不创建任何任务。"""
    from server.media_tools import videos as mod

    project_dir = fake_ctx.pm.get_project_path("demo")
    (project_dir / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {
            "segment_id": "E1S01",
            "novel_text": "第一段旁白。",
            "video_prompt": "脏数据",
            "generated_assets": ["bad"],
        },
        {
            "segment_id": "E1S02",
            "novel_text": "第二段旁白。",
            "video_prompt": "合法条目",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        },
    ]
    enqueued: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    # E1S01 的分镜图绑定不可用：整批准入不成立，零任务入队，合法条目也如实报告被搁置的原因。
    assert enqueued == []
    result = _generation_result(out)
    assert sorted(result.blocked) == ["E1S01", "E1S02"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S01"] == "generation_unit_input_unusable"
    assert codes["E1S02"] == "generation_batch_admission_withheld"
    assert out.get("is_error") is True


async def test_generate_videos_episode_scope_error(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.script_payload = {"content_mode": "narration", "segments": [], "episode": 1}  # type: ignore[attr-defined]
    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


async def test_generate_reference_video_rejects_unbound_active_script_before_generation(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import videos as mod

    _activate_unbound_project(fake_ctx, generation_mode="reference_video")
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    enqueue.assert_not_awaited()


async def test_generate_reference_video_legacy_unresolvable_episode_fails_before_generation(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload.pop("episode")  # type: ignore[attr-defined]
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_episode_scope(fake_ctx), {"script": "draft.json"})

    assert out.get("is_error") is True
    assert "无法确定集号" in out["content"][0]["text"]
    enqueue.assert_not_awaited()


async def test_generate_videos_episode_scope_reference_rejects_malformed_unit_container(fake_ctx: ToolContext) -> None:
    """``video_units`` 非数组：生成模式闸门只问键在不在，容器校验落在入队侧，
    须报出可定位的结构错误而不是下传到 unit 迭代抛 TypeError。"""
    _use_reference_route(fake_ctx)
    for malformed in (
        {"E1U1": {}},
        {},
        "",
        False,
        None,
    ):
        # 键在场即按类型判定，不看真值：``{}`` / ``""`` / ``False`` 同样是类型错误，
        # 报成「为空」会把成因埋掉。
        fake_ctx.pm.script_payload = _reference_video_script(video_units=malformed)  # type: ignore[attr-defined]
        tool_obj = _episode_scope(fake_ctx)
        out = await _call(tool_obj, {"script": "episode_1.json"})
        assert out.get("is_error") is True
        text = out["content"][0]["text"]
        assert "video_units 必须是数组" in text


async def test_generate_videos_episode_scope_reference_duration_needs_confirmation(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """申请秒数与剧本总时长不一致时，首次调用不入队，返回内容含总时长/申请秒数/差异说明。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    async def fake_active_tasks(**_kwargs):
        return []

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr("server.services.video_batch_admission.get_active_tasks_for_resources", fake_active_tasks)

    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "E1U1" in text
    assert "5" in text and "8" in text
    assert "费用" in text and "本次请求" in text
    assert "confirmed_request_duration_seconds" in text
    projection = out["request_projections"][0]
    assert projection == {
        "allowed": False,
        "kind": "reference_request_projection",
        "advisory": True,
        "unit_id": "E1U1",
        "declared_capability": "r2v",
        "hydrated_capability": "r2v",
        "provider_id": "fake",
        "model_id": "fake-r2v",
        "planned_duration": 5,
        "current_visual_duration": None,
        "duration_input": 5,
        "request_duration": 8,
        "request_cost": {
            "amount": 0.64,
            "currency": "USD",
            "provider_id": "fake",
            "model_id": "fake-r2v",
            "request_duration_seconds": 8,
        },
        "problems": [
            {
                "code": "reference_duration_confirmation_required",
                "blocking": True,
                "unit_id": "E1U1",
                "locations": [{"path": ["duration_seconds"], "line": None}],
                "params": {
                    "script_duration": 5,
                    "duration_input": 5,
                    "request_duration": 8,
                    "adjustment": "up",
                    "current_visual_duration": None,
                },
                "action": "confirm_duration",
            }
        ],
    }
    assert enqueued == []
    # 待确认不是 prose-only 的死角：调用方能拿到机器可读结论，不必解析文本猜测。
    result = _generation_result(out)
    assert result.blocked == ["E1U1"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "reference_duration_confirmation_required"
    assert item.problem.action == "confirm_request_duration"


async def test_generate_videos_episode_scope_reference_returns_structured_projection_blocker(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    """Agent 失败信封保留公共投影的稳定 problem 字段，不只返回人读文本。"""
    from lib.reference_video.request_projection import ProjectionProblem

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    class _BlockedProjection:
        unit_id = "E1U1"
        cost = None
        planned_duration = 5
        request_duration = None
        current_visual_duration = None
        blocking_problems = (
            ProjectionProblem(
                code="reference_supported_durations_missing",
                blocking=True,
                params=(("provider", "fake"), ("model", "fake-model")),
            ),
        )

        def to_advisory_payload(self):
            return {
                "allowed": False,
                "kind": "reference_request_projection",
                "advisory": True,
                "unit_id": self.unit_id,
                "declared_capability": "i2v",
                "hydrated_capability": "i2v",
                "provider_id": None,
                "model_id": None,
                "planned_duration": 5,
                "duration_input": 5,
                "request_duration": None,
                "problems": [problem.to_payload(unit_id=self.unit_id) for problem in self.blocking_problems],
            }

    async def _blocked(**_kwargs):
        return _BlockedProjection()

    monkeypatch.setattr("server.services.video_batch_admission.project_reference_unit_request", _blocked)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert out["request_projections"][0] == {
        "allowed": False,
        "kind": "reference_request_projection",
        "advisory": True,
        "unit_id": "E1U1",
        "declared_capability": "i2v",
        "hydrated_capability": "i2v",
        "provider_id": None,
        "model_id": None,
        "planned_duration": 5,
        "duration_input": 5,
        "request_duration": None,
        "problems": [
            {
                "code": "reference_supported_durations_missing",
                "blocking": True,
                "unit_id": "E1U1",
                "locations": [{"path": ["duration_seconds"], "line": None}],
                "params": {"provider": "fake", "model": "fake-model"},
                "action": "configure_video_model",
            }
        ],
    }


def test_every_video_agent_tool_exposes_narration_delivery(fake_ctx: ToolContext) -> None:
    """整批与单条走同一准入，交付方式由请求显式选择，批量入口不得省略该选项。"""

    tools = (
        _episode_scope(fake_ctx),
        _all_scope(fake_ctx),
        _selected_scope(fake_ctx),
        _scene_scope(fake_ctx),
    )

    for tool_obj in tools:
        schema = tool_obj.input_schema
        assert isinstance(schema, dict)
        properties = schema.get("properties")
        assert isinstance(properties, dict)
        assert properties["narration_delivery"]["enum"] == ["post_production", "use_tts"]
        assert "confirmed_request_duration_seconds" in properties


async def test_generate_videos_episode_scope_reference_duration_confirm_enqueues(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """带精确申请档位的再次调用按取档结果入队并生成成功。"""
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        for spec in specs:
            enqueued.append(spec)
            if on_success:
                on_success(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id="t1",
                        status="succeeded",
                        result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
                    )
                )
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(fake_ctx)
    out = await _call(
        tool_obj,
        {"script": "episode_1.json", "confirmed_request_duration_seconds": 8},
    )

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_videos_reference_force_false_reuses_existing_video(fake_ctx: ToolContext, db_factory) -> None:
    _use_reference_route(fake_ctx)
    video_path = _select_manual_video(
        fake_ctx.project_path,
        resource_type="reference_videos",
        resource_id="E1U1",
        content=b"existing-video",
    )
    script = _reference_video_script()
    script["video_units"][0]["generated_assets"] = {"video_clip": video_path}
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    fake_ctx.queue = GenerationQueue(session_factory=db_factory)

    out = await _call(
        generate_videos_tool(fake_ctx),
        {
            "script": "episode_1.json",
            "target": {"scope": "selected", "ids": ["E1U1"]},
            "narration_delivery": "use_tts",
        },
    )

    assert out.get("is_error") is not True, out
    assert (await fake_ctx.queue.list_tasks(project_name="demo"))["items"] == []
    assert [item["unit_id"] for item in out["generation_result"]["skipped"]] == ["E1U1"]


async def test_generate_videos_scene_force_false_reuses_existing_video(fake_ctx: ToolContext) -> None:
    video_path = _select_manual_video(
        fake_ctx.project_path,
        resource_type="videos",
        resource_id="E1S01",
        content=b"existing-video",
    )
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"]["video_clip"] = video_path  # type: ignore[attr-defined]

    out = await _call(
        generate_videos_tool(fake_ctx),
        {"script": "episode_1.json", "target": {"scope": "scene", "ids": ["E1S01"]}},
    )

    assert out.get("is_error") is not True, out
    assert (await fake_ctx.queue.list_tasks(project_name="demo"))["items"] == []
    assert [item["unit_id"] for item in out["generation_result"]["skipped"]] == ["E1S01"]


@pytest.mark.parametrize(
    "target,force",
    [
        ({"scope": "episode"}, False),
        ({"scope": "episode", "episode": 2}, False),
        ({"scope": "episode", "episode": 1, "ids": ["E1S01"]}, False),
        ({"scope": "episode", "episode": 1, "extra": True}, False),
        ({"scope": "all", "episode": 1}, False),
        ({"scope": "all", "ids": ["E1S01"]}, False),
        ({"scope": "scene", "ids": []}, False),
        ({"scope": "scene", "ids": ["E1S01", "E1S02"]}, False),
        ({"scope": "scene", "ids": ["E1S01"], "episode": 1}, False),
        ({"scope": "selected"}, False),
        ({"scope": "selected", "ids": ["E1S01"], "episode": 1}, False),
        ({"scope": "all"}, True),
        ({"scope": "episode", "episode": 1}, True),
    ],
)
async def test_generate_videos_rejects_invalid_target_or_non_explicit_force(
    fake_ctx: ToolContext,
    target: dict[str, Any],
    force: bool,
) -> None:
    out = await _call(
        generate_videos_tool(fake_ctx),
        {"script": "episode_1.json", "target": target, "force": force},
    )

    assert out.get("is_error") is True


async def test_generate_videos_ignores_legacy_batch_checkpoint_files(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import videos as mod

    checkpoint = fake_ctx.project_path / "videos" / ".checkpoint_ep1.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True
    assert checkpoint.read_text(encoding="utf-8") == "not-json"
    assert list(checkpoint.parent.glob(".checkpoint_*.json")) == [checkpoint]


def test_generate_videos_definition_has_only_the_unified_name_and_no_resume(fake_ctx: ToolContext) -> None:
    definition = generate_videos_tool(fake_ctx)

    assert definition.name == "generate_videos"
    assert "resume" not in definition.input_schema["properties"]
    target_branches = definition.input_schema["properties"]["target"]["oneOf"]
    assert {branch["properties"]["scope"]["const"] for branch in target_branches} == {
        "episode",
        "scene",
        "all",
        "selected",
    }
    assert all(branch["additionalProperties"] is False for branch in target_branches)
    assert not any(
        hasattr(enqueue_videos_mod, name)
        for name in (
            "generate_video_episode_tool",
            "generate_video_scene_tool",
            "generate_video_all_tool",
            "generate_video_selected_tool",
        )
    )


async def test_generate_videos_rejects_retired_resume_parameter(fake_ctx: ToolContext) -> None:
    out = await _call(
        generate_videos_tool(fake_ctx),
        {
            "script": "episode_1.json",
            "target": {"scope": "episode", "episode": 1},
            "resume": True,
        },
    )

    assert out.get("is_error") is True
    assert "durable batch" in out["content"][0]["text"]


async def test_generate_videos_resubmits_only_remaining_ids_from_a_durable_batch(
    fake_ctx: ToolContext,
) -> None:
    from lib.db.base import DEFAULT_USER_ID
    from server.tool_runtime import CallerContext

    segments = fake_ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
    segments.append(
        {
            **segments[0],
            "segment_id": "E1S02",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        }
    )
    (fake_ctx.project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
    queue = fake_ctx.queue
    fake_ctx.caller = CallerContext(user_id=DEFAULT_USER_ID, source="mcp")

    first = await _call(
        generate_videos_tool(fake_ctx),
        {"script": "episode_1.json", "target": {"scope": "episode", "episode": 1}},
    )
    first_batch = first["generation_batch"]
    first_task = await queue.claim_next_task(media_type="video")
    assert first_task is not None, first
    await queue.mark_task_succeeded(first_task["task_id"], {"file_path": "videos/scene_E1S01.mp4"})
    second_task = await queue.claim_next_task(media_type="video")
    assert second_task is not None
    await queue.mark_task_failed(second_task["task_id"], "provider failed")
    video_path = _select_manual_video(
        fake_ctx.project_path,
        resource_type="videos",
        resource_id="E1S01",
        content=b"finished-video",
    )
    segments[0]["generated_assets"]["video_clip"] = video_path

    terminal = await queue.get_generation_batch(project_name="demo", batch_id=first_batch["batch_id"])
    assert terminal.done is True
    assert terminal.generation_result is not None
    assert terminal.generation_result.succeeded == ["E1S01"]
    assert terminal.generation_result.failed == ["E1S02"]

    retried = await _call(
        generate_videos_tool(fake_ctx),
        {
            "script": "episode_1.json",
            "target": {"scope": "selected", "ids": ["E1S01", "E1S02"]},
            "force": False,
        },
    )

    assert [item["unit_id"] for item in retried["generation_batch"]["skipped"]] == ["E1S01"]
    assert [(item["unit_id"], item["status"]) for item in retried["generation_batch"]["members"]] == [
        ("E1S02", "queued")
    ]
    assert len((await queue.list_tasks(project_name="demo"))["items"]) == 3


async def test_generate_videos_episode_scope_confirms_two_tiers_in_one_batch(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """一批里档位不止一个时按 unit 确认，原目标集合仍作为一批重发，不必拆成几次调用。"""
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script(  # type: ignore[attr-defined]
        video_units=[
            {
                "unit_id": "E1U1",
                "text": "@张三 推门",
                "duration_seconds": 5,
            },
            {
                "unit_id": "E1U2",
                "text": "@张三 回头",
                "duration_seconds": 6,
            },
        ]
    )
    tiers = {"E1U1": 8, "E1U2": 12}

    def fake_precheck(_ctx, unit):
        seconds = tiers[str(unit.get("unit_id"))]
        return DurationSlot(seconds=seconds, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        for spec in specs:
            enqueued.append(spec)
            if on_success:
                on_success(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id=f"t-{spec.resource_id}",
                        status="succeeded",
                        result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
                    )
                )
        return [], []

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = _episode_scope(fake_ctx)

    # 未确认：两个档位都在结论里，零任务入队。
    unconfirmed = await _call(tool_obj, {"script": "episode_1.json"})
    assert enqueued == []
    listed = {
        tier["request_duration_seconds"]: tier["unit_ids"]
        for tier in unconfirmed["batch_admission"]["confirmation"]["tiers"]
    }
    assert listed == {8: ["E1U1"], 12: ["E1U2"]}

    out = await _call(
        tool_obj,
        {"script": "episode_1.json", "confirmed_request_durations": {"E1U1": 8, "E1U2": 12}},
    )

    assert out.get("is_error") is not True, out
    assert sorted(spec.resource_id for spec in enqueued) == ["E1U1", "E1U2"]
    # 各任务带的是自己那一档确认：worker 重投影时读任务上的这份选项，
    # 只写整批共用的一份会让准入已接受的档位在执行期重新变成待确认。
    confirmed = {
        spec.resource_id: (spec.payload or {})["reference_request_options"]["confirmed_request_duration_seconds"]
        for spec in enqueued
    }
    assert confirmed == {"E1U1": 8, "E1U2": 12}


@pytest.mark.parametrize(
    "invalid",
    [0, -1, 9.5, True, "12"],
    ids=["zero", "negative", "fraction", "boolean", "string"],
)
def test_confirmed_request_durations_rejects_non_positive_int(invalid: object) -> None:
    """按 unit 记的档位与标量档位同一口径：非正整数在入口就拒绝。"""
    from server.media_tools.videos import _confirmed_request_durations

    with pytest.raises(ValueError, match="必须是大于 0 的整数秒档位"):
        _confirmed_request_durations({"confirmed_request_durations": {"E1U1": invalid}})


def test_confirmed_request_durations_rejects_non_mapping() -> None:
    from server.media_tools.videos import _confirmed_request_durations

    with pytest.raises(ValueError, match="必须是 unit_id 到秒数档位的对象"):
        _confirmed_request_durations({"confirmed_request_durations": [8]})


def test_every_video_agent_tool_exposes_per_unit_confirmations(fake_ctx: ToolContext) -> None:
    """四个入口都能按 unit 确认档位：少一个，那个入口就只能拆成几次调用。"""

    for tool_obj in (
        _episode_scope(fake_ctx),
        _all_scope(fake_ctx),
        _selected_scope(fake_ctx),
        _scene_scope(fake_ctx),
    ):
        properties = tool_obj.input_schema["properties"]  # type: ignore[index]
        assert properties["confirmed_request_durations"]["additionalProperties"] == {"type": "integer", "minimum": 1}


@pytest.mark.parametrize("delivery", ["post_production", "use_tts"])
def test_a_declared_narration_delivery_reaches_the_request_projection(delivery: str) -> None:
    from server.media_tools.videos import _reference_request_options

    assert _reference_request_options({"narration_delivery": delivery}).narration_delivery == delivery


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"narration_delivery": None},
        {"narration_delivery": "post-production"},
        {"narration_delivery": "POST_PRODUCTION"},
        {"narration_delivery": "tts"},
    ],
)
def test_an_undeclared_or_unknown_narration_delivery_is_refused(args: dict[str, Any]) -> None:
    """缺省与拼错都不再折成后期配音——那会让整批按调用方没选过的交付方式准入并计费。"""

    from server.media_tools.videos import _reference_request_options

    with pytest.raises(ValueError, match="narration_delivery 必填"):
        _reference_request_options(args)


def test_every_video_agent_tool_requires_narration_delivery(fake_ctx: ToolContext) -> None:
    for tool_obj in (
        _episode_scope(fake_ctx),
        _all_scope(fake_ctx),
        _selected_scope(fake_ctx),
        _scene_scope(fake_ctx),
    ):
        assert "narration_delivery" in tool_obj.input_schema["required"]  # type: ignore[index]


@pytest.mark.parametrize("delivery_args", [{}, {"narration_delivery": "post-production"}])
async def test_no_video_tool_enqueues_without_a_declared_narration_delivery(
    fake_ctx: ToolContext,
    monkeypatch,
    delivery_args: dict[str, Any],
) -> None:
    from server.media_tools import videos as mod

    async def _never_enqueue(*_args, **_kwargs):
        raise AssertionError("交付方式未声明时不得入队")

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _never_enqueue)

    calls = [
        (_episode_scope(fake_ctx), {"script": "episode_1.json"}),
        (_all_scope(fake_ctx), {"script": "episode_1.json"}),
        (_selected_scope(fake_ctx), {"script": "episode_1.json", "scene_ids": ["E1S01"]}),
        (_scene_scope(fake_ctx), {"script": "episode_1.json", "scene_id": "E1S01"}),
    ]
    for tool_obj, args in calls:
        out = await tool_obj.handler({**args, **delivery_args})
        assert out["is_error"] is True
        text = out["content"][0]["text"]
        assert "narration_delivery 必填" in text
        assert "post_production" in text and "use_tts" in text


async def test_generate_videos_episode_scope_reference_honors_requested_narration_delivery(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from lib.generation_queue_client import BatchTaskResult
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        del project_name, on_failure
        for spec in specs:
            enqueued.append(spec)
            if on_success:
                on_success(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id="t1",
                        status="succeeded",
                        result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
                    )
                )
        return [], []

    projected_deliveries: list[str] = []
    base_projection = _fake_reference_projection()

    async def _capture_delivery(**kwargs):
        projected_deliveries.append(kwargs["options"].narration_delivery)
        return await base_projection(**kwargs)

    active_tts = AsyncMock(return_value=frozenset())
    monkeypatch.setattr("server.services.video_batch_admission.project_reference_unit_request", _capture_delivery)
    monkeypatch.setattr("server.services.video_batch_admission.active_tts_resource_ids", active_tts)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = _episode_scope(fake_ctx)

    completed = await _call(
        tool_obj,
        {"script": "episode_1.json", "narration_delivery": "post_production"},
    )

    assert completed.get("is_error") is not True
    assert projected_deliveries == ["post_production"]
    # 后期配音不查 TTS 在途状态：该路径不以 TTS 为输入。
    active_tts.assert_not_awaited()
    assert enqueued[0].payload["reference_request_options"] == {
        "narration_delivery": "post_production",
    }

    projected_deliveries.clear()
    await _call(tool_obj, {"script": "episode_1.json", "narration_delivery": "use_tts"})
    assert projected_deliveries == ["use_tts"]
    active_tts.assert_awaited()


@pytest.mark.parametrize(
    "invalid_confirmation",
    [0, -1, 9.5, True, "12"],
    ids=["zero", "negative", "fraction", "boolean", "string"],
)
def test_reference_request_options_rejects_invalid_confirmed_duration(invalid_confirmation: object) -> None:
    from server.media_tools.videos import _reference_request_options

    with pytest.raises(ValueError, match="confirmed_request_duration_seconds 必须是大于 0 的整数秒档位"):
        _reference_request_options(
            {
                "narration_delivery": "use_tts",
                "confirmed_request_duration_seconds": invalid_confirmation,
            }
        )


async def test_generate_videos_episode_scope_reference_duration_repeat_without_confirm_still_blocked(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """不带确认参数的重复调用仍不入队。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(fake_ctx)
    await _call(tool_obj, {"script": "episode_1.json"})
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True
    assert enqueued == []


async def test_generate_videos_episode_scope_reference_duration_exact_enqueues_directly(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """总时长为档位成员时单次调用直接入队，行为与现状一致。"""
    from lib.reference_video.duration_slots import EXACT, DurationSlot
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=5, total_seconds=5, adjustment=EXACT)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        successes = []
        for spec in specs:
            enqueued.append(spec)
            done = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
            )
            successes.append(done)
            if on_success:
                on_success(done)
        return successes, []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_videos_episode_scope_reference_duration_skips_unit_without_shots(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """没有 shots 的 unit 不进入确认清单，而是以自己的问题码拦住整批。

    build_specs 本就会拒绝没有 shots 的 unit（见 test_build_reference_specs_*）；
    预检若仍去解析它，申请时长的转述本身就是失实的，用户会被要求确认一个
    不存在的请求。
    """
    from lib.reference_video.duration_slots import EXACT, DurationSlot
    from server.media_tools import videos as mod

    script = _reference_video_script()
    script["video_units"].append({"unit_id": "E1U2", "duration_seconds": 5})
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    precheck_calls: list[str] = []

    def fake_precheck(ctx, unit):
        precheck_calls.append(unit["unit_id"])
        return DurationSlot(seconds=5, total_seconds=5, adjustment=EXACT)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        successes = []
        for spec in specs:
            enqueued.append(spec)
            done = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
            )
            successes.append(done)
            if on_success:
                on_success(done)
        return successes, []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert precheck_calls == ["E1U1"]
    # 整批准入不成立，本次零任务入队。
    assert enqueued == []
    result = _generation_result(out)
    assert sorted(result.blocked) == ["E1U1", "E1U2"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1U2"] == "generation_unit_request_invalid"
    assert codes["E1U1"] == "generation_batch_admission_withheld"


async def test_generate_videos_episode_scope_reference_duration_resolves_project_context_once(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """批量预检让每个可入队 unit 都经过公共 request projection。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.media_tools import videos as mod

    script = _reference_video_script()
    script["video_units"].append(
        {
            "unit_id": "E1U2",
            "text": "@张三 转身",
            "duration_seconds": 5,
        }
    )
    script["video_units"].append(
        {
            "unit_id": "E1U3",
            "text": "空镜转场",
            "duration_seconds": 5,
        }
    )
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    context_calls: list[Any] = []

    def fake_precheck(_ctx, _unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck, calls=context_calls),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    # 三个 unit 均 5 秒、申请 8 秒 → 都需确认，本批不入队；实际水合桶随每个结果可观察。
    assert out.get("is_error") is not True, out
    assert context_calls == ["r2v", "r2v", "i2v"]
    assert enqueued == []


async def test_generate_videos_episode_scope_reference_skips_duration_context_when_nothing_to_precheck(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """整批都没有可预检的 unit 时不解析项目能力——解析推迟到第一个真正要取档的 unit，
    重构不能让「全部已完成/全部被跳过」的批次凭空多付一轮 DB 往返。"""
    from server.media_tools import videos as mod

    script = _reference_video_script()
    for unit in script["video_units"]:
        unit["text"] = ""
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    projection_calls: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        return [], []

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(calls=projection_calls),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(fake_ctx)
    await _call(tool_obj, {"script": "episode_1.json"})

    assert projection_calls == []


async def test_generate_videos_episode_scope_reference_skips_duration_context_when_prompt_blank(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """正文全空白时 build_specs 会拒绝该 unit——预检须复用同一份结构校验提前判定，
    不能先触发项目能力解析再让 build_specs 事后跳过。"""
    from server.media_tools import videos as mod

    script = _reference_video_script()
    for unit in script["video_units"]:
        unit["text"] = "   "
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    projection_calls: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        return [], []

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(calls=projection_calls),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert projection_calls == []
    assert "E1U1" in out["content"][0]["text"]


async def test_generate_videos_episode_scope_ad_reference_duration_needs_confirmation(
    ad_reference_ctx: ToolContext, monkeypatch
) -> None:
    """广告/短片的参考生视频走同一条视频单元时长确认闸门。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.media_tools import videos as mod

    seen_units: list[dict[str, Any]] = []

    def fake_precheck(ctx, unit):
        seen_units.append(unit)
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = _episode_scope(ad_reference_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert enqueued == []
    assert [unit["unit_id"] for unit in seen_units] == ["E1U1"]


@pytest.mark.parametrize(
    ("make_tool", "extra_args"),
    [
        (_scene_scope, {"scene_id": "E1U1"}),
        (_all_scope, {}),
        (_selected_scope, {"scene_ids": ["E1U1"]}),
    ],
    ids=["scene", "all", "selected"],
)
async def test_generate_video_reference_duration_confirmation_across_entries(
    fake_ctx: ToolContext, monkeypatch, make_tool, extra_args: dict[str, Any]
) -> None:
    """reference 路径的整集与点名入口共用确认闸门：未确认不入队、确认后入队。"""
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.media_tools import videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        for spec in specs:
            enqueued.append(spec)
            if on_success:
                on_success(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id="t1",
                        status="succeeded",
                        result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
                    )
                )
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    async def fake_active_tasks(**_kwargs):
        return []

    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr("server.services.video_batch_admission.get_active_tasks_for_resources", fake_active_tasks)

    tool_obj = make_tool(fake_ctx)
    pending = await _call(tool_obj, {"script": "episode_1.json", **extra_args})

    assert pending.get("is_error") is not True, pending
    assert enqueued == []
    text = pending["content"][0]["text"]
    assert "费用" in text and "本次请求" in text
    assert "confirmed_request_duration_seconds" in text

    confirmed = await _call(
        tool_obj,
        {"script": "episode_1.json", **extra_args, "confirmed_request_duration_seconds": 8},
    )

    assert confirmed.get("is_error") is not True, confirmed
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_videos_scene_scope_reference_use_tts_exposes_the_shared_cross_tier_quote(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import EXACT, DurationSlot
    from server.media_tools import videos as mod
    from server.services.cost_estimation import VideoRequestQuote

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(_ctx, _unit):
        return DurationSlot(seconds=8, total_seconds=8, adjustment=EXACT)

    async def _current_options(**kwargs):
        return replace(
            kwargs["options"],
            current_tts_duration_seconds=8.0,
            current_visual_duration_seconds=4,
        )

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        del project_name, on_failure
        for spec in specs:
            enqueued.append(spec)
            if on_success:
                on_success(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id="t1",
                        status="succeeded",
                        result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
                    )
                )
        return [], []

    monkeypatch.setattr(
        "server.services.video_batch_admission.prepare_current_reference_video_request_options", _current_options
    )
    monkeypatch.setattr(
        "server.services.video_batch_admission.project_reference_unit_request",
        _fake_reference_projection(fake_precheck),
    )
    monkeypatch.setattr(
        "server.services.video_batch_admission.active_tts_resource_ids", AsyncMock(return_value=frozenset())
    )
    monkeypatch.setattr(
        "server.services.video_batch_admission.get_active_tasks_for_resources", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr(
        "server.services.video_batch_admission.quote_video_request",
        AsyncMock(return_value=VideoRequestQuote(0.8, "USD", "fake", "fake-r2v", 8)),
    )
    tool_obj = _scene_scope(fake_ctx)

    pending = await _call(
        tool_obj,
        {"script": "episode_1.json", "scene_id": "E1U1", "narration_delivery": "use_tts"},
    )

    assert pending.get("is_error") is not True, pending
    assert pending["request_projections"][0]["request_cost"] == {
        "amount": 0.8,
        "currency": "USD",
        "provider_id": "fake",
        "model_id": "fake-r2v",
        "request_duration_seconds": 8,
    }
    assert "0.8 USD" in pending["content"][0]["text"]
    assert "现有视觉档位 4s，将申请 8s（成片更长 4s）" in pending["content"][0]["text"]
    assert enqueued == []

    accepted = await _call(
        tool_obj,
        {
            "script": "episode_1.json",
            "scene_id": "E1U1",
            "narration_delivery": "use_tts",
            "confirmed_request_duration_seconds": 8,
        },
    )
    assert accepted.get("is_error") is not True, accepted
    assert enqueued[0].payload["reference_request_options"] == {
        "narration_delivery": "use_tts",
        "confirmed_request_duration_seconds": 8,
    }


async def test_generate_videos_scene_scope_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import videos as mod

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)
    tool_obj = _scene_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is not True


async def test_generate_videos_scene_scope_use_tts_returns_structured_blocker_without_enqueuing(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.narration_delivery import (
        USE_TTS,
        NarrationDeliveryPreparation,
        NarrationDeliveryProblem,
        NarrationTtsStatus,
        prepare_narrated_video_duration,
    )
    from server.media_tools import videos as mod

    async def fake_prepare(**kwargs):
        narration = NarrationDeliveryPreparation(
            delivery=USE_TTS,
            unit_id="E1S01",
            speech_mode=None,
            tts_status=NarrationTtsStatus.MISSING,
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="basis",
            actual_duration_seconds=None,
            problems=(
                NarrationDeliveryProblem(
                    code="tts_missing",
                    reason="tts_audio_missing",
                    action="generate_tts",
                    locations=(),
                ),
            ),
        )
        return prepare_narrated_video_duration(
            narration=narration,
            planned_duration_seconds=4,
            supported_durations=(4, 8, 12),
            confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
        )

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "server.services.video_batch_admission.active_tts_resource_ids", AsyncMock(return_value=frozenset())
    )
    monkeypatch.setattr(
        "server.services.video_batch_admission.prepare_current_storyboard_narrated_video_duration",
        fake_prepare,
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        _scene_scope(fake_ctx),
        {"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": "use_tts"},
    )

    assert out["is_error"] is True
    assert out["request_projections"][0]["problems"][0]["code"] == "tts_missing"
    enqueue.assert_not_awaited()


async def test_generate_videos_scene_scope_use_tts_requires_exact_tier_and_queues_only_request_facts(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.narration_delivery import (
        USE_TTS,
        NarrationDeliveryPreparation,
        NarrationTtsStatus,
        VideoRequestCostFacts,
        prepare_narrated_video_duration,
    )
    from server.media_tools import videos as mod
    from server.services.cost_estimation import VideoRequestQuote

    async def fake_prepare(**kwargs):
        narration = NarrationDeliveryPreparation(
            delivery=USE_TTS,
            unit_id="E1S01",
            speech_mode=None,
            tts_status=NarrationTtsStatus.CURRENT,
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="basis",
            actual_duration_seconds=9.5,
            problems=(),
        )
        return replace(
            prepare_narrated_video_duration(
                narration=narration,
                planned_duration_seconds=4,
                supported_durations=(4, 8, 12),
                confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
            ),
            cost=VideoRequestCostFacts("openai", "sora-2", "720p", 12, True),
        )

    enqueue = AsyncMock(side_effect=_fake_scene_batch)
    monkeypatch.setattr(
        "server.services.video_batch_admission.active_tts_resource_ids", AsyncMock(return_value=frozenset())
    )
    monkeypatch.setattr(
        "server.services.video_batch_admission.prepare_current_storyboard_narrated_video_duration",
        fake_prepare,
    )
    monkeypatch.setattr(
        "server.services.video_batch_admission.quote_video_request",
        AsyncMock(return_value=VideoRequestQuote(1.2, "USD", "openai", "sora-2", 12)),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
    tool_obj = _scene_scope(fake_ctx)

    pending = await _call(
        tool_obj,
        {"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": "use_tts"},
    )
    assert pending.get("is_error") is not True
    assert pending["request_projections"][0]["problems"][0]["code"] == "reference_duration_confirmation_required"
    assert pending["request_projections"][0]["request_cost"] == {
        "amount": 1.2,
        "currency": "USD",
        "provider_id": "openai",
        "model_id": "sora-2",
        "request_duration_seconds": 12,
    }
    assert "1.2 USD" in pending["content"][0]["text"]
    enqueue.assert_not_awaited()

    completed = await _call(
        tool_obj,
        {
            "script": "episode_1.json",
            "scene_id": "E1S01",
            "narration_delivery": "use_tts",
            "confirmed_request_duration_seconds": 12,
        },
    )

    assert completed.get("is_error") is not True
    payload = enqueue.await_args.kwargs["specs"][0].payload
    assert "duration_seconds" not in payload
    assert payload["narration_delivery_options"] == {
        "narration_delivery": "use_tts",
        "confirmed_request_duration_seconds": 12,
    }
    assert "basis_digest" not in payload["narration_delivery_options"]
    assert "actual_duration_seconds" not in payload["narration_delivery_options"]


async def test_generate_videos_scene_scope_use_tts_blocks_when_exact_cost_is_unavailable(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from lib.narration_delivery import (
        USE_TTS,
        NarrationDeliveryPreparation,
        NarrationTtsStatus,
        VideoRequestCostFacts,
        prepare_narrated_video_duration,
    )
    from server.media_tools import videos as mod

    async def fake_prepare(**kwargs):
        narration = NarrationDeliveryPreparation(
            delivery=USE_TTS,
            unit_id="E1S01",
            speech_mode=None,
            tts_status=NarrationTtsStatus.CURRENT,
            artifact_path="audio/segment_E1S01.wav",
            basis_digest="basis",
            actual_duration_seconds=9.5,
            problems=(),
        )
        return replace(
            prepare_narrated_video_duration(
                narration=narration,
                planned_duration_seconds=4,
                supported_durations=(4, 8, 12),
                confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
            ),
            cost=VideoRequestCostFacts("openai", "sora-2", "720p", 12, True),
        )

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "server.services.video_batch_admission.active_tts_resource_ids", AsyncMock(return_value=frozenset())
    )
    monkeypatch.setattr(
        "server.services.video_batch_admission.prepare_current_storyboard_narrated_video_duration",
        fake_prepare,
    )
    monkeypatch.setattr("server.services.video_batch_admission.quote_video_request", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    result = await _call(
        _scene_scope(fake_ctx),
        {"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": "use_tts"},
    )

    assert result["is_error"] is True
    assert result["request_projections"][0]["allowed"] is False
    assert [problem["code"] for problem in result["request_projections"][0]["problems"]] == [
        "reference_duration_confirmation_required",
        "video_request_cost_unavailable",
    ]
    enqueue.assert_not_awaited()


async def test_generate_videos_scene_scope_accepts_legacy_drama_dialogue(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "drama"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "drama",
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "video_prompt": {
                    "action": "阿离转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [{"speaker": "张三", "line": "跟紧我。"}],
                },
                "voiceover": [],
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)
    out = await _call(_scene_scope(fake_ctx), {"script": "episode_1.json", "scene_id": "E1S01"})

    assert out.get("is_error") is not True, out


async def test_generate_videos_scene_scope_accepts_speech_free_legacy_drama(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "drama"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "drama",
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "video_prompt": {
                    "action": "阿离转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                },
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)
    out = await _call(_scene_scope(fake_ctx), {"script": "episode_1.json", "scene_id": "E1S01"})

    assert out.get("is_error") is not True, out


async def test_generate_videos_scene_scope_accepts_legacy_narration_string_prompt(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from server.media_tools import videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "narration"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "风吹过旷野。",
                "video_prompt": "Slow pan across the field",
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)
    out = await _call(_scene_scope(fake_ctx), {"script": "episode_1.json", "scene_id": "E1S01"})

    assert out.get("is_error") is not True, out


async def test_generate_videos_episode_scope_storyboard_batch_blocks_on_mixed_speech(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """分镜图生视频的整批入口同样过发声准入：一个混合发声条目扣下整批，零任务入队。"""
    from server.media_tools import videos as mod

    project_dir = fake_ctx.pm.get_project_path("demo")
    for segment_id in ("E1S01", "E1S02"):
        (project_dir / "storyboards" / f"scene_{segment_id}.png").write_bytes(b"png")
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {
            "segment_id": "E1S01",
            "novel_text": "风吹过旷野。",
            # 旁白与角色台词同时出现：需要重规划，不是可以直接下单的条目。
            "video_prompt": {"dialogue": [{"speaker": "阿离", "line": "快走。"}]},
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
        },
        {
            "segment_id": "E1S02",
            "novel_text": "他停下脚步。",
            "video_prompt": "第二镜",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        },
    ]

    enqueued: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr(
        "server.services.video_batch_admission.get_active_tasks_for_resources", AsyncMock(return_value=[])
    )

    out = await _call(_episode_scope(fake_ctx), {"script": "episode_1.json"})

    assert enqueued == []
    assert out["is_error"] is True
    result = _generation_result(out)
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1S01"] == "mixed_speech"
    assert codes["E1S02"] == "generation_batch_admission_withheld"


@pytest.mark.parametrize("case", SPEECH_CONTRACT_CASES, ids=lambda case: case.route_id)
async def test_six_route_agent_single_video_generation_returns_structured_admission_without_enqueuing(
    fake_ctx: ToolContext,
    monkeypatch,
    case: SpeechContractCase,
) -> None:
    from server.media_tools import videos as mod

    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {"content_mode": case.content_mode, "generation_mode": case.generation_mode}
    )
    fake_ctx.pm.script_payload = case.script()  # type: ignore[attr-defined]
    batch_enqueue = AsyncMock()
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", batch_enqueue)
    # reference_video 生成模式在准入失败前先探测在途任务（真实 DB 查询）；三个 storyboard
    # case 走的是不摸 DB 的直连准入分支，只有 reference_video 三个 case 需要这个 mock。
    monkeypatch.setattr(
        "server.services.video_batch_admission.get_active_tasks_for_resources", AsyncMock(return_value=[])
    )

    out = await _call(_scene_scope(fake_ctx), {"script": "episode_1.json", "scene_id": case.unit_id})

    assert out.get("is_error") is True
    problem = out["speech_admission"]["problems"][0]
    assert out["speech_admission"]["unit_id"] == case.unit_id
    assert problem["code"] == "mixed_speech"
    assert [tuple(location["path"]) for location in problem["locations"]] == list(case.expected_locations)
    assert problem["reason"] == "character_and_narrator_mixed"
    assert problem["action"] == "replan_unit"
    batch_enqueue.assert_not_awaited()


async def test_generate_videos_scene_scope_missing(fake_ctx: ToolContext) -> None:
    tool_obj = _scene_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "NO_SUCH"})
    assert out.get("is_error") is True


@pytest.mark.parametrize(
    "storyboard_value",
    [
        123,  # 剧本 JSON 里的脏数据（非字符串）须可读失败而非未处理 TypeError
        "/etc/passwd",  # 绝对路径：越权引用项目外文件
        "../../outside.png",  # `..` 穿越出项目目录
    ],
)
async def test_generate_videos_scene_scope_rejects_invalid_storyboard_image(
    fake_ctx: ToolContext, storyboard_value: object
) -> None:
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {"storyboard_image": storyboard_value}  # type: ignore[attr-defined]
    tool_obj = _scene_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is True
    # 锁定 resolve_storyboard_image_ref 抛出的 canonical 消息，而不是模糊子串或通用失败文本
    assert f"invalid storyboard image path: {storyboard_value!r}" in out["content"][0]["text"]


async def test_generate_videos_all_scope_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import videos as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id, task_id="t1", status="succeeded", result={"file_path": "videos/x.mp4"}
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = _all_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True


@pytest.mark.parametrize("source", ["embedded", "mcp"])
async def test_generate_videos_all_scope_preserves_the_selected_manual_upload(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    db_factory,
    source: Any,
) -> None:
    from lib.db.base import DEFAULT_USER_ID
    from lib.generation_queue_client import TaskSpec
    from server.media_tools import videos as mod
    from server.tool_runtime import CallerContext

    fake_ctx.queue = GenerationQueue(session_factory=db_factory)
    fake_ctx.caller = CallerContext(user_id=DEFAULT_USER_ID, source=source)

    project_path = fake_ctx.project_path
    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    artifact_path = _select_manual_video(
        project_path,
        resource_type="videos",
        resource_id="E1S01",
        content=b"manual-video",
    )
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"]["video_clip"] = artifact_path  # type: ignore[attr-defined]
    enqueue = AsyncMock(return_value=([], []))
    spec = TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id="E1S01",
        prompt="manual upload must not be replaced",
        script_file="episode_1.json",
    )
    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _MissingEverythingResolver())
    monkeypatch.setattr(mod, "artifact_is_usable", lambda *_args: False)
    monkeypatch.setattr(mod, "build_storyboard_video_specs", lambda **_kwargs: ([spec], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_all_scope(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    # 选中的手动上传照旧可用：既不进 requested 也不重生，只作为 skipped 报告。
    if source == "mcp":
        assert out["generation_batch"]["members"] == []
        assert [entry["unit_id"] for entry in out["generation_batch"]["skipped"]] == ["E1S01"]
    else:
        result = _generation_result(out)
        assert result.requested == []
        assert [entry.unit_id for entry in result.skipped] == ["E1S01"]
        assert out["batch_id"]
    enqueue.assert_not_awaited()


async def test_generate_videos_all_scope_error(fake_ctx: ToolContext) -> None:
    def boom(*a, **kw):
        raise RuntimeError("broken")

    fake_ctx.pm.load_script = boom  # type: ignore[attr-defined]
    tool_obj = _all_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


async def test_generate_videos_selected_scope_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import videos as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        for s in specs:
            if on_success:
                on_success(
                    BatchTaskResult(
                        resource_id=s.resource_id,
                        task_id="t1",
                        status="succeeded",
                        result={"file_path": f"videos/scene_{s.resource_id}.mp4"},
                    )
                )
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = _selected_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_ids": ["E1S01"]})
    assert out.get("is_error") is not True


async def test_generate_videos_selected_scope_no_match(fake_ctx: ToolContext) -> None:
    tool_obj = _selected_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_ids": ["NO_SUCH"]})
    assert out.get("is_error") is True


def test_asset_description_gate_rejects_invalid_description() -> None:
    """空白 / 非字符串描述都拿不到可用 description，由调用方按逐 ID blocked 报告，
    不应抛错（.strip()）或漏到 from_request 而中断整批。"""
    from lib.asset_types import ASSET_SPECS
    from server.media_tools.assets import _description_of, asset_unit_id

    bucket = ASSET_SPECS["character"].bucket_key
    project = {
        bucket: {
            "Alice": {"description": "   "},  # 空白
            "Carol": {"description": {"x": 1}},  # 非字符串，.strip() 会抛 AttributeError
            "Bob": {"description": "勇士"},
        }
    }

    assert _description_of(project, "character", asset_unit_id("character", "Alice")) is None
    assert _description_of(project, "character", asset_unit_id("character", "Carol")) is None
    assert _description_of(project, "character", asset_unit_id("character", "Bob")) == "勇士"


def test_asset_requested_ids_resolve_nfd_registered_key() -> None:
    """Agent 给的名字与桶 key 形态可以不同：按坐标系解析后落到真实落盘 key 的 unit ID。"""

    from lib.asset_types import ASSET_SPECS
    from server.media_tools.assets import _requested_unit_ids, asset_unit_id

    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    bucket = ASSET_SPECS["character"].bucket_key
    project = {bucket: {name_nfd: {"description": "存量 NFD 角色"}}}

    assert _requested_unit_ids(project, "character", [name_nfc]) == [asset_unit_id("character", name_nfd)]
    # 同一资产的两种拼写解析到同一个 unit ID，只入一次队。
    assert _requested_unit_ids(project, "character", [name_nfc, name_nfd]) == [asset_unit_id("character", name_nfd)]


def test_build_video_specs_does_not_validate_duration_at_enqueue(tmp_path) -> None:
    """duration 是能力维度，入队侧不再校验——任意 duration 都透传给执行层（见 ADR-0001）。"""
    from server.services.video_batch_admission import build_storyboard_video_specs as _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S01.png").write_bytes(b"png")
    project = _activated_project(tmp_path, {"S01": "storyboards/scene_S01.png"})
    items = [
        {
            "segment_id": "S01",
            "novel_text": "他在旷野上奔跑。",
            "video_prompt": "一个奔跑的镜头",
            "duration_seconds": 7,  # 不属于任何典型 supported_durations
            "generated_assets": {"storyboard_image": "storyboards/scene_S01.png"},
        }
    ]
    specs, _refused = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert len(specs) == 1
    assert specs[0].payload["duration_seconds"] == 7

    # 未显式指定 duration 时不携带该键，留给执行层按 caps 收口默认。
    items[0].pop("duration_seconds")
    specs2, _ = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert "duration_seconds" not in specs2[0].payload


@pytest.mark.parametrize(
    "storyboard_value",
    [
        123,  # 剧本 JSON 里的脏数据（非字符串）
        "/etc/passwd",  # 绝对路径
        "../../outside.png",  # `..` 穿越出项目目录
    ],
)
def test_build_video_specs_skips_invalid_storyboard_image_without_aborting_batch(
    tmp_path: Path, storyboard_value: object
) -> None:
    """批量入队场景下，单个条目 storyboard_image 非法（脏数据/越界/绝对路径）只记为该 ID 的
    blocked，不应让 `project_dir / storyboard_image` 抛未处理异常中断整批。"""
    from server.services.video_batch_admission import build_storyboard_video_specs as _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S02.png").write_bytes(b"png")
    project = _activated_project(tmp_path, {"S02": "storyboards/scene_S02.png"})
    items = [
        {
            "segment_id": "S01",
            "novel_text": "第一段旁白。",
            "video_prompt": "非法引用",
            "generated_assets": {"storyboard_image": storyboard_value},
        },
        {
            "segment_id": "S02",
            "novel_text": "第二段旁白。",
            "video_prompt": "合法引用",
            "generated_assets": {"storyboard_image": "storyboards/scene_S02.png"},
        },
    ]
    specs, refused = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert _refused_problems(refused) == {"S01": ("generation_unit_input_unusable", "generate_dependency")}


def test_build_video_specs_skips_non_dict_generated_assets_without_aborting_batch(tmp_path: Path) -> None:
    """generated_assets 容器本身被外部编辑损坏为非 dict（如 list）时按「没有分镜图」跳过，
    不应让 `.get("storyboard_image")` 在非 dict 上抛未处理 AttributeError 中断整批。"""
    from server.services.video_batch_admission import build_storyboard_video_specs as _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S02.png").write_bytes(b"png")
    project = _activated_project(tmp_path, {"S02": "storyboards/scene_S02.png"})
    items = [
        {
            "segment_id": "S01",
            "novel_text": "第一段旁白。",
            "video_prompt": "脏数据",
            "generated_assets": ["bad"],
        },
        {
            "segment_id": "S02",
            "novel_text": "第二段旁白。",
            "video_prompt": "合法引用",
            "generated_assets": {"storyboard_image": "storyboards/scene_S02.png"},
        },
    ]
    specs, refused = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        skeleton_kind="segments",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        project=project,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert _refused_problems(refused) == {"S01": ("generation_unit_input_unusable", "generate_dependency")}


async def test_generate_videos_scene_scope_generated_assets_non_dict_readable_rejection(fake_ctx: ToolContext) -> None:
    """generated_assets 容器本身非 dict 时须走「没有分镜图」的可读拒绝分支，
    不应在单条路径上抛未处理 AttributeError。"""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = ["bad"]  # type: ignore[attr-defined]
    tool_obj = _scene_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is True
    assert "请先运行 generate_storyboards" in out["content"][0]["text"]


def _admitted_video_yaml(item: dict, **kwargs) -> dict:
    """准入渲染出口产出的 YAML 段。

    该出口与执行路径共用 ``render_storyboard_video_prompt``，尾部统一追加的反向约束不是
    YAML，解析前剥离。
    """
    import yaml

    from lib.prompt_builders import append_video_negative_tail
    from server.services.video_batch_admission import storyboard_video_prompt

    rendered = storyboard_video_prompt(item, **kwargs)
    return yaml.safe_load(rendered.removesuffix(append_video_negative_tail("")).rstrip())


def test_storyboard_video_prompt_drama_sources_dialogue_from_utterances() -> None:
    """drama：准入渲染出口从分镜级 dialogue-kind utterances 派生 video YAML 台词，
    voiceover-kind 不进；narration / ad（无 utterances 字段）原样渲染既有 video_prompt.dialogue。"""
    drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "utterances": [
            {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
            {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
        ],
    }
    parsed = _admitted_video_yaml(drama_item, content_mode="drama")
    assert parsed["Dialogue"] == [{"Speaker": "王", "Line": "你来了。"}]

    narration_item = {
        "segment_id": "E1S01",
        "video_prompt": {
            "action": "走",
            "camera_motion": "Static",
            "ambiance_audio": "脚步声",
            "dialogue": [{"speaker": "Alice", "line": "hello"}],
        },
    }
    parsed_narr = _admitted_video_yaml(narration_item, content_mode="narration")
    assert parsed_narr["Dialogue"] == [{"Speaker": "Alice", "Line": "hello"}]


def test_storyboard_video_prompt_injects_voice_profiles_when_characters_given() -> None:
    """drama：传入带非空 voice_style 的角色资产时 YAML 顶部出现 Voice_Profiles；
    voice_characters 缺省（既有调用点行为）不注入。"""
    drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "utterances": [{"kind": "dialogue", "speaker": "王", "text": "你来了。"}],
    }
    characters = {"王": {"voice_style": "低沉沙哑"}}

    parsed = _admitted_video_yaml(drama_item, content_mode="drama", voice_characters=characters)
    assert parsed["Voice_Profiles"] == [{"Speaker": "王", "Voice_Style": "低沉沙哑"}]

    parsed_default = _admitted_video_yaml(drama_item, content_mode="drama")
    assert "Voice_Profiles" not in parsed_default

    parsed_no_style = _admitted_video_yaml(
        drama_item, content_mode="drama", voice_characters={"王": {"voice_style": ""}}
    )
    assert "Voice_Profiles" not in parsed_no_style


def test_storyboard_video_prompt_injects_voice_profiles_from_legacy_dialogue() -> None:
    """utterances 迁移前的存量 drama 剧本（无 utterances 字段，台词仍在
    video_prompt.dialogue）：改走 legacy 出口派生 Voice_Profiles，不因缺 utterances 静默丢失。"""
    legacy_drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {
            "action": "起身",
            "camera_motion": "Static",
            "ambiance_audio": "风声",
            "dialogue": [{"speaker": "王", "line": "你来了。"}],
        },
    }
    characters = {"王": {"voice_style": "低沉沙哑"}}

    parsed = _admitted_video_yaml(legacy_drama_item, content_mode="drama", voice_characters=characters)
    assert parsed["Voice_Profiles"] == [{"Speaker": "王", "Voice_Style": "低沉沙哑"}]
    assert parsed["Dialogue"] == [{"Speaker": "王", "Line": "你来了。"}]


def test_storyboard_video_prompt_strips_caller_supplied_voice_profiles_for_non_drama() -> None:
    """narration/ad（item 无 utterances 字段）剧本 video_prompt 自带 voice_profiles 时一律剥离：
    该声明段唯一来源是 build_drama_video_prompt 的机械派生，剧本残留值不得越权、绕过 C 类
    （真无声）门控直达 YAML。"""
    narration_item = {
        "segment_id": "E1S01",
        "video_prompt": {
            "action": "走",
            "camera_motion": "Static",
            "ambiance_audio": "脚步声",
            "voice_profiles": [{"Speaker": "赝品", "Voice_Style": "越权"}],
        },
    }
    parsed = _admitted_video_yaml(narration_item, content_mode="narration")
    assert "Voice_Profiles" not in parsed


async def test_resolve_voice_context_skips_non_drama(fake_ctx: ToolContext) -> None:
    """narration/ad：不解析 voice_consistency，直接跳过（无 drama dialogue speaker 概念）。"""
    from server.services.video_batch_admission import resolve_voice_context as _resolve_voice_context

    assert await _resolve_voice_context(fake_ctx.pm.project_payload, "narration") is None


async def test_resolve_voice_context_drama_reads_project_characters_and_gate(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """drama：读项目角色资产，无声（C 类真无声、或本集关闭音频）时退回不注入。"""
    from server.services import video_batch_admission as admission_mod

    async def fake_not_silent(_project, _episode=None):
        return False

    monkeypatch.setattr(admission_mod, "resolve_project_is_silent", fake_not_silent)
    characters = await admission_mod.resolve_voice_context(fake_ctx.pm.project_payload, "drama")
    assert characters == fake_ctx.pm.project_payload["characters"]  # type: ignore[attr-defined]

    async def fake_silent(_project, _episode=None):
        return True

    monkeypatch.setattr(admission_mod, "resolve_project_is_silent", fake_silent)
    assert await admission_mod.resolve_voice_context(fake_ctx.pm.project_payload, "drama") is None


def test_build_reference_specs_routes_through_guard(tmp_path) -> None:
    """参考生视频 prompt 只用于统一结构守卫，不冻结进任务 payload。"""
    from server.media_tools.videos import _build_reference_specs

    units = [
        {
            "unit_id": "E1U1",
            "text": "@张三 推门",
        }
    ]
    specs, _refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert len(specs) == 1
    assert specs[0].task_type == "reference_video"
    assert specs[0].resource_id == "E1U1"
    assert "prompt" not in specs[0].payload
    assert specs[0].payload["script_file"] == "episode_1.json"


def test_build_reference_specs_skips_blank_prompt(tmp_path) -> None:
    """正文全空白的 unit 被跳过并告警，不漏到执行层（结构校验上移到守卫点）。"""
    from server.media_tools.videos import _build_reference_specs

    units = [
        {"unit_id": "E1U1", "text": "   \n"},
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert _refused_problems(refused) == {"E1U1": ("generation_unit_request_invalid", "fix_input")}


def test_build_reference_specs_skips_mixed_speech_without_aborting_batch(tmp_path) -> None:
    from server.media_tools.videos import _build_reference_specs

    units = [
        {
            "unit_id": "E1U1",
            "text": "@[张三]：{快走。}\n{风吹过旷野。}",
        },
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]

    specs, refused = _build_reference_specs(
        units=units,
        script_filename="episode_1.json",
        skip_ids=None,
    )

    assert [spec.resource_id for spec in specs] == ["E1U2"]
    # 发声准入的问题码原样透出，调用方不必读文本判断下一步。
    assert _refused_problems(refused) == {"E1U1": ("mixed_speech", "replan_unit")}


def test_screening_keeps_bad_unit_ids_out_of_spec_building(tmp_path) -> None:
    """unit_id 为空或键缺失（Agent 裸写 JSON 可致）在筛查处按位置记名拒收，健康的 unit 照常构造。"""
    from server.media_tools.videos import _build_reference_specs
    from server.services.video_batch_admission import screen_script_entries

    entries = [
        {"unit_id": "", "text": "@张三 推门"},  # 空串
        {"text": "@王五 起身"},  # 缺 unit_id 键
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    units, tickets = screen_script_entries(entries, requested_ids=None)

    assert [ticket.unit_id for ticket in tickets] == ["video_units[0]", "video_units[1]"]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert refused == []


def test_build_reference_specs_handles_a_non_string_text(tmp_path) -> None:
    """text 为显式 null 的畸形 unit 不应崩溃整批，且不得把 'None' 注入 prompt。"""
    from server.media_tools.videos import _build_reference_specs

    units = [
        # text 显式 null → 被守卫点按「text 必须是字符串」拒收（不注入 'None'）。
        {"unit_id": "E1U1", "text": None},
        {"unit_id": "E1U2", "text": "@李四 转身"},
    ]
    specs, refused = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert all("None" not in (s.payload.get("prompt") or "") for s in specs)
    # 显式拒收与静默跳过在 specs 上不可分辨，问题码才锁得住守卫点确实拒了这一条。
    assert _refused_problems(refused) == {"E1U1": ("generation_unit_request_invalid", "fix_input")}


# ---------------------------------------------------------------------------
# enqueue_videos — ad + reference_video（统一 video_units）
# ---------------------------------------------------------------------------


def _ad_reference_unit(**overrides: Any) -> dict[str, Any]:
    unit: dict[str, Any] = {
        "unit_id": "E1U1",
        "duration_seconds": 5,
        "text": "@[保温杯] 置于桌面",
        "generated_assets": {},
    }
    unit.update(overrides)
    return unit


@pytest.fixture
def ad_reference_ctx(fake_ctx: ToolContext) -> ToolContext:
    fake_ctx.config_resolver = _fake_caps_resolver(supported_durations=(5,), default_duration=5)

    pm = fake_ctx.pm
    pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "style": "明亮写实",
            "products": {"保温杯": {"description": "主推商品", "reference_images": ["products/保温杯.png"]}},
            "episodes": [{"episode": 1, "title": "短片", "script_file": "scripts/episode_1.json"}],
        }
    )
    pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "ad",
        "episode": 1,
        "title": "短片",
        "video_units": [_ad_reference_unit()],
    }
    product = fake_ctx.project_path / "products" / "保温杯.png"
    product.parent.mkdir(parents=True, exist_ok=True)
    product.write_bytes(b"product")
    return fake_ctx


def _successful_reference_batch(ctx: ToolContext, enqueued: list[Any]):
    async def fake_batch(*, project_name: str, specs: list[Any], on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        successes: list[BatchTaskResult] = []
        for spec in specs:
            enqueued.append(spec)
            output = ctx.project_path / "reference_videos" / f"{spec.resource_id}.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"\x00")
            result = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
            )
            successes.append(result)
            if on_success:
                on_success(result)
        return successes, []

    return fake_batch


async def test_generate_videos_episode_scope_reference_skips_malformed_unit_entries(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """脏 unit 元素交给逐条校验拒绝，不在完成扫描、音频闸门或时长预检抛未处理异常。"""
    from server.media_tools import videos as mod

    valid = ad_reference_ctx.pm.script_payload["video_units"][0]  # type: ignore[attr-defined]
    ad_reference_ctx.pm.script_payload["video_units"] = ["bad", {}, valid]  # type: ignore[attr-defined]
    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        _episode_scope(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    # 脏 unit 逐条记为 blocked（没有 unit_id 可寻址时按位置编号），并拦住整批。
    assert enqueued == []
    result = _generation_result(out)
    assert sorted(result.blocked) == ["E1U1", "video_units[0]", "video_units[1]"]
    codes = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert codes["E1U1"] == "generation_batch_admission_withheld"


async def test_generate_videos_episode_scope_ad_reference_enqueues_existing_video_units(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """广告/短片的参考生视频直接消费自包含 video_units，不派生或写入 reference_units。"""
    from server.media_tools import videos as mod

    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        _episode_scope(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]
    script = ad_reference_ctx.pm.script_payload  # type: ignore[attr-defined]
    assert [unit["unit_id"] for unit in script["video_units"]] == ["E1U1"]
    assert "reference_units" not in script


async def test_generate_videos_episode_scope_ad_reference_does_not_claim_orphan_file(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名文件没有 generated_assets 归属时仍须入队，不能把孤儿文件报告为成功。"""
    from server.media_tools import videos as mod

    orphan = ad_reference_ctx.project_path / "reference_videos/E1U1.mp4"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        _episode_scope(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]


async def test_generate_videos_episode_scope_ad_reference_preserves_the_selected_manual_upload(
    ad_reference_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.media_tools import videos as mod

    project_path = ad_reference_ctx.project_path
    ad_reference_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION  # type: ignore[attr-defined]
    artifact_path = _select_manual_video(
        project_path,
        resource_type="reference_videos",
        resource_id="E1U1",
        content=b"manual-reference-video",
    )
    ad_reference_ctx.pm.script_payload["video_units"][0]["generated_assets"] = {  # type: ignore[attr-defined]
        "video_clip": artifact_path
    }
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _MissingEverythingResolver())
    monkeypatch.setattr(mod, "artifact_is_usable", lambda *_args: False)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_all_scope(ad_reference_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert result.requested == []
    assert [entry.unit_id for entry in result.skipped] == ["E1U1"]
    enqueue.assert_not_awaited()


async def test_generate_videos_episode_scope_reference_blocks_a_clip_whose_manifest_state_is_unreadable(
    ad_reference_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整集参考生视频里某 unit 已有成片、但 Manifest 比对抛错（BLOCKED）时必须报
    blocked，不能让 ``artifact_is_usable`` 的 fail-loud 异常穿透成整批 tool_error——
    与 storyboard 整集路线的同一场判定必须同步处理（同一个不可读产物、两条路线）。
    """
    from lib.artifact_manifest import ArtifactBlocker, ArtifactComparison
    from server.media_tools import videos as mod

    project_path = ad_reference_ctx.project_path
    ad_reference_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION  # type: ignore[attr-defined]
    artifact_path = "reference_videos/E1U1.mp4"
    output = project_path / artifact_path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"\x00")
    ad_reference_ctx.pm.script_payload["video_units"][0]["generated_assets"] = {  # type: ignore[attr-defined]
        "video_clip": artifact_path
    }

    class _BlockedResolver:
        def compare(self, key, *, artifact_path):
            if artifact_path == "reference_videos/E1U1.mp4":
                return ArtifactComparison(
                    status=ArtifactStatus.BLOCKED,
                    artifact_path=artifact_path,
                    blocker=ArtifactBlocker(
                        code="manifest_read_failed", path=artifact_path, detail="sidecar unreadable"
                    ),
                )
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

        def resolve_usable_entry(self, key, *, artifact_path):
            return None

        def compare_frozen_entry(self, key, entry):
            return self.compare(key, artifact_path=entry.artifact_path)

        def artifact_content_digest(self, artifact_path):
            return "0" * 64

    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _BlockedResolver())
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(_episode_scope(ad_reference_ctx), {"script": "episode_1.json"})

    result = _generation_result(out)
    assert result.succeeded == []
    assert result.blocked == ["E1U1"]
    blocked_item = next(item for item in result.items if item.unit_id == "E1U1")
    assert blocked_item.problem is not None
    assert blocked_item.problem.code == "generation_artifact_state_unavailable"
    enqueue.assert_not_awaited()


async def test_generate_videos_episode_scope_ad_reference_replan_shell_cannot_enqueue(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """迁移保留的 needs_replan 空壳可被读取，但不能提交生成任务。"""
    from server.media_tools import videos as mod

    ad_reference_ctx.pm.script_payload["video_units"] = [  # type: ignore[attr-defined]
        _ad_reference_unit(
            shots=[],
            references=[],
            duration_seconds=0,
            needs_replan=True,
            generated_assets={"source_signature": "legacy"},
        )
    ]
    called = False

    async def _fail_if_enqueued(*args: Any, **kwargs: Any):
        nonlocal called
        called = True
        raise AssertionError("needs_replan shell must not enqueue")

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fail_if_enqueued)

    out = await _call(
        _episode_scope(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is True
    assert out["speech_admission"]["allowed"] is False
    assert out["speech_admission"]["unit_id"] == "E1U1"
    assert out["speech_admission"]["problems"][0]["code"] == "needs_replan"
    assert out["speech_admission"]["problems"][0]["action"] == "replan_unit"
    assert "E1U1" in out["content"][0]["text"]
    assert not called


async def test_generate_videos_episode_scope_ad_reference_replan_unit_cannot_reuse_owned_clip(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """迁移保留的已归属视频不能绕过 needs_replan 生成闸门。"""
    from server.media_tools import videos as mod

    ad_reference_ctx.pm.script_payload["video_units"] = [  # type: ignore[attr-defined]
        _ad_reference_unit(
            needs_replan=True,
            migration_requires_content_replan=True,
            generated_assets={"video_clip": "reference_videos/E1U1.mp4"},
        )
    ]
    owned = ad_reference_ctx.project_path / "reference_videos/E1U1.mp4"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_bytes(b"legacy")
    called = False

    async def _fail_if_enqueued(*args: Any, **kwargs: Any):
        nonlocal called
        called = True
        raise AssertionError("needs_replan unit must not enqueue")

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fail_if_enqueued)

    out = await _call(
        _episode_scope(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is True
    assert "E1U1" in out["content"][0]["text"]
    assert not called


async def test_generate_videos_selected_scope_ad_reference_regenerates_named_unit(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """广告点名重做沿用统一 video_unit 路径。"""
    from server.media_tools import videos as mod

    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        _selected_scope(ad_reference_ctx),
        {"script": "episode_1.json", "scene_ids": ["E1U1"]},
    )

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]


# ---------------------------------------------------------------------------
# Retired parameter rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retired_param", sorted(enqueue_videos_mod._RETIRED_PARAMS))
async def test_video_tools_reject_retired_params(fake_ctx: ToolContext, retired_param: str) -> None:
    """已退役的参数名传给任何一个视频工具都被拒，报错点名该参数并给出当下写法。"""
    for factory in (
        _episode_scope,
        _scene_scope,
        _all_scope,
        _selected_scope,
    ):
        tool_obj = factory(fake_ctx)
        args: dict[str, Any] = {"script": "episode_1.json", retired_param: "dummy"}
        if factory is _scene_scope:
            args["scene_id"] = "E1S01"
        if factory is _selected_scope:
            args["scene_ids"] = ["E1S01"]
        result = await tool_obj.handler(args)
        assert result["is_error"], f"{tool_obj.name} 未拒绝 {retired_param!r}"
        text = result["content"][0]["text"]
        assert retired_param in text
        assert "已不存在" in text
        if retired_param in {"shot_ids", "unit_id", "unit_ids"}:
            assert "target.ids" in text


async def test_retired_param_rejection_does_not_preempt_the_script_filename_error(
    fake_ctx: ToolContext,
) -> None:
    """报错次序：剧本文件名先校验，退役参数其次——与本模块声明的入参报错次序一致。"""
    tool_obj = _episode_scope(fake_ctx)

    result = await tool_obj.handler({"script": "../escape.json", "shot_ids": ["E1S01"]})

    assert result["is_error"]
    assert "shot_ids" not in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# 生成分派：六种创作类型×生成模式组合
# ---------------------------------------------------------------------------


_SKELETON_BY_MODE_PAIR: dict[tuple[str, str], str] = {
    ("narration", "storyboard"): "segments",
    ("drama", "storyboard"): "scenes",
    ("ad", "storyboard"): "shots",
    ("narration", "reference_video"): "video_units",
    ("drama", "reference_video"): "video_units",
    ("ad", "reference_video"): "video_units",
}


@pytest.mark.parametrize(("content_mode", "generation_mode"), sorted(_SKELETON_BY_MODE_PAIR))
def test_video_generation_dispatches_by_generation_mode_for_every_content_mode(
    fake_ctx: ToolContext,
    content_mode: str,
    generation_mode: str,
) -> None:
    """六个组合各自派给正确的生成模式，且骨架闸门放行本组合应有的骨架。"""
    fake_ctx.pm.project_payload["content_mode"] = content_mode  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode  # type: ignore[attr-defined]
    skeleton = _SKELETON_BY_MODE_PAIR[(content_mode, generation_mode)]
    script = {"content_mode": content_mode, "episode": 1, skeleton: []}

    route = enqueue_videos_mod._resolve_reference_route(fake_ctx, script)

    assert route == ("reference" if generation_mode == "reference_video" else None)


@pytest.mark.parametrize(("content_mode", "generation_mode"), sorted(_SKELETON_BY_MODE_PAIR))
def test_video_generation_refuses_a_script_from_the_other_generation_mode(
    fake_ctx: ToolContext,
    content_mode: str,
    generation_mode: str,
) -> None:
    """骨架来自另一种生成模式时六个组合一律拒绝入队，不静默按剧本形态改派。"""
    other_mode = "storyboard" if generation_mode == "reference_video" else "reference_video"
    fake_ctx.pm.project_payload["content_mode"] = content_mode  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode  # type: ignore[attr-defined]
    mismatched = _SKELETON_BY_MODE_PAIR[(content_mode, other_mode)]
    script = {"content_mode": content_mode, "episode": 1, mismatched: []}

    with pytest.raises(SkeletonRouteMismatchError):
        enqueue_videos_mod._resolve_reference_route(fake_ctx, script)


async def test_post_production_video_never_asks_for_the_missing_tts(fake_ctx: ToolContext, monkeypatch) -> None:
    """后期配音的视频请求既不自动补 TTS，也不把缺 TTS 报成一条待办。"""
    from server.media_tools import videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "narration"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S01.png"
    }
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)

    out = await _call(
        _scene_scope(fake_ctx),
        {"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": "post_production"},
    )

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert result.succeeded == ["E1S01"]
    assert all(item.problem is None for item in result.items)


@pytest.mark.parametrize(
    ("scope", "args"),
    [
        ("episode", {"script": "episode_1.json"}),
        ("scene", {"script": "episode_1.json", "scene_id": "E1U1"}),
        ("all", {"script": "episode_1.json"}),
        ("selected", {"script": "episode_1.json", "scene_ids": ["E1U1"]}),
    ],
)
async def test_generate_videos_rejects_mismatched_unit_script_on_storyboard_route(
    fake_ctx: ToolContext, scope: str, args: dict[str, Any]
) -> None:
    """分镜图生视频项目下的 video_units 骨架剧本：四个入口一律结构报错 + 重拆指引。

    静默降档与悄悄换路径都不可构造——存量混排集的唯一出路是重拆重生成。
    """
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1", "text": "x", "duration_seconds": 5}],
    }
    tool_obj = _videos_tool_for_scope(fake_ctx, scope)
    out = await _call(tool_obj, args)

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "骨架" in text and "重新拆分" in text


async def test_generate_videos_episode_scope_rejects_mismatched_storyboard_script_on_reference_route(
    fake_ctx: ToolContext,
) -> None:
    """反向：参考生视频项目下的分镜骨架剧本同样被拒，指引重跑 unit 拆分。"""

    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]
    tool_obj = _episode_scope(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "generate_script_plan" in out["content"][0]["text"]
