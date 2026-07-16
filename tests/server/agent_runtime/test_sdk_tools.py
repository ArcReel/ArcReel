"""Tests for ArcReel SDK in-process MCP tools.

Each tool: 1 happy-path and 1 error-path. Heavy plumbing
(``batch_enqueue_and_wait`` / ``enqueue_and_wait`` / ``ScriptGenerator`` etc.)
is monkeypatched, so the tests exercise schema wiring + error envelope
behavior without hitting the real queue or providers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.agent_runtime.sdk_tools import build_arcreel_mcp_server
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.enqueue_assets import (
    generate_assets_tool,
    list_pending_assets_tool,
)
from server.agent_runtime.sdk_tools.enqueue_grid import generate_grid_tool
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
    split_reference_video_units_tool,
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


async def test_generate_storyboards_error(fake_ctx: ToolContext, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ValueError("bad script")

    fake_ctx.pm.load_script = boom  # type: ignore[attr-defined]
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# enqueue_grid
# ---------------------------------------------------------------------------


async def test_generate_grid_list_only(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.project_payload["generation_mode"] = "grid"  # type: ignore[attr-defined]
    # Need enough segments to form a group with valid layout
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    assert "分组" in out["content"][0]["text"]


async def test_generate_grid_wrong_mode(fake_ctx: ToolContext) -> None:
    # project doesn't have generation_mode='grid' → error
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
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


async def test_generate_video_episode_error(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.script_payload = {"content_mode": "narration", "segments": [], "episode": 1}  # type: ignore[attr-defined]
    tool_obj = generate_video_episode_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


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
    parsed = yaml.safe_load(_get_video_prompt(drama_item))
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
    parsed_narr = yaml.safe_load(_get_video_prompt(narration_item))
    assert parsed_narr["Dialogue"] == [{"Speaker": "Alice", "Line": "hello"}]


def test_build_reference_specs_routes_through_guard(tmp_path) -> None:
    """参考生视频入队经统一守卫点：prompt 由 shots 拼接后随 payload 入队（见 ADR-0001）。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    # production 的 shots[*].text 由 parse_prompt 产出、已剥离 "Shot N (Xs):" header，
    # fixture 用同样的 header-stripped 形态以贴近真实数据。
    units = [
        {
            "unit_id": "E1U1",
            "shots": [{"duration": 3, "text": "@张三 推门"}],
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
        {"unit_id": "E1U1", "shots": [{"duration": 3, "text": "   "}, {"duration": 2, "text": ""}]},
        {"unit_id": "E1U2", "shots": [{"duration": 3, "text": "@李四 转身"}]},
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
        {"unit_id": "", "shots": [{"duration": 3, "text": "@张三 推门"}]},  # 空串
        {"shots": [{"duration": 3, "text": "@王五 起身"}]},  # 缺 unit_id 键 → 不应抛 KeyError
        {"unit_id": "E1U2", "shots": [{"duration": 3, "text": "@李四 转身"}]},
    ]
    log: list[str] = []
    specs, _ = _build_reference_specs(units=units, script_filename="episode_1.json", skip_ids=None, log=log)
    assert [s.resource_id for s in specs] == ["E1U2"]


def test_build_reference_specs_handles_malformed_shots(tmp_path) -> None:
    """畸形 shots（显式 null text / 非 dict 元素）不应崩溃整批，且不得把 'None' 注入 prompt。"""
    from server.agent_runtime.sdk_tools.enqueue_videos import _build_reference_specs

    units = [
        # text 显式 null + 一个非 dict 元素 → 拼接后为空 → 被守卫点判空跳过（不注入 'None'）。
        {"unit_id": "E1U1", "shots": [{"duration": 3, "text": None}, "garbage"]},
        {"unit_id": "E1U2", "shots": [{"duration": 3, "text": "@李四 转身"}]},
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

    async def fake_resolve(_project):
        return {"provider_id": "fake", "supported_durations": [4, 6, 8]}

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is not True
    assert json.loads(out["content"][0]["text"])["provider_id"] == "fake"


async def test_get_video_capabilities_error(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project):
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


async def test_normalize_drama_script_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p):
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

    async def fake_caps(_p):
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

    async def fake_caps(_p):
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
    """工具必须把 episode 注入 build_normalize_prompt，避免 LLM 写错 E\\d+ 前缀（#574）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter2.txt").write_text("第二集开场", encoding="utf-8")

    async def fake_caps(_p):
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

    async def fake_caps(_p):
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

    async def fake_caps(_p):
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
# episode_planning — plan_episodes / replan_episodes 薄包装
# ---------------------------------------------------------------------------


def _fake_planner_cls(result: Any, captured: dict[str, Any] | None = None):
    """构造可注入的 EpisodePlanner 替身：create() 工厂 + plan/replan 返回预置结果。"""

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

        async def replan(self, from_episode, instructions, *, confirm_consumed=False):
            if captured is not None:
                captured["replan_args"] = (from_episode, instructions, confirm_consumed)
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


async def test_replan_episodes_passes_args_and_reports_stale(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[EpisodePlanSummary(episode=2, title="辞别下山", hook="甲", reading_units=700, ledger_status="stale")],
        cursor=None,
        stale_episodes=[2],
        settings_updated={"episode_target_units": 800},
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    out = await _call(
        mod.replan_episodes_tool(fake_ctx),
        {"from_episode": 2, "instructions": "每集短一点", "confirm_consumed": True},
    )

    assert out.get("is_error") is not True
    assert captured["replan_args"] == (2, "每集短一点", True)
    text = out["content"][0]["text"]
    assert "stale" in text
    assert "episode_target_units" in text


async def test_replan_episodes_includes_ledger_stats(fake_ctx: ToolContext, monkeypatch) -> None:
    """replan 是偏差修复的主要动作，返回一律附全局核对材料，闭合复核循环。"""
    from lib.episode_planner import EpisodePlanSummary, LedgerStats, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    stats = LedgerStats(total_episodes=3, smallest=[(3, 23), (2, 37), (1, 45)], median_units=37, target_units=None)
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=2, title="辞别下山", hook="甲", reading_units=700, ledger_status="planned")
        ],
        cursor=None,
        ledger_stats=stats,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result))
    out = await _call(
        mod.replan_episodes_tool(fake_ctx),
        {"from_episode": 2, "instructions": "第2集在下山处收尾"},
    )

    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "累计总集数：3" in text
    assert "第 3 集（约 23）" in text
    assert "有偏差须向用户明确说明" in text


async def test_replan_episodes_confirmation_required(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib.episode_planner import ReplanConfirmationRequired
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(ReplanConfirmationRequired(consumed_episodes=[2, 3])))
    out = await _call(mod.replan_episodes_tool(fake_ctx), {"from_episode": 2, "instructions": "重排"})

    assert out.get("is_error") is not True  # 预期内的流程出口，不是错误
    text = out["content"][0]["text"]
    assert "已消费" in text and "confirm_consumed" in text


async def test_replan_episodes_rejects_missing_instructions(fake_ctx: ToolContext) -> None:
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(mod.replan_episodes_tool(fake_ctx), {"from_episode": 2})
    assert out.get("is_error") is True


async def test_replan_episodes_rejects_string_confirm_consumed(fake_ctx: ToolContext) -> None:
    """confirm_consumed 是确认安全边界：非布尔值（如字符串 "false"）必须拒绝而非真值化。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(
        mod.replan_episodes_tool(fake_ctx),
        {"from_episode": 2, "instructions": "重排", "confirm_consumed": "false"},
    )
    assert out.get("is_error") is True
    assert "confirm_consumed" in out["content"][0]["text"]


async def test_replan_episodes_rejects_overlong_instructions(fake_ctx: ToolContext) -> None:
    """instructions 超长按参数错误拒绝。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    out = await _call(
        mod.replan_episodes_tool(fake_ctx),
        {"from_episode": 2, "instructions": "重" * (mod._MAX_INSTRUCTIONS_LEN + 1)},
    )
    assert out.get("is_error") is True
    assert "过长" in out["content"][0]["text"]


async def test_replan_episodes_accepts_boundary_length_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """instructions 恰好等于上限长度应被接受（覆盖 > 比较的差一边界）。"""
    from lib.episode_planner import EpisodePlanSummary, PlanResult
    from server.agent_runtime.sdk_tools import episode_planning as mod

    captured: dict[str, Any] = {}
    result = PlanResult(
        episodes=[
            EpisodePlanSummary(episode=2, title="辞别下山", hook="甲", reading_units=700, ledger_status="planned")
        ],
        cursor=None,
    )
    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(result, captured))
    text = "重" * mod._MAX_INSTRUCTIONS_LEN
    out = await _call(mod.replan_episodes_tool(fake_ctx), {"from_episode": 2, "instructions": text})

    assert out.get("is_error") is not True
    assert captured["replan_args"] == (2, text, False)


async def test_replan_episodes_rejects_non_integer_from_episode(fake_ctx: ToolContext) -> None:
    """from_episode 必须是 JSON 整数：布尔与字符串都拒绝。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    for bad in (True, "2"):
        out = await _call(
            mod.replan_episodes_tool(fake_ctx),
            {"from_episode": bad, "instructions": "重排"},
        )
        assert out.get("is_error") is True
        assert "from_episode" in out["content"][0]["text"]


async def test_replan_episodes_planner_value_error_not_mislabeled_as_param_error(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """重排器内部抛出的 ValueError（如供应商未配置）走通用工具错误，不被误标为参数错误。"""
    from server.agent_runtime.sdk_tools import episode_planning as mod

    monkeypatch.setattr(mod, "EpisodePlanner", _fake_planner_cls(ValueError("未找到可用的 text 供应商")))
    out = await _call(mod.replan_episodes_tool(fake_ctx), {"from_episode": 2, "instructions": "重排"})

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "未找到可用的 text 供应商" in text
    assert "参数错误" not in text  # 供应商未配置不是入参问题


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

    async def _fake_max_duration(_project: dict[str, Any]) -> int | None:
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


def _rv_caps(default=4, durations=(4, 6, 8), max_duration=12, max_refs=3):
    async def fake_caps(_p):
        return default, list(durations), max_duration, max_refs

    return fake_caps


async def test_fetch_reference_caps_with_fallback_clips_shot_durations_to_static_range(monkeypatch) -> None:
    """resolver 声明的 supported_durations 含 >15 的值时（如 vidu Q3 系列达 16、agnes 达 18），
    返回的单 shot 时长集合须与 REFERENCE_SHOT_DURATION_RANGE 求交集——否则 step1 允许 LLM 选中
    的 shot 时长会在 step2 读回校验（复用同一静态区间的 Shot 模型）时 fail-loud。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    class _FakeResolver:
        def __init__(self, _factory):
            pass

        async def video_capabilities_for_project(self, _project):
            return {"supported_durations": [1, 8, 16, 18], "max_duration": 18, "default_duration": 16}

    monkeypatch.setattr(mod, "ConfigResolver", _FakeResolver)

    default, durations, max_duration, max_refs = await mod._fetch_reference_caps_with_fallback({})

    assert durations == [1, 8]
    assert max_duration == 18  # 单 unit 总时长上限沿用 resolver 原始声明，不受单 shot 过滤影响
    assert default is None  # 16 已被过滤掉，非法 default 归 None
    assert max_refs is None


def _rv_generator_returning(units: list[dict], captured: dict[str, Any] | None = None):
    """构造返回指定 units JSON 的假 TextGenerator.create（可选捕获 task_type / project_name）。"""

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


def _rv_source(fake_ctx: ToolContext) -> None:
    src = fake_ctx.project_path / "source"
    src.mkdir(parents=True)
    (src / "episode_1.txt").write_text("张三在村口等人", encoding="utf-8")


async def test_split_reference_video_units_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "DRY RUN" in prompt_text
    # episode 注入 unit_id 前缀、资产候选与能力约束进 prompt
    assert "E1U" in prompt_text
    assert "张三" in prompt_text
    assert "12 秒" in prompt_text


async def test_split_reference_video_units_happy_derives_references(fake_ctx: ToolContext, monkeypatch) -> None:
    """happy path：结构化 step1 落盘，references 从 shot 文本 @ 引用机械派生（并集、首现顺序）；
    模型经文本管道按 SCRIPT 任务解析并携带 project_name 入账。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    captured: dict[str, Any] = {}
    units = [
        {
            "unit_id": "E1U01",
            "shots": [
                {"duration": 4, "text": "@[张三] 走向 @[村口]"},
                {"duration": 6, "text": "@[张三] 停下脚步"},
            ],
        }
    ]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units, captured))

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True, out

    step1_path = fake_ctx.project_path / "drafts" / "episode_1" / "step1_reference_units.json"
    assert step1_path.exists()
    saved = json.loads(step1_path.read_text(encoding="utf-8"))
    assert saved["units"][0]["references"] == [
        {"type": "character", "name": "张三"},
        {"type": "scene", "name": "村口"},
    ]
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


async def test_split_reference_video_units_rejects_unregistered_asset(fake_ctx: ToolContext, monkeypatch) -> None:
    """shot 文本引用未登记资产名 → fail-loud，不写盘（资产名引用完整性）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    units = [{"unit_id": "E1U01", "shots": [{"duration": 4, "text": "@[不存在的人] 出场"}]}]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units))

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "未登记" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_reference_units.json").exists()


async def test_split_reference_video_units_rejects_over_max_refs(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    units = [{"unit_id": "E1U01", "shots": [{"duration": 4, "text": "@[张三] 与 @[李四] 在 @[村口]"}]}]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps(max_refs=2))
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units))

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "references" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_reference_units.json").exists()


async def test_split_reference_video_units_rejects_over_max_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """单 shot 时长合法（枚举成员）但 unit 总时长超上限 → 工具后校验 fail-loud。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    units = [
        {
            "unit_id": "E1U01",
            "shots": [{"duration": 6, "text": "@[张三] 起身"}, {"duration": 6, "text": "@[张三] 出门"}],
        }
    ]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps(max_duration=8))
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units))

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "总时长" in out["content"][0]["text"]


async def test_split_reference_video_units_rejects_out_of_enum_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """本地校验复用动态 schema：超出 supported_durations 的 shot 时长被拦截，不落盘。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    units = [{"unit_id": "E1U01", "shots": [{"duration": 5, "text": "@[张三] 起身"}]}]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units))

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "step1 拆分内容结构校验失败" in out["content"][0]["text"]
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_reference_units.json").exists()


async def test_split_reference_video_units_rejects_empty_units(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning([]))

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert not (fake_ctx.project_path / "drafts" / "episode_1" / "step1_reference_units.json").exists()


async def test_split_reference_video_units_rejects_duplicate_unit_ids(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    unit = {"unit_id": "E1U01", "shots": [{"duration": 4, "text": "@[张三] 起身"}]}
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", _rv_caps())
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning([unit, dict(unit)]))

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    assert "unit_id 重复" in out["content"][0]["text"]


async def test_split_reference_video_units_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = split_reference_video_units_tool(fake_ctx)
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
