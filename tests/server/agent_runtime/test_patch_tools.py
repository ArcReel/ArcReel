"""端到端测试：剧本/项目 JSON 编辑 MCP 工具（patch_episode_script / insert_segment /
remove_segment / split_segment / patch_project）。

用真实 ProjectManager 跑工具 handler → 编辑核心 → 写盘咽喉的完整路径，断言落盘结果与
错误信封（结构「不更坏」校验、upsert 校验真实生效），不 mock 私有方法。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.patch_project import patch_project_tool
from server.agent_runtime.sdk_tools.patch_script import (
    insert_segment_tool,
    patch_episode_script_tool,
    remove_segment_tool,
    split_segment_tool,
)


def _segment(segment_id: str, duration: int = 4) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "duration_seconds": duration,
        "novel_text": "原文",
        "characters_in_segment": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": [_segment("E1S01"), _segment("E1S02")],
    }


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script("demo", _script(), "episode_1.json")
    return ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)


async def _call(tool_obj, args: dict[str, Any]) -> dict[str, Any]:
    return await tool_obj.handler(args)


def _load(ctx: ToolContext) -> dict[str, Any]:
    return ctx.pm.load_script("demo", "episode_1.json")


class TestPatchEpisodeScript:
    async def test_patch_nested_field(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "id": "E1S02", "field": "image_prompt.scene", "value": "新场景"},
        )
        assert out.get("is_error") is not True
        assert _load(ctx)["segments"][1]["image_prompt"]["scene"] == "新场景"

    async def test_patch_unknown_id_errors(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "id": "E9", "field": "duration_seconds", "value": 5},
        )
        assert out.get("is_error") is True

    async def test_patch_to_invalid_blocked_by_funnel(self, ctx: ToolContext) -> None:
        """把合法剧本改非法（duration 越界）→ 写盘咽喉「不更坏」语义当场挡下。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "id": "E1S01", "field": "duration_seconds", "value": 999},
        )
        assert out.get("is_error") is True
        assert _load(ctx)["segments"][0]["duration_seconds"] == 4  # 未落盘

    async def test_patch_rejects_path_in_script_arg(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "../x.json", "id": "E1S01", "field": "duration_seconds", "value": 5},
        )
        assert out.get("is_error") is True


class TestInsertRemoveSplit:
    async def test_insert_adds_at_position(self, ctx: ToolContext) -> None:
        out = await _call(
            insert_segment_tool(ctx),
            {"script": "episode_1.json", "after_id": "E1S01", "item": _segment("IGN")},
        )
        assert out.get("is_error") is not True
        ids = [s["segment_id"] for s in _load(ctx)["segments"]]
        assert ids == ["E1S01", "E1S01_1", "E1S02"]

    async def test_remove_by_id(self, ctx: ToolContext) -> None:
        out = await _call(remove_segment_tool(ctx), {"script": "episode_1.json", "id": "E1S01"})
        assert out.get("is_error") is not True
        assert [s["segment_id"] for s in _load(ctx)["segments"]] == ["E1S02"]

    async def test_split_keeps_first_id_clears_assets(self, ctx: ToolContext) -> None:
        # part 自带已生成资产，验证 split 改变分镜身份后会清空它（旧资产无合理归属）
        part_a = _segment("a")
        part_a["generated_assets"] = {"storyboard_image": "stale.png", "status": "completed"}
        out = await _call(
            split_segment_tool(ctx),
            {"script": "episode_1.json", "id": "E1S01", "parts": [part_a, _segment("b")]},
        )
        assert out.get("is_error") is not True
        saved = _load(ctx)["segments"]
        ids = [s["segment_id"] for s in saved]
        assert ids == ["E1S01", "E1S01_1", "E1S02"]
        assert not saved[0].get("generated_assets")
        assert not saved[1].get("generated_assets")

    async def test_split_too_few_parts_errors(self, ctx: ToolContext) -> None:
        out = await _call(
            split_segment_tool(ctx),
            {"script": "episode_1.json", "id": "E1S01", "parts": [_segment("a")]},
        )
        assert out.get("is_error") is True


class TestPatchProject:
    async def test_add_new_character(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": "豪放"}}},
        )
        assert out.get("is_error") is not True
        chars = ctx.pm.load_project("demo")["characters"]
        assert chars["李白"]["description"] == "白衣剑客"
        assert chars["李白"]["voice_style"] == "豪放"

    async def test_modify_existing_character_merges_fields(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"table": "characters", "entries": {"李白": {"description": "剑客"}}})
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "改后描述"}}},
        )
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["characters"]["李白"]["description"] == "改后描述"

    async def test_invalid_entry_blocked_and_not_written(self, ctx: ToolContext) -> None:
        """缺 description 的资产结构非法 → 校验失败、不落盘。"""
        out = await _call(
            patch_project_tool(ctx),
            {"table": "scenes", "entries": {"空场景": {"voice_style": "x"}}},
        )
        assert out.get("is_error") is True
        assert "空场景" not in ctx.pm.load_project("demo").get("scenes", {})

    async def test_unknown_table_errors(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"table": "weapons", "entries": {"剑": {"description": "x"}}})
        assert out.get("is_error") is True

    async def test_upsert_allowed_when_project_already_invalid(self, ctx: ToolContext) -> None:
        """「不更坏」：项目本就含与资产无关的历史非法（空 style）时，patch_project 仍应成功——
        否则带历史脏数据的项目会整条编辑路径不可用。"""
        ctx.pm.update_project("demo", lambda p: p.update({"style": ""}))
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        assert out.get("is_error") is not True
        assert "李白" in ctx.pm.load_project("demo").get("characters", {})

    async def test_non_string_description_rejected(self, ctx: ToolContext) -> None:
        """description 必须是非空字符串：agent 误传数字（如 LLM 把"1"输出成 int）
        会让原 truthy 校验放行、错误数据作为合法资产落盘——守卫点须 fail-loud。"""
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"阿青": {"description": 1}}},
        )
        assert out.get("is_error") is True
        assert "阿青" not in ctx.pm.load_project("demo").get("characters", {})

    async def test_upsert_fails_loud_when_bucket_not_dict(self, ctx: ToolContext) -> None:
        """bucket_key 已存在却非 dict（历史脏数据，如 list）→ fail-loud，
        而非在 bucket.get 处抛含糊的 AttributeError。"""
        ctx.pm.update_project("demo", lambda p: p.update({"characters": []}))
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        assert out.get("is_error") is True
