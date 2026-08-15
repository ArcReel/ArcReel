"""Tests for ArcReel SDK in-process MCP tools.

Each tool: 1 happy-path and 1 error-path. Heavy plumbing
(``batch_enqueue_and_wait`` / ``enqueue_and_wait`` / ``ScriptGenerator`` etc.)
is monkeypatched, so the tests exercise schema wiring + error envelope
behavior without hitting the real queue or providers.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib import script_review
from lib.artifact_manifest import ArtifactStatus
from lib.generation_result import (
    GenerationBatchResult,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
)
from lib.project_manager import ProjectManager
from lib.reference_video.draft_validation import DraftViolation
from lib.reference_video.quarantine import (
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    quarantine_path,
    write_quarantine,
)
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.resource_paths import resource_relative_path
from lib.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from server.agent_runtime.sdk_tools import build_arcreel_mcp_server
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.enqueue_assets import (
    generate_assets_tool,
    list_pending_assets_tool,
)
from server.agent_runtime.sdk_tools.enqueue_grid import generate_grid_tool
from server.agent_runtime.sdk_tools.enqueue_image_edits import edit_images_tool
from server.agent_runtime.sdk_tools.enqueue_storyboards import generate_storyboards_tool
from server.agent_runtime.sdk_tools.enqueue_videos import (
    generate_video_all_tool,
    generate_video_episode_tool,
    generate_video_scene_tool,
    generate_video_selected_tool,
)
from server.agent_runtime.sdk_tools.text_generation import (
    _parse_normalized_content,
    generate_episode_script_tool,
    get_video_capabilities_tool,
    normalize_drama_script_tool,
    open_reference_step1_for_edit_tool,
    split_narration_segments_tool,
    split_reference_video_units_tool,
    validate_and_promote_reference_draft_tool,
)
from tests.fakes import fake_reference_caps_fetcher
from tests.speech_contract_cases import SPEECH_CONTRACT_CASES, SpeechContractCase

# ---------------------------------------------------------------------------
# Generation result contract helpers
# ---------------------------------------------------------------------------


def _probe_builder() -> tuple[GenerationResultBuilder, dict[str, GenerationTargetState]]:
    """A bare builder + empty state map, for exercising spec builders in isolation."""

    return GenerationResultBuilder("probe", GenerationSelectionMode.EXPLICIT), {}


def _blocked_problems(builder: GenerationResultBuilder) -> dict[str, tuple[str, str]]:
    """Map each blocked/failed unit ID to its ``(code, action)`` pair."""

    return {
        item.unit_id: (item.problem.code, item.problem.action.value)
        for item in builder.build().items
        if item.problem is not None
    }


async def _fake_scene_batch(*, project_name, specs, on_success=None, on_failure=None):
    """Stand in for the queue: every scene spec lands its canonical mp4."""

    from lib.generation_queue_client import BatchTaskResult

    return [
        BatchTaskResult(
            resource_id=spec.resource_id,
            task_id="t1",
            status="succeeded",
            result={"file_path": f"videos/scene_{spec.resource_id}.mp4"},
        )
        for spec in specs
    ], []


def _generation_result(out: dict[str, Any]) -> GenerationBatchResult:
    """Read the structured contract out of a tool response, never its text."""

    return GenerationBatchResult.model_validate(out["generation_result"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakePM:
    def __init__(self, project_name: str, project_dir: Path):
        self._project_name = project_name
        self._project_dir = project_dir
        self.project_payload: dict[str, Any] = {
            "content_mode": "drama",
            "generation_mode": "storyboard",
            "source_kind": "novel",
            "source_language": "中文",
            "overview": {},
            "episodes": [],
            "characters": {"张三": {"description": "主角"}, "李四": {"description": ""}},
            "scenes": {"村口": {"description": "黄昏的村口"}},
            "props": {},
            "products": {"保温杯": {"description": "不锈钢保温杯", "reference_images": [], "selling_points": []}},
            "style": "anime",
            "style_description": "soft pastel",
        }
        self.script_payload: dict[str, Any] = {
            "content_mode": "narration",
            "episode": 1,
            "segments": [
                {
                    "segment_id": "E1S01",
                    "image_prompt": "村口黄昏",
                    "novel_text": "黄昏时分，风吹过村口。",
                    "video_prompt": {"action": "镜头平移", "camera_motion": "Pan", "ambiance_audio": "风声"},
                    "duration_seconds": 4,
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                },
            ],
        }

    def get_project_path(self, _name: str) -> Path:
        return self._project_dir

    def load_project(self, _name: str) -> dict[str, Any]:
        return self.project_payload

    def load_script(self, _name: str, _filename: str) -> dict[str, Any]:
        return self.script_payload

    def project_exists(self, _name: str) -> bool:
        return True

    def get_pending_characters(self, _name: str) -> list[dict[str, Any]]:
        return [
            {"name": "张三", "description": "主角描述"},
            {"name": "李四", "description": ""},
        ]

    def get_pending_project_scenes(self, _name: str) -> list[dict[str, Any]]:
        return [{"name": "村口", "description": "黄昏村口"}]

    def get_pending_project_props(self, _name: str) -> list[dict[str, Any]]:
        return []

    def get_pending_project_products(self, _name: str) -> list[dict[str, Any]]:
        return [{"name": "保温杯", "description": "不锈钢保温杯"}]


@pytest.fixture
def fake_ctx(tmp_path: Path) -> ToolContext:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    # Build a storyboard image so video tools can find it.
    (project_dir / "storyboards").mkdir()
    (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")

    return ToolContext(
        project_name="demo",
        projects_root=tmp_path,
        pm=_FakePM("demo", project_dir),  # type: ignore[arg-type]
    )


class _MissingEverythingResolver:
    """An active Manifest that never admits a formal artifact as usable."""

    def compare(self, key, *, artifact_path=None):
        from lib.artifact_manifest import ArtifactComparison, ArtifactStatus

        return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path or "")


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


@pytest.fixture(autouse=True)
def _stub_audio_switch_guard(monkeypatch):
    """视频入队前的音频开关预检要读真实配置库，本文件不覆盖它的行为，一律放行。

    行为覆盖在 tests/server/agent_runtime/test_enqueue_videos_audio_switch.py。
    """
    from server.agent_runtime.sdk_tools import enqueue_videos as _mod

    async def _noop(_project, _capability):
        return None

    monkeypatch.setattr(_mod, "assert_audio_switch_supported", _noop)


def _fake_reference_projection(
    slot_for=None,
    calls: list[str] | None = None,
    *,
    current_tts_duration_seconds: float | None = None,
):
    """Agent 工具测试用的 in-process request projection adapter。"""

    async def _project(*, project, script, unit, options=None, **_kwargs):
        from lib.reference_video.request_projection import (
            ProviderProjectionCandidate,
            ReferenceUnitRequestProjector,
            ResolvedReferenceAsset,
            canonicalize_references,
        )

        references = canonicalize_references(unit.get("references"))
        capability = "r2v" if references else "i2v"
        if calls is not None:
            calls.append(capability)
        if slot_for is None:
            requested_seconds = int(unit.get("duration_seconds") or 8)
        else:
            requested_seconds = int(slot_for(None, unit).seconds)

        class _Capabilities:
            async def resolve_candidate(self, project, capability):
                del project
                return ProviderProjectionCandidate(
                    capability=capability,
                    provider_id="fake",
                    model_id=f"fake-{capability}",
                    supported_durations=(requested_seconds,),
                    max_reference_images=9,
                    resolution="1080p",
                    generate_audio=True,
                    requested_generate_audio=True,
                    has_audio_track=True,
                    audio_switch_controllable=True,
                )

        class _Available:
            def is_available(self, asset):
                del asset
                return True

        resolved_assets = [
            ResolvedReferenceAsset(path=Path(f"{reference.type}/{reference.name}.png"), reference=reference)
            for reference in references
        ]
        if options is not None and current_tts_duration_seconds is not None:
            options = replace(options, current_tts_duration_seconds=current_tts_duration_seconds)
        return await ReferenceUnitRequestProjector(_Capabilities(), _Available()).project_current(
            project=project,
            script=script,
            unit=unit,
            resolved_assets=resolved_assets,
            options=options,
        )

    return _project


@pytest.fixture(autouse=True)
def _stub_reference_request_projection(monkeypatch):
    """Agent 工具接线测试不访问真实供应商配置或项目资产文件。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as _mod

    monkeypatch.setattr(_mod, "project_reference_unit_request", _fake_reference_projection())


async def _call(tool_obj, args: dict[str, Any]) -> dict[str, Any]:
    return await tool_obj.handler(args)


def _activate_unbound_project(fake_ctx: ToolContext, *, generation_mode: str = "storyboard") -> None:
    project = fake_ctx.pm.project_payload  # type: ignore[attr-defined]
    project.update(
        {
            "schema_version": 8,
            "content_mode": "narration",
            "generation_mode": generation_mode,
            "episodes": [],
        }
    )
    (fake_ctx.project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")


# ---------------------------------------------------------------------------
# build_arcreel_mcp_server
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_arcreel_mcp_server_contains_all_tools(tmp_path: Path) -> None:
    srv = build_arcreel_mcp_server(project_name="demo", projects_root=tmp_path)
    assert srv["name"] == "arcreel"
    # SDK exposes the registered tools on srv["instance"]; we just sanity-check
    # the type returned matches the spec contract.
    assert "instance" in srv


@pytest.mark.unit
def test_generate_narration_audio_registered() -> None:
    """旁白配音工具必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "generate_narration_audio" in ARCREEL_MCP_TOOL_IDS


# ---------------------------------------------------------------------------
# validate_script_filename — shared guard for all enqueue tools
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "scripts/episode_1.json",  # 任何分隔符都拒（包括 scripts/ 前缀）
        "../etc/passwd",
        "sub/dir/file.json",
        "a\\b.json",
        ".",
        "..",
    ],
)
def test_validate_script_filename_rejects_paths(bad: str) -> None:
    from server.agent_runtime.sdk_tools._context import validate_script_filename

    with pytest.raises(ValueError):
        validate_script_filename(bad)


@pytest.mark.unit
def test_validate_script_filename_accepts_basename() -> None:
    from server.agent_runtime.sdk_tools._context import validate_script_filename

    assert validate_script_filename("episode_1.json") == "episode_1.json"


@pytest.mark.unit
async def test_generate_storyboards_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    """Agent 传带路径分隔符的 script 名必须被 handler 拒绝（共享 validate_script_filename 防御）。"""
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "../etc/passwd"})
    assert out.get("is_error") is True
    assert "路径分隔符" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# enqueue_assets
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_list_pending_assets_happy(fake_ctx: ToolContext) -> None:
    tool_obj = list_pending_assets_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "张三" in text
    assert "村口" in text
    assert "保温杯" in text


@pytest.mark.unit
async def test_pending_asset_tools_include_an_unclaimed_schema8_sheet(tmp_path: Path, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_assets as mod

    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    project_dir = pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_scene("demo", "客厅", "宽敞的客厅")
    pm.update_scene_sheet("demo", "客厅", "scenes/客厅.png")
    (project_dir / "scenes" / "客厅.png").write_bytes(b"png")
    ctx = ToolContext(project_name="demo", projects_root=projects_root, pm=pm)

    listed = await _call(list_pending_assets_tool(ctx), {"type": "scene"})

    assert "客厅" in listed["content"][0]["text"]

    enqueued: list[str] = []

    async def _capture_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _capture_batch)

    await _call(generate_assets_tool(ctx), {"type": "scene"})

    assert enqueued == ["客厅"]


@pytest.mark.unit
async def test_list_pending_assets_error(fake_ctx: ToolContext, monkeypatch) -> None:
    def boom(_name):
        raise RuntimeError("db down")

    fake_ctx.pm.get_pending_characters = boom  # type: ignore[attr-defined]
    tool_obj = list_pending_assets_tool(fake_ctx)
    out = await _call(tool_obj, {"type": "character"})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_assets_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_assets as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 1},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = generate_assets_tool(fake_ctx)
    out = await _call(tool_obj, {"type": "character"})
    # 李四 没有 description，作为 blocked 逐 ID 报告；缺口存在时整体判为 error，
    # 调用方不需要读文本就知道哪几个 ID 还没做成。
    result = _generation_result(out)
    assert result.succeeded == ["character/张三"]
    assert result.blocked == ["character/李四"]
    assert sorted(result.requested) == sorted(result.succeeded + result.blocked)
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_assets_rejects_an_explicitly_empty_name_list(fake_ctx: ToolContext, monkeypatch) -> None:
    """``names: []`` 是调用方错误，绝不能被当成「全部缺图资产」去扫全库付费。"""
    from server.agent_runtime.sdk_tools import enqueue_assets as mod

    async def fail_batch(**_kwargs):
        raise AssertionError("空选择不该入队任何任务")

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fail_batch)

    out = await _call(generate_assets_tool(fake_ctx), {"type": "character", "names": []})

    assert out.get("is_error") is True
    assert "不能为空数组" in out["content"][0]["text"]


@pytest.mark.unit
async def test_generate_assets_names_without_type(fake_ctx: ToolContext) -> None:
    tool_obj = generate_assets_tool(fake_ctx)
    out = await _call(tool_obj, {"names": ["张三"]})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# enqueue_narration_audio
# ---------------------------------------------------------------------------


def _narration_audio_script() -> dict[str, Any]:
    return {
        "content_mode": "narration",
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "却说天下大势，分久必合。",
                "video_prompt": {},
                "generated_assets": {},
            },
            {
                "segment_id": "E1S02",
                "novel_text": "话说周末七国分争。",
                "video_prompt": {},
                "generated_assets": {"narration_audio": "audio/segment_E1S02.wav"},
            },
        ],
    }


class _AllStaleResolver:
    """An active Manifest whose every artifact is usable but no longer current."""

    def compare(self, key, *, artifact_path=None):
        from lib.artifact_manifest import ArtifactComparison, ArtifactStatus

        return ArtifactComparison(status=ArtifactStatus.STALE, artifact_path=artifact_path or "")


@pytest.mark.integration
async def test_generate_narration_audio_missing_only_reuses_a_stale_recording(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """missing-only 只补 missing：已失效但可用的旧配音被复用，不重新付费生成。"""
    from lib.artifact_manifest import ArtifactStatus
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = {"narration_audio": "audio/segment_E1S01.wav"}
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    enqueue = AsyncMock()
    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _AllStaleResolver())
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(mod.generate_narration_audio_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert result.requested == []
    assert {entry.unit_id: entry.artifact_status for entry in result.skipped} == {
        "E1S01": ArtifactStatus.STALE,
        "E1S02": ArtifactStatus.STALE,
    }
    enqueue.assert_not_awaited()


@pytest.mark.integration
async def test_generate_narration_audio_explicit_ids_regenerate_a_stale_recording(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """点名即强制：同一个 stale 单元在显式选择下照常重新生成。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = {"narration_audio": "audio/segment_E1S01.wav"}
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="succeeded", result={}) for s in specs
        ], []

    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _AllStaleResolver())
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(
        mod.generate_narration_audio_tool(fake_ctx),
        {"script": "episode_1.json", "segment_ids": ["E1S01"]},
    )

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1S01"]
    assert _generation_result(out).succeeded == ["E1S01"]


@pytest.mark.integration
async def test_post_production_video_never_asks_for_the_missing_tts(fake_ctx: ToolContext, monkeypatch) -> None:
    """后期配音的视频请求既不自动补 TTS，也不把缺 TTS 报成一条待办。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "narration"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S01.png"
    }
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)

    out = await _call(
        generate_video_scene_tool(fake_ctx),
        {"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": "post_production"},
    )

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert result.succeeded == ["E1S01"]
    assert all(item.problem is None for item in result.items)


@pytest.mark.integration
async def test_generate_storyboards_reports_a_partial_batch_per_id(fake_ctx: ToolContext, monkeypatch) -> None:
    """一批里有成有败时逐 ID 分账，失败项带稳定 code，不需要读文本判断重试。"""
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": "E1S01", "image_prompt": "村口黄昏", "generated_assets": {}},
        {"segment_id": "E1S02", "image_prompt": "山道清晨", "generated_assets": {}},
    ]

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult
        from lib.task_failure import encode_failure

        succeeded = [
            BatchTaskResult(
                resource_id="E1S01",
                task_id="t1",
                status="succeeded",
                result={"file_path": "storyboards/scene_E1S01.png"},
                task={"provider_id": "openai", "provider_job_id": "job-1"},
            )
        ]
        failed = [
            BatchTaskResult(
                resource_id="E1S02",
                task_id="t2",
                status="failed",
                error=encode_failure("video_capability_missing_i2v"),
                task={"provider_id": "openai", "provider_job_id": None},
            )
        ]
        return succeeded, failed

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(generate_storyboards_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    result = _generation_result(out)
    assert result.succeeded == ["E1S01"]
    assert result.failed == ["E1S02"]
    assert set(result.requested) == {"E1S01", "E1S02"}
    failed_item = next(item for item in result.items if item.unit_id == "E1S02")
    assert failed_item.problem is not None
    assert failed_item.problem.code == "video_capability_missing_i2v"
    assert failed_item.problem.action.value == "configure_provider"
    # 供应商提交与任务状态分开报告：这一条任务失败但从未提交给供应商。
    assert failed_item.task_state.value == "failed"
    assert failed_item.provider_checkpoint is not None
    assert failed_item.provider_checkpoint.submitted is False


@pytest.mark.unit
async def test_generate_narration_audio_enqueues_missing_segments(fake_ctx: ToolContext, monkeypatch) -> None:
    """不传 segment_ids → 只为缺 narration_audio 的段入队 tts 任务，prompt 为该段 novel_text。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"audio/segment_{s.resource_id}.wav"},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]
    spec = captured[0]
    assert spec.task_type == "tts"
    assert spec.media_type == "audio"
    assert spec.payload["prompt"] is None
    assert spec.payload["script_file"] == "episode_1.json"
    text = out["content"][0]["text"]
    assert "1 succeeded" in text
    assert "audio/segment_E1S01.wav" in text


@pytest.mark.integration
async def test_generate_narration_audio_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    _activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    enqueued = False

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        nonlocal enqueued
        enqueued = True
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(mod.generate_narration_audio_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    assert enqueued is False


@pytest.mark.unit
async def test_generate_narration_audio_uses_canonical_filename_when_episode_field_is_absent(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script.pop("episode")
    script["segments"] = script["segments"][:1]
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": 8,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "episodes": [{"episode": 2, "script_file": "scripts/episode_2.json"}],
        }
    )
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(fake_ctx.pm.project_payload),  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    captured: list[Any] = []
    selected_episodes: list[int] = []
    build_candidates = mod._candidates

    def _capture_episode(*args, **kwargs):
        selected_episodes.append(kwargs["episode"])
        return build_candidates(*args, **kwargs)

    async def _batch(*, project_name, specs, on_success=None, on_failure=None):
        captured.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "_candidates", _capture_episode)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _batch)

    out = await _call(mod.generate_narration_audio_tool(fake_ctx), {"script": "episode_2.json"})

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1S01"]
    assert selected_episodes == [2]


@pytest.mark.unit
async def test_generate_narration_audio_selects_item_with_corrupt_generated_assets(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = "corrupt"
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"audio/segment_{s.resource_id}.wav"},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]


@pytest.mark.unit
async def test_generate_narration_audio_explicit_ids_regenerate(fake_ctx: ToolContext, monkeypatch) -> None:
    """传 segment_ids → 即使该段已有 narration_audio 也重新入队（批量范围/单段重生语义）。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="succeeded", result={}) for s in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": ["E1S02"]})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S02"]


@pytest.mark.unit
async def test_generate_narration_audio_blank_text_reported(fake_ctx: ToolContext, monkeypatch) -> None:
    """novel_text 空白的段不能静默丢弃：不入队、在输出中可见，显式点名时按错误上报。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"].append({"segment_id": "E1S03", "novel_text": "   ", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="succeeded", result={}) for s in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)

    # 扫描模式：空白段根本不是缺口，不进 requested，也不阻塞其余段
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]
    result = _generation_result(out)
    assert "E1S03" not in result.requested

    # 显式点名空白段：该段按 blocked 上报，带稳定 code 与下一步动作
    captured.clear()
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": ["E1S03"]})
    assert out.get("is_error") is True
    assert captured == []
    result = _generation_result(out)
    assert result.requested == ["E1S03"]
    assert result.blocked == ["E1S03"]
    problem = result.items[0].problem
    assert problem is not None
    # 发声准入自己的问题码原样透出，调用方不必读文本判断下一步。
    assert problem.code == "parse_failed"
    assert problem.action.value == "fix_input"


@pytest.mark.unit
async def test_generate_narration_audio_partial_unmatched_reported(fake_ctx: ToolContext, monkeypatch) -> None:
    """部分 id 不命中不能静默丢弃：命中的照常入队，未命中的按 blocked 逐 ID 上报。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="succeeded", result={}) for s in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": ["E1S01", "E1S99"]})

    assert out.get("is_error") is True
    assert [s.resource_id for s in captured] == ["E1S01"]
    result = _generation_result(out)
    assert sorted(result.requested) == ["E1S01", "E1S99"]
    assert result.succeeded == ["E1S01"]
    assert result.blocked == ["E1S99"]
    unmatched = next(item for item in result.items if item.unit_id == "E1S99")
    assert unmatched.problem is not None
    assert unmatched.problem.code == "generation_unit_not_found"


@pytest.mark.unit
async def test_generate_narration_audio_accepts_drama_narrator_scene(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.project_payload["content_mode"] = "drama"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "drama",
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "utterances": [{"kind": "voiceover", "speaker": None, "text": "夜幕降临。"}],
                "generated_assets": {},
            }
        ],
    }
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        del project_name, on_success, on_failure
        captured.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1S01"]
    assert captured[0].payload == {"prompt": None, "script_file": "episode_1.json"}


@pytest.mark.integration
async def test_generate_narration_audio_uses_project_mode_for_drama_without_content_mode(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.project_payload["content_mode"] = "drama"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "episode": 1,
        "scenes": [
            {
                "scene_id": "E1S01",
                "utterances": [{"kind": "voiceover", "speaker": None, "text": "夜幕降临。"}],
                "generated_assets": {},
            }
        ],
    }
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        del project_name, on_success, on_failure
        captured.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1S01"]


@pytest.mark.integration
async def test_generate_narration_audio_accepts_reference_narrator_unit(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "镜头1：海面\n{风从远方吹来。}"}],
                "references": [],
                "duration_seconds": 8,
                "generated_assets": {},
            }
        ],
    }
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        del project_name, on_success, on_failure
        captured.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1U1"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("generate_video_episode_tool", {"script": "episode_1.json"}),
        ("generate_video_scene_tool", {"script": "episode_1.json", "scene_id": "E1U1"}),
        ("generate_video_all_tool", {"script": "episode_1.json"}),
        ("generate_video_selected_tool", {"script": "episode_1.json", "scene_ids": ["E1U1"]}),
    ],
)
async def test_generate_video_rejects_mismatched_unit_script_on_storyboard_route(
    fake_ctx: ToolContext, tool_name: str, args: dict[str, Any]
) -> None:
    """分镜路线项目下的 video_units 骨架剧本：四个入口一律结构报错 + 重拆指引。

    静默降档与悄悄换路径都不可构造——存量混排集的唯一出路是重拆重生成。
    """
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1", "shots": [{"text": "x"}], "duration_seconds": 5}],
    }
    tool_obj = getattr(mod, tool_name)(fake_ctx)
    out = await _call(tool_obj, args)

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "骨架" in text and "重新拆分" in text


@pytest.mark.integration
async def test_generate_video_episode_rejects_mismatched_storyboard_script_on_reference_route(
    fake_ctx: ToolContext,
) -> None:
    """反向：参考路线项目下的分镜骨架剧本同样被拒，指引重跑 unit 拆分。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]
    tool_obj = mod.generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "split-reference-video-units" in out["content"][0]["text"]


@pytest.mark.integration
async def test_generate_narration_audio_rejects_mismatched_script(fake_ctx: ToolContext) -> None:
    """分镜路线项目下的 video_units 骨架剧本：结构报错 + 重拆指引，不静默换路径。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "骨架" in text and "重新拆分" in text


@pytest.mark.unit
async def test_generate_narration_audio_rejects_string_segment_ids(fake_ctx: ToolContext) -> None:
    """segment_ids 传裸字符串会被逐字符迭代成 {'E','1','S'...}，必须显式拒绝。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": "E1S01"})
    assert out.get("is_error") is True
    assert "数组" in out["content"][0]["text"]


@pytest.mark.unit
async def test_generate_narration_audio_skips_segment_without_id(fake_ctx: ToolContext, monkeypatch) -> None:
    """缺 segment_id 的片段不能让整批中断：无 ID 可寻址故不进契约，其余片段照常入队。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"].append({"novel_text": "有文本但缺 id 的片段。", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="succeeded", result={}) for s in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]
    assert _generation_result(out).requested == ["E1S01"]


@pytest.mark.unit
async def test_generate_narration_audio_no_match_error(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": ["NO_SUCH"]})
    assert out.get("is_error") is True
    result = _generation_result(out)
    assert result.requested == ["NO_SUCH"]
    assert result.blocked == ["NO_SUCH"]


@pytest.mark.unit
async def test_generate_narration_audio_all_done(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = {"narration_audio": "audio/segment_E1S01.wav"}
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    # 已有配音的单元被复用而非重生：不进 requested，只作为 skipped 报告。
    result = _generation_result(out)
    assert result.requested == []
    assert [entry.unit_id for entry in result.skipped] == ["E1S01", "E1S02"]


@pytest.mark.unit
async def test_generate_narration_audio_task_failures_surface(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        fails = [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="failed", error="provider down")
            for s in specs
        ]
        return [], fails

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "0 succeeded, 1 failed" in text
    assert "provider down" in text


@pytest.mark.unit
async def test_generate_narration_audio_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "../etc/passwd"})
    assert out.get("is_error") is True
    assert "路径分隔符" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# enqueue_storyboards
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_generate_storyboards_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"storyboards/scene_{s.resource_id}.png"},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    # Strip storyboard_image to force selection
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}  # type: ignore[attr-defined]
    semantic_prompt = {
        "scene": "村口黄昏",
        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
    }
    fake_ctx.pm.script_payload["segments"][0]["image_prompt"] = semantic_prompt  # type: ignore[attr-defined]
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    assert captured[0].payload["prompt"] == semantic_prompt


@pytest.mark.integration
async def test_generate_storyboards_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    _activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {}  # type: ignore[attr-defined]
    enqueued = False

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        nonlocal enqueued
        enqueued = True
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(mod.generate_storyboards_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    assert enqueued is False


@pytest.mark.unit
async def test_generate_storyboards_selects_item_with_corrupt_generated_assets(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"storyboards/scene_{s.resource_id}.png"},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = "corrupt"  # type: ignore[attr-defined]
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]


@pytest.mark.integration
async def test_generate_storyboards_rejects_mismatched_unit_script(fake_ctx: ToolContext) -> None:
    """失配剧本不能落进"✨ 所有片段的分镜图都已生成"的假成功——报结构错误并指引重拆。"""
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "骨架" in text and "重新拆分" in text


@pytest.mark.unit
async def test_generate_storyboards_error(fake_ctx: ToolContext, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ValueError("bad script")

    fake_ctx.pm.load_script = boom  # type: ignore[attr-defined]
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# enqueue_image_edits
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_edit_images_registered() -> None:
    """edit_images 必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "edit_images" in ARCREEL_MCP_TOOL_IDS


@pytest.mark.unit
async def test_edit_images_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]

    async def fake_i2i(_project):
        return True

    async def fake_batch(*, project_name, specs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 2},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    )
    assert out.get("is_error") is not True, out
    text = out["content"][0]["text"]
    assert "1 succeeded" in text
    assert "张三" in text


@pytest.mark.unit
async def test_edit_images_i2i_unavailable(fake_ctx: ToolContext, monkeypatch) -> None:
    """i2i 不可用时直接报错，不创建任何任务（复用服务端 fail-fast 判断点）。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_i2i(_project):
        return False

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    )
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_edit_images_active_asset_without_a_manifest_claim_is_not_enqueued(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lib.artifact_manifest import ArtifactComparison, ArtifactKey, ArtifactStatus
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["schema_version"] = 8  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]
    comparisons = []

    class _Currency:
        def compare(self, key, *, artifact_path):
            comparisons.append((key, artifact_path))
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

        def resolve_usable_entry(self, key, *, artifact_path):
            self.compare(key, artifact_path=artifact_path)
            return None

    async def fake_i2i(_project):
        return True

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _Currency())
    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        edit_images_tool(fake_ctx),
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "换发色"}]},
    )

    assert out.get("is_error") is True
    assert comparisons == [(ArtifactKey.asset_sheet("character", "张三"), "characters/zhangsan.png")]
    enqueue.assert_not_awaited()


@pytest.mark.unit
async def test_edit_images_storyboard_requires_script_file(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_i2i(_project):
        return True

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "storyboard", "edits": [{"id": "E1S01", "instruction": "去杂物"}]})
    assert out.get("is_error") is True
    assert "script_file" in out["content"][0]["text"]


@pytest.mark.unit
async def test_edit_images_storyboard_rejects_an_unbound_script_before_provider(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    fake_ctx.pm.project_payload["schema_version"] = 8  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["episodes"] = []  # type: ignore[attr-defined]
    provider_gate = AsyncMock(return_value=True)
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "_i2i_provider_available", provider_gate)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        edit_images_tool(fake_ctx),
        {
            "resource_type": "storyboard",
            "script_file": "episode_1.json",
            "edits": [{"id": "E1S01", "instruction": "去掉背景杂物"}],
        },
    )

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    provider_gate.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.unit
async def test_edit_images_rejects_unknown_resource_type(fake_ctx: ToolContext) -> None:
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "video", "edits": [{"id": "x", "instruction": "y"}]})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_edit_images_skips_missing_current_image(fake_ctx: ToolContext, monkeypatch) -> None:
    """资产没有可编辑的当前图（sheet 字段未设置）时跳过并告警，不入队。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_i2i(_project):
        return True

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    tool_obj = edit_images_tool(fake_ctx)
    # 李四 没有 character_sheet
    out = await _call(tool_obj, {"resource_type": "character", "edits": [{"id": "李四", "instruction": "换发色"}]})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "李四" in text
    assert "没有可编辑的当前图" in text


@pytest.mark.unit
async def test_edit_images_rejects_empty_edits(fake_ctx: ToolContext) -> None:
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": []})
    assert out.get("is_error") is True
    assert "edits 不能为空" in out["content"][0]["text"]


@pytest.mark.unit
async def test_edit_images_build_specs_warnings(fake_ctx: ToolContext, monkeypatch) -> None:
    """畸形条目分两路：有 ID 的进逐 ID blocked，无 ID 可寻址的留在 warnings；合法条目仍正常入队。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]

    async def fake_i2i(_project):
        return True

    async def fake_batch(*, project_name, specs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 2},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {
            "resource_type": "character",
            "edits": [
                "not-a-dict",  # 非 dict 条目
                {"id": "", "instruction": "x"},  # 缺 id
                {"id": "张三", "instruction": "改发型"},  # 合法，唯一入队的一条
                {"id": "张三", "instruction": "again"},  # 重复 id
                {"id": "李四", "instruction": ""},  # 缺指令
                {"id": "王五", "instruction": "改"},  # 资源不存在
            ],
        },
    )
    text = out["content"][0]["text"]
    # 无 ID 可寻址的条目没有可报告的 unit，只能留在文本告警里。
    assert "非法条目" in text
    assert "缺少 id 的条目" in text
    assert "重复出现" in text

    result = _generation_result(out)
    assert result.succeeded == ["张三"]
    assert sorted(result.blocked) == ["李四", "王五"]
    problems = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert problems == {
        "李四": "generation_unit_request_invalid",
        "王五": "generation_unit_not_found",
    }


@pytest.mark.unit
async def test_edit_images_storyboard_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    """storyboard 分支带合法 script_file 时应正常解析剧本并入队（覆盖 validate_script_filename + load_script 调用）。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_i2i(_project):
        return True

    async def fake_batch(*, project_name, specs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"storyboards/scene_{s.resource_id}.png", "version": 2},
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {
            "resource_type": "storyboard",
            "script_file": "episode_1.json",
            "edits": [{"id": "E1S01", "instruction": "去掉背景杂物"}],
        },
    )
    assert out.get("is_error") is not True, out
    assert "1 succeeded" in out["content"][0]["text"]


@pytest.mark.unit
async def test_edit_images_reports_failures(fake_ctx: ToolContext, monkeypatch) -> None:
    """批量入队返回失败项时，摘要与明细都要带上失败原因。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]

    async def fake_i2i(_project):
        return True

    async def fake_batch(*, project_name, specs):
        from lib.generation_queue_client import BatchTaskResult

        fail = [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="failed", error="provider timeout")
            for s in specs
        ]
        return [], fail

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": [{"id": "张三", "instruction": "改发型"}]})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "0 succeeded, 1 failed" in text
    assert "provider timeout" in text


@pytest.mark.unit
async def test_edit_images_unexpected_exception(fake_ctx: ToolContext) -> None:
    """未预期的异常（如 pm 读取项目失败）要落到统一的 tool_error 兜底，而非向上抛出。"""

    def boom(_name: str) -> dict[str, Any]:
        raise RuntimeError("db down")

    fake_ctx.pm.load_project = boom  # type: ignore[method-assign]
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": [{"id": "张三", "instruction": "x"}]})
    assert out.get("is_error") is True
    assert "edit_images 失败" in out["content"][0]["text"]


@pytest.mark.unit
async def test_i2i_provider_available_true(monkeypatch) -> None:
    from lib.config.resolver import ConfigResolver
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_resolve(self, project, payload, *, capability):
        assert capability == "i2i"
        return object()

    monkeypatch.setattr(ConfigResolver, "resolve_image_backend", fake_resolve)
    assert await mod._i2i_provider_available({}) is True


@pytest.mark.unit
async def test_i2i_provider_available_false_on_value_error(monkeypatch) -> None:
    from lib.config.resolver import ConfigResolver
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_resolve(self, project, payload, *, capability):
        raise ValueError("未找到可用的 image 供应商")

    monkeypatch.setattr(ConfigResolver, "resolve_image_backend", fake_resolve)
    assert await mod._i2i_provider_available({}) is False


# ---------------------------------------------------------------------------
# enqueue_grid
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_generate_grid_list_only(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    # Need enough segments to form a group with valid layout
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    assert "分组" in out["content"][0]["text"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("allow_large_grid", "expected", "forbidden"),
    [(True, "grid_16 (4×4)", "grid_9"), (False, "grid_9 (3×3)", "grid_16")],
)
async def test_generate_grid_list_only_respects_4k_gate(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    allow_large_grid: bool,
    expected: str,
    forbidden: str,
) -> None:
    # 非 4K 时 4×4 / 5×5 不出现在面向 agent 的分组预览里
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]

    async def _gate(_project: dict) -> bool:
        return allow_large_grid

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert expected in text
    assert forbidden not in text


@pytest.mark.unit
async def test_generate_grid_list_only_shows_split_for_oversized_group(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 超过单张格数上限的分组，预览按切块后的张数与档位展示，与实际入队同源
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "2 张宫格: grid_9 (3×3) + grid_4 (2×2)" in text


@pytest.mark.unit
async def test_generate_grid_falls_back_on_null_aspect_ratio(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # project.json 允许把 aspect_ratio 显式写为 null；SDK 入队路径须回退到默认比例，
    # 否则 None 会写进宫格规划、任务 payload 与记录上冻结的比例
    from lib.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["aspect_ratio"] = None  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(*, project_name, task_type, media_type, resource_id, payload, script_file, source):
        payloads.append(payload)
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str) -> dict[str, Any]:
        return {"status": "succeeded"}

    async def fake_split(project_name: str, grid: Any) -> Any:
        from server.services.grid_split import GridSplitResult

        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.enqueue_task_only", fake_enqueue)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.wait_for_task", fake_wait)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.apply_grid_split", fake_split)

    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True

    assert [p["video_aspect_ratio"] for p in payloads] == ["9:16"]
    assert [g.video_aspect_ratio for g in GridManager(fake_ctx.project_path).list_all()] == ["9:16"]


@pytest.mark.integration
async def test_generate_grid_split_failure_keeps_the_paid_image_and_fails_the_id(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """联合图已付费落盘、切分失败：该组每个场景都记为 failed，不声称产物已就位。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(*, project_name, task_type, media_type, resource_id, payload, script_file, source):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str) -> dict[str, Any]:
        return {"status": "succeeded", "provider_id": "openai", "provider_job_id": "job-1"}

    async def failing_split(project_name: str, grid: Any) -> Any:
        raise RuntimeError("cannot write the split cells")

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.enqueue_task_only", fake_enqueue)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.wait_for_task", fake_wait)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.apply_grid_split", failing_split)

    out = await _call(generate_grid_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    result = _generation_result(out)
    assert result.succeeded == []
    # 逐场景 ID 报告：一张宫格覆盖的四个场景各自拿到自己的失败结论。
    assert result.failed == ["E1S01", "E1S02", "E1S03", "E1S04"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "generation_post_processing_failed"
    assert item.problem.params["grid_id"].startswith("grid_")
    # 任务与供应商提交都成功（钱已花），只有产物没有被标成就位。
    assert item.task_state.value == "succeeded"
    assert item.provider_checkpoint is not None
    assert item.provider_checkpoint.submitted is True
    assert item.artifact_status is not ArtifactStatus.CURRENT


@pytest.mark.unit
async def test_generate_grid_reports_each_scene_of_a_shared_grid(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同组场景共享一张宫格，结果仍逐场景 ID 报告：落格的成功、没落格的单独失败。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(*, project_name, task_type, media_type, resource_id, payload, script_file, source):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str) -> dict[str, Any]:
        return {"status": "succeeded", "provider_id": "openai", "provider_job_id": "job-1"}

    async def partial_split(project_name: str, grid: Any) -> Any:
        from server.services.grid_split import GridSplitResult

        # 最后一格对应的分镜已不在剧本里，切分时被跳过。
        return GridSplitResult(
            updated_scene_ids=list(grid.scene_ids[:-1]),
            missing_scene_ids=[grid.scene_ids[-1]],
            asset_fingerprints={},
        )

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.enqueue_task_only", fake_enqueue)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.wait_for_task", fake_wait)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.apply_grid_split", partial_split)

    out = await _call(generate_grid_tool(fake_ctx), {"script": "episode_1.json"})

    result = _generation_result(out)
    assert result.succeeded == ["E1S01", "E1S02", "E1S03"]
    assert result.failed == ["E1S04"]
    assert set(result.requested) == set(result.succeeded) | set(result.failed) | set(result.blocked)
    done = next(item for item in result.items if item.unit_id == "E1S01")
    assert done.artifact_path == "storyboards/scene_E1S01.png"
    dropped = next(item for item in result.items if item.unit_id == "E1S04")
    assert dropped.problem is not None
    assert dropped.problem.code == "generation_post_processing_failed"
    # 联合图这一次是花了钱的，所以未落格的那一格也带着成功的任务与供应商提交。
    assert dropped.task_state.value == "succeeded"


@pytest.mark.unit
async def test_generate_grid_rejects_an_explicitly_empty_scene_selection(fake_ctx: ToolContext) -> None:
    """显式空集合不是「全部」：拒绝请求，而不是静默扫全集。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]

    out = await _call(generate_grid_tool(fake_ctx), {"script": "episode_1.json", "scene_ids": []})

    assert out.get("is_error") is True
    assert "不能为空数组" in out["content"][0]["text"]


@pytest.mark.unit
async def test_generate_grid_cleans_superseded_records(fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """重生成清理规则对 SDK 路径生效：旧记录不残留在前端列表。

    通过 generate_grid 重生成某组宫格后，该组旧的已完成记录（同脚本同集、
    scene_ids 是当前组子集）被清理；其它组/代与非在途无关的记录不得误删。
    """
    from lib.grid.models import GridGeneration
    from lib.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "video_prompt": "v", "segment_break": False}
        for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(*, project_name, task_type, media_type, resource_id, payload, script_file, source):
        return {"task_id": f"t{resource_id}"}

    async def fake_wait(_task_id: str) -> dict[str, Any]:
        return {"status": "succeeded"}

    async def fake_split(project_name: str, grid: Any) -> Any:
        from server.services.grid_split import GridSplitResult

        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.enqueue_task_only", fake_enqueue)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.wait_for_task", fake_wait)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.apply_grid_split", fake_split)

    # 预置两代旧记录：一代属于本组（应被清理），一代属于其它组（不得误删）
    gm = GridManager(fake_ctx.project_path)
    superseded = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    superseded.status = "completed"
    gm.save(superseded)
    other_group = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S99"],
        rows=1,
        cols=1,
        grid_size="grid_1",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    other_group.status = "completed"
    gm.save(other_group)

    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True

    remaining = gm.list_all()
    ids = [g.id for g in remaining]
    assert superseded.id not in ids, "superseded old record must be cleaned up"
    assert other_group.id in ids, "records of other groups must not be deleted"
    fresh = [g for g in remaining if g.id != other_group.id]
    assert [g.scene_ids for g in fresh] == [["E1S01", "E1S02", "E1S03", "E1S04"]]


@pytest.mark.unit
async def test_generate_grid_list_only_falls_back_on_null_aspect_ratio(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 预览路径与入队路径同源，同样不能让 None 流进 plan_grid_chunks
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["aspect_ratio"] = None  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    assert "grid_4 (2×2)" in out["content"][0]["text"]


@pytest.mark.unit
async def test_generate_grid_splits_oversized_group_into_multiple_grids(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 12 场景 + 非 4K（上限 9）：入队 2 张宫格，场景不重不漏，每张 prompt 场景数与格数一致
    from lib.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    all_ids = [f"E1S{i:02d}" for i in range(1, 13)]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": sid, "image_prompt": "p", "video_prompt": "v", "segment_break": False} for sid in all_ids
    ]

    async def _gate(_project: dict) -> bool:
        return False

    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(*, project_name, task_type, media_type, resource_id, payload, script_file, source):
        payloads.append(payload)
        return {"task_id": f"t{len(payloads)}"}

    async def fake_wait(_task_id: str) -> dict[str, Any]:
        return {"status": "succeeded"}

    # 生成成功后工具会对每张宫格显式调用切分；此处替换为假实现，单独锁定入队分块行为
    split_calls: list[str] = []

    async def fake_split(project_name: str, grid: Any) -> Any:
        from server.services.grid_split import GridSplitResult

        split_calls.append(grid.id)
        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.resolve_large_grid_allowed", _gate)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.enqueue_task_only", fake_enqueue)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.wait_for_task", fake_wait)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_grid.apply_grid_split", fake_split)

    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    # 每张生成成功的宫格都被显式切分
    assert len(split_calls) == 2

    assert [(len(p["scene_ids"]), p["grid_size"]) for p in payloads] == [(9, "grid_9"), (3, "grid_4")]
    # 场景不重不漏且保持顺序
    assert [sid for p in payloads for sid in p["scene_ids"]] == all_ids
    # 每张的 prompt 按自身块与档位构建
    assert "3×3" in payloads[0]["prompt"]
    assert "2×2" in payloads[1]["prompt"]

    # 落盘的 grid 记录与 payload 一致，帧链长度等于格数
    grids = sorted(GridManager(fake_ctx.project_path).list_all(), key=lambda g: len(g.scene_ids), reverse=True)
    assert [(g.scene_ids, g.rows, g.cols) for g in grids] == [(all_ids[:9], 3, 3), (all_ids[9:], 2, 2)]
    assert all(len(g.frame_chain) == g.rows * g.cols for g in grids)


@pytest.mark.unit
async def test_generate_grid_wrong_mode(fake_ctx: ToolContext) -> None:
    # 项目未开启 grid_storyboard → error
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_grid_rejected_on_reference_video_route(fake_ctx: ToolContext) -> None:
    # reference_video 路线无分镜图步骤：即使残留 grid_storyboard=true 也不适用宫格工具
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_grid_legacy_unresolvable_episode_fails_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_grid as mod

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload.pop("episode")  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    enqueue = AsyncMock(side_effect=AssertionError("must not enqueue"))
    monkeypatch.setattr(mod, "enqueue_task_only", enqueue)

    out = await _call(mod.generate_grid_tool(fake_ctx), {"script": "draft.json"})

    assert out.get("is_error") is True
    assert "无法确定集号" in out["content"][0]["text"]
    enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# enqueue_videos
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_generate_video_episode_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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
    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True


@pytest.mark.integration
@pytest.mark.parametrize("selection", ["episode", "selected"])
@pytest.mark.parametrize("claim_state", ["missing", "blocked"])
async def test_storyboard_resume_requires_usable_manifest_video_claim(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    claim_state: str,
) -> None:
    """A checkpoint can reuse only the exact canonical video admitted by active currency."""
    from lib.artifact_manifest import (
        ArtifactBlocker,
        ArtifactComparison,
        ArtifactKey,
        ArtifactManifestEntry,
        ArtifactManifestError,
        ArtifactStatus,
    )
    from lib.generation_queue_client import BatchTaskResult
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    project = fake_ctx.pm.project_payload  # type: ignore[attr-defined]
    project.update(
        {
            "schema_version": 8,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1  # type: ignore[attr-defined]

    candidate = fake_ctx.project_path / "videos/scene_E1S01.mp4"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"unclaimed-checkpoint-output")
    if selection == "episode":
        checkpoint = mod._episode_checkpoint_path(fake_ctx.project_path, 1)
        args = {"script": "episode_1.json", "resume": True}
        tool_obj = mod.generate_video_episode_tool(fake_ctx)
    else:
        scenes_hash = hashlib.md5(b"E1S01").hexdigest()[:8]
        checkpoint = mod._selected_checkpoint_path(fake_ctx.project_path, scenes_hash)
        args = {"script": "episode_1.json", "scene_ids": ["E1S01"], "resume": True}
        tool_obj = mod.generate_video_selected_tool(fake_ctx)
    mod._save_checkpoint_at(checkpoint, ["E1S01"], "2026-01-01T00:00:00+00:00")

    checked_video_paths: list[str] = []

    class _Currency:
        def compare(self, key, *, artifact_path):
            if key == ArtifactKey.episode_video(1, "E1S01"):
                checked_video_paths.append(artifact_path)
                if claim_state == "blocked":
                    return ArtifactComparison(
                        status=ArtifactStatus.BLOCKED,
                        artifact_path=artifact_path,
                        blocker=ArtifactBlocker(
                            code="manifest_unreadable",
                            path=artifact_path,
                            detail="checkpoint video claim is blocked",
                        ),
                    )
                return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)
            return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=artifact_path)

        def resolve_usable_entry(self, key, *, artifact_path):
            comparison = self.compare(key, artifact_path=artifact_path)
            if comparison.status is ArtifactStatus.BLOCKED:
                assert comparison.blocker is not None
                raise ArtifactManifestError(comparison.blocker.detail)
            if comparison.status not in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}:
                return None
            return ArtifactManifestEntry(artifact_path=artifact_path, basis_digest="selected")

        def compare_frozen_entry(self, key, entry):
            return self.compare(key, artifact_path=entry.artifact_path)

        def artifact_content_digest(self, artifact_path):
            return "0" * 64

    currency = _Currency()

    enqueued: list[str] = []

    async def _batch(*, project_name, specs, on_success=None, on_failure=None):
        for spec in specs:
            enqueued.append(spec.resource_id)
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

    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: currency)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _batch)

    out = await _call(tool_obj, args)

    assert checked_video_paths == ["videos/scene_E1S01.mp4"]
    if claim_state == "blocked":
        assert out.get("is_error") is True
        assert "checkpoint video claim is blocked" in out["content"][0]["text"]
        assert enqueued == []
    else:
        assert out.get("is_error") is not True, out
        assert enqueued == ["E1S01"]


@pytest.mark.integration
async def test_generate_video_episode_declares_the_missing_only_selection_it_performs(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """整集生成从不强制重生：已有可用片段一律复用，所以选择模式如实报 missing-only。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S01.png"
    }
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)

    out = await _call(mod.generate_video_episode_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert result.selection.value == "missing_only"
    assert result.succeeded == ["E1S01"]


@pytest.mark.integration
async def test_generate_video_episode_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from lib.generation_queue_client import TaskSpec
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _activate_unbound_project(fake_ctx)
    submitted = False
    spec = TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id="E1S01",
        prompt="test",
        script_file="episode_1.json",
    )

    def fake_build_specs(**_kwargs):
        return [spec], {"E1S01": 0}

    async def fake_submit(**_kwargs):
        nonlocal submitted
        submitted = True
        return []

    monkeypatch.setattr(mod, "_build_video_specs", fake_build_specs)
    monkeypatch.setattr(mod, "_submit_with_checkpoint", fake_submit)

    out = await _call(mod.generate_video_episode_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    assert submitted is False


@pytest.mark.unit
async def test_generate_video_episode_resolves_episode_from_canonical_filename(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    """A schema-8 script may rely on its canonical filename for the episode identity."""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload.pop("episode")  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": 8,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "episodes": [{"episode": 2, "script_file": "scripts/episode_2.json"}],
        }
    )
    captured: dict[str, int] = {}
    build_video_specs = mod._build_video_specs

    def _capture_episode(**kwargs):
        captured["episode"] = kwargs["episode"]
        return build_video_specs(**{**kwargs, "project": {}})

    async def _batch(*, project_name, specs, on_success=None, on_failure=None):
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

    monkeypatch.setattr(mod, "_build_video_specs", _capture_episode)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _batch)
    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: None)

    out = await _call(generate_video_episode_tool(fake_ctx), {"script": "episode_2.json"})

    assert out.get("is_error") is not True, out
    assert captured == {"episode": 2}


@pytest.mark.integration
async def test_generate_video_episode_non_dict_generated_assets_does_not_abort_batch(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """整集入队先按 generated_assets.video_clip 过滤已完成条目。容器被外部编辑损坏为非 dict
    时该过滤须按「未生成」处理，而不是在 pending 过滤阶段就抛未处理 AttributeError——那会
    让整批在到达逐条跳过逻辑之前就中断。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    project_dir = fake_ctx.pm.get_project_path("demo")
    (project_dir / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": "E1S01", "video_prompt": "脏数据", "generated_assets": ["bad"]},
        {
            "segment_id": "E1S02",
            "video_prompt": "合法条目",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        },
    ]
    enqueued: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(spec.resource_id for spec in specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    # E1S01 的分镜图绑定不可用，作为 blocked 逐 ID 报告；合法条目照常入队。
    assert enqueued == ["E1S02"]
    result = _generation_result(out)
    assert result.blocked == ["E1S01"]
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_video_episode_error(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.script_payload = {"content_mode": "narration", "segments": [], "episode": 1}  # type: ignore[attr-defined]
    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


def _reference_video_script(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content_mode": "narration",
        "episode": 1,
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "@张三 推门"}],
                "references": [{"type": "character", "name": "张三"}],
                "duration_seconds": 5,
            }
        ],
    }
    payload.update(overrides)
    return payload


def _use_reference_route(fake_ctx: ToolContext) -> None:
    """把 fake 项目切到参考生视频路线——路线是项目级事实，剧本不携带戳。"""
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]


@pytest.mark.integration
async def test_generate_reference_video_rejects_unbound_active_script_before_generation(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _activate_unbound_project(fake_ctx, generation_mode="reference_video")
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]
    generated = False

    async def fake_generate(**_kwargs):
        nonlocal generated
        generated = True
        return mod.ReferenceGenerationComplete(paths=[], projections=[])

    monkeypatch.setattr(mod, "_generate_reference_units", fake_generate)

    out = await _call(mod.generate_video_episode_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    assert generated is False


@pytest.mark.integration
async def test_generate_reference_video_legacy_unresolvable_episode_fails_before_generation(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload.pop("episode")  # type: ignore[attr-defined]
    generate = AsyncMock(return_value=mod.ReferenceGenerationComplete(paths=[], projections=[]))
    monkeypatch.setattr(mod, "_generate_reference_units", generate)

    out = await _call(mod.generate_video_episode_tool(fake_ctx), {"script": "draft.json"})

    assert out.get("is_error") is True
    assert "无法确定集号" in out["content"][0]["text"]
    generate.assert_not_awaited()


@pytest.mark.integration
async def test_generate_video_episode_reference_rejects_malformed_unit_container(fake_ctx: ToolContext) -> None:
    """``video_units`` 非数组：路线闸门只问键在不在，容器校验落在入队侧，
    须报出可定位的结构错误而不是下传到 unit 迭代抛 TypeError。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import generate_video_episode_tool

    _use_reference_route(fake_ctx)
    for malformed, type_name in (
        ({"E1U1": {}}, "dict"),
        ({}, "dict"),
        ("", "str"),
        (False, "bool"),
        (None, "NoneType"),
    ):
        # 键在场即按类型判定，不看真值：``{}`` / ``""`` / ``False`` 同样是类型错误，
        # 报成「为空」会把成因埋掉。
        fake_ctx.pm.script_payload = _reference_video_script(video_units=malformed)  # type: ignore[attr-defined]
        tool_obj = generate_video_episode_tool(fake_ctx)
        out = await _call(tool_obj, {"script": "episode_1.json"})
        assert out.get("is_error") is True
        text = out["content"][0]["text"]
        assert "video_units 必须是数组" in text
        assert type_name in text


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_needs_confirmation(fake_ctx: ToolContext, monkeypatch) -> None:
    """申请秒数与剧本总时长不一致时，首次调用不入队，返回内容含总时长/申请秒数/差异说明。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    async def fake_active_tasks(**_kwargs):
        return []

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr(mod, "get_active_tasks_for_resources", fake_active_tasks)

    tool_obj = generate_video_episode_tool(fake_ctx)
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


@pytest.mark.integration
async def test_generate_video_episode_reference_returns_structured_projection_blocker(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    """Agent 失败信封保留公共投影的稳定 problem 字段，不只返回人读文本。"""
    from lib.reference_video.request_projection import ProjectionProblem
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    class _BlockedProjection:
        unit_id = "E1U1"
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

    monkeypatch.setattr(mod, "project_reference_unit_request", _blocked)

    out = await _call(mod.generate_video_episode_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert out["request_projection"] == {
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


@pytest.mark.unit
def test_only_single_video_agent_tool_exposes_narration_delivery(fake_ctx: ToolContext) -> None:
    batch_tools = (
        generate_video_episode_tool(fake_ctx),
        generate_video_all_tool(fake_ctx),
        generate_video_selected_tool(fake_ctx),
    )

    for tool_obj in batch_tools:
        schema = tool_obj.input_schema
        assert isinstance(schema, dict)
        properties = schema.get("properties")
        assert isinstance(properties, dict)
        assert "narration_delivery" not in properties
        assert "confirmed_request_duration_seconds" in properties
    scene_schema = generate_video_scene_tool(fake_ctx).input_schema
    assert isinstance(scene_schema, dict)
    scene_properties = scene_schema.get("properties")
    assert isinstance(scene_properties, dict)
    assert "narration_delivery" in scene_properties


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_confirm_enqueues(fake_ctx: ToolContext, monkeypatch) -> None:
    """带精确申请档位的再次调用按取档结果入队并生成成功。"""
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {"script": "episode_1.json", "confirmed_request_duration_seconds": 8},
    )

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


@pytest.mark.integration
async def test_generate_video_episode_reference_ignores_out_of_schema_tts_choice(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from lib.generation_queue_client import BatchTaskResult
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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
    monkeypatch.setattr(mod, "project_reference_unit_request", _capture_delivery)
    monkeypatch.setattr(mod, "active_tts_resource_ids", active_tts)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = generate_video_episode_tool(fake_ctx)

    completed = await _call(
        tool_obj,
        {"script": "episode_1.json", "narration_delivery": "use_tts"},
    )

    assert completed.get("is_error") is not True
    assert projected_deliveries == ["post_production"]
    active_tts.assert_not_awaited()
    assert enqueued[0].payload["reference_request_options"] == {
        "narration_delivery": "post_production",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_confirmation",
    [0, -1, 9.5, True, "12"],
    ids=["zero", "negative", "fraction", "boolean", "string"],
)
def test_reference_request_options_rejects_invalid_confirmed_duration(invalid_confirmation: object) -> None:
    from server.agent_runtime.sdk_tools.enqueue_videos import _reference_request_options

    with pytest.raises(ValueError, match="confirmed_request_duration_seconds 必须是大于 0 的整数秒档位"):
        _reference_request_options(
            {
                "narration_delivery": "use_tts",
                "confirmed_request_duration_seconds": invalid_confirmation,
            }
        )


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_repeat_without_confirm_still_blocked(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """不带确认参数的重复调用仍不入队。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    await _call(tool_obj, {"script": "episode_1.json"})
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True
    assert enqueued == []


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_exact_enqueues_directly(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """总时长为档位成员时单次调用直接入队，行为与现状一致。"""
    from lib.reference_video.duration_slots import EXACT, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=5, total_seconds=5, adjustment=EXACT)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_skips_unit_without_shots(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """没有 shots 的 unit 不进入确认清单，不阻塞其余合法 unit 直接入队。

    build_specs 本就会跳过没有 shots 的 unit（见 test_build_reference_specs_*）；
    预检若不做同一过滤，会把这个注定不会入队的 unit 纳入确认清单，阻塞整批，
    且申请时长的转述本身失实。
    """
    from lib.reference_video.duration_slots import EXACT, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    script = _reference_video_script()
    script["video_units"].append({"unit_id": "E1U2", "duration_seconds": 5})
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    precheck_calls: list[str] = []

    def fake_precheck(ctx, unit):
        precheck_calls.append(unit["unit_id"])
        return DurationSlot(seconds=5, total_seconds=5, adjustment=EXACT)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert precheck_calls == ["E1U1"]
    assert [s.resource_id for s in enqueued] == ["E1U1"]
    # 没有 shots 的 unit 作为 blocked 逐 ID 报告，不阻塞 E1U1 入队。
    result = _generation_result(out)
    assert result.succeeded == ["E1U1"]
    assert result.blocked == ["E1U2"]


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_resolves_project_context_once(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """批量预检让每个可入队 unit 都经过公共 request projection。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    script = _reference_video_script()
    script["video_units"].append(
        {
            "unit_id": "E1U2",
            "shots": [{"text": "@张三 转身"}],
            "references": [{"type": "character", "name": "张三"}],
            "duration_seconds": 5,
        }
    )
    script["video_units"].append(
        {
            "unit_id": "E1U3",
            "shots": [{"text": "空镜转场"}],
            "references": [],
            "duration_seconds": 5,
        }
    )
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    context_calls: list[Any] = []

    def fake_precheck(_ctx, _unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    monkeypatch.setattr(
        mod,
        "project_reference_unit_request",
        _fake_reference_projection(fake_precheck, calls=context_calls),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    # 三个 unit 均 5 秒、申请 8 秒 → 都需确认，本批不入队；实际水合桶随每个结果可观察。
    assert out.get("is_error") is not True, out
    assert context_calls == ["r2v", "r2v", "i2v"]
    assert enqueued == []


@pytest.mark.integration
async def test_generate_video_episode_reference_skips_duration_context_when_nothing_to_precheck(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """整批都没有可预检的 unit 时不解析项目能力——解析推迟到第一个真正要取档的 unit，
    重构不能让「全部已完成/全部被跳过」的批次凭空多付一轮 DB 往返。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    script = _reference_video_script()
    for unit in script["video_units"]:
        unit["shots"] = []
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    projection_calls: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        return [], []

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(calls=projection_calls))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    await _call(tool_obj, {"script": "episode_1.json"})

    assert projection_calls == []


@pytest.mark.integration
async def test_generate_video_episode_reference_skips_duration_context_when_prompt_blank(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """shots 非空但拼接后提示词全空白时，build_specs 会拒绝该 unit——预检须复用同一份
    结构校验提前判定，不能先触发项目能力解析再让 build_specs 事后跳过。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    script = _reference_video_script()
    for unit in script["video_units"]:
        unit["shots"] = [{"text": "   "}]
    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    projection_calls: list[str] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        return [], []

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(calls=projection_calls))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert projection_calls == []
    assert "E1U1" in out["content"][0]["text"]


@pytest.mark.integration
async def test_generate_video_episode_ad_reference_duration_needs_confirmation(
    ad_reference_ctx: ToolContext, monkeypatch
) -> None:
    """ad 参考直出走同一条 unit 时长确认闸门。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    seen_units: list[dict[str, Any]] = []

    def fake_precheck(ctx, unit):
        seen_units.append(unit)
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None, *, capability=None):
        return None

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(ad_reference_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert enqueued == []
    assert [unit["unit_id"] for unit in seen_units] == ["E1U1"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("make_tool", "extra_args"),
    [
        (generate_video_scene_tool, {"scene_id": "E1U1"}),
        (generate_video_all_tool, {}),
        (generate_video_selected_tool, {"scene_ids": ["E1U1"]}),
    ],
    ids=["scene", "all", "selected"],
)
async def test_generate_video_reference_duration_confirmation_across_entries(
    fake_ctx: ToolContext, monkeypatch, make_tool, extra_args: dict[str, Any]
) -> None:
    """reference 路径的整集与点名入口共用确认闸门：未确认不入队、确认后入队。"""
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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

    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr(mod, "get_active_tasks_for_resources", fake_active_tasks)

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


@pytest.mark.integration
async def test_generate_video_scene_reference_use_tts_exposes_the_shared_cross_tier_quote(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import EXACT, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod
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

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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

    monkeypatch.setattr(mod, "prepare_current_reference_video_request_options", _current_options)
    monkeypatch.setattr(mod, "project_reference_unit_request", _fake_reference_projection(fake_precheck))
    monkeypatch.setattr(mod, "active_tts_resource_ids", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(mod, "get_active_tasks_for_resources", AsyncMock(return_value=[]))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr(
        mod,
        "quote_video_request",
        AsyncMock(return_value=VideoRequestQuote(0.8, "USD", "fake", "fake-r2v", 8)),
    )
    tool_obj = generate_video_scene_tool(fake_ctx)

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


@pytest.mark.unit
async def test_generate_video_scene_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _fake_scene_batch)
    tool_obj = generate_video_scene_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is not True


@pytest.mark.integration
async def test_generate_video_scene_use_tts_returns_structured_blocker_without_enqueuing(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.narration_delivery import (
        USE_TTS,
        NarrationDeliveryPreparation,
        NarrationDeliveryProblem,
        NarrationTtsStatus,
        prepare_narrated_video_duration,
    )
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

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
    monkeypatch.setattr(mod, "active_tts_resource_ids", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(mod, "_prepare_storyboard_delivery_for_item", fake_prepare, raising=False)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        generate_video_scene_tool(fake_ctx),
        {"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": "use_tts"},
    )

    assert out["is_error"] is True
    assert out["request_projection"]["problems"][0]["code"] == "tts_missing"
    enqueue.assert_not_awaited()


@pytest.mark.integration
async def test_generate_video_scene_use_tts_requires_exact_tier_and_queues_only_request_facts(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.narration_delivery import (
        USE_TTS,
        NarrationDeliveryPreparation,
        NarrationTtsStatus,
        VideoRequestCostFacts,
        prepare_narrated_video_duration,
    )
    from server.agent_runtime.sdk_tools import enqueue_videos as mod
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
    monkeypatch.setattr(mod, "active_tts_resource_ids", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(mod, "_prepare_storyboard_delivery_for_item", fake_prepare, raising=False)
    monkeypatch.setattr(
        mod,
        "quote_video_request",
        AsyncMock(return_value=VideoRequestQuote(1.2, "USD", "openai", "sora-2", 12)),
    )
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
    tool_obj = generate_video_scene_tool(fake_ctx)

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


@pytest.mark.integration
async def test_generate_video_scene_use_tts_blocks_when_exact_cost_is_unavailable(
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
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

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
    monkeypatch.setattr(mod, "active_tts_resource_ids", AsyncMock(return_value=frozenset()))
    monkeypatch.setattr(mod, "_prepare_storyboard_delivery_for_item", fake_prepare, raising=False)
    monkeypatch.setattr(mod, "quote_video_request", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    result = await _call(
        generate_video_scene_tool(fake_ctx),
        {"script": "episode_1.json", "scene_id": "E1S01", "narration_delivery": "use_tts"},
    )

    assert result["is_error"] is True
    assert result["request_projection"]["allowed"] is False
    assert [problem["code"] for problem in result["request_projection"]["problems"]] == [
        "reference_duration_confirmation_required",
        "video_request_cost_unavailable",
    ]
    enqueue.assert_not_awaited()


@pytest.mark.unit
async def test_generate_video_scene_accepts_legacy_drama_dialogue(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "drama"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "drama",
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
    out = await _call(generate_video_scene_tool(fake_ctx), {"script": "episode_1.json", "scene_id": "E1S01"})

    assert out.get("is_error") is not True, out


@pytest.mark.unit
async def test_generate_video_scene_accepts_speech_free_legacy_drama(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "drama"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "drama",
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
    out = await _call(generate_video_scene_tool(fake_ctx), {"script": "episode_1.json", "scene_id": "E1S01"})

    assert out.get("is_error") is not True, out


@pytest.mark.unit
async def test_generate_video_scene_accepts_legacy_narration_string_prompt(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.project_payload["content_mode"] = "narration"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
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
    out = await _call(generate_video_scene_tool(fake_ctx), {"script": "episode_1.json", "scene_id": "E1S01"})

    assert out.get("is_error") is not True, out


@pytest.mark.unit
@pytest.mark.parametrize("case", SPEECH_CONTRACT_CASES, ids=lambda case: case.route_id)
async def test_six_route_agent_single_video_generation_returns_structured_admission_without_enqueuing(
    fake_ctx: ToolContext,
    monkeypatch,
    case: SpeechContractCase,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {"content_mode": case.content_mode, "generation_mode": case.generation_mode}
    )
    fake_ctx.pm.script_payload = case.script()  # type: ignore[attr-defined]
    batch_enqueue = AsyncMock()
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", batch_enqueue)

    out = await _call(generate_video_scene_tool(fake_ctx), {"script": "episode_1.json", "scene_id": case.unit_id})

    assert out.get("is_error") is True
    problem = out["speech_admission"]["problems"][0]
    assert out["speech_admission"]["unit_id"] == case.unit_id
    assert problem["code"] == "mixed_speech"
    assert [tuple(location["path"]) for location in problem["locations"]] == list(case.expected_locations)
    assert problem["reason"] == "character_and_narrator_mixed"
    assert problem["action"] == "replan_unit"
    batch_enqueue.assert_not_awaited()


@pytest.mark.unit
async def test_generate_video_scene_missing(fake_ctx: ToolContext) -> None:
    tool_obj = generate_video_scene_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "NO_SUCH"})
    assert out.get("is_error") is True


@pytest.mark.integration
@pytest.mark.parametrize(
    "storyboard_value",
    [
        123,  # 剧本 JSON 里的脏数据（非字符串）须可读失败而非未处理 TypeError
        "/etc/passwd",  # 绝对路径：越权引用项目外文件
        "../../outside.png",  # `..` 穿越出项目目录
    ],
)
async def test_generate_video_scene_rejects_invalid_storyboard_image(
    fake_ctx: ToolContext, storyboard_value: object
) -> None:
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = {"storyboard_image": storyboard_value}  # type: ignore[attr-defined]
    tool_obj = generate_video_scene_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is True
    # 锁定 resolve_storyboard_image_ref 抛出的 canonical 消息，而不是模糊子串或通用失败文本
    assert f"invalid storyboard image path: {storyboard_value!r}" in out["content"][0]["text"]


@pytest.mark.unit
async def test_generate_video_all_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id, task_id="t1", status="succeeded", result={"file_path": "videos/x.mp4"}
            )
            for s in specs
        ]
        return succ, []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = generate_video_all_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True


@pytest.mark.integration
async def test_generate_video_all_preserves_the_selected_manual_upload(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lib.generation_queue_client import TaskSpec
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    project_path = fake_ctx.project_path
    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": 8,
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
    monkeypatch.setattr(mod, "_build_video_specs", lambda **_kwargs: ([spec], {"E1S01": 0}))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(generate_video_all_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    # 选中的手动上传照旧可用：既不进 requested 也不重生，只作为 skipped 报告。
    result = _generation_result(out)
    assert result.requested == []
    assert [entry.unit_id for entry in result.skipped] == ["E1S01"]
    enqueue.assert_not_awaited()


@pytest.mark.unit
async def test_generate_video_all_error(fake_ctx: ToolContext) -> None:
    def boom(*a, **kw):
        raise RuntimeError("broken")

    fake_ctx.pm.load_script = boom  # type: ignore[attr-defined]
    tool_obj = generate_video_all_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_video_selected_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
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
    tool_obj = generate_video_selected_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_ids": ["E1S01"]})
    assert out.get("is_error") is not True


@pytest.mark.unit
async def test_generate_video_selected_no_match(fake_ctx: ToolContext) -> None:
    tool_obj = generate_video_selected_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_ids": ["NO_SUCH"]})
    assert out.get("is_error") is True


@pytest.mark.unit
def test_asset_description_gate_rejects_invalid_description() -> None:
    """空白 / 非字符串描述都拿不到可用 description，由调用方按逐 ID blocked 报告，
    不应抛错（.strip()）或漏到 from_request 而中断整批。"""
    from lib.asset_types import ASSET_SPECS
    from server.agent_runtime.sdk_tools.enqueue_assets import _description_of, asset_unit_id

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


@pytest.mark.unit
def test_asset_requested_ids_resolve_nfd_registered_key() -> None:
    """智能体给的名字与桶 key 形态可以不同：按坐标系解析后落到真实落盘 key 的 unit ID。"""
    import unicodedata

    from lib.asset_types import ASSET_SPECS
    from server.agent_runtime.sdk_tools.enqueue_assets import _requested_unit_ids, asset_unit_id

    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    bucket = ASSET_SPECS["character"].bucket_key
    project = {bucket: {name_nfd: {"description": "存量 NFD 角色"}}}

    assert _requested_unit_ids(project, "character", [name_nfc]) == [asset_unit_id("character", name_nfd)]
    # 同一资产的两种拼写解析到同一个 unit ID，只入一次队。
    assert _requested_unit_ids(project, "character", [name_nfc, name_nfd]) == [asset_unit_id("character", name_nfd)]


@pytest.mark.unit
def test_build_video_specs_does_not_validate_duration_at_enqueue(tmp_path) -> None:
    """duration 是能力维度，入队侧不再校验——任意 duration 都透传给执行层（见 ADR-0001）。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S01.png").write_bytes(b"png")
    items = [
        {
            "segment_id": "S01",
            "video_prompt": "一个奔跑的镜头",
            "duration_seconds": 7,  # 不属于任何典型 supported_durations
            "generated_assets": {"storyboard_image": "storyboards/scene_S01.png"},
        }
    ]
    builder, states = _probe_builder()
    specs, order_map = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        builder=builder,
        states=states,
    )
    assert len(specs) == 1
    assert specs[0].payload["duration_seconds"] == 7

    # 未显式指定 duration 时不携带该键，留给执行层按 caps 收口默认。
    items[0].pop("duration_seconds")
    builder2, states2 = _probe_builder()
    specs2, _ = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        builder=builder2,
        states=states2,
    )
    assert "duration_seconds" not in specs2[0].payload


@pytest.mark.integration
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
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S02.png").write_bytes(b"png")
    items = [
        {
            "segment_id": "S01",
            "video_prompt": "非法引用",
            "generated_assets": {"storyboard_image": storyboard_value},
        },
        {
            "segment_id": "S02",
            "video_prompt": "合法引用",
            "generated_assets": {"storyboard_image": "storyboards/scene_S02.png"},
        },
    ]
    builder, states = _probe_builder()
    specs, order_map = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        builder=builder,
        states=states,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert _blocked_problems(builder) == {"S01": ("generation_unit_input_unusable", "generate_dependency")}


@pytest.mark.integration
def test_build_video_specs_skips_non_dict_generated_assets_without_aborting_batch(tmp_path: Path) -> None:
    """generated_assets 容器本身被外部编辑损坏为非 dict（如 list）时按「没有分镜图」跳过，
    不应让 `.get("storyboard_image")` 在非 dict 上抛未处理 AttributeError 中断整批。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_video_specs

    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_S02.png").write_bytes(b"png")
    items = [
        {"segment_id": "S01", "video_prompt": "脏数据", "generated_assets": ["bad"]},
        {
            "segment_id": "S02",
            "video_prompt": "合法引用",
            "generated_assets": {"storyboard_image": "storyboards/scene_S02.png"},
        },
    ]
    builder, states = _probe_builder()
    specs, order_map = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        builder=builder,
        states=states,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert _blocked_problems(builder) == {"S01": ("generation_unit_input_unusable", "generate_dependency")}


@pytest.mark.integration
async def test_generate_video_scene_generated_assets_non_dict_readable_rejection(fake_ctx: ToolContext) -> None:
    """generated_assets 容器本身非 dict 时须走「没有分镜图」的可读拒绝分支，
    不应在单条路径上抛未处理 AttributeError。"""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = ["bad"]  # type: ignore[attr-defined]
    tool_obj = generate_video_scene_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is True
    assert "没有分镜图" in out["content"][0]["text"]


@pytest.mark.unit
def test_get_video_prompt_drama_sources_dialogue_from_utterances() -> None:
    """drama：_get_video_prompt 从场景级 dialogue-kind utterances 派生 video YAML 台词，
    voiceover-kind 不进；narration / ad（无 utterances 字段）原样渲染既有 video_prompt.dialogue。"""
    import yaml

    from server.agent_runtime.sdk_tools.enqueue_videos import _get_video_prompt

    drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "utterances": [
            {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
            {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
        ],
    }
    parsed = yaml.safe_load(_get_video_prompt(drama_item, content_mode="drama"))
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
    parsed_narr = yaml.safe_load(_get_video_prompt(narration_item, content_mode="narration"))
    assert parsed_narr["Dialogue"] == [{"Speaker": "Alice", "Line": "hello"}]


@pytest.mark.unit
def test_get_video_prompt_injects_voice_profiles_when_characters_given() -> None:
    """drama：传入带非空 voice_style 的角色资产时 YAML 顶部出现 Voice_Profiles；
    voice_characters 缺省（既有调用点行为）不注入。"""
    import yaml

    from server.agent_runtime.sdk_tools.enqueue_videos import _get_video_prompt

    drama_item = {
        "scene_id": "E1S01",
        "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "utterances": [{"kind": "dialogue", "speaker": "王", "text": "你来了。"}],
    }
    characters = {"王": {"voice_style": "低沉沙哑"}}

    parsed = yaml.safe_load(_get_video_prompt(drama_item, content_mode="drama", voice_characters=characters))
    assert parsed["Voice_Profiles"] == [{"Speaker": "王", "Voice_Style": "低沉沙哑"}]

    parsed_default = yaml.safe_load(_get_video_prompt(drama_item, content_mode="drama"))
    assert "Voice_Profiles" not in parsed_default

    parsed_no_style = yaml.safe_load(
        _get_video_prompt(drama_item, content_mode="drama", voice_characters={"王": {"voice_style": ""}})
    )
    assert "Voice_Profiles" not in parsed_no_style


@pytest.mark.unit
def test_get_video_prompt_injects_voice_profiles_from_legacy_dialogue() -> None:
    """utterances 迁移前的存量 drama 剧本（无 utterances 字段，台词仍在
    video_prompt.dialogue）：改走 legacy 出口派生 Voice_Profiles，不因缺 utterances 静默丢失。"""
    import yaml

    from server.agent_runtime.sdk_tools.enqueue_videos import _get_video_prompt

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

    parsed = yaml.safe_load(_get_video_prompt(legacy_drama_item, content_mode="drama", voice_characters=characters))
    assert parsed["Voice_Profiles"] == [{"Speaker": "王", "Voice_Style": "低沉沙哑"}]
    assert parsed["Dialogue"] == [{"Speaker": "王", "Line": "你来了。"}]


@pytest.mark.unit
def test_get_video_prompt_strips_caller_supplied_voice_profiles_for_non_drama() -> None:
    """narration/ad（item 无 utterances 字段）剧本 video_prompt 自带 voice_profiles 时一律剥离：
    该声明段唯一来源是 build_drama_video_prompt 的机械派生，剧本残留值不得越权、绕过 C 类
    （真无声）门控直达 YAML。"""
    import yaml

    from server.agent_runtime.sdk_tools.enqueue_videos import _get_video_prompt

    narration_item = {
        "segment_id": "E1S01",
        "video_prompt": {
            "action": "走",
            "camera_motion": "Static",
            "ambiance_audio": "脚步声",
            "voice_profiles": [{"Speaker": "赝品", "Voice_Style": "越权"}],
        },
    }
    parsed = yaml.safe_load(_get_video_prompt(narration_item, content_mode="narration"))
    assert "Voice_Profiles" not in parsed


@pytest.mark.unit
async def test_resolve_voice_context_skips_non_drama(fake_ctx: ToolContext) -> None:
    """narration/ad：不解析 voice_consistency，直接跳过（无 drama dialogue speaker 概念）。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _resolve_voice_context

    assert await _resolve_voice_context(fake_ctx, "narration") is None


@pytest.mark.unit
async def test_resolve_voice_context_drama_reads_project_characters_and_gate(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """drama：读项目角色资产，无声（C 类真无声、或本集关闭音频）时退回不注入。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    async def fake_not_silent(_project, _episode=None):
        return False

    monkeypatch.setattr(mod, "resolve_project_is_silent", fake_not_silent)
    characters = await mod._resolve_voice_context(fake_ctx, "drama")
    assert characters == fake_ctx.pm.project_payload["characters"]  # type: ignore[attr-defined]

    async def fake_silent(_project, _episode=None):
        return True

    monkeypatch.setattr(mod, "resolve_project_is_silent", fake_silent)
    assert await mod._resolve_voice_context(fake_ctx, "drama") is None


@pytest.mark.unit
def test_build_reference_specs_routes_through_guard(tmp_path) -> None:
    """参考生视频 prompt 只用于统一结构守卫，不冻结进任务 payload。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    # production 的 shots[*].text 由 parse_prompt 产出、已剥离 "Shot N (Xs):" header，
    # fixture 用同样的 header-stripped 形态以贴近真实数据。
    units = [
        {
            "unit_id": "E1U1",
            "shots": [{"text": "@张三 推门"}],
            "references": [{"type": "character", "name": "张三"}],
        }
    ]
    builder, states = _probe_builder()
    specs, order_map = _build_reference_specs(
        units=units, script_filename="episode_1.json", skip_ids=None, builder=builder, states=states
    )
    assert len(specs) == 1
    assert specs[0].task_type == "reference_video"
    assert specs[0].resource_id == "E1U1"
    assert "prompt" not in specs[0].payload
    assert specs[0].payload["script_file"] == "episode_1.json"


@pytest.mark.unit
def test_build_reference_specs_skips_blank_prompt(tmp_path) -> None:
    """shots 存在但文本全空白的 unit 被跳过并告警，不漏到执行层（结构校验上移到守卫点）。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        {"unit_id": "E1U1", "shots": [{"text": "   "}, {"text": ""}]},
        {"unit_id": "E1U2", "shots": [{"text": "@李四 转身"}]},
    ]
    builder, states = _probe_builder()
    specs, order_map = _build_reference_specs(
        units=units, script_filename="episode_1.json", skip_ids=None, builder=builder, states=states
    )
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert _blocked_problems(builder) == {"E1U1": ("generation_unit_request_invalid", "fix_input")}


@pytest.mark.unit
def test_build_reference_specs_skips_mixed_speech_without_aborting_batch(tmp_path) -> None:
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        {
            "unit_id": "E1U1",
            "shots": [{"text": "@[张三]：{快走。}\n{风吹过旷野。}"}],
            "references": [],
        },
        {"unit_id": "E1U2", "shots": [{"text": "@李四 转身"}], "references": []},
    ]
    builder, states = _probe_builder()

    specs, order_map = _build_reference_specs(
        units=units,
        script_filename="episode_1.json",
        skip_ids=None,
        builder=builder,
        states=states,
    )

    assert [spec.resource_id for spec in specs] == ["E1U2"]
    assert order_map == {"E1U2": 1}
    # 发声准入的问题码原样透出，调用方不必读文本判断下一步。
    assert _blocked_problems(builder) == {"E1U1": ("mixed_speech", "replan_unit")}


@pytest.mark.unit
def test_build_reference_specs_skips_bad_unit_id_without_aborting_batch(tmp_path) -> None:
    """unit_id 为空或键缺失（Agent 裸写 JSON 可致）都跳过该 unit 而非中断整批：
    空串经 from_request 抛 ValueError 被捕获，缺键经 .get 归一化为空串后同样被拒。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        {"unit_id": "", "shots": [{"text": "@张三 推门"}]},  # 空串
        {"shots": [{"text": "@王五 起身"}]},  # 缺 unit_id 键 → 不应抛 KeyError
        {"unit_id": "E1U2", "shots": [{"text": "@李四 转身"}]},
    ]
    builder, states = _probe_builder()
    specs, _ = _build_reference_specs(
        units=units, script_filename="episode_1.json", skip_ids=None, builder=builder, states=states
    )
    assert [s.resource_id for s in specs] == ["E1U2"]


@pytest.mark.unit
def test_build_reference_specs_handles_malformed_shots(tmp_path) -> None:
    """畸形 shots（显式 null text / 非 dict 元素）不应崩溃整批，且不得把 'None' 注入 prompt。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        # text 显式 null + 一个非 dict 元素 → 拼接后为空 → 被守卫点判空跳过（不注入 'None'）。
        {"unit_id": "E1U1", "shots": [{"text": None}, "garbage"]},
        {"unit_id": "E1U2", "shots": [{"text": "@李四 转身"}]},
    ]
    builder, states = _probe_builder()
    specs, _ = _build_reference_specs(
        units=units, script_filename="episode_1.json", skip_ids=None, builder=builder, states=states
    )
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert all("None" not in (s.payload.get("prompt") or "") for s in specs)


# ---------------------------------------------------------------------------
# text_generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_video_capabilities_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project, _episode=None):
        return {"provider_id": "fake", "supported_durations": [4, 6, 8]}

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is not True
    assert json.loads(out["content"][0]["text"])["provider_id"] == "fake"


@pytest.mark.unit
async def test_get_video_capabilities_resolves_by_project(fake_ctx: ToolContext, monkeypatch) -> None:
    """能力按项目路线解析：工具不收集号，多余的集号入参被忽略、不改变解析口径。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    seen: list[str] = []

    async def fake_resolve(project_name):
        seen.append(project_name)
        return {"provider_id": "fake", "supported_durations": [4, 6, 8]}

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    assert (await _call(tool_obj, {})).get("is_error") is not True
    assert (await _call(tool_obj, {"episode": 3})).get("is_error") is not True
    assert seen == [fake_ctx.project_name, fake_ctx.project_name]


@pytest.mark.unit
async def test_get_video_capabilities_annotates_reference_unit_tiers(fake_ctx: ToolContext, monkeypatch) -> None:
    """参考路径项目另返回两套逐 unit 生效档位，供手工改 step1 时与生成侧对同一份数字。"""
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "generation_mode": "reference_video",
        }

    fake_ctx.pm.project_payload["model_settings"] = {  # type: ignore[attr-defined]
        "gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}
    }
    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)
    out = await _call(get_video_capabilities_tool(fake_ctx), {})
    assert out.get("is_error") is not True, out
    payload = json.loads(out["content"][0]["text"])
    assert payload["reference_unit_durations"] == {"with_references": [8], "without_references": [4, 6, 8]}
    # 全集原样保留：它是型号声明，不是生效档位
    assert payload["supported_durations"] == [4, 6, 8]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("generation_mode", "content_mode"),
    [("storyboard", "drama"), ("reference_video", "ad")],
)
async def test_get_video_capabilities_skips_tiers_off_episode_reference_path(
    fake_ctx: ToolContext, monkeypatch, generation_mode: str, content_mode: str
) -> None:
    """非剧集参考路径不补该字段：其它路径没有逐 unit 引用状态，ad 镜头时长也不受档位枚举管辖。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "generation_mode": generation_mode,
            "content_mode": content_mode,
        }

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    out = await _call(get_video_capabilities_tool(fake_ctx), {})
    assert "reference_unit_durations" not in json.loads(out["content"][0]["text"])


@pytest.mark.unit
async def test_get_video_capabilities_shares_rest_resolution_entry(fake_ctx: ToolContext, monkeypatch) -> None:
    """agent 工具与 REST 能力查询走同一个解析入口 ``ConfigResolver.video_capabilities``。

    两侧各自解析会让 agent 写剧本时看到的时长 / 参考图上限与界面显示的不是同一个模型。
    """
    from lib.config.resolver import ConfigResolver

    seen: list[str] = []

    async def fake_video_capabilities(_self, project_name=None, episode=None):
        seen.append(project_name)
        return {"provider_id": "kling", "model": "kling-v3-omni", "supported_durations": [5]}

    monkeypatch.setattr(ConfigResolver, "video_capabilities", fake_video_capabilities)
    out = await _call(get_video_capabilities_tool(fake_ctx), {})
    assert out.get("is_error") is not True, out
    assert json.loads(out["content"][0]["text"])["model"] == "kling-v3-omni"
    assert seen == [fake_ctx.project_name]


@pytest.mark.unit
async def test_get_video_capabilities_error(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project, _episode=None):
        raise FileNotFoundError("missing project.json")

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_episode_script_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_segments.json").write_text("step1 content", encoding="utf-8")
    (project_path / "project.json").write_text(json.dumps({"content_mode": "narration"}), encoding="utf-8")

    class _FakeGenerator:
        def __init__(self, _path):
            pass

        async def build_prompt(self, _episode, *, instructions=None):
            return "fake prompt"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "fake prompt" in out["content"][0]["text"]


@pytest.mark.unit
async def test_generate_episode_script_missing_step1(fake_ctx: ToolContext) -> None:
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 99})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_episode_script_writes_to_default_project_scripts(fake_ctx: ToolContext, monkeypatch) -> None:
    """output 参数已下线；写出路径必须由 ScriptGenerator 内部决定，handler 不应让 agent 控制。"""
    from lib import script_review
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    step1 = drafts / "step1_segments.json"
    step1.write_text("step1", encoding="utf-8")
    # step1→step2 审核 gate：须先确认才放行生成，否则 handler 早返 gate 阻塞而非调 ScriptGenerator。
    # 把已存确认指纹对齐当前 step1 内容指纹，模拟「用户已在 Web 确认」。
    fingerprint = script_review.content_fingerprint(step1)
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "episodes": [{"episode": 1, "step1_review": {"fingerprint": fingerprint, "confirmed_at": "t"}}],
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, dict[str, Any]] = {"calls": {}}

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path):
            return cls()

        async def generate(self, **kwargs) -> Path:
            captured["calls"] = kwargs
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)

    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True
    # handler 不再传 output_path —— ScriptGenerator 自己决定写到哪里
    assert "output_path" not in captured["calls"]


@pytest.mark.unit
async def test_generate_episode_script_ad_skips_step1(fake_ctx: ToolContext, monkeypatch) -> None:
    """ad 一键生成不依赖 step1 中间文件：缺 drafts/ 也不报 step1 错误。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps({"content_mode": "ad", "target_duration": 30}), encoding="utf-8"
    )

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path):
            return cls()

        async def generate(self, **_kwargs) -> Path:
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True


@pytest.mark.unit
def test_parse_normalized_content_uses_dynamic_duration_schema() -> None:
    """_parse_normalized_content 复用按 supported_durations 构造的动态 schema：合法 duration 经模型
    校验并补全默认字段；超出枚举的 duration 触发 fail-loud（抛 ValueError），而非被静态模型(ge=1,le=60)
    静默放行、也不降级保留未校验内容写盘。"""
    from lib.script_models import build_drama_normalized_script_model

    model = build_drama_normalized_script_model([4, 6, 8])
    base_scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "characters_in_scene": ["林清"],
        "scene_description": "林清立于窗前。",
    }

    valid = _parse_normalized_content(json.dumps({"title": "t", "scenes": [base_scene]}), model)
    # 合法 duration → 模型校验通过，补全 DramaSceneContent 默认字段（source_text 默认空串）
    assert valid["scenes"][0]["duration_seconds"] == 8
    assert valid["scenes"][0]["source_text"] == ""

    bad = {**base_scene, "duration_seconds": 5}  # 5 不在 supported_durations
    # 超出枚举 → 动态 schema 校验失败 → fail-loud 抛 ValueError，不把未校验内容当成正式 step1 落盘
    with pytest.raises(ValueError, match="step1 规范化内容结构校验失败"):
        _parse_normalized_content(json.dumps({"title": "t", "scenes": [bad]}), model)


@pytest.mark.unit
async def test_fetch_caps_with_fallback_uses_write_layer_default(monkeypatch) -> None:
    """resolver 失败时软回退须与自定义供应商写入层的保守默认（duration_presets.DEFAULT_FALLBACK）
    同一真相源——独立维护第二套回退集会让 LLM 拿到供应商未必支持的时长。"""
    from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def raising_caps(_p, *, episode=None, generation_mode=None):
        raise ValueError("no provider configured")

    monkeypatch.setattr(mod, "fetch_video_caps", raising_caps)
    default, durations = await mod._fetch_caps_with_fallback({}, 1)
    assert default is None
    assert durations == DEFAULT_FALLBACK


@pytest.mark.unit
async def test_fetch_caps_with_fallback_drops_out_of_range_default(monkeypatch) -> None:
    """收窄后落在集合外的已保存 default_duration 归 None（回到 auto 档），不拖垮整个工具。

    ``build_normalize_prompt`` 对非成员 default 是 fail-loud 的：用户在 720p 下存过 4 秒、
    改到 1080p 后 Veo 收窄为 [8]，不归 None 会让 normalize_drama_script 直接抛 ValueError。
    """
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _narrowed_caps(_p, *, episode=None, generation_mode=None):
        return 4, [8]

    monkeypatch.setattr(mod, "fetch_video_caps", _narrowed_caps)
    default, durations = await mod._fetch_caps_with_fallback({}, 1)
    assert default is None
    assert durations == [8]

    async def _in_range_caps(_p, *, episode=None, generation_mode=None):
        return 8, [4, 6, 8]

    monkeypatch.setattr(mod, "fetch_video_caps", _in_range_caps)
    default, durations = await mod._fetch_caps_with_fallback({}, 1)
    assert default == 8
    assert durations == [4, 6, 8]


@pytest.mark.unit
async def test_fetch_video_caps_narrows_durations_by_constraints(monkeypatch) -> None:
    """交给 LLM 的时长集合已按项目分辨率经联动约束收窄。

    Veo 项目保存 1080p 时只接受 8 秒；不收窄的话 drama / narration 拆分会产出 4/6 秒镜头，
    视频入队时才被 backend 拒。
    """
    from server.agent_runtime.sdk_tools import _context as ctx_mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "default_duration": 4,
        }

    monkeypatch.setattr(ctx_mod, "resolve_video_caps", _fake_caps)

    project_1080p = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "1080p"}}}
    default, durations = await ctx_mod.fetch_video_caps(project_1080p)
    assert durations == [8]
    # default_duration 原样返回（用户配置值），成员性由调用方按各自口径判定
    assert default == 4

    # 未配置分辨率：普通路径省略 resolution 参数，供应商按自己的默认档位（Veo 720p）接受 4/6/8，
    # 故不施加分辨率约束——按 provider 兜底档位收窄会凭空把剧本节奏锁死 8 秒。
    _default, durations = await ctx_mod.fetch_video_caps({})
    assert durations == [4, 6, 8]

    # 项目显式选了无声明的分辨率时不收窄。
    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}}
    _default, durations = await ctx_mod.fetch_video_caps(project)
    assert durations == [4, 6, 8]

    # 参考图路径：即便分辨率无声明也收窄
    _default, durations = await ctx_mod.fetch_video_caps(project, generation_mode="reference_video")
    assert durations == [8]


@pytest.mark.unit
async def test_normalize_drama_script_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "DRY RUN" in out["content"][0]["text"]


@pytest.mark.parametrize(
    ("tool_factory", "content_mode", "generation_mode"),
    [
        (normalize_drama_script_tool, "narration", "storyboard"),
        (split_narration_segments_tool, "drama", "storyboard"),
        (split_reference_video_units_tool, "drama", "storyboard"),
    ],
    ids=("normalize", "narration-split", "reference-split"),
)
@pytest.mark.unit
async def test_step1_tools_reject_incompatible_project_axes_before_capability_lookup(
    fake_ctx: ToolContext,
    monkeypatch,
    tool_factory,
    content_mode: str,
    generation_mode: str,
) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        content_mode=content_mode,
        generation_mode=generation_mode,
    )
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("从前有座山", encoding="utf-8")

    async def unexpected_caps(*_args, **_kwargs):
        pytest.fail("incompatible step1 tool must reject before capability lookup")

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", unexpected_caps)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", unexpected_caps)

    result = await _call(tool_factory(fake_ctx), {"episode": 1, "dry_run": True})

    assert result.get("is_error") is True
    message = result["content"][0]["text"]
    assert content_mode in message
    assert generation_mode in message


@pytest.mark.unit
async def test_normalize_drama_script_projects_durable_inputs_once(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib import artifact_provenance
    from server.agent_runtime.sdk_tools import text_generation as mod

    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("从前有座山", encoding="utf-8")
    calls = 0
    original = artifact_provenance.project_step1_prompt_inputs

    def counted_projection(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(artifact_provenance, "project_step1_prompt_inputs", counted_projection)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)

    result = await _call(normalize_drama_script_tool(fake_ctx), {"episode": 1, "dry_run": True})

    assert result.get("is_error") is not True, result
    assert calls == 1


@pytest.mark.unit
async def test_normalize_drama_script_wires_target_language(fake_ctx: ToolContext, monkeypatch) -> None:
    """normalize 把项目 source_language 透传为 build_normalize_prompt 的 target_language——
    非中文项目的 step1 输出语言据此切换，而非恒退默认中文。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    # 工具经 ctx.pm.load_project 取项目；source_language 是输出语言的唯一真相源
    fake_ctx.pm.project_payload["source_language"] = "English"  # type: ignore[attr-defined]
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("once upon a time", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "English" in out["content"][0]["text"]


@pytest.mark.unit
async def test_normalize_drama_script_rejects_empty_scenes(fake_ctx: ToolContext, monkeypatch) -> None:
    """normalize 产出空 scenes → 工具报错，不把空 step1 当成功产物写盘（与 _load_drama_step1_content 同口径）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    class _EmptyGenerator:
        async def generate(self, _request, project_name=None):
            class _R:
                text = json.dumps({"title": "第一集", "scenes": []}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None):
        return _EmptyGenerator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    # 空 scenes 不写盘，避免生成阶段才必然失败
    assert not (project_path / "drafts" / "episode_1" / "step1_normalized_script.json").exists()


@pytest.mark.unit
async def test_normalize_drama_script_injects_episode_into_prompt(fake_ctx: ToolContext, monkeypatch) -> None:
    """工具必须把 episode 注入 build_normalize_prompt，避免 LLM 写错 E\\d+ 前缀。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter2.txt").write_text("第二集开场", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 2, "dry_run": True, "source": "source/chapter2.txt"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "E2S01" in prompt_text
    assert "第 2 集" in prompt_text or "E2S{两位序号}" in prompt_text
    assert "E1S01" not in prompt_text


@pytest.mark.unit
async def test_normalize_drama_script_injects_episode_outline(fake_ctx: ToolContext, monkeypatch) -> None:
    """内容抽取前移后，分集大纲（故事节点 / 钩子）随 step1 注入 normalize prompt（见 ADR 0041）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")
    fake_ctx.pm.project_payload["episodes"] = [  # type: ignore[attr-defined]
        {
            "episode": 1,
            "title": "初入江湖",
            "hook": "少年坠崖生死未卜",
            "outline": {"story_beats": ["少年下山"], "next_episode_teaser": None},
        }
    ]

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "少年下山" in prompt_text
    assert "少年坠崖生死未卜" in prompt_text


@pytest.mark.unit
async def test_normalize_drama_script_passes_project_name_to_backend(fake_ctx: ToolContext, monkeypatch) -> None:
    """工具必须把 ctx.project_name 传给 TextGenerator.create/generate，
    否则项目级文本档位覆盖被跳过，且 usage tracking 会丢 project_name。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            captured["generate_project_name"] = project_name

            class _R:
                # step1 现在产出结构化 JSON（DramaNormalizedScript），非 markdown 表
                text = json.dumps(
                    {
                        "title": "第一集",
                        "scenes": [
                            {
                                "scene_id": "E1S01",
                                "duration_seconds": 4,
                                "segment_break": False,
                                "characters_in_scene": [],
                                "scenes": [],
                                "props": [],
                                "scene_description": "山中清晨",
                                "utterances": [],
                                "source_text": "从前有座山",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return _R()

    async def fake_create(task_type, project_name=None):
        captured["task_type"] = task_type
        captured["create_project_name"] = project_name
        return _FakeGenerator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})

    assert out.get("is_error") is not True, out
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo", (
        f"normalize_drama_script 必须向 TextGenerator.create 传入 project_name，"
        f"实际传入: {captured.get('create_project_name')!r}"
    )
    assert captured["generate_project_name"] == "demo", (
        f"normalize_drama_script 必须向 TextGenerator.generate 传入 project_name，"
        f"实际传入: {captured.get('generate_project_name')!r}"
    )


@pytest.mark.unit
async def test_normalize_drama_script_registers_the_frozen_explicit_source_basis(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifact_provenance import build_step1_basis
    from server.agent_runtime.sdk_tools import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,  # type: ignore[attr-defined]
        "schema_version": 8,
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
    }
    fake_ctx.pm.project_payload = project  # type: ignore[attr-defined]
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_path = fake_ctx.project_path / "source" / "selected.txt"
    source_path.parent.mkdir(parents=True)
    frozen_source = "被显式选中的生成原文"
    source_path.write_text(frozen_source, encoding="utf-8")
    expected = build_step1_basis(frozen_source, episode=1, project=project)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    class _Generator:
        async def generate(self, _request, project_name=None):
            source_path.write_text("等待供应商期间改过的原文", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest  # type: ignore[attr-defined]
            project_file.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {
                            "title": "第一集",
                            "scenes": [
                                {
                                    "scene_id": "E1S01",
                                    "duration_seconds": 4,
                                    "segment_break": False,
                                    "characters_in_scene": [],
                                    "scenes": [],
                                    "props": [],
                                    "scene_description": "山中清晨",
                                    "utterances": [],
                                    "source_text": frozen_source,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(
        normalize_drama_script_tool(fake_ctx),
        {"episode": 1, "source": "source/selected.txt"},
    )

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_step1(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


@pytest.mark.unit
async def test_normalize_drama_script_preserves_legacy_request_basis_when_manifest_activates(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifact_provenance import build_step1_basis
    from server.agent_runtime.sdk_tools import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,  # type: ignore[attr-defined]
        "schema_version": 7,
        "title": "项目",
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
        "overview": {},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }
    fake_ctx.pm.project_payload = project  # type: ignore[attr-defined]
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    selected_source = source_dir / "selected.txt"
    selected_source.write_text("实际发送给供应商的原文", encoding="utf-8")
    (source_dir / "episode_1.txt").write_text("激活器可重建的另一份原文", encoding="utf-8")
    expected = build_step1_basis("实际发送给供应商的原文", episode=1, project=project)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    class _Generator:
        async def generate(self, _request, project_name=None):
            activated = {**project, "schema_version": 8}
            fake_ctx.pm.project_payload = activated  # type: ignore[attr-defined]
            project_file.write_text(json.dumps(activated, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {
                            "title": "第一集",
                            "scenes": [
                                {
                                    "scene_id": "E1S01",
                                    "duration_seconds": 4,
                                    "segment_break": False,
                                    "characters_in_scene": [],
                                    "scenes": [],
                                    "props": [],
                                    "scene_description": "山中清晨",
                                    "utterances": [],
                                    "source_text": "实际发送给供应商的原文",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(
        normalize_drama_script_tool(fake_ctx),
        {"episode": 1, "source": "source/selected.txt"},
    )

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_step1(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


@pytest.mark.unit
async def test_normalize_drama_script_marks_mixed_machine_candidate_before_review(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    source_dir = project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            class _Result:
                text = json.dumps(
                    {
                        "title": "第一集",
                        "scenes": [
                            {
                                "scene_id": "E1S01",
                                "duration_seconds": 4,
                                "segment_break": False,
                                "characters_in_scene": ["阿离"],
                                "scenes": [],
                                "props": [],
                                "scene_description": "阿离站在山门前。",
                                "utterances": [
                                    {"kind": "dialogue", "speaker": "阿离", "text": "我回来了。"},
                                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                                ],
                                "source_text": "三年后，阿离回到山门。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return _Result()

    async def fake_create(_task_type, project_name=None):
        return _FakeGenerator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(normalize_drama_script_tool(fake_ctx), {"episode": 1})

    assert result.get("is_error") is not True, result
    saved = json.loads(
        (project_path / "drafts" / "episode_1" / "step1_normalized_script.json").read_text(encoding="utf-8")
    )
    assert saved["scenes"][0]["needs_replan"] is True
    assert [utterance["text"] for utterance in saved["scenes"][0]["utterances"]] == ["我回来了。", "三年后。"]


@pytest.mark.unit
async def test_normalize_drama_script_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# _build_prompt：Style 去重 + 「画风：」前缀清理
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    @pytest.mark.unit
    def test_structured_no_duplicate_style(self) -> None:
        from server.agent_runtime.sdk_tools.enqueue_storyboards import _build_prompt

        segment = {
            "segment_id": "E1S01",
            "image_prompt": {
                "scene": "村口黄昏",
                "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
            },
        }
        out = _build_prompt(segment, "画风：真人电视剧风格", "Soft light", "segment_id")

        # Style 只出现一次（YAML 内），不再有前缀 "Style: ..." 行重复注入
        assert out.count("Style:") == 1
        # 「画风：」前缀被清理，不会渲染成 "Style: 画风：..."
        assert "画风：" not in out
        assert "Style: 真人电视剧风格" in out
        # style_description 仍以 Visual style 前缀注入
        assert out.startswith("Visual style: Soft light")

    @pytest.mark.unit
    def test_unstructured_keeps_style_prefix_normalized(self) -> None:
        from server.agent_runtime.sdk_tools.enqueue_storyboards import _build_prompt

        segment = {"segment_id": "E1S02", "image_prompt": "村口黄昏的长镜头"}
        out = _build_prompt(segment, "画风：真人电视剧风格", "", "segment_id")

        # 非结构化纯字符串 prompt 不含 Style，前缀补上且去掉「画风：」
        assert out.count("Style:") == 1
        assert "画风：" not in out
        assert out.startswith("Style: 真人电视剧风格")
        assert "\n\n村口黄昏的长镜头\n\n" in out
        assert out.endswith("画面避免：水印、多余文字、Logo。")


# ---------------------------------------------------------------------------
# episode_planning — plan_episodes 薄包装
# ---------------------------------------------------------------------------


def _fake_planner_cls(result: Any, captured: dict[str, Any] | None = None):
    """构造可注入的 EpisodePlanner 替身：create() 工厂 + plan() 返回预置结果。"""

    class _FakePlanner:
        def __init__(self) -> None:
            pass

        @classmethod
        async def create(cls, project_path):
            if captured is not None:
                captured["project_path"] = project_path
            return cls()

        async def plan(self, instructions=None):
            if captured is not None:
                captured["plan_instructions"] = instructions
            if isinstance(result, BaseException):
                raise result
            return result

    return _FakePlanner


@pytest.mark.unit
async def test_plan_episodes_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(
                episode=1, title="古玉藏诀", hook="剑诀来历成谜", reading_units=812, ledger_status="planned"
            ),
            EpisodePlanSummary(
                episode=2, title="城门遇袭", hook="少女是谁", reading_units=903, ledger_status="planned"
            ),
        ],
        cursor={"source_file": "source/novel.txt", "offset": 1715},
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "古玉藏诀" in text and "剑诀来历成谜" in text and "812" in text
    assert "城门遇袭" in text
    assert captured["project_path"] == fake_ctx.project_path
    assert captured["plan_instructions"] is None  # 不传时透传 None


@pytest.mark.unit
async def test_plan_episodes_forwards_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """用户分集偏好经 instructions 透传给 EpisodePlanner.plan（strip 后非空）。"""
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=1, title="第一章", hook="悬念", reading_units=800, ledger_status="planned")
        ],
        cursor=None,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": "  按章节对齐切分  "})

    assert out.get("is_error") is not True
    assert captured["plan_instructions"] == "按章节对齐切分"


@pytest.mark.unit
async def test_plan_episodes_blank_instructions_treated_as_none(fake_ctx: ToolContext, monkeypatch) -> None:
    """纯空白 instructions 视同未传：透传 None，与不传逐字一致。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None, source_exhausted=True), captured)
    )
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": "   \n "})

    assert out.get("is_error") is not True
    assert captured["plan_instructions"] is None


@pytest.mark.unit
async def test_plan_episodes_rejects_non_string_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 传非字符串（如数组）按参数错误上报，不静默吞掉。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None)))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": ["按章切"]})

    assert out.get("is_error") is True
    assert "instructions" in out["content"][0]["text"]


@pytest.mark.unit
async def test_plan_episodes_rejects_overlong_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 超长按参数错误提前拒绝，不注入 prompt。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None)))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": "章" * (mod.MAX_INSTRUCTIONS_LEN + 1)})

    assert out.get("is_error") is True
    assert "过长" in out["content"][0]["text"]


@pytest.mark.unit
async def test_plan_episodes_accepts_boundary_length_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 恰好等于上限长度应被接受（覆盖 > 比较的差一边界）。"""
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=1, title="第一章", hook="悬念", reading_units=800, ledger_status="planned")
        ],
        cursor=None,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    text = "章" * mod.MAX_INSTRUCTIONS_LEN
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": text})

    assert out.get("is_error") is not True
    assert captured["plan_instructions"] == text


@pytest.mark.unit
async def test_plan_episodes_planner_value_error_not_mislabeled_as_param_error(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """规划器内部抛出的 ValueError（如供应商未配置）走通用工具错误，不被误标为参数错误。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(ValueError("未找到可用的 text 供应商")))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "未找到可用的 text 供应商" in text
    assert "参数错误" not in text  # 供应商未配置不是入参问题


@pytest.mark.unit
async def test_plan_episodes_source_exhausted(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    result = PlanResult(episodes=[], cursor=None, source_exhausted=True)
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    assert "全部规划" in out["content"][0]["text"]


@pytest.mark.unit
async def test_plan_episodes_source_exhausted_includes_ledger_stats(fake_ctx: ToolContext, monkeypatch) -> None:
    """再次调用无新内容（早退路径）：附全局核对材料供主 agent 核对结构性偏好。"""
    from lib.episode_planner import LedgerStats, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    stats = LedgerStats(total_episodes=30, smallest=[(30, 57), (12, 640)], median_units=812, target_units=800)
    result = PlanResult(episodes=[], cursor=None, source_exhausted=True, ledger_stats=stats)
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "累计总集数：30" in text
    assert "第 30 集（约 57）" in text
    assert "第 12 集（约 640）" in text
    assert "中位数：约 812" in text
    assert "目标体量设置：约 800" in text
    assert "有偏差须向用户明确说明" in text


@pytest.mark.unit
async def test_plan_episodes_normal_batch_reports_total_planned_line_only(fake_ctx: ToolContext, monkeypatch) -> None:
    """常规（非耗尽）批次没有 ledger_stats：只附「累计已规划 N 集」一行，不带全局核对材料。"""
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=5, title="第五集", hook="悬念", reading_units=800, ledger_status="planned")
        ],
        cursor={"source_file": "source/novel.txt", "offset": 4000},
        source_exhausted=False,
        total_planned=5,
        ledger_stats=None,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "累计已规划 5 集。" in text
    assert "累计总集数" not in text  # 不附全局核对材料
    assert "体量最小的几集" not in text


@pytest.mark.unit
async def test_plan_episodes_error_envelope(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import EpisodePlanningError
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(EpisodePlanningError("校验耗尽")))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is True
    assert "校验耗尽" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# episode_planning — reset_episode_planning 薄包装
# ---------------------------------------------------------------------------


def _fake_reset(result: Any, captured: dict[str, Any] | None = None):
    def _reset(project_path, *, from_episode, confirm_consumed):
        if captured is not None:
            captured["args"] = (project_path, from_episode, confirm_consumed)
        if isinstance(result, BaseException):
            raise result
        return result

    return _reset


@pytest.mark.unit
async def test_reset_episode_planning_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_reset import EpisodeResetResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = EpisodeResetResult(
        removed_episodes=[1, 2],
        deleted_files=["source/episode_1.txt"],
        archived_files=[("source/episode_2.txt", "source/_episode_2.txt.bak")],
        consumed_episodes=[],
    )
    monkeypatch.setattr(mod, "reset_episode_planning", _fake_reset(result, captured))

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 1})

    assert out.get("is_error") is not True
    assert captured["args"][1:] == (1, False)
    text = out["content"][0]["text"]
    assert "清空 2 集" in text
    assert "source/_episode_2.txt.bak" in text
    assert "plan_episodes" in text  # 指路后续动作


@pytest.mark.unit
async def test_reset_episode_planning_confirmation_required(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_reset import ResetConfirmationRequired
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(
        mod,
        "reset_episode_planning",
        _fake_reset(ResetConfirmationRequired(consumed_episodes=[1, 3], archived_files=[])),
    )
    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 1})

    assert out.get("is_error") is not True  # 预期内的流程出口，不是错误
    text = out["content"][0]["text"]
    assert "已消费" in text and "confirm_consumed" in text


@pytest.mark.unit
async def test_reset_episode_planning_forwards_confirm(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_reset import EpisodeResetResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = EpisodeResetResult(removed_episodes=[1], deleted_files=[], archived_files=[], consumed_episodes=[1])
    monkeypatch.setattr(mod, "reset_episode_planning", _fake_reset(result, captured))

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 1, "confirm_consumed": True})

    assert captured["args"][1:] == (1, True)
    assert "未删除" in out["content"][0]["text"]  # 产物保留须对主 agent 说明


@pytest.mark.unit
async def test_reset_episode_planning_partial_reset_error(fake_ctx: ToolContext, monkeypatch) -> None:
    """部分重置前置校验未通过（如源文指纹不一致）按可读错误返回，不走通用异常兜底。"""
    from lib.episode_reset import EpisodeResetError
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(
        mod, "reset_episode_planning", _fake_reset(EpisodeResetError("源文件已被修改或移除：source/novel.txt"))
    )
    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 3})

    assert out.get("is_error") is True
    assert "源文件已被修改或移除" in out["content"][0]["text"]


@pytest.mark.unit
async def test_reset_episode_planning_partial_reset_success_message(fake_ctx: ToolContext, monkeypatch) -> None:
    """部分重置成功时的摘要区分于全量重置：报清空范围与新起点，而非「账本已空」。"""
    from lib.episode_reset import EpisodeResetResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    result = EpisodeResetResult(
        removed_episodes=[2, 3], deleted_files=["source/episode_2.txt"], archived_files=[], consumed_episodes=[]
    )
    monkeypatch.setattr(mod, "reset_episode_planning", _fake_reset(result))

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": 2})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "部分重置" in text
    assert "第 2 集起共 2 集" in text
    assert "第 1 集原文范围末尾" in text
    assert "新集号从第 2 集起" in text
    assert "账本已空" not in text


@pytest.mark.unit
async def test_reset_episode_planning_rejects_string_confirm_consumed(fake_ctx: ToolContext) -> None:
    """confirm_consumed 是确认安全边界：非布尔值必须拒绝而非真值化。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(
        mod.reset_episode_planning_tool(fake_ctx),
        {"from_episode": 1, "confirm_consumed": "true"},
    )
    assert out.get("is_error") is True
    assert "confirm_consumed" in out["content"][0]["text"]


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1, "1", True, None])
async def test_reset_episode_planning_rejects_bad_from_episode(fake_ctx: ToolContext, bad: Any) -> None:
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {"from_episode": bad})
    assert out.get("is_error") is True
    assert "from_episode" in out["content"][0]["text"]


@pytest.mark.unit
async def test_reset_episode_planning_requires_from_episode(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(mod.reset_episode_planning_tool(fake_ctx), {})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# enqueue_videos — ad + reference_video（统一 video_units）
# ---------------------------------------------------------------------------


def _ad_reference_unit(**overrides: Any) -> dict[str, Any]:
    unit: dict[str, Any] = {
        "unit_id": "E1U1",
        "duration_seconds": 5,
        "shots": [{"text": "镜头1：@[保温杯] 置于桌面"}],
        "references": [{"type": "product", "name": "保温杯"}],
        "generated_assets": {},
    }
    unit.update(overrides)
    return unit


@pytest.fixture
def ad_reference_ctx(fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> ToolContext:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    pm = fake_ctx.pm
    pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "style": "明亮写实",
            "products": {"保温杯": {"description": "主推产品"}},
            "episodes": [{"episode": 1, "title": "短片", "script_file": "scripts/episode_1.json"}],
        }
    )
    pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "ad",
        "episode": 1,
        "title": "短片",
        "video_units": [_ad_reference_unit()],
    }

    async def _fake_no_active_tasks(**_kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def _allow_audio_switch(_project: dict[str, Any], _capability: str) -> None:
        return None

    monkeypatch.setattr(mod, "get_active_tasks_for_resources", _fake_no_active_tasks)
    monkeypatch.setattr(mod, "assert_audio_switch_supported", _allow_audio_switch)
    return fake_ctx


def _successful_reference_batch(ctx: ToolContext, enqueued: list[Any]):
    async def fake_batch(*, project_name: str, specs: list[Any], on_success=None, on_failure=None):
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


@pytest.mark.integration
async def test_generate_video_episode_reference_skips_malformed_unit_entries(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """脏 unit 元素交给逐条校验跳过，不在完成扫描、音频闸门或时长预检中断整批。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    valid = ad_reference_ctx.pm.script_payload["video_units"][0]  # type: ignore[attr-defined]
    ad_reference_ctx.pm.script_payload["video_units"] = ["bad", {}, valid]  # type: ignore[attr-defined]
    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        generate_video_episode_tool(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert [spec.resource_id for spec in enqueued] == ["E1U1"]
    # 脏 unit 逐条记为 blocked（没有 unit_id 可寻址时按位置编号），合法 unit 照常入队。
    result = _generation_result(out)
    assert result.succeeded == ["E1U1"]
    assert sorted(result.blocked) == ["unit_0", "unit_1"]


@pytest.mark.integration
async def test_generate_video_episode_ad_reference_enqueues_existing_video_units(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """广告参考路线直接消费自包含 video_units，不派生或写入 reference_units。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        generate_video_episode_tool(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]
    script = ad_reference_ctx.pm.script_payload  # type: ignore[attr-defined]
    assert [unit["unit_id"] for unit in script["video_units"]] == ["E1U1"]
    assert "reference_units" not in script


@pytest.mark.integration
async def test_generate_video_episode_ad_reference_does_not_claim_orphan_file(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名文件没有 generated_assets 归属时仍须入队，不能把孤儿文件报告为成功。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    orphan = ad_reference_ctx.project_path / "reference_videos/E1U1.mp4"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        generate_video_episode_tool(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]


@pytest.mark.integration
async def test_generate_video_episode_ad_reference_preserves_the_selected_manual_upload(
    ad_reference_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    project_path = ad_reference_ctx.project_path
    ad_reference_ctx.pm.project_payload["schema_version"] = 8  # type: ignore[attr-defined]
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

    out = await _call(generate_video_all_tool(ad_reference_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    result = _generation_result(out)
    assert result.requested == []
    assert [entry.unit_id for entry in result.skipped] == ["E1U1"]
    enqueue.assert_not_awaited()


@pytest.mark.unit
async def test_generate_video_episode_ad_reference_replan_shell_cannot_enqueue(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """迁移保留的 needs_replan 空壳可被读取，但不能提交生成任务。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

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
        generate_video_episode_tool(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is True
    assert out["speech_admission"]["allowed"] is False
    assert out["speech_admission"]["unit_id"] == "E1U1"
    assert out["speech_admission"]["problems"][0]["code"] == "needs_replan"
    assert out["speech_admission"]["problems"][0]["action"] == "replan_unit"
    assert "E1U1" in out["content"][0]["text"]
    assert not called


@pytest.mark.unit
async def test_generate_video_episode_ad_reference_replan_unit_cannot_reuse_owned_clip(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """迁移保留的已归属视频不能绕过 needs_replan 生成闸门。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

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
        generate_video_episode_tool(ad_reference_ctx),
        {"script": "episode_1.json"},
    )

    assert out.get("is_error") is True
    assert "E1U1" in out["content"][0]["text"]
    assert not called


@pytest.mark.integration
async def test_generate_video_selected_ad_reference_regenerates_named_unit(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """广告点名重做沿用统一 video_unit 路径。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    enqueued: list[Any] = []
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _successful_reference_batch(ad_reference_ctx, enqueued))

    out = await _call(
        generate_video_selected_tool(ad_reference_ctx),
        {"script": "episode_1.json", "scene_ids": ["E1U1"]},
    )

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in enqueued] == ["E1U1"]


# ---------------------------------------------------------------------------
# split_reference_video_units
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_returns_declared_slots(monkeypatch) -> None:
    """unit 时长就是发给供应商的那个值，档位原样取自模型声明（不与任何静态区间求交）。"""
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {"supported_durations": [1, 8, 16, 18], "max_duration": 18, "default_duration": 16}

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    caps = await mod._fetch_reference_caps_with_fallback({}, 1)

    assert caps.durations == [1, 8, 16, 18]
    assert caps.reference_durations == [1, 8, 16, 18]
    assert caps.text_durations == [1, 8, 16, 18]
    assert caps.max_duration == 18
    assert caps.default_duration == 16  # 是档位成员，照常采信
    assert caps.max_refs is None


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_narrows_unit_duration_cap(monkeypatch) -> None:
    """档位随联动约束收窄：海螺在 1080p 下只接受 6 秒，全集是 [6, 10]。

    不收窄的话 step1 会按 10 秒拆出 unit，step2 的枚举 schema 再把它判非法。
    """
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "minimax",
            "model": "MiniMax-Hailuo-2.3",
            "supported_durations": [6, 10],
            "max_duration": 10,
            "default_duration": None,
        }

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"minimax/MiniMax-Hailuo-2.3": {"resolution": "1080p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.durations == [6]
    assert caps.max_duration == 6


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_narrows_slots_by_resolution(monkeypatch) -> None:
    """分辨率联动约束同样收窄 unit 档位：Veo 1080p 下只接受 8 秒。"""
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "max_duration": 8,
            "default_duration": None,
        }

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "1080p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.durations == [8]
    assert caps.max_duration == 8


@pytest.mark.unit
async def test_reference_unit_duration_tiers_does_not_assume_containment(monkeypatch) -> None:
    """两套档位之间无包含关系可假定：两条约束自相矛盾时带图那套反而更宽。

    ``constrain_durations`` 在交集为空时回退到未收窄候选，故型号同时声明「带图仅 8s」与
    「1080p 仅 6s」时，带图集回退成全集、不带图集收成 [6]。调用方须显式取并集当枚举。
    i2v 桶解析按不可解析处理——退回两桶同模型口径，联动矛盾在单模型内就能成立。
    """
    from lib.config import resolver as resolver_mod
    from lib.config.registry import ModelInfo
    from server.agent_runtime.sdk_tools import _context

    contradictory = ModelInfo(
        display_name="contradictory",
        media_type="video",
        capabilities=[],
        supported_durations=[4, 6, 8],
        duration_resolution_constraints={"1080p": [6]},
        reference_image_durations=[8],
    )
    monkeypatch.setattr(resolver_mod, "model_info_for", lambda *_args: contradictory)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"p/m": {"resolution": "1080p"}}}
    with_refs, without_refs = await _context.reference_unit_duration_tiers(
        project, {"provider_id": "p", "model": "m"}, [4, 6, 8]
    )

    assert with_refs == [4, 6, 8]
    assert without_refs == [6]
    assert not set(with_refs) <= set(without_refs)


@pytest.mark.unit
async def test_reference_unit_duration_tiers_without_refs_follow_i2v_bucket(monkeypatch) -> None:
    """不带图档位按 i2v 桶模型求值：无引用 unit 执行期降级到 i2v 桶执行，创作侧放行的秒数
    须与该桶模型的声明一致，否则会放行 r2v 独有档位、漏掉 i2v 独有档位。"""
    from server.agent_runtime.sdk_tools import _context

    async def _i2v_caps(_project, *, capability=None):
        assert capability == "i2v"
        return {"provider_id": "ark", "model": "doubao-seedance-1-5-pro-251215", "supported_durations": [5, 10]}

    monkeypatch.setattr(_context, "resolve_video_caps", _i2v_caps)

    with_refs, without_refs = await _context.reference_unit_duration_tiers(
        {}, {"provider_id": "minimax", "model": "S2V-01"}, [6, 10]
    )

    assert with_refs == [6, 10]
    assert without_refs == [5, 10]


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_splits_tiers_by_reference_state(monkeypatch) -> None:
    """「参考图↔时长」约束逐 unit 生效：Veo 720p 下带引用只剩 8 秒，无引用仍有 4/6/8。

    枚举与 prompt 候选取并集——一律按带图收窄会把无引用 unit 本可申请的短档也收掉。
    """
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "max_duration": 8,
            "default_duration": None,
        }

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.reference_durations == [8]
    assert caps.text_durations == [4, 6, 8]
    assert caps.durations == [4, 6, 8]
    assert caps.max_duration == 8
    assert caps.tiers_for(has_references=True) == [8]
    assert caps.tiers_for(has_references=False) == [4, 6, 8]


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_uses_write_layer_default(monkeypatch) -> None:
    """rv 路径的软回退与 _fetch_caps_with_fallback 同口径，取 duration_presets.DEFAULT_FALLBACK。"""
    from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _raising_caps(_project, _episode=None):
        raise ValueError("no provider configured")

    monkeypatch.setattr(mod, "resolve_video_caps", _raising_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)
    caps = await mod._fetch_reference_caps_with_fallback({}, 1)
    assert caps.default_duration is None
    assert caps.durations == DEFAULT_FALLBACK
    assert caps.max_duration == max(DEFAULT_FALLBACK)
    assert caps.max_refs is None


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_preserves_silent_intent_on_failure(monkeypatch) -> None:
    """能力查询失败时，`raw["requested_generate_audio"]` 仍随项目覆盖走，不回退成 True。

    它不依赖能力接口独立解析（同 generation_context.py），否则声音提示层会漏发
    WARN_SILENT_EPISODE，误导用户以为本集仍会尝试组装参考音频。独立解析本身照原样
    mock 掉（不经 async_session_factory 打真实 DB）：这条测不验证 DB 读取，只验证
    能力查询失败下 caps 字典的组装口径，打真 DB 只会让结果依赖本机是否已初始化好应用库。
    """
    from lib.config.resolver import ConfigResolver
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _raising_caps(_project, _episode=None):
        raise ValueError("no provider configured")

    async def _fake_project_audio(self, project):
        return bool(project.get("video_generate_audio", True))

    monkeypatch.setattr(mod, "resolve_video_caps", _raising_caps)
    monkeypatch.setattr(ConfigResolver, "video_generate_audio_for_project", _fake_project_audio)
    caps = await mod._fetch_reference_caps_with_fallback({"video_generate_audio": False}, 1)
    assert caps.voice.requested_generate_audio is False


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_degrades_silent_on_double_failure(monkeypatch) -> None:
    """独立解析也失败（双重故障）时收紧到 False，不得落回 True。

    与其余能力字段「不明时不额外收紧」相反：这里不明时假定无声，代价只是少发一条声音
    提示；假定有声则会让 `derive_voice_bindings` 在派生阶段继续算参考音频，误导排查方向。
    """
    from lib.config.resolver import ConfigResolver
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _raising_caps(_project, _episode=None):
        raise ValueError("no provider configured")

    async def _raising_project_audio(self, _project):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(mod, "resolve_video_caps", _raising_caps)
    monkeypatch.setattr(ConfigResolver, "video_generate_audio_for_project", _raising_project_audio)
    caps = await mod._fetch_reference_caps_with_fallback({"video_generate_audio": False}, 1)
    assert caps.voice.requested_generate_audio is False


def _rv_generator_returning(units: list[dict], captured: dict[str, Any] | None = None):
    """构造返回指定扁平 units JSON 的假 TextGenerator.create（可选捕获 task_type / project_name）。"""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps({"units": units}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
        return _FakeGenerator()

    return fake_create


_RV_NOVEL = "张三在村口等人"


def _rv_project(fake_ctx: ToolContext, generation_mode: str = "reference_video") -> None:
    """把项目声明成参考生视频路径——隔离草稿的拆分 / 晋升 / 阻塞判定都以此为前提。

    盘上的 project.json 与 pm 的内存视图同步：生成入口从盘上读，晋升工具经 ``pm.load_project`` 读。
    """
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps({"content_mode": "narration", "generation_mode": generation_mode}, ensure_ascii=False),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["content_mode"] = "narration"  # pyright: ignore[reportAttributeAccessIssue]
    fake_ctx.pm.project_payload["generation_mode"] = generation_mode  # pyright: ignore[reportAttributeAccessIssue]


def _rv_source(fake_ctx: ToolContext) -> None:
    _rv_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def _rv_unit(text: str, *, duration: int = 8, source_text: str = _RV_NOVEL) -> dict:
    """step1 的 LLM 产出形状：一层扁平（时长 + 原文锚 + 书写层正文）。"""
    return {"duration_seconds": duration, "source_text": source_text, "text": text}


def _rv_step1_path(fake_ctx: ToolContext):
    return fake_ctx.project_path / "drafts" / "episode_1" / "step1_reference_units.json"


async def _run_rv_split(fake_ctx: ToolContext, monkeypatch, units: list[dict], **caps_kwargs) -> dict:
    from server.agent_runtime.sdk_tools import text_generation as mod

    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher(**caps_kwargs))
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units))
    return await _call(split_reference_video_units_tool(fake_ctx), {"episode": 1})


@pytest.mark.unit
async def test_split_reference_video_units_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher())

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "DRY RUN" in prompt_text
    # 集号、资产候选与能力约束进 prompt；书写层语法规范随之注入
    assert "第 1 集" in prompt_text
    assert "张三" in prompt_text
    assert "12 秒" in prompt_text
    assert "镜头N：" in prompt_text


@pytest.mark.unit
async def test_split_reference_video_units_happy_derives_structure(fake_ctx: ToolContext, monkeypatch) -> None:
    """happy path：LLM 只写扁平正文，unit_id / shots / references 全部由工具机械派生后落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    captured: dict[str, Any] = {}
    units = [_rv_unit("镜头1：@[张三] 走向 @[村口]\n镜头2：@[张三] 停下脚步")]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher())
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units, captured))

    out = await _call(split_reference_video_units_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is not True, out

    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    unit = saved["units"][0]
    assert unit["unit_id"] == "E1U01"
    assert [s["text"] for s in unit["shots"]] == ["@[张三] 走向 @[村口]", "@[张三] 停下脚步"]
    assert unit["references"] == [
        {"type": "character", "name": "张三"},
        {"type": "scene", "name": "村口"},
    ]
    assert unit["source_text"] == _RV_NOVEL
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


@pytest.mark.unit
async def test_split_reference_video_units_numbers_unit_ids_by_order(fake_ctx: ToolContext, monkeypatch) -> None:
    """unit_id 按数组序号机械编号：LLM 不写 id，也就不存在重复 / 错集号可写。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("镜头1：@[张三] 起身"), _rv_unit("镜头1：@[张三] 出门")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert [u["unit_id"] for u in saved["units"]] == ["E1U01", "E1U02"]


@pytest.mark.unit
async def test_split_reference_video_units_derives_dialogue_without_reference_image(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """规范台词行的说话人位不进参考图（画外说话的角色附参考图会诱导入画）。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("镜头1：门开了\n@[张三]：{我来了。}")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["references"] == []


@pytest.mark.unit
async def test_split_reference_video_units_rejects_unregistered_asset(fake_ctx: ToolContext, monkeypatch) -> None:
    """正文引用未登记资产名 → fail-loud，不写盘（资产名引用完整性）。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])
    assert out.get("is_error") is True
    assert "未登记" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_rejects_unregistered_speaker(fake_ctx: ToolContext, monkeypatch) -> None:
    """说话人位未登记同样阻断：说话人决定该句台词绑哪段参考音频。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：门开了\n@[无名氏]：{我来了。}")])
    assert out.get("is_error") is True
    assert "说话人未登记" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_rejects_over_max_refs(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[张三] 与 @[李四] 在 @[村口]")], max_refs=2)
    assert out.get("is_error") is True
    assert "references" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.integration
async def test_split_reference_video_units_rejects_duration_off_reference_tier(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """带 `@` 引用的 unit 取了只有无引用 unit 才合法的时长 → 判违约、不写正式文件。

    枚举卡的是两套档位的并集，这类越界过得了 schema；不在此拦，执行期才会申请不到。
    """
    _rv_source(fake_ctx)
    out = await _run_rv_split(
        fake_ctx,
        monkeypatch,
        [_rv_unit("镜头1：@[张三] 起身", duration=4)],
        reference_durations=(8,),
    )
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "生效档位" in text and "[8]" in text
    # 与其余违约类同口径落隔离草稿：档位越界同样是 agent 改一改草稿就能修好的内容违约
    assert not _rv_step1_path(fake_ctx).exists()
    assert [v["code"] for v in _read_rv_quarantine(fake_ctx)["violations"]] == ["duration_off_tier"]


@pytest.mark.integration
async def test_split_reference_video_units_accepts_wide_tier_without_references(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """无 `@` 引用的 unit 不受「参考图↔时长」约束，仍可取更短的档位。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(
        fake_ctx,
        monkeypatch,
        [_rv_unit("镜头1：门被风吹开", duration=4)],
        reference_durations=(8,),
    )
    assert out.get("is_error") is not True, out
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["duration_seconds"] == 4
    assert saved["units"][0]["references"] == []


@pytest.mark.unit
async def test_split_reference_video_units_rejects_out_of_enum_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """本地校验复用动态 schema：超出 supported_durations 的 unit 时长被拦截，不落盘。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[张三] 起身", duration=5)])
    assert out.get("is_error") is True
    assert "step1 拆分内容结构校验失败" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_rejects_empty_units(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [])
    assert out.get("is_error") is True
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_rejects_non_verbatim_source_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """source_text 非源文逐字子串 → 响亮失败（模型转述 / 杜撰原文）。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("镜头1：@[张三] 起身", source_text="张三在城里等人")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is True
    assert "不是小说原文的逐字片段" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_accepts_source_text_substring(fake_ctx: ToolContext, monkeypatch) -> None:
    """锚只需是源文子串：unit 是画面单元，不必覆盖整段原文。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("镜头1：@[张三] 起身", source_text="张三在村口")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out


@pytest.mark.unit
async def test_split_reference_video_units_rejects_dialogue_overload(fake_ctx: ToolContext, monkeypatch) -> None:
    """台词量按语速估算超过 unit 时长（宽容系数外）→ 阻断。"""
    _rv_source(fake_ctx)
    long_line = "这是一段非常长的台词" * 6  # 60 字，zh 语速 5 字/秒 → 约 12 秒
    units = [_rv_unit(f"镜头1：@[张三] 起身\n@[张三]：{{{long_line}}}", duration=4)]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is True
    assert "超过该 unit" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_rejects_braces_in_description(fake_ctx: ToolContext, monkeypatch) -> None:
    """描述行误用花括号保留语法 → 阻断（写在描述行里的台词不会被识别，须响亮失败）。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[张三] 说 {我来了}，转身离开")])
    assert out.get("is_error") is True
    assert "花括号" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_rejects_too_many_shots(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    text = "\n".join(f"镜头{i}：@[张三] 动作 {i}" for i in range(1, 6))
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit(text)])
    assert out.get("is_error") is True
    assert "超过单 unit 上限" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# 隔离草稿与修复晋升闭环（step1）
# ---------------------------------------------------------------------------


def _rv_quarantine_path(fake_ctx: ToolContext):
    return quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP1)


def _read_rv_quarantine(fake_ctx: ToolContext) -> dict:
    return json.loads(_rv_quarantine_path(fake_ctx).read_text(encoding="utf-8"))


async def _promote(fake_ctx: ToolContext, monkeypatch, **caps_kwargs) -> dict:
    from server.agent_runtime.sdk_tools import text_generation as mod

    if not (fake_ctx.project_path / "project.json").exists():
        _rv_project(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher(**caps_kwargs))
    return await _call(validate_and_promote_reference_draft_tool(fake_ctx), {"episode": 1})


#: 七类阻断违约的最小触发样例（违约类 → 扁平 unit），共 8 条：「``@[X]`` 未登记」一类按出现位置
#: 拆成描述位（unregistered_asset）与台词行 speaker 位（unregistered_speaker）两条，两处走不同入口，
#: 合测会漏掉其中一处。逐类断言「落隔离草稿 + 正式文件干净 + 报告按类定位」，而不是只验其中
#: 一两类——各类共用同一次遍历，漏测哪一类都可能在该类上退回「丢弃重抽」。
#: ``duration_off_tier``（时长不在该 unit 引用状态的生效档位内）需要另一套 caps 才触发，
#: 单列在 ``test_split_reference_video_units_rejects_duration_off_reference_tier``。
_RV_VIOLATION_CASES = [
    ("unclosed_brace", _rv_unit("镜头1：@[张三] 起身，喊了一句 {我来了")),
    ("dialogue_line_syntax", _rv_unit("镜头1：门开了\n@[张三]：我来了。")),
    ("unregistered_asset", _rv_unit("镜头1：@[不存在的人] 出场")),
    ("unregistered_speaker", _rv_unit("镜头1：门开了\n@[无名氏]：{我来了。}")),
    ("braces_in_description", _rv_unit("镜头1：@[张三] 说 {我来了}，转身离开")),
    ("source_text_not_verbatim", _rv_unit("镜头1：@[张三] 起身", source_text="张三在城里等人")),
    ("too_many_shots", _rv_unit("\n".join(f"镜头{i}：@[张三] 动作 {i}" for i in range(1, 6)))),
    ("dialogue_overload", _rv_unit("镜头1：@[张三] 起身\n@[张三]：{" + "这是一段非常长的台词" * 6 + "}", duration=4)),
]


@pytest.mark.unit
@pytest.mark.parametrize(("code", "unit"), _RV_VIOLATION_CASES, ids=[c for c, _ in _RV_VIOLATION_CASES])
async def test_split_reference_video_units_quarantines_each_violation_class(
    fake_ctx: ToolContext, monkeypatch, code: str, unit: dict
) -> None:
    """七类阻断违约逐类：产物落隔离草稿、正式文件不被写出、报告按违约类逐条定位。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [unit])

    assert out.get("is_error") is True
    assert not _rv_step1_path(fake_ctx).exists()

    envelope = _read_rv_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_STEP1
    assert [v["code"] for v in envelope["violations"]] == [code]
    assert envelope["violations"][0]["label"] == "unit E1U01"
    # 隔离草稿装的是扁平书写层产物（agent 要改的那一层），不是派生后的落盘形状
    assert envelope["content"]["units"][0]["text"] == unit["text"]
    assert "shots" not in envelope["content"]["units"][0]

    report = out["content"][0]["text"]
    assert f"[{code}]" in report
    assert "unit E1U01" in report
    assert str(_rv_quarantine_path(fake_ctx)) in report
    assert "validate_and_promote_reference_draft" in report


@pytest.mark.unit
async def test_split_reference_video_units_reports_all_bad_units_in_one_round(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """报告逐条覆盖所有坏 unit，不停在第一个——否则 agent 每修一处就要再跑一轮付费拆分。"""
    _rv_source(fake_ctx)
    units = [
        _rv_unit("镜头1：@[张三] 起身"),
        _rv_unit("镜头1：@[不存在的人] 出场"),
        _rv_unit("镜头1：@[张三] 说 {我来了}"),
    ]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)

    assert out.get("is_error") is True
    envelope = _read_rv_quarantine(fake_ctx)
    assert [v["label"] for v in envelope["violations"]] == ["unit E1U02", "unit E1U03"]
    assert [v["code"] for v in envelope["violations"]] == ["unregistered_asset", "braces_in_description"]
    # 合法的 unit 也原样留在草稿里：agent 只需改坏的那些
    assert len(envelope["content"]["units"]) == 3


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_promotes_after_repair(fake_ctx: ToolContext, monkeypatch) -> None:
    """agent 修好隔离草稿后晋升：正式 step1 落盘、草稿清除、结构由正文机械派生。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "镜头1：@[张三] 在 @[村口] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["unit_id"] == "E1U01"
    assert saved["units"][0]["references"] == [
        {"type": "character", "name": "张三"},
        {"type": "scene", "name": "村口"},
    ]


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_reports_again_without_round_limit(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """再违约则再返回刷新后的报告、草稿留在原地，可反复晋升——无收敛轮次上限。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])

    for _round in range(3):
        out = await _promote(fake_ctx, monkeypatch)
        assert out.get("is_error") is True
        assert "unregistered_asset" in out["content"][0]["text"]
        assert _rv_quarantine_path(fake_ctx).exists()
        assert not _rv_step1_path(fake_ctx).exists()

    # 改成另一类违约后报告随之刷新，不是上一轮的陈旧快照
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "镜头1：@[张三] 说 {我来了}"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    await _promote(fake_ctx, monkeypatch)
    assert [v["code"] for v in _read_rv_quarantine(fake_ctx)["violations"]] == ["braces_in_description"]


# ---------------------------------------------------------------------------
# open_reference_step1_for_edit
# ---------------------------------------------------------------------------


def _write_rv_step1(fake_ctx: ToolContext, units: list[dict]) -> None:
    """直接铺一份正式 step1（模拟上一轮拆分的落盘产物）。"""
    path = _rv_step1_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": units}, ensure_ascii=False), encoding="utf-8")


def _rv_saved_unit(shots: list[str], *, unit_id: str = "E1U01", duration: int = 8) -> dict:
    """正式 step1 的落盘形状（含机器派生的 unit_id / shots / references）。"""
    return {
        "unit_id": unit_id,
        "shots": [{"text": t} for t in shots],
        "duration_seconds": duration,
        "references": [{"type": "character", "name": "张三"}],
        "source_text": _RV_NOVEL,
    }


async def _open_for_edit(fake_ctx: ToolContext, **args) -> dict:
    if not (fake_ctx.project_path / "project.json").exists():
        _rv_project(fake_ctx)
    return await _call(open_reference_step1_for_edit_tool(fake_ctx), {"episode": 1, **args})


@pytest.mark.unit
async def test_open_reference_step1_for_edit_returns_flat_writing_layer(fake_ctx: ToolContext) -> None:
    """取回的草稿装扁平书写层，不装派生物：agent 改的是正文 / 锚 / 时长，
    unit_id / shots / references 由晋升时按正文重新派生，放进草稿等于给漂移开口子。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身", "@[张三] 走向 @[村口]"])])

    out = await _open_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.get("is_error") is not True, out
    envelope = _read_rv_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_STEP1
    assert envelope["violations"] == []
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    unit = envelope["content"]["units"][0]
    assert set(unit) == {"duration_seconds", "source_text", "text"}
    assert unit["duration_seconds"] == 8
    assert unit["source_text"] == _RV_NOVEL
    # 多镜头 unit 的 text 必须带回 `镜头N：` header：落盘的 shots[*].text 不带 header，
    # 裸拼接后晋升时会被 parse_prompt 重新解析成一个镜头，分镜结构静默丢失。
    assert unit["text"] == "镜头1：@[张三] 起身\n镜头2：@[张三] 走向 @[村口]"


@pytest.mark.unit
async def test_open_reference_step1_for_edit_leaves_official_file_untouched(fake_ctx: ToolContext) -> None:
    """取回只是开编辑工位，正式文件一步不动——改动落回正式文件只发生在持锁的晋升侧。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])
    before = _rv_step1_path(fake_ctx).read_text(encoding="utf-8")

    await _open_for_edit(fake_ctx)

    assert _rv_step1_path(fake_ctx).read_text(encoding="utf-8") == before


@pytest.mark.unit
async def test_open_reference_step1_for_edit_round_trips_through_promote(fake_ctx: ToolContext, monkeypatch) -> None:
    """情况 B 的完整闭环：取回 → 改草稿 → 晋升。改动经晋升侧的持锁写盘落回正式文件，
    结构字段按新正文重新派生（references 跟着正文里的 @ 引用走）。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])

    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "镜头1：@[张三] 在 @[村口] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["shots"] == [{"text": "@[张三] 在 @[村口] 出场"}]
    assert saved["units"][0]["references"] == [
        {"type": "character", "name": "张三"},
        {"type": "scene", "name": "村口"},
    ]


@pytest.mark.unit
async def test_open_reference_step1_for_edit_refuses_to_clobber_existing_draft(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """已有隔离草稿在场时不覆盖：那份草稿可能已含 agent 未晋升的修改（或是待处置的违约产物），
    拿正式文件盖过去等于抹掉它手上的工作。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])
    before = _rv_quarantine_path(fake_ctx).read_text(encoding="utf-8")
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert _rv_quarantine_path(fake_ctx).read_text(encoding="utf-8") == before
    assert "validate_and_promote_reference_draft" in out["content"][0]["text"]


@pytest.mark.unit
async def test_open_reference_step1_for_edit_without_official_file(fake_ctx: ToolContext) -> None:
    """没有正式 step1 时指回首次拆分工具，而不是开一份空草稿让 agent 手写整集。"""
    _rv_source(fake_ctx)

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert "split_reference_video_units" in out["content"][0]["text"]
    assert not _rv_quarantine_path(fake_ctx).exists()


@pytest.mark.unit
async def test_open_reference_step1_for_edit_keeps_malformed_duration_verbatim(fake_ctx: ToolContext) -> None:
    """盘上 unit 的字段类型不符时原样带进草稿，不归一化成合法值：``8.0`` 被改写成 ``0``
    后，agent 从草稿里看到的是一个它没写过的时长，晋升报告说「时长不在档位内」也对不上
    盘上的原值。原样带过则由晋升侧 schema 逐条报告，agent 看得见错在哪。"""
    _rv_source(fake_ctx)
    unit = _rv_saved_unit(["@[张三] 起身"])
    unit["duration_seconds"] = 8.0
    _write_rv_step1(fake_ctx, [unit])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    assert _read_rv_quarantine(fake_ctx)["content"]["units"][0]["duration_seconds"] == 8.0


@pytest.mark.unit
async def test_open_reference_step1_for_edit_keeps_malformed_non_dict_unit_slot(fake_ctx: ToolContext) -> None:
    """盘上 units 混入非 dict 元素时不能直接丢弃：跳过会让草稿数组比正式文件短一个，若剩余
    unit 都能过校验，晋升会悄悄覆盖正式文件、丢失这个 unit 而无人知晓。留空占位在原数组
    位置，让晋升侧 schema 判它结构非法、逐条报出。"""
    _rv_source(fake_ctx)
    good_unit = _rv_saved_unit(["@[张三] 起身"])
    path = _rv_step1_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": [good_unit, "不是对象"]}, ensure_ascii=False), encoding="utf-8")

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    units = _read_rv_quarantine(fake_ctx)["content"]["units"]
    assert len(units) == 2
    assert units[1] == {"duration_seconds": None, "source_text": "", "text": ""}


@pytest.mark.unit
async def test_open_reference_step1_for_edit_blanks_shot_with_embedded_fake_header(fake_ctx: ToolContext) -> None:
    """盘上 shot 自身文本里恰好有一行形如「镜头N：」（旧数据经 Web 端保存，字段不禁止这种
    文本）时，render 后重新解析会把这一个 shot 误判成两个——原样晋升也会带着错位的分镜覆盖
    正式文件。清空为占位交给 schema 判非法，而不是悄悄晋升一份分镜数对不上的内容。"""
    _rv_source(fake_ctx)
    unit = _rv_saved_unit(["描述行\n镜头2：这是台词内容"])
    path = _rv_step1_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": [unit]}, ensure_ascii=False), encoding="utf-8")

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    assert _read_rv_quarantine(fake_ctx)["content"]["units"][0]["text"] == ""


@pytest.mark.unit
async def test_open_reference_step1_for_edit_rejects_missing_source_without_side_effect(
    fake_ctx: ToolContext,
) -> None:
    """`source` 指向不存在的文件时不落盘草稿：草稿一旦创建就把这个坏路径记进 meta.source，
    晋升时 `_load_novel_source` 会反复报错，而草稿在场又挡住重新取回改正 source，agent
    会卡在一个自己改不动的死角。校验失败时不产生持久副作用，agent 改对参数重试即可。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])

    out = await _open_for_edit(fake_ctx, source="source/episode_不存在.txt")

    assert out.get("is_error") is True
    assert not _rv_quarantine_path(fake_ctx).exists()


@pytest.mark.unit
async def test_open_reference_step1_for_edit_rejects_non_reference_episode(fake_ctx: ToolContext) -> None:
    """切走参考路径的集不给编辑：盘上的 step1 与该集此刻的生成路径无关。与晋升工具同一判据。"""
    _rv_source(fake_ctx)
    _rv_project(fake_ctx, generation_mode="image_to_video")
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert not _rv_quarantine_path(fake_ctx).exists()


# ---------------------------------------------------------------------------
# step1 乐观并发控制（取回时记基线指纹，晋升前锁内比对）
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_open_reference_step1_for_edit_records_base_fingerprint(fake_ctx: ToolContext) -> None:
    """取回时把正式文件此刻的内容指纹记进 meta.base_fingerprint，供晋升前基线比对。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    meta = _read_rv_quarantine(fake_ctx)["meta"]
    assert meta["base_fingerprint"] == script_review.content_fingerprint(_rv_step1_path(fake_ctx))


@pytest.mark.unit
async def test_promote_conflicts_when_official_changed_after_open(fake_ctx: ToolContext, monkeypatch) -> None:
    """「用户在审阅门编辑 + agent 改隔离草稿并晋升」的双端并发：取回后正式文件被另一写入方
    改过时，晋升中止并返回冲突报告（含最新内容与合并指引），不静默覆盖对方的修改；草稿
    留在原地。按报告把 meta.base_fingerprint 更新为现值（显式确认已合并）后方可重新晋升。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")

    # 模拟取回之后 Web 端保存改写了正式文件
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 在 @[村口] 等候"])])
    web_version = _rv_step1_path(fake_ctx).read_text(encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert "并发冲突" in report
    assert "base_fingerprint" in report
    # 冲突报告附上盘上现值的扁平书写层，供 agent 对照合并
    assert "在 @[村口] 等候" in report
    # 正式文件未被覆盖，草稿仍在场
    assert _rv_step1_path(fake_ctx).read_text(encoding="utf-8") == web_version
    assert _rv_quarantine_path(fake_ctx).exists()

    # 按报告指引更新基线指纹（显式确认已合并对方修改）后重新晋升即放行
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["meta"]["base_fingerprint"] = script_review.content_fingerprint(_rv_step1_path(fake_ctx))
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


@pytest.mark.unit
async def test_promote_conflict_report_renders_missing_fingerprint_as_json_null(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """取回后正式文件被删除：现值指纹是 null，报告须按 JSON 字面量给出而非字符串 "None"。
    照报告把 meta.base_fingerprint 设为 null 后重晋升即放行——写成字符串则永远比对不上、冲突解不掉。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    _rv_step1_path(fake_ctx).unlink()

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert "null" in report
    assert "None" not in report

    envelope = _read_rv_quarantine(fake_ctx)
    envelope["meta"]["base_fingerprint"] = None
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


@pytest.mark.unit
async def test_promote_without_base_fingerprint_meta_promotes_unchecked(fake_ctx: ToolContext, monkeypatch) -> None:
    """基线机制引入前产出的存量草稿缺 meta.base_fingerprint 键：按无基线晋升，不被新校验卡死。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_rv_quarantine(fake_ctx)
    del envelope["meta"]["base_fingerprint"]
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    # 取回后正式文件又被改过——存量草稿无基线可比，照旧覆盖（维持引入前语义）
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 在 @[村口] 等候"])])

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_violation_quarantine_records_base_fingerprint(fake_ctx: ToolContext, monkeypatch) -> None:
    """拆分违约落隔离草稿时同样记基线：修好晋升前正式文件被并发改写的话按基线中止。
    首拆时正式文件不存在，基线为 null——晋升时若正式文件已被另一次拆分写出，同样判冲突。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])

    meta = _read_rv_quarantine(fake_ctx)["meta"]
    assert "base_fingerprint" in meta
    assert meta["base_fingerprint"] is None

    # 草稿在场期间正式文件被写出（另一路径），修好草稿后晋升应报冲突而非覆盖
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "镜头1：@[张三] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is True
    assert "并发冲突" in out["content"][0]["text"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "hint"),
    [
        (lambda u: u.update(duration_seconds=7), "7"),
        (lambda u: u.pop("duration_seconds"), "duration_seconds"),
        (lambda u: u.update(source_text=""), "source_text"),
    ],
    ids=["off_slot_duration", "duration_removed", "blank_source_text"],
)
async def test_validate_and_promote_reference_draft_rejects_schema_breach(
    fake_ctx: ToolContext, monkeypatch, mutate, hint: str
) -> None:
    """草稿改坏 schema 层字段同样只回报告：晋升与产出走同一份 schema，正式文件不被污染。

    时长枚举在产出侧由 response_schema 卡死；晋升侧若只判内容约束，agent 把 duration_seconds
    改成非档位值或整个删掉（收成 0 秒）就能一路进正式 step1。
    """
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    mutate(envelope["content"]["units"][0])
    envelope["content"]["units"][0]["text"] = "镜头1：@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is True
    assert hint in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()
    assert [v["code"] for v in _read_rv_quarantine(fake_ctx)["violations"]] == ["schema_invalid"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutate_content",
    [
        lambda c: c.pop("units"),
        lambda c: c.update(units={}),
        lambda c: c.update(units=[]),
    ],
    ids=["units_removed", "units_not_a_list", "units_emptied"],
)
async def test_validate_and_promote_reference_draft_reports_broken_outer_shape(
    fake_ctx: ToolContext, monkeypatch, mutate_content
) -> None:
    """外层形状被改坏同样刷新报告，而不是抛一句裸错误。

    units 整个删掉 / 改成非数组 / 清空都是 agent 编辑草稿时会犯的错。只有逐 unit 的字段违约
    刷新报告的话，这几种就被甩出了「按报告改完再晋升」的循环。
    """
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    mutate_content(envelope["content"])
    edited_content = copy.deepcopy(envelope["content"])
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is True
    assert "content.units" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()
    refreshed = _read_rv_quarantine(fake_ctx)
    assert [v["code"] for v in refreshed["violations"]] == ["schema_invalid"]
    # 草稿留在原地且原样保留 agent 写的那份内容：做收编会把它的原稿改形，它照着报告回看时
    # 反而对不上自己写的东西，改完再晋升这条路就断了
    assert _rv_quarantine_path(fake_ctx).exists()
    assert refreshed["content"] == edited_content


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_requires_source_provenance(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """meta.source 被改掉后不晋升：按整个 source/ 重解析比产出时更松，别集的原文锚会恰好命中。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    assert "source" in envelope["meta"], "拆分侧须一律写出 source 键（未指定源文时为 null）"
    envelope["meta"] = {}
    envelope["content"]["units"][0]["text"] = "镜头1：@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is True
    assert "meta.source 缺失" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_reports_promotion_not_split(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """晋升成功的摘要要说「晋升」：说成「拆分」会让 agent 以为自己的修改被一次重抽覆盖了。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "镜头1：@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert "晋升" in out["content"][0]["text"]


@pytest.mark.unit
async def test_writing_reference_step1_clears_stale_step2_quarantine(fake_ctx: ToolContext, monkeypatch) -> None:
    """step1 一变即清掉在场的 step2 隔离草稿：它以旧 step1 为 diff 基底，留着就永远晋升不了。"""
    _rv_source(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "镜头1：@[张三] 起身"}]},
        violations=[],
    )
    step2_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP2)
    assert step2_path.exists()

    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[张三] 起身")])

    assert out.get("is_error") is not True, out
    assert not step2_path.exists()


@pytest.mark.unit
async def test_promote_reference_step1_preserves_step2_draft_when_content_unchanged(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """情况 B 中途放弃、原样晋升：取回草稿未改动即晋升，写回的 step1 与盘上原值逐字相同，
    此时不该清在场的 step2 隔离草稿——它的保结构 diff 仍然对得上这份没变的基底，agent
    放弃 step1 修改不该连带销毁一份仍然有效的 step2 修复草稿。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "镜头1：@[张三] 起身"}]},
        violations=[],
    )
    step2_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP2)
    assert step2_path.exists()

    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert step2_path.exists()


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_step2_uses_async_factory(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """step2 晋升走 ``ScriptGenerator.create``：晋升同样经 _add_metadata 落盘，裸构造会把
    metadata.generator 记成 "unknown"，与直接生成路径的同一份产物对不上。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "镜头1：@[张三] 起身"}]},
        violations=[],
    )

    class _FakeGenerator:
        def __init__(self, _path) -> None:
            raise AssertionError("晋升不得裸构造 ScriptGenerator")

        @classmethod
        async def create(cls, project_path):
            obj = cls.__new__(cls)
            obj.project_path = project_path
            return obj

        async def promote_reference_step2_draft(self, episode: int):
            return self.project_path / "scripts" / f"episode_{episode}.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is not True, out
    assert "episode_1.json" in out["content"][0]["text"]


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_refuses_after_mode_switch(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """切走参考路径后不再晋升残留草稿：晋升会按参考路径的形状覆盖该集正式剧本。"""
    _rv_project(fake_ctx, generation_mode="storyboard")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "镜头1：@[张三] 起身"}]},
        violations=[],
    )

    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is True
    assert "不走参考生视频路径" in out["content"][0]["text"]


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_step2_blocked_by_review_gate(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """step1 未经确认时 step2 草稿不晋升：常规生成路径在工具入口就被 gate 拦，两条路不该分叉。

    隔离期间用户在 Web 端改过 step1 会让确认指纹失效，该集回到 pending_review——此时晋升等于
    拿一份用户没确认过的 step1 合成正式剧本。
    """
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_project(fake_ctx)
    step1 = _rv_step1_path(fake_ctx)
    step1.parent.mkdir(parents=True, exist_ok=True)
    step1.write_text(json.dumps({"units": []}, ensure_ascii=False), encoding="utf-8")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "镜头1：@[张三] 起身"}]},
        violations=[],
    )
    monkeypatch.setattr(mod.script_review, "gate_blocks_step2", lambda *_args, **_kw: True)

    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is True
    assert "尚未经 web 审核确认" in out["content"][0]["text"]


@pytest.mark.unit
async def test_validate_and_promote_reference_draft_without_draft(fake_ctx: ToolContext, monkeypatch) -> None:
    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is True
    assert "没有待处置的隔离草稿" in out["content"][0]["text"]


@pytest.mark.unit
async def test_split_reference_video_units_clears_stale_quarantine_on_success(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """重拆分成功即清掉上一轮的隔离草稿——留着会让 gate 与生成侧继续阻塞在已被取代的产物上。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])
    assert _rv_quarantine_path(fake_ctx).exists()

    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[张三] 起身")])
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


@pytest.mark.unit
async def test_split_reference_video_units_surfaces_tolerated_voice_warnings(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """三类声音降级 warning 不阻断落盘，但随产物呈现——否则直到生成后才听得出声音打了折。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(
        fake_ctx,
        monkeypatch,
        [_rv_unit("镜头1：@[张三] 起身\n@[张三]：{我来了。}")],
        voice=VoiceRenderSettings(voice_consistency="native", max_reference_audio=2, model_id="m"),
    )

    assert out.get("is_error") is not True, out
    assert _rv_step1_path(fake_ctx).exists()
    text = out["content"][0]["text"]
    assert "声音降级提示" in text
    assert "未设置参考音频" in text


@pytest.mark.unit
async def test_split_reference_video_units_keeps_voice_warnings_on_per_image_backend(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """逐图挂载型 backend 下 warning 照常呈现：拆分阶段还没有参考图，那一位不该参与判定。

    开着 ``reference_audio_per_image`` 而不给参考图集合，会把每个说话人都判成「无画面可挂」，
    那条 warning 不在容忍列表内会被丢弃——超出段数上限这类提示反而不见了。
    """
    _rv_source(fake_ctx)
    fake_ctx.pm.project_payload["characters"] = {  # pyright: ignore[reportAttributeAccessIssue]
        "张三": {"description": "主角", "reference_audio": "characters/refs_audio/张三.wav"},
        "李四": {"description": "", "reference_audio": "characters/refs_audio/李四.wav"},
    }
    out = await _run_rv_split(
        fake_ctx,
        monkeypatch,
        [_rv_unit("镜头1：@[张三] 起身\n@[张三]：{我来了。}\n@[李四]：{你终于来了。}")],
        voice=VoiceRenderSettings(
            voice_consistency="native", max_reference_audio=1, model_id="m", requires_reference_image=True
        ),
    )

    assert out.get("is_error") is not True, out
    assert "参考音频最多 1 段" in out["content"][0]["text"]


def _write_rv_quarantine(fake_ctx: ToolContext) -> None:
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP1,
        content={"units": []},
        violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
    )


@pytest.mark.unit
async def test_generate_episode_script_blocked_by_quarantine(fake_ctx: ToolContext) -> None:
    """隔离草稿在场时 step2 入口阻塞，且给出「改草稿再晋升」而非「去 Web 端确认」的出路。"""
    _rv_project(fake_ctx)
    step1 = _rv_step1_path(fake_ctx)
    step1.parent.mkdir(parents=True, exist_ok=True)
    step1.write_text(json.dumps({"units": []}, ensure_ascii=False), encoding="utf-8")
    _write_rv_quarantine(fake_ctx)

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    assert "违约产物待处置" in out["content"][0]["text"]
    assert "validate_and_promote_reference_draft" in out["content"][0]["text"]


@pytest.mark.unit
async def test_generate_episode_script_quarantine_precedes_missing_step1(fake_ctx: ToolContext) -> None:
    """首次拆分就违约时正式 step1 本就不存在——先报缺文件会把 agent 引回重跑拆分（丢弃重抽）。"""
    _rv_project(fake_ctx)
    _write_rv_quarantine(fake_ctx)
    assert not _rv_step1_path(fake_ctx).exists()

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "违约产物待处置" in text
    assert "未找到 Step 1 文件" not in text


@pytest.mark.unit
async def test_generate_episode_script_ignores_quarantine_after_mode_switch(fake_ctx: ToolContext) -> None:
    """切走参考路径后残留的隔离草稿与新路径无关：非参考路径不清它们，仍判会把该集永久卡死。"""
    _rv_project(fake_ctx, generation_mode="storyboard")
    _write_rv_quarantine(fake_ctx)

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    # 卡在「缺 narration step1」这道常规校验上，而不是参考路径的隔离草稿
    assert "违约产物待处置" not in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# split_narration_segments
# ---------------------------------------------------------------------------


def _nr_caps(default=4, durations=(4, 6, 8)):
    async def fake_caps(_p, _episode=None):
        return default, list(durations)

    return fake_caps


def _nr_project(fake_ctx: ToolContext) -> None:
    _rv_project(fake_ctx, generation_mode="storyboard")


def _nr_source(fake_ctx: ToolContext) -> None:
    _nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(_RV_NOVEL, encoding="utf-8")


def _nr_generator_returning(segments: list[dict], captured: dict[str, Any] | None = None):
    """构造返回指定 segments JSON 的假 TextGenerator.create（可选捕获 task_type / project_name）。"""

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            if captured is not None:
                captured["generate_project_name"] = project_name

            class _R:
                text = json.dumps({"episode": 1, "segments": segments}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None):
        if captured is not None:
            captured["task_type"] = task_type
            captured["create_project_name"] = project_name
        return _FakeGenerator()

    return fake_create


def _nr_segment(segment_id="E1S01", duration=4, novel_text="张三走向村口。", **extra):
    seg = {
        "segment_id": segment_id,
        "novel_text": novel_text,
        "duration_seconds": duration,
        "segment_break": False,
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
    }
    seg.update(extra)
    return seg


@pytest.mark.unit
async def test_split_narration_segments_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "DRY RUN" in prompt_text
    # episode 注入 segment_id 前缀、资产候选与能力档位进 prompt
    assert "E1S" in prompt_text
    assert "张三" in prompt_text
    assert "4" in prompt_text
    # 未传 instructions 时无用户意见分节
    assert "# 用户意见" not in prompt_text


@pytest.mark.unit
async def test_split_narration_segments_injects_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 原样进 prompt 末尾的中性「用户意见」分节，不附加强度措辞。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "单个片段出场人物尽量不超过两人"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "# 用户意见" in prompt_text
    assert "单个片段出场人物尽量不超过两人" in prompt_text
    assert "必须全部落实" not in prompt_text


@pytest.mark.unit
async def test_split_narration_segments_rejects_bad_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 超长 / 非字符串按参数错误拒绝；空白 strip 后视同未传（校验为四个生成工具共享）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    tool_obj = split_narration_segments_tool(fake_ctx)

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "长" * 4001})
    assert out.get("is_error") is True
    assert "4000" in out["content"][0]["text"]

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": 42})
    assert out.get("is_error") is True

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "   \n  "})
    assert out.get("is_error") is not True, out
    assert "# 用户意见" not in out["content"][0]["text"]


@pytest.mark.unit
async def test_normalize_drama_script_injects_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "打斗场面多拆几个短镜头"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "# 用户意见" in prompt_text
    assert "打斗场面多拆几个短镜头" in prompt_text


@pytest.mark.unit
async def test_split_reference_video_units_injects_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher())

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "单 unit 出场人物尽量不超过两人"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "# 用户意见" in prompt_text
    assert "单 unit 出场人物尽量不超过两人" in prompt_text


@pytest.mark.unit
async def test_generate_episode_script_forwards_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """handler 把 instructions 原样转交 ScriptGenerator（dry_run 与生成路径同口径）。"""
    from lib import script_review
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    step1 = drafts / "step1_segments.json"
    step1.write_text("step1", encoding="utf-8")
    fingerprint = script_review.content_fingerprint(step1)
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "episodes": [{"episode": 1, "step1_review": {"fingerprint": fingerprint, "confirmed_at": "t"}}],
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        def __init__(self, _path):
            pass

        @classmethod
        async def create(cls, _path):
            return cls(_path)

        async def build_prompt(self, _episode, *, instructions=None):
            captured["build_prompt"] = instructions
            return "fake prompt"

        async def generate(self, *, episode, instructions=None):
            captured["generate"] = instructions
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)

    out = await _call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "偏好特写镜头"})
    assert out.get("is_error") is not True, out
    assert captured["build_prompt"] == "偏好特写镜头"

    out = await _call(tool_obj, {"episode": 1, "instructions": "偏好特写镜头"})
    assert out.get("is_error") is not True, out
    assert captured["generate"] == "偏好特写镜头"


@pytest.mark.unit
async def test_split_narration_segments_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    """happy path：结构化片段 step1 落盘；模型经文本管道按 SCRIPT 任务解析并携带 project_name 入账。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("张三走向村口。他停下脚步，久久凝望。", encoding="utf-8")
    captured: dict[str, Any] = {}
    segments = [
        _nr_segment("E1S01", 4, "张三走向村口。", characters_in_segment=["张三"], scenes=["村口"]),
        _nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
    ]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments, captured))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True, out

    step1_path = fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json"
    assert step1_path.exists()
    saved = json.loads(step1_path.read_text(encoding="utf-8"))
    assert [s["segment_id"] for s in saved["segments"]] == ["E1S01", "E1S02"]
    # novel_text 逐字保留
    assert saved["segments"][0]["novel_text"] == "张三走向村口。"
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


@pytest.mark.unit
async def test_split_narration_segments_registers_the_frozen_combined_source_basis(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifact_provenance import build_step1_basis
    from server.agent_runtime.sdk_tools import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,  # type: ignore[attr-defined]
        "schema_version": 8,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
    }
    fake_ctx.pm.project_payload = project  # type: ignore[attr-defined]
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    first_source = source_dir / "episode_1.txt"
    second_source = source_dir / "episode_2.txt"
    first_source.write_text("第一段原文。", encoding="utf-8")
    second_source.write_text("第二段原文。", encoding="utf-8")
    frozen_source = "第一段原文。\n\n第二段原文。"
    expected = build_step1_basis(frozen_source, episode=1, project=project)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    class _Generator:
        async def generate(self, _request, project_name=None):
            second_source.write_text("等待供应商期间改过的第二段。", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest  # type: ignore[attr-defined]
            project_file.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {"episode": 1, "segments": [_nr_segment(novel_text=frozen_source)]}, ensure_ascii=False
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(split_narration_segments_tool(fake_ctx), {"episode": 1})

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_step1(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


@pytest.mark.unit
async def test_split_narration_segments_rejects_out_of_enum_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """静态片段 schema 的 duration 是开区间，超出 supported_durations 的时长由工具后校验拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 5)]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "duration_seconds 非法" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_duplicate_segment_ids(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4), _nr_segment("E1S01", 6)]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "segment_id 重复" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_blank_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """novel_text 为纯空白（如单个空格）满足 schema min_length=1 却无实际旁白内容，须被后校验拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4, "张三在村口等人"), _nr_segment("E1S02", 4, novel_text=" ")]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "novel_text 为空白" in out["content"][0]["text"]
    assert "E1S02" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_empty_segments(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([]))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_missing_field(fake_ctx: ToolContext, monkeypatch) -> None:
    """缺资产字段（characters_in_segment 等）由既有片段 schema（NarrationStep1Segment strict）拦截。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    bad = {"segment_id": "E1S01", "novel_text": "缺字段", "duration_seconds": 4, "segment_break": False}
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([bad]))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "step1 拆分内容结构校验失败" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_unregistered_asset_reference(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """characters_in_segment / scenes / props 引用了 project.json 未登记的名称须被拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4, "张三在村口等人", characters_in_segment=["王五"])]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "未登记的资产名" in out["content"][0]["text"]
    assert "王五" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def _nr_source_and_call(fake_ctx: ToolContext, monkeypatch, source_text: str, segments: list[dict]):
    from server.agent_runtime.sdk_tools import text_generation as mod

    _nr_project(fake_ctx)
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(source_text, encoding="utf-8")
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    return await _call(tool_obj, {"episode": 1})


@pytest.mark.unit
async def test_split_narration_segments_rejects_truncated_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """片段合并后比源文短（模型删减）：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [_nr_segment("E1S01", 4, "张三走向村口。")],
    )
    assert out.get("is_error") is True
    assert "novel_text 未逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_rewritten_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """片段文字被模型改写（非逐字）：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            _nr_segment("E1S01", 4, "张三缓缓走向村口。"),
            _nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
        ],
    )
    assert out.get("is_error") is True
    assert "novel_text 未逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_reordered_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """片段顺序被模型打乱：novel_text 完整性校验拦截，不落盘。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。他停下脚步，久久凝望。",
        [
            _nr_segment("E1S01", 6, "他停下脚步，久久凝望。", segment_break=True),
            _nr_segment("E1S02", 4, "张三走向村口。"),
        ],
    )
    assert out.get("is_error") is True
    assert "novel_text 未逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_dropped_word_space(fake_ctx: ToolContext, monkeypatch) -> None:
    """空格分词语言里模型丢失词间空格（"Hello world" -> "Helloworld"）属实质内容损坏，须拦截。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello world, this is fine.",
        [_nr_segment("E1S01", 4, "Helloworld, this is fine.")],
    )
    assert out.get("is_error") is True
    assert "novel_text 未逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_accepts_split_at_paragraph_break(fake_ctx: ToolContext, monkeypatch) -> None:
    """片段边界恰好落在源文的段落换行处：边界处允许可选空格，不应误报删减。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口。\n他停下脚步，久久凝望。",
        [
            _nr_segment("E1S01", 4, "张三走向村口。"),
            _nr_segment("E1S02", 6, "他停下脚步，久久凝望。", segment_break=True),
        ],
    )
    assert out.get("is_error") is not True, out
    step1_path = fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json"
    assert step1_path.exists()


@pytest.mark.unit
async def test_split_narration_segments_accepts_split_at_halfwidth_punctuation(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """片段边界落在半角标点后（源文无空白分隔）：边界处允许可选空格，不应误报删减。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "张三走向村口.他停下脚步.",
        [
            _nr_segment("E1S01", 4, "张三走向村口."),
            _nr_segment("E1S02", 6, "他停下脚步.", segment_break=True),
        ],
    )
    assert out.get("is_error") is not True, out
    step1_path = fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json"
    assert step1_path.exists()


@pytest.mark.unit
async def test_split_narration_segments_rejects_dropped_space_after_punctuation(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """标点后的词间空格在片段内部（非边界）丢失："Hello, world." -> "Hello,world."，属实质内容损坏，须拦截。"""
    out = await _nr_source_and_call(
        fake_ctx,
        monkeypatch,
        "Hello, world. This is fine.",
        [_nr_segment("E1S01", 4, "Hello,world. This is fine.")],
    )
    assert out.get("is_error") is True
    assert "novel_text 未逐字、完整覆盖小说原文" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


@pytest.mark.unit
async def test_split_narration_segments_no_source(fake_ctx: ToolContext) -> None:
    _nr_project(fake_ctx)
    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


@pytest.mark.unit
async def test_generate_episode_script_reference_legacy_md_hints_resplit(fake_ctx: ToolContext) -> None:
    """reference_video 集仅存旧 .md 拆分表时，generate_episode_script 给出重跑拆分提示。"""
    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "episodes": [{"episode": 1, "generation_mode": "reference_video"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.md").write_text("| E1U1 |", encoding="utf-8")

    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "重跑 split-reference-video-units" in text
    assert "step1_reference_units.json" in text
