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
    generate_episode_script_tool,
    get_video_capabilities_tool,
    normalize_drama_script_tool,
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

    async def fake_caps(_project):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_video_caps", fake_caps)

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

    async def fake_caps(_project):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_video_caps", fake_caps)

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

    async def fake_caps(_project):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_video_caps", fake_caps)

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

    async def fake_caps(_project):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_video_caps", fake_caps)

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
    (drafts / "step1_segments.md").write_text("step1 content", encoding="utf-8")
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
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_segments.md").write_text("step1", encoding="utf-8")
    (project_path / "project.json").write_text(json.dumps({"content_mode": "narration"}), encoding="utf-8")

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


async def test_normalize_drama_script_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


async def test_normalize_drama_script_passes_project_name(fake_ctx: ToolContext, monkeypatch) -> None:
    """normalize_drama_script 必须把 ctx.project_name 透传给 create_text_backend_for_task，
    否则项目级 text_backend_script 覆盖会被 resolver 跳过（_resolve_text_backend 第 1 步要求 project_name）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p):
        return 4, [4, 6, 8]

    captured: dict[str, Any] = {}

    class _FakeBackend:
        async def generate(self, _req):
            class _R:
                text = "| 场景 ID | x |\n|---|---|\n| E1S01 | y |"
                input_tokens = 0
                output_tokens = 0

            return _R()

    async def fake_factory(task_type, project_name=None):
        captured["task_type"] = task_type
        captured["project_name"] = project_name
        return _FakeBackend()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod, "create_text_backend_for_task", fake_factory)

    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True, out
    assert captured["project_name"] == "demo"
