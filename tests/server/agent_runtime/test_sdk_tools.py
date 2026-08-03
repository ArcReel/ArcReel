"""Tests for ArcReel SDK in-process MCP tools.

Each tool: 1 happy-path and 1 error-path. Heavy plumbing
(``batch_enqueue_and_wait`` / ``enqueue_and_wait`` / ``ScriptGenerator`` etc.)
is monkeypatched, so the tests exercise schema wiring + error envelope
behavior without hitting the real queue or providers.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lib.reference_video.draft_validation import DraftViolation
from lib.reference_video.quarantine import (
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    quarantine_path,
    write_quarantine,
)
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakePM:
    def __init__(self, project_name: str, project_dir: Path):
        self._project_name = project_name
        self._project_dir = project_dir
        self.project_payload: dict[str, Any] = {
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
                    "video_prompt": "镜头平移",
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


async def _call(tool_obj, args: dict[str, Any]) -> dict[str, Any]:
    return await tool_obj.handler(args)


# ---------------------------------------------------------------------------
# build_arcreel_mcp_server
# ---------------------------------------------------------------------------


def test_build_arcreel_mcp_server_contains_all_tools(tmp_path: Path) -> None:
    srv = build_arcreel_mcp_server(project_name="demo", projects_root=tmp_path)
    assert srv["name"] == "arcreel"
    # SDK exposes the registered tools on srv["instance"]; we just sanity-check
    # the type returned matches the spec contract.
    assert "instance" in srv


def test_generate_narration_audio_registered() -> None:
    """旁白配音工具必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "generate_narration_audio" in ARCREEL_MCP_TOOL_IDS


# ---------------------------------------------------------------------------
# validate_script_filename — shared guard for all enqueue tools
# ---------------------------------------------------------------------------


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


def test_validate_script_filename_accepts_basename() -> None:
    from server.agent_runtime.sdk_tools._context import validate_script_filename

    assert validate_script_filename("episode_1.json") == "episode_1.json"


async def test_generate_storyboards_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    """Agent 传带路径分隔符的 script 名必须被 handler 拒绝（共享 validate_script_filename 防御）。"""
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "../etc/passwd"})
    assert out.get("is_error") is True
    assert "路径分隔符" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# enqueue_assets
# ---------------------------------------------------------------------------


async def test_list_pending_assets_happy(fake_ctx: ToolContext) -> None:
    tool_obj = list_pending_assets_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "张三" in text
    assert "村口" in text
    assert "保温杯" in text


async def test_list_pending_assets_error(fake_ctx: ToolContext, monkeypatch) -> None:
    def boom(_name):
        raise RuntimeError("db down")

    fake_ctx.pm.get_pending_characters = boom  # type: ignore[attr-defined]
    tool_obj = list_pending_assets_tool(fake_ctx)
    out = await _call(tool_obj, {"type": "character"})
    assert out.get("is_error") is True


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
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "1 succeeded" in text
    assert "张三" in text


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
                "generated_assets": {},
            },
            {
                "segment_id": "E1S02",
                "novel_text": "话说周末七国分争。",
                "generated_assets": {"narration_audio": "audio/segment_E1S02.wav"},
            },
        ],
    }


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
    assert spec.payload["prompt"] == "却说天下大势，分久必合。"
    assert spec.payload["script_file"] == "episode_1.json"
    text = out["content"][0]["text"]
    assert "1 succeeded" in text
    assert "audio/segment_E1S01.wav" in text


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


async def test_generate_narration_audio_blank_text_reported(fake_ctx: ToolContext, monkeypatch) -> None:
    """novel_text 空白的段不能静默丢弃：不入队、在输出中可见，显式点名时按错误上报。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"].append({"segment_id": "E1S03", "novel_text": "   ", "generated_assets": {}})
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

    # 扫描模式：空白段跳过且在输出中告警，不阻塞其余段，不算整体失败
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in captured] == ["E1S01"]
    assert "E1S03" in out["content"][0]["text"]

    # 显式点名空白段：该段按失败上报，header 计数与 is_error 口径一致
    captured.clear()
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": ["E1S03"]})
    assert out.get("is_error") is True
    assert captured == []
    text = out["content"][0]["text"]
    assert "E1S03" in text
    assert "0 succeeded, 1 failed" in text


async def test_generate_narration_audio_partial_unmatched_reported(fake_ctx: ToolContext, monkeypatch) -> None:
    """部分 id 不命中不能静默丢弃：命中的照常入队，未命中的按失败上报。"""
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
    text = out["content"][0]["text"]
    assert "1 succeeded, 1 failed" in text
    assert "E1S99" in text and "片段不存在" in text


async def test_generate_narration_audio_rejects_drama_script(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "drama",
        "episode": 1,
        "scenes": [{"scene_id": "E1S01", "generated_assets": {}}],
    }
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True
    assert "narration" in out["content"][0]["text"]


async def test_generate_narration_audio_rejects_reference_video_script(fake_ctx: ToolContext) -> None:
    """reference_video 模式无 segments，必须显式报错而非假装'已全部生成'。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "episode": 1,
        "video_units": [{"unit_id": "E1U1"}],
    }
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True
    assert "reference_video" in out["content"][0]["text"]


async def test_generate_narration_audio_rejects_string_segment_ids(fake_ctx: ToolContext) -> None:
    """segment_ids 传裸字符串会被逐字符迭代成 {'E','1','S'...}，必须显式拒绝。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": "E1S01"})
    assert out.get("is_error") is True
    assert "数组" in out["content"][0]["text"]


async def test_generate_narration_audio_skips_segment_without_id(fake_ctx: ToolContext, monkeypatch) -> None:
    """缺 segment_id 的片段不能让整批中断：跳过并告警，其余片段照常入队。"""
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"].append({"novel_text": "有文本但缺 id 的片段。", "generated_assets": {}})
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
    assert "跳过 1 个缺少 segment_id 的片段" in out["content"][0]["text"]


async def test_generate_narration_audio_no_match_error(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    fake_ctx.pm.script_payload = _narration_audio_script()  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "segment_ids": ["NO_SUCH"]})
    assert out.get("is_error") is True
    assert "没有找到匹配的片段" in out["content"][0]["text"]


async def test_generate_narration_audio_all_done(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    script = _narration_audio_script()
    script["segments"][0]["generated_assets"] = {"narration_audio": "audio/segment_E1S01.wav"}
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]
    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    assert "所有片段的旁白音频都已生成" in out["content"][0]["text"]


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


async def test_generate_narration_audio_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import enqueue_narration_audio as mod

    tool_obj = mod.generate_narration_audio_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "../etc/passwd"})
    assert out.get("is_error") is True
    assert "路径分隔符" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# enqueue_storyboards
# ---------------------------------------------------------------------------


async def test_generate_storyboards_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_storyboards as mod

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

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
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True


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


def test_edit_images_registered() -> None:
    """edit_images 必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "edit_images" in ARCREEL_MCP_TOOL_IDS


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


async def test_edit_images_storyboard_requires_script_file(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_i2i(_project):
        return True

    monkeypatch.setattr(mod, "_i2i_provider_available", fake_i2i)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "storyboard", "edits": [{"id": "E1S01", "instruction": "去杂物"}]})
    assert out.get("is_error") is True
    assert "script_file" in out["content"][0]["text"]


async def test_edit_images_rejects_unknown_resource_type(fake_ctx: ToolContext) -> None:
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "video", "edits": [{"id": "x", "instruction": "y"}]})
    assert out.get("is_error") is True


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


async def test_edit_images_rejects_empty_edits(fake_ctx: ToolContext) -> None:
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": []})
    assert out.get("is_error") is True
    assert "edits 不能为空" in out["content"][0]["text"]


async def test_edit_images_build_specs_warnings(fake_ctx: ToolContext, monkeypatch) -> None:
    """_build_specs 的告警分支（非法条目/缺 id/重复 id/缺指令/资源不存在）逐一命中，合法条目仍正常入队。"""
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
    assert "非法条目" in text
    assert "缺少 id 的条目" in text
    assert "重复出现" in text
    assert "缺少编辑指令" in text
    assert "王五" in text and "不存在，跳过" in text
    assert "1 succeeded" in text


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


async def test_edit_images_unexpected_exception(fake_ctx: ToolContext) -> None:
    """未预期的异常（如 pm 读取项目失败）要落到统一的 tool_error 兜底，而非向上抛出。"""

    def boom(_name: str) -> dict[str, Any]:
        raise RuntimeError("db down")

    fake_ctx.pm.load_project = boom  # type: ignore[method-assign]
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": [{"id": "张三", "instruction": "x"}]})
    assert out.get("is_error") is True
    assert "edit_images 失败" in out["content"][0]["text"]


async def test_i2i_provider_available_true(monkeypatch) -> None:
    from lib.config.resolver import ConfigResolver
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_resolve(self, project, payload, *, capability):
        assert capability == "i2i"
        return object()

    monkeypatch.setattr(ConfigResolver, "resolve_image_backend", fake_resolve)
    assert await mod._i2i_provider_available({}) is True


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


async def test_generate_grid_wrong_mode(fake_ctx: ToolContext) -> None:
    # 项目未开启 grid_storyboard → error
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


async def test_generate_grid_rejected_on_reference_video_route(fake_ctx: ToolContext) -> None:
    # reference_video 路线无分镜图步骤：即使残留 grid_storyboard=true 也不适用宫格工具
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# enqueue_videos
# ---------------------------------------------------------------------------


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

    assert out.get("is_error") is not True
    assert enqueued == ["E1S02"]


async def test_generate_video_episode_error(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.script_payload = {"content_mode": "narration", "segments": [], "episode": 1}  # type: ignore[attr-defined]
    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


def _reference_video_script(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content_mode": "narration",
        "generation_mode": "reference_video",
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


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_needs_confirmation(fake_ctx: ToolContext, monkeypatch) -> None:
    """申请秒数与剧本总时长不一致时，首次调用不入队，返回内容含总时长/申请秒数/差异说明。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit, ad_shots):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None):
        return None

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "precheck_unit", fake_precheck)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "E1U1" in text
    assert "5" in text and "8" in text
    assert "confirm_duration" in text
    assert enqueued == []


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_confirm_enqueues(fake_ctx: ToolContext, monkeypatch) -> None:
    """带 confirm_duration=true 的再次调用按取档结果入队并生成成功。"""
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit, ad_shots):
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

    async def fake_duration_context(_project, _episode=None):
        return None

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "precheck_unit", fake_precheck)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "confirm_duration": True})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in enqueued] == ["E1U1"]


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_repeat_without_confirm_still_blocked(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """不带确认参数的重复调用仍不入队。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit, ad_shots):
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None):
        return None

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "precheck_unit", fake_precheck)
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

    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit, ad_shots):
        return DurationSlot(seconds=5, total_seconds=5, adjustment=EXACT)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

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

    async def fake_duration_context(_project, _episode=None):
        return None

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "precheck_unit", fake_precheck)
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
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    precheck_calls: list[str] = []

    def fake_precheck(ctx, unit, ad_shots):
        precheck_calls.append(unit["unit_id"])
        return DurationSlot(seconds=5, total_seconds=5, adjustment=EXACT)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

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

    async def fake_duration_context(_project, _episode=None):
        return None

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "precheck_unit", fake_precheck)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert precheck_calls == ["E1U1"]
    assert [s.resource_id for s in enqueued] == ["E1U1"]
    assert "E1U2" in out["content"][0]["text"]


@pytest.mark.integration
async def test_generate_video_episode_reference_duration_resolves_project_context_once(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """批量预检 N 个 unit 时项目视频能力/分辨率只解析一次，逐 unit 取档改走纯函数 precheck_unit。

    重构前 ``_pending_duration_confirmations`` 对每个待确认 unit 各自触发一轮 DB 往返
    （``resolve_project_supported_durations``）；重构后项目级 IO 收口到批次开始时的
    一次 ``resolve_project_duration_context`` 调用，逐 unit 只做纯计算。
    """
    from server.agent_runtime.sdk_tools import enqueue_videos as mod
    from server.services.reference_video_tasks import ProjectDurationContext

    script = _reference_video_script()
    script["video_units"].append(
        {
            "unit_id": "E1U2",
            "shots": [{"text": "@张三 转身"}],
            "references": [{"type": "character", "name": "张三"}],
            "duration_seconds": 5,
        }
    )
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    context_calls: list[dict[str, Any]] = []

    async def fake_duration_context(project, _episode=None):
        context_calls.append(project)
        return ProjectDurationContext(supported_durations=(4, 8, 12), resolution=None, provider_id="", model_name=None)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    # 两个 unit 均 5 秒、档位无 5 → 都需确认，本批不入队；解析只发生一次。
    assert out.get("is_error") is not True, out
    assert len(context_calls) == 1
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
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    context_calls: list[dict[str, Any]] = []

    async def fake_duration_context(project, _episode=None):
        context_calls.append(project)
        raise AssertionError("无可预检 unit 时不应解析项目视频能力")

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        return [], []

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    await _call(tool_obj, {"script": "episode_1.json"})

    assert context_calls == []


@pytest.mark.integration
async def test_generate_video_episode_reference_skips_duration_context_when_prompt_blank(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """shots 非空但拼接后提示词全空白时，build_specs 会拒绝该 unit——预检须复用同一份
    结构校验提前判定，不能先触发项目能力解析再让 build_specs 事后跳过（见 Codex review）。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    script = _reference_video_script()
    for unit in script["video_units"]:
        unit["shots"] = [{"text": "   "}]
    fake_ctx.pm.script_payload = script  # type: ignore[attr-defined]

    context_calls: list[dict[str, Any]] = []

    async def fake_duration_context(project, _episode=None):
        context_calls.append(project)
        raise AssertionError("整批提示词均空白时不应解析项目视频能力")

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        return [], []

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert context_calls == []
    assert "E1U1" in out["content"][0]["text"]


@pytest.mark.integration
async def test_generate_video_episode_ad_reference_duration_needs_confirmation(
    ad_reference_ctx: ToolContext, monkeypatch
) -> None:
    """ad 参考直出走同一条确认闸门，且预检拿到的是水合后的成员镜头（ad_shots 非空）。"""
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    seen_ad_shots: list[Any] = []

    def fake_precheck(ctx, unit, ad_shots):
        seen_ad_shots.append(ad_shots)
        return DurationSlot(seconds=8, total_seconds=5, adjustment=UP)

    enqueued: list[Any] = []

    async def fake_batch(*, project_name, specs, on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    async def fake_duration_context(_project, _episode=None):
        return None

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "precheck_unit", fake_precheck)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(ad_reference_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert enqueued == []
    assert seen_ad_shots and seen_ad_shots[0]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("make_tool", "extra_args"),
    [
        (generate_video_scene_tool, {"scene_id": "E1S01"}),
        (generate_video_all_tool, {}),
        (generate_video_selected_tool, {"scene_ids": ["E1S01"]}),
    ],
    ids=["scene", "all", "selected"],
)
async def test_generate_video_reference_duration_confirmation_across_entries(
    fake_ctx: ToolContext, monkeypatch, make_tool, extra_args: dict[str, Any]
) -> None:
    """四个入口在 reference 路径下共用同一条确认闸门：未确认不入队、确认后入队。

    确认文本必须保留本次已产生的 log——scene / selected 的「scene_id 被忽略，转整集生成」
    正是靠它告诉用户，他同意的是整集而非所选的那个 scene。
    """
    from lib.generation_queue_client import BatchTaskResult
    from lib.reference_video.duration_slots import UP, DurationSlot
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    fake_ctx.pm.script_payload = _reference_video_script()  # type: ignore[attr-defined]

    def fake_precheck(ctx, unit, ad_shots):
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

    async def fake_duration_context(_project, _episode=None):
        return None

    monkeypatch.setattr(mod, "resolve_project_duration_context", fake_duration_context)
    monkeypatch.setattr(mod, "precheck_unit", fake_precheck)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = make_tool(fake_ctx)
    pending = await _call(tool_obj, {"script": "episode_1.json", **extra_args})

    assert pending.get("is_error") is not True, pending
    assert enqueued == []
    text = pending["content"][0]["text"]
    assert "confirm_duration" in text
    if make_tool is not generate_video_all_tool:
        assert "转整集生成" in text

    confirmed = await _call(tool_obj, {"script": "episode_1.json", **extra_args, "confirm_duration": True})

    assert confirmed.get("is_error") is not True, confirmed
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_video_scene_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    async def fake_enqueue(**kwargs):
        return {"task": {}, "result": {"file_path": "videos/scene_E1S01.mp4"}}

    monkeypatch.setattr(mod, "enqueue_and_wait", fake_enqueue)
    tool_obj = generate_video_scene_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is not True


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


async def test_generate_video_all_error(fake_ctx: ToolContext) -> None:
    def boom(*a, **kw):
        raise RuntimeError("broken")

    fake_ctx.pm.load_script = boom  # type: ignore[attr-defined]
    tool_obj = generate_video_all_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


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


async def test_generate_video_selected_no_match(fake_ctx: ToolContext) -> None:
    tool_obj = generate_video_selected_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_ids": ["NO_SUCH"]})
    assert out.get("is_error") is True


def test_build_asset_specs_skips_invalid_description(monkeypatch) -> None:
    """空白 / 非字符串描述都被跳过并告警，不应抛错（.strip()）或漏到 from_request 而中断整批。"""
    from lib.asset_types import ASSET_SPECS
    from server.agent_runtime.sdk_tools.enqueue_assets import _build_specs

    bucket = ASSET_SPECS["character"].bucket_key

    class _PM:
        def load_project(self, _name):
            return {
                bucket: {
                    "Alice": {"description": "   "},  # 空白
                    "Carol": {"description": {"x": 1}},  # 非字符串，.strip() 会抛 AttributeError
                    "Bob": {"description": "勇士"},
                }
            }

    warnings: list[str] = []
    specs = _build_specs(_PM(), "demo", "character", ["Alice", "Carol", "Bob"], warnings)  # type: ignore[arg-type]
    assert [s.resource_id for s in specs] == ["Bob"]
    assert any("Alice" in w for w in warnings)
    assert any("Carol" in w for w in warnings)


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
    log: list[str] = []
    specs, order_map = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        log=log,
    )
    assert len(specs) == 1
    assert specs[0].payload["duration_seconds"] == 7

    # 未显式指定 duration 时不携带该键，留给执行层按 caps 收口默认。
    items[0].pop("duration_seconds")
    specs2, _ = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        log=[],
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
    """批量入队场景下，单个条目 storyboard_image 非法（脏数据/越界/绝对路径）只跳过并记日志，
    不应让 `project_dir / storyboard_image` 抛未处理异常中断整批。"""
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
    log: list[str] = []
    specs, order_map = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        log=log,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert any("S01" in line for line in log)


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
    log: list[str] = []
    specs, order_map = _build_video_specs(
        items=items,
        id_field="segment_id",
        content_mode="narration",
        script_filename="episode_1.json",
        project_dir=tmp_path,
        skip_ids=None,
        log=log,
    )
    assert [s.resource_id for s in specs] == ["S02"]
    assert any("S01" in line for line in log)


@pytest.mark.integration
async def test_generate_video_scene_generated_assets_non_dict_readable_rejection(fake_ctx: ToolContext) -> None:
    """generated_assets 容器本身非 dict 时须走「没有分镜图」的可读拒绝分支，
    不应在单条路径上抛未处理 AttributeError。"""
    fake_ctx.pm.script_payload["segments"][0]["generated_assets"] = ["bad"]  # type: ignore[attr-defined]
    tool_obj = generate_video_scene_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "scene_id": "E1S01"})
    assert out.get("is_error") is True
    assert "没有分镜图" in out["content"][0]["text"]


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


async def test_resolve_voice_characters_skips_non_drama(fake_ctx: ToolContext) -> None:
    """narration/ad：不解析 voice_consistency，直接跳过（无 drama dialogue speaker 概念）。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _resolve_voice_characters

    assert await _resolve_voice_characters(fake_ctx, "narration") is None


async def test_resolve_voice_characters_drama_reads_project_characters_and_gate(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """drama：读项目角色资产，voice_consistency 为 none（C 类真无声）时退回不注入。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    async def fake_voice_consistency(_project, _episode=None):
        return "soft"

    monkeypatch.setattr(mod, "resolve_project_voice_consistency", fake_voice_consistency)
    characters = await mod._resolve_voice_characters(fake_ctx, "drama")
    assert characters == fake_ctx.pm.project_payload["characters"]  # type: ignore[attr-defined]

    async def fake_voice_consistency_none(_project, _episode=None):
        return "none"

    monkeypatch.setattr(mod, "resolve_project_voice_consistency", fake_voice_consistency_none)
    assert await mod._resolve_voice_characters(fake_ctx, "drama") is None


def test_build_reference_specs_routes_through_guard(tmp_path) -> None:
    """参考生视频入队经统一守卫点：prompt 由 shots 拼接后随 payload 入队（见 ADR-0001）。"""
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
    log: list[str] = []
    specs, order_map = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None, log=log)
    assert len(specs) == 1
    assert specs[0].task_type == "reference_video"
    assert specs[0].resource_id == "E1U1"
    # 拼接出的 prompt 经守卫点校验后落入 payload。
    assert specs[0].payload["prompt"] == "@张三 推门"
    assert specs[0].payload["script_file"] == "episode_1.json"


def test_build_reference_specs_skips_blank_prompt(tmp_path) -> None:
    """shots 存在但文本全空白的 unit 被跳过并告警，不漏到执行层（结构校验上移到守卫点）。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        {"unit_id": "E1U1", "shots": [{"text": "   "}, {"text": ""}]},
        {"unit_id": "E1U2", "shots": [{"text": "@李四 转身"}]},
    ]
    log: list[str] = []
    specs, order_map = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None, log=log)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert any("E1U1" in w for w in log)


def test_build_reference_specs_skips_bad_unit_id_without_aborting_batch(tmp_path) -> None:
    """unit_id 为空或键缺失（Agent 裸写 JSON 可致）都跳过该 unit 而非中断整批：
    空串经 from_request 抛 ValueError 被捕获，缺键经 .get 归一化为空串后同样被拒。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        {"unit_id": "", "shots": [{"text": "@张三 推门"}]},  # 空串
        {"shots": [{"text": "@王五 起身"}]},  # 缺 unit_id 键 → 不应抛 KeyError
        {"unit_id": "E1U2", "shots": [{"text": "@李四 转身"}]},
    ]
    log: list[str] = []
    specs, _ = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None, log=log)
    assert [s.resource_id for s in specs] == ["E1U2"]


def test_build_reference_specs_handles_malformed_shots(tmp_path) -> None:
    """畸形 shots（显式 null text / 非 dict 元素）不应崩溃整批，且不得把 'None' 注入 prompt。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        # text 显式 null + 一个非 dict 元素 → 拼接后为空 → 被守卫点判空跳过（不注入 'None'）。
        {"unit_id": "E1U1", "shots": [{"text": None}, "garbage"]},
        {"unit_id": "E1U2", "shots": [{"text": "@李四 转身"}]},
    ]
    log: list[str] = []
    specs, _ = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None, log=log)
    assert [s.resource_id for s in specs] == ["E1U2"]
    assert all("None" not in (s.payload.get("prompt") or "") for s in specs)


# ---------------------------------------------------------------------------
# text_generation
# ---------------------------------------------------------------------------


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
async def test_get_video_capabilities_passes_episode_through(fake_ctx: ToolContext, monkeypatch) -> None:
    """带 episode 时集号传到解析入口：生成模式可被单集覆盖，智能体须拿该集口径的能力。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    seen: list[int | None] = []

    async def fake_resolve(_project, episode=None):
        seen.append(episode)
        return {"provider_id": "fake", "supported_durations": [4, 6, 8]}

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    assert (await _call(tool_obj, {"episode": 3})).get("is_error") is not True
    # 省略集号仍按项目级解析
    assert (await _call(tool_obj, {})).get("is_error") is not True
    assert seen == [3, None]


@pytest.mark.unit
async def test_get_video_capabilities_annotates_reference_unit_tiers(fake_ctx: ToolContext, monkeypatch) -> None:
    """参考路径项目另返回两套逐 unit 生效档位，供手工改 step1 时与生成侧对同一份数字。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project, _episode=None):
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

    async def fake_resolve(_project, _episode=None):
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


async def test_get_video_capabilities_error(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project, _episode=None):
        raise FileNotFoundError("missing project.json")

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is True


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

        async def build_prompt(self, _episode):
            return "fake prompt"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "fake prompt" in out["content"][0]["text"]


async def test_generate_episode_script_missing_step1(fake_ctx: ToolContext) -> None:
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 99})
    assert out.get("is_error") is True


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

    # 项目显式选了无声明的分辨率：不收窄，与改动前一致
    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}}
    _default, durations = await ctx_mod.fetch_video_caps(project)
    assert durations == [4, 6, 8]

    # 参考图路径：即便分辨率无声明也收窄
    _default, durations = await ctx_mod.fetch_video_caps(project, generation_mode="reference_video")
    assert durations == [8]


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


async def test_normalize_drama_script_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# _build_prompt：Style 去重 + 「画风：」前缀清理
# ---------------------------------------------------------------------------


class TestBuildPrompt:
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

    def test_unstructured_keeps_style_prefix_normalized(self) -> None:
        from server.agent_runtime.sdk_tools.enqueue_storyboards import _build_prompt

        segment = {"segment_id": "E1S02", "image_prompt": "村口黄昏的长镜头"}
        out = _build_prompt(segment, "画风：真人电视剧风格", "", "segment_id")

        # 非结构化纯字符串 prompt 不含 Style，前缀补上且去掉「画风：」
        assert out.count("Style:") == 1
        assert "画风：" not in out
        assert out.startswith("Style: 真人电视剧风格")
        assert out.endswith("村口黄昏的长镜头")


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


async def test_plan_episodes_rejects_non_string_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 传非字符串（如数组）按参数错误上报，不静默吞掉。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None)))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": ["按章切"]})

    assert out.get("is_error") is True
    assert "instructions" in out["content"][0]["text"]


async def test_plan_episodes_rejects_overlong_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 超长按参数错误提前拒绝，不注入 prompt。"""
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(PlanResult(episodes=[], cursor=None)))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": "章" * (mod._MAX_INSTRUCTIONS_LEN + 1)})

    assert out.get("is_error") is True
    assert "过长" in out["content"][0]["text"]


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
    text = "章" * mod._MAX_INSTRUCTIONS_LEN
    out = await _call(mod.plan_episodes_tool(fake_ctx), {"instructions": text})

    assert out.get("is_error") is not True
    assert captured["plan_instructions"] == text


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


async def test_plan_episodes_source_exhausted(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    result = PlanResult(episodes=[], cursor=None, source_exhausted=True)
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(mod.plan_episodes_tool(fake_ctx), {})

    assert out.get("is_error") is not True
    assert "全部规划" in out["content"][0]["text"]


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
# enqueue_videos — ad + reference_video（派生分组直出）
# ---------------------------------------------------------------------------


def _ad_shot(shot_id: str, duration: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播",
        "products_in_shot": [],
        "image_prompt": {
            "scene": f"{shot_id} 画面",
            "composition": {"shot_type": "Close-up", "lighting": "自然光", "ambiance": "明亮"},
        },
        "video_prompt": {
            "action": f"{shot_id} 动作",
            "camera_motion": "Static",
            "ambiance_audio": "",
            "dialogue": [],
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def ad_reference_ctx(fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> ToolContext:
    from contextlib import contextmanager

    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    pm = fake_ctx.pm
    pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "style": "明亮写实",
            "episodes": [{"episode": 1, "title": "短片", "script_file": "scripts/episode_1.json"}],
        }
    )
    pm.script_payload = {  # type: ignore[attr-defined]
        "content_mode": "ad",
        "episode": 1,
        "title": "短片",
        "shots": [
            _ad_shot("E1S1", 3, products_in_shot=["保温杯"]),
            _ad_shot("E1S2", 2),
        ],
    }

    @contextmanager
    def _locked(_name: str, _filename: str, **_kw: Any):
        yield pm.script_payload  # type: ignore[attr-defined]

    pm.locked_script = _locked  # type: ignore[attr-defined]

    async def _fake_max_duration(_project: dict[str, Any], _episode: int | None = None) -> int | None:
        return 15

    monkeypatch.setattr(mod, "resolve_max_unit_duration", _fake_max_duration)
    return fake_ctx


async def test_generate_video_episode_ad_reference_derives_and_enqueues(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ad + reference_video：自动派生分组、持久化索引、按 unit 入队 reference_video 任务。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    enqueued: list[Any] = []

    async def fake_batch(*, project_name: str, specs: list[Any], on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        for spec in specs:
            enqueued.append(spec)
            out = ad_reference_ctx.project_path / "reference_videos" / f"{spec.resource_id}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00")
            br = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"reference_videos/{spec.resource_id}.mp4"},
            )
            if on_success:
                on_success(br)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(ad_reference_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert [s.resource_id for s in enqueued] == ["E1U1"]
    assert enqueued[0].task_type == "reference_video"
    # 派生索引持久化进剧本
    script = ad_reference_ctx.pm.script_payload  # type: ignore[attr-defined]
    assert script["reference_units"][0]["shot_ids"] == ["E1S1", "E1S2"]
    assert script["reference_units"][0]["references"][0] == {"type": "product", "name": "保温杯"}


async def test_generate_video_episode_ad_reference_regenerates_reset_unit(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成员/参考集变化导致 sync 重置 unit 后，磁盘残留的同名旧产物不得当作已完成跳过。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    pm = ad_reference_ctx.pm
    # 旧索引：E1U1 仅含 E1S1 且已完成；当前 shots 派生出的 E1U1 含 E1S1+E1S2 → sync 重置
    pm.script_payload["reference_units"] = [  # type: ignore[attr-defined]
        {
            "unit_id": "E1U1",
            "shot_ids": ["E1S1"],
            "references": [{"type": "product", "name": "保温杯"}],
            "generated_assets": {"video_clip": "reference_videos/E1U1.mp4", "status": "completed"},
        }
    ]
    stale = ad_reference_ctx.project_path / "reference_videos" / "E1U1.mp4"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"\x00")

    enqueued: list[Any] = []

    async def fake_batch(*, project_name: str, specs: list[Any], on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

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

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(ad_reference_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    # 重置后的 unit 必须重新入队，而不是凭旧文件跳过
    assert [s.resource_id for s in enqueued] == ["E1U1"]


async def test_generate_video_episode_ad_reference_skips_unchanged_unit_with_output(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成员与参考集未变且产物在盘的 unit 按已完成跳过，不重复入队。"""
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    pm = ad_reference_ctx.pm
    pm.script_payload["reference_units"] = [  # type: ignore[attr-defined]
        {
            "unit_id": "E1U1",
            "shot_ids": ["E1S1", "E1S2"],
            "references": [{"type": "product", "name": "保温杯"}],
            "generated_assets": {"video_clip": "reference_videos/E1U1.mp4", "status": "completed"},
        }
    ]
    done = ad_reference_ctx.project_path / "reference_videos" / "E1U1.mp4"
    done.parent.mkdir(parents=True, exist_ok=True)
    done.write_bytes(b"\x00")

    enqueued: list[Any] = []

    async def fake_batch(*, project_name: str, specs: list[Any], on_success=None, on_failure=None):
        enqueued.extend(specs)
        return [], []

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_episode_tool(ad_reference_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out
    assert enqueued == []


async def test_generate_video_all_ad_reference_falls_through_to_episode(
    ad_reference_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_videos as mod

    async def fake_batch(*, project_name: str, specs: list[Any], on_success=None, on_failure=None):
        from lib.generation_queue_client import BatchTaskResult

        for spec in specs:
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

    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)

    tool_obj = generate_video_all_tool(ad_reference_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})

    assert out.get("is_error") is not True, out


# ---------------------------------------------------------------------------
# split_reference_video_units
# ---------------------------------------------------------------------------


def _rv_caps(default=4, durations=(4, 6, 8), reference_durations=None, max_duration=12, max_refs=3, caps=None):
    from server.agent_runtime.sdk_tools.text_generation import ReferenceSplitCaps

    async def fake_caps(_p, _episode=None):
        return ReferenceSplitCaps(
            default_duration=default,
            durations=list(durations),
            reference_durations=list(durations if reference_durations is None else reference_durations),
            text_durations=list(durations),
            max_duration=max_duration,
            max_refs=max_refs,
            raw=dict(caps or {}),
        )

    return fake_caps


async def test_fetch_reference_caps_with_fallback_returns_declared_slots(monkeypatch) -> None:
    """unit 时长就是发给供应商的那个值，档位原样取自模型声明（不与任何静态区间求交）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {"supported_durations": [1, 8, 16, 18], "max_duration": 18, "default_duration": 16}

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

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

    project = {"model_settings": {"minimax/MiniMax-Hailuo-2.3": {"resolution": "1080p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.durations == [6]
    assert caps.max_duration == 6


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_narrows_slots_by_resolution(monkeypatch) -> None:
    """分辨率联动约束同样收窄 unit 档位：Veo 1080p 下只接受 8 秒。"""
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

    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "1080p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.durations == [8]
    assert caps.max_duration == 8


@pytest.mark.unit
def test_reference_unit_duration_tiers_does_not_assume_containment(monkeypatch) -> None:
    """两套档位之间无包含关系可假定：两条约束自相矛盾时带图那套反而更宽。

    ``constrain_durations`` 在交集为空时回退到未收窄候选，故型号同时声明「带图仅 8s」与
    「1080p 仅 6s」时，带图集回退成全集、不带图集收成 [6]。调用方须显式取并集当枚举。
    """
    from lib.config import resolver as resolver_mod
    from lib.config.registry import ModelInfo
    from server.agent_runtime.sdk_tools._context import reference_unit_duration_tiers

    contradictory = ModelInfo(
        display_name="contradictory",
        media_type="video",
        capabilities=[],
        supported_durations=[4, 6, 8],
        duration_resolution_constraints={"1080p": [6]},
        reference_image_durations=[8],
    )
    monkeypatch.setattr(resolver_mod, "model_info_for", lambda *_args: contradictory)

    project = {"model_settings": {"p/m": {"resolution": "1080p"}}}
    with_refs, without_refs = reference_unit_duration_tiers(project, {"provider_id": "p", "model": "m"}, [4, 6, 8])

    assert with_refs == [4, 6, 8]
    assert without_refs == [6]
    assert not set(with_refs) <= set(without_refs)


@pytest.mark.unit
async def test_fetch_reference_caps_with_fallback_splits_tiers_by_reference_state(monkeypatch) -> None:
    """「参考图↔时长」约束逐 unit 生效：Veo 720p 下带引用只剩 8 秒，无引用仍有 4/6/8。

    枚举与 prompt 候选取并集——一律按带图收窄会把无引用 unit 本可申请的短档也收掉。
    """
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

    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.reference_durations == [8]
    assert caps.text_durations == [4, 6, 8]
    assert caps.durations == [4, 6, 8]
    assert caps.max_duration == 8
    assert caps.tiers_for(has_references=True) == [8]
    assert caps.tiers_for(has_references=False) == [4, 6, 8]


async def test_fetch_reference_caps_with_fallback_uses_write_layer_default(monkeypatch) -> None:
    """rv 路径的软回退与 _fetch_caps_with_fallback 同口径，取 duration_presets.DEFAULT_FALLBACK。"""
    from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _raising_caps(_project, _episode=None):
        raise ValueError("no provider configured")

    monkeypatch.setattr(mod, "resolve_video_caps", _raising_caps)
    caps = await mod._fetch_reference_caps_with_fallback({}, 1)
    assert caps.default_duration is None
    assert caps.durations == DEFAULT_FALLBACK
    assert caps.max_duration == max(DEFAULT_FALLBACK)
    assert caps.max_refs is None


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

    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps(**caps_kwargs))
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units))
    return await _call(split_reference_video_units_tool(fake_ctx), {"episode": 1})


async def test_split_reference_video_units_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())

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


async def test_split_reference_video_units_happy_derives_structure(fake_ctx: ToolContext, monkeypatch) -> None:
    """happy path：LLM 只写扁平正文，unit_id / shots / references 全部由工具机械派生后落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    captured: dict[str, Any] = {}
    units = [_rv_unit("镜头1：@[张三] 走向 @[村口]\n镜头2：@[张三] 停下脚步")]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())
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


async def test_split_reference_video_units_numbers_unit_ids_by_order(fake_ctx: ToolContext, monkeypatch) -> None:
    """unit_id 按数组序号机械编号：LLM 不写 id，也就不存在重复 / 错集号可写。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("镜头1：@[张三] 起身"), _rv_unit("镜头1：@[张三] 出门")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert [u["unit_id"] for u in saved["units"]] == ["E1U01", "E1U02"]


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


async def test_split_reference_video_units_rejects_unregistered_asset(fake_ctx: ToolContext, monkeypatch) -> None:
    """正文引用未登记资产名 → fail-loud，不写盘（资产名引用完整性）。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[不存在的人] 出场")])
    assert out.get("is_error") is True
    assert "未登记" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_unregistered_speaker(fake_ctx: ToolContext, monkeypatch) -> None:
    """说话人位未登记同样阻断：说话人决定该句台词绑哪段参考音频。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：门开了\n@[无名氏]：{我来了。}")])
    assert out.get("is_error") is True
    assert "说话人未登记" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


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


async def test_split_reference_video_units_rejects_out_of_enum_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """本地校验复用动态 schema：超出 supported_durations 的 unit 时长被拦截，不落盘。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[张三] 起身", duration=5)])
    assert out.get("is_error") is True
    assert "step1 拆分内容结构校验失败" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_empty_units(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [])
    assert out.get("is_error") is True
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_non_verbatim_source_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """source_text 非源文逐字子串 → 响亮失败（模型转述 / 杜撰原文）。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("镜头1：@[张三] 起身", source_text="张三在城里等人")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is True
    assert "不是小说原文的逐字片段" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_accepts_source_text_substring(fake_ctx: ToolContext, monkeypatch) -> None:
    """锚只需是源文子串：unit 是画面单元，不必覆盖整段原文。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("镜头1：@[张三] 起身", source_text="张三在村口")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out


async def test_split_reference_video_units_rejects_dialogue_overload(fake_ctx: ToolContext, monkeypatch) -> None:
    """台词量按语速估算超过 unit 时长（宽容系数外）→ 阻断。"""
    _rv_source(fake_ctx)
    long_line = "这是一段非常长的台词" * 6  # 60 字，zh 语速 5 字/秒 → 约 12 秒
    units = [_rv_unit(f"镜头1：@[张三] 起身\n@[张三]：{{{long_line}}}", duration=4)]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is True
    assert "超过该 unit" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_braces_in_description(fake_ctx: ToolContext, monkeypatch) -> None:
    """描述行误用花括号保留语法 → 阻断（写在描述行里的台词不会被识别，须响亮失败）。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("镜头1：@[张三] 说 {我来了}，转身离开")])
    assert out.get("is_error") is True
    assert "花括号" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_too_many_shots(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    text = "\n".join(f"镜头{i}：@[张三] 动作 {i}" for i in range(1, 6))
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit(text)])
    assert out.get("is_error") is True
    assert "超过单 unit 上限" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


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
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps(**caps_kwargs))
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


async def test_open_reference_step1_for_edit_leaves_official_file_untouched(fake_ctx: ToolContext) -> None:
    """取回只是开编辑工位，正式文件一步不动——改动落回正式文件只发生在持锁的晋升侧。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])
    before = _rv_step1_path(fake_ctx).read_text(encoding="utf-8")

    await _open_for_edit(fake_ctx)

    assert _rv_step1_path(fake_ctx).read_text(encoding="utf-8") == before


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


async def test_open_reference_step1_for_edit_without_official_file(fake_ctx: ToolContext) -> None:
    """没有正式 step1 时指回首次拆分工具，而不是开一份空草稿让 agent 手写整集。"""
    _rv_source(fake_ctx)

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert "split_reference_video_units" in out["content"][0]["text"]
    assert not _rv_quarantine_path(fake_ctx).exists()


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


async def test_open_reference_step1_for_edit_rejects_non_reference_episode(fake_ctx: ToolContext) -> None:
    """切走参考路径的集不给编辑：盘上的 step1 与该集此刻的生成路径无关。与晋升工具同一判据。"""
    _rv_source(fake_ctx)
    _rv_project(fake_ctx, generation_mode="image_to_video")
    _write_rv_step1(fake_ctx, [_rv_saved_unit(["@[张三] 起身"])])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert not _rv_quarantine_path(fake_ctx).exists()


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


async def test_validate_and_promote_reference_draft_without_draft(fake_ctx: ToolContext, monkeypatch) -> None:
    out = await _promote(fake_ctx, monkeypatch)
    assert out.get("is_error") is True
    assert "没有待处置的隔离草稿" in out["content"][0]["text"]


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


async def test_split_reference_video_units_surfaces_tolerated_voice_warnings(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """三类声音降级 warning 不阻断落盘，但随产物呈现——否则直到生成后才听得出声音打了折。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(
        fake_ctx,
        monkeypatch,
        [_rv_unit("镜头1：@[张三] 起身\n@[张三]：{我来了。}")],
        caps={"voice_consistency": "native", "max_reference_audio_count": 2, "model": "m"},
    )

    assert out.get("is_error") is not True, out
    assert _rv_step1_path(fake_ctx).exists()
    text = out["content"][0]["text"]
    assert "声音降级提示" in text
    assert "未设置参考音频" in text


def _write_rv_quarantine(fake_ctx: ToolContext) -> None:
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP1,
        content={"units": []},
        violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
    )


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


async def test_split_narration_segments_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
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


async def test_split_narration_segments_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    """happy path：结构化片段 step1 落盘；模型经文本管道按 SCRIPT 任务解析并携带 project_name 入账。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

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


async def test_split_narration_segments_rejects_out_of_enum_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """静态片段 schema 的 duration 是开区间，超出 supported_durations 的时长由工具后校验拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    segments = [_nr_segment("E1S01", 5)]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "duration_seconds 非法" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_duplicate_segment_ids(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4), _nr_segment("E1S01", 6)]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "segment_id 重复" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_blank_novel_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """novel_text 为纯空白（如单个空格）满足 schema min_length=1 却无实际旁白内容，须被后校验拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    segments = [_nr_segment("E1S01", 4, "张三在村口等人"), _nr_segment("E1S02", 4, novel_text=" ")]
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "novel_text 为空白" in out["content"][0]["text"]
    assert "E1S02" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_empty_segments(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([]))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_missing_field(fake_ctx: ToolContext, monkeypatch) -> None:
    """缺资产字段（characters_in_segment 等）由既有片段 schema（NarrationStep1Segment strict）拦截。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    bad = {"segment_id": "E1S01", "novel_text": "缺字段", "duration_seconds": 4, "segment_break": False}
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([bad]))

    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "step1 拆分内容结构校验失败" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_segments.json").exists()


async def test_split_narration_segments_rejects_unregistered_asset_reference(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """characters_in_segment / scenes / props 引用了 project.json 未登记的名称须被拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
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

    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text(source_text, encoding="utf-8")
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", _nr_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    tool_obj = split_narration_segments_tool(fake_ctx)
    return await _call(tool_obj, {"episode": 1})


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


async def test_split_narration_segments_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = split_narration_segments_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


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
