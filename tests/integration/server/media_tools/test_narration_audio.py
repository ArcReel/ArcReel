"""Tests for enqueue_narration_audio."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from lib.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.media_tools.context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _activate_unbound_project,
    _call,
    _generation_result,
    _reference_video_script,
    _use_reference_route,
)

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
        from lib.artifact_manifest import ArtifactComparison

        return ArtifactComparison(status=ArtifactStatus.STALE, artifact_path=artifact_path or "")


async def test_generate_narration_audio_missing_only_reuses_a_stale_recording(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """missing-only 只补 missing：已失效但可用的旧配音被复用，不重新付费生成。"""
    from server.media_tools import narration_audio as mod

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


async def test_generate_narration_audio_explicit_ids_regenerate_a_stale_recording(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """点名即强制：同一个 stale 单元在显式选择下照常重新生成。"""
    from server.media_tools import narration_audio as mod

    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = {"narration_audio": "audio/segment_E1S01.wav"}
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
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


async def test_generate_narration_audio_enqueues_missing_segments(fake_ctx: ToolContext, monkeypatch) -> None:
    """不传 segment_ids → 只为缺 narration_audio 的段入队 tts 任务，prompt 为该段 novel_text。"""
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
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
    assert "成功 1 件" in text
    assert "audio/segment_E1S01.wav" in text


async def test_generate_narration_audio_covers_reference_video_units(fake_ctx: ToolContext, monkeypatch) -> None:
    """参考生视频的 video_units 同样可点名配音——入口按当前骨架取单元，不限生成模式。"""
    from server.media_tools import narration_audio as mod

    _use_reference_route(fake_ctx)
    fake_ctx.pm.script_payload = _reference_video_script(  # type: ignore[attr-defined]
        video_units=[{"unit_id": "E1U1", "duration_seconds": 5, "text": "{风吹过旷野。}"}]
    )
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"audio/unit_{s.resource_id}.wav"},
            )
            for s in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    out = await _call(mod.generate_narration_audio_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1U1"]
    assert captured[0].task_type == "tts"
    assert "成功 1 件" in out["content"][0]["text"]


async def test_generate_narration_audio_rejects_unbound_active_script_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import narration_audio as mod

    _activate_unbound_project(fake_ctx)
    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    enqueued = False

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        nonlocal enqueued
        enqueued = True
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    out = await _call(mod.generate_narration_audio_tool(fake_ctx), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    assert enqueued is False


async def test_generate_narration_audio_uses_canonical_filename_when_episode_field_is_absent(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import narration_audio as mod

    script = _narration_audio_script()
    script.pop("episode")
    script["segments"] = script["segments"][:1]
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
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

    async def _batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="succeeded", result={}) for s in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", _batch)

    out = await _call(mod.generate_narration_audio_tool(fake_ctx), {"script": "episode_2.json"})

    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1S01"]
    # 集号取自项目绑定而非剧本自述：产物身份随之落在第 2 集
    assert _generation_result(out).items[0].artifact_key == ArtifactKey.episode_audio(2, "E1S01").encode()


async def test_generate_narration_audio_selects_item_with_corrupt_generated_assets(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
    from server.media_tools import narration_audio as mod

    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = "corrupt"
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
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


async def test_generate_narration_audio_explicit_ids_regenerate(fake_ctx: ToolContext, monkeypatch) -> None:
    """传 segment_ids → 即使该段已有 narration_audio 也重新入队（批量范围/单段重生语义）。"""
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
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


async def test_generate_narration_audio_blank_text_reported(fake_ctx: ToolContext, monkeypatch) -> None:
    """novel_text 空白的段不能静默丢弃：不入队、在输出中可见，显式点名时按错误上报。"""
    from server.media_tools import narration_audio as mod

    script = _narration_audio_script()
    script["segments"].append({"segment_id": "E1S03", "novel_text": "   ", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
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


async def test_generate_narration_audio_partial_unmatched_reported(fake_ctx: ToolContext, monkeypatch) -> None:
    """部分 id 不命中不能静默丢弃：命中的照常入队，未命中的按 blocked 逐 ID 上报。"""
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
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


async def test_generate_narration_audio_accepts_drama_narrator_scene(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import narration_audio as mod

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

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        del project_name, on_success, on_failure
        captured.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1S01"]
    assert captured[0].payload == {"prompt": None, "script_file": "episode_1.json"}


async def test_generate_narration_audio_uses_project_mode_for_drama_without_content_mode(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import narration_audio as mod

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

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        del project_name, on_success, on_failure
        captured.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1S01"]


async def test_generate_narration_audio_accepts_reference_narrator_unit(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "海面\n{风从远方吹来。}",
                "duration_seconds": 8,
                "generated_assets": {},
            }
        ],
    }
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        del project_name, on_success, on_failure
        captured.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [spec.resource_id for spec in captured] == ["E1U1"]


async def test_generate_narration_audio_rejects_mismatched_script(fake_ctx: ToolContext) -> None:
    """分镜图生视频项目下的 video_units 骨架剧本：结构报错 + 重拆指引，不静默换路径。"""
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "骨架" in text
    assert "重新拆分" in text


async def test_generate_narration_audio_rejects_string_segment_ids(fake_ctx: ToolContext) -> None:
    """segment_ids 传裸字符串会被逐字符迭代成 {'E','1','S'...}，必须显式拒绝。"""
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": "E1S01"})
    assert out.get("is_error") is True
    assert "数组" in out["content"][0]["text"]


async def test_generate_narration_audio_skips_segment_without_id(fake_ctx: ToolContext, monkeypatch) -> None:
    """缺 segment_id 的分镜不能让整批中断：无 ID 可寻址故不进契约，其余分镜照常入队。"""
    from server.media_tools import narration_audio as mod

    script = _narration_audio_script()
    # 两个分镜都缺配音：本用例的主题是无 ID 分镜的可寻址性，不掺入已有配音的复用判定。
    script["segments"][1]["generated_assets"] = {}
    script["segments"].append({"novel_text": "有文本但缺 id 的片段。", "video_prompt": {}, "generated_assets": {}})
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    captured: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        captured.extend(specs)
        return [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="succeeded", result={}) for s in specs
        ], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01", "E1S02"]
    assert _generation_result(out).requested == ["E1S01", "E1S02"]


async def test_generate_narration_audio_no_match_error(fake_ctx: ToolContext) -> None:
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": ["NO_SUCH"]})
    assert out.get("is_error") is True
    result = _generation_result(out)
    assert result.requested == ["NO_SUCH"]
    assert result.blocked == ["NO_SUCH"]


async def test_generate_narration_audio_all_done(fake_ctx: ToolContext) -> None:
    from server.media_tools import narration_audio as mod

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


async def test_generate_narration_audio_task_failures_surface(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.media_tools import narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None, **_batch_kwargs):
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
    assert "成功 0 件、失败 1 件" in text
    assert "provider down" in text


async def test_generate_narration_audio_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    from server.media_tools import narration_audio as mod

    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "../etc/passwd"})
    assert out.get("is_error") is True
    assert "路径分隔符" in out["content"][0]["text"]
