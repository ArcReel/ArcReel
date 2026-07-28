"""端到端测试：剧本/项目 JSON 编辑 MCP 工具（patch_episode_script / insert_segment /
remove_segment / split_segment / patch_project）。

用真实 ProjectManager 跑工具 handler → 编辑核心 → 写盘统一入口的完整路径，断言落盘结果与
错误信封（结构「不更坏」校验、upsert 校验真实生效），不 mock 私有方法。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.patch_episode_meta import patch_episode_meta_tool
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


def _scene(scene_id: str, duration: int = 8) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "duration_seconds": duration,
        "scene_type": "剧情",
        "characters_in_scene": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _drama_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "drama",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": [_scene("E1S01"), _scene("E1S02")],
    }


def _unit(unit_id: str) -> dict[str, Any]:
    shots = [{"duration": 3, "text": "镜头1"}, {"duration": 4, "text": "镜头2"}]
    return {
        "unit_id": unit_id,
        "shots": shots,
        "references": [],
        "duration_seconds": sum(s["duration"] for s in shots),
    }


def _reference_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": [_unit("E1U1"), _unit("E1U2")],
    }


def _ad_shot(shot_id: str, duration: int = 5) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播文案",
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _ad_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "ad",
        "shots": [_ad_shot("E1S01"), _ad_shot("E1S02")],
    }


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script("demo", _script(), "episode_1.json")
    return ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)


@pytest.fixture
def drama_ctx(tmp_path: Path) -> ToolContext:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="drama")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    pm.save_script("demo", _drama_script(), "episode_1.json")
    return ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)


@pytest.fixture
def ref_ctx(tmp_path: Path) -> ToolContext:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script("demo", _reference_script(), "episode_1.json")
    return ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)


@pytest.fixture
def ad_ctx(tmp_path: Path) -> ToolContext:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo", content_mode="ad")
    pm.create_project_metadata("demo", "Demo", "Anime", "ad")
    pm.save_script("demo", _ad_script(), "episode_1.json")
    return ToolContext(project_name="demo", projects_root=tmp_path, pm=pm)


async def _call(tool_obj, args: dict[str, Any]) -> dict[str, Any]:
    return await tool_obj.handler(args)


def _load(ctx: ToolContext) -> dict[str, Any]:
    return ctx.pm.load_script("demo", "episode_1.json")


def _text(out: dict[str, Any]) -> str:
    """从 tool 返回的 ``{"content": [{"type": "text", "text": ...}]}`` 中抽出文本。"""
    blocks = out.get("content") or []
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))


class TestPatchEpisodeScript:
    async def test_batch_multi_segment_multi_field(self, ctx: ToolContext) -> None:
        """一次调用改多分镜 × 多字段，全部落盘。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {
                "script": "episode_1.json",
                "edits": {
                    "E1S01": {"image_prompt.scene": "新场景一", "duration_seconds": 6},
                    "E1S02": {"video_prompt.action": "抬头"},
                },
            },
        )
        assert out.get("is_error") is not True
        saved = _load(ctx)["segments"]
        assert saved[0]["image_prompt"]["scene"] == "新场景一"
        assert saved[0]["duration_seconds"] == 6
        assert saved[1]["video_prompt"]["action"] == "抬头"

    async def test_single_edit_is_length_one_map(self, ctx: ToolContext) -> None:
        """单条编辑 = 长度 1 的 map（不再有 id/field/value 单条形态）。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S02": {"image_prompt.scene": "新场景"}}},
        )
        assert out.get("is_error") is not True
        assert _load(ctx)["segments"][1]["image_prompt"]["scene"] == "新场景"

    async def test_unknown_id_rolls_back_whole_batch(self, ctx: ToolContext) -> None:
        """一批里含未命中 id → 整批零落盘（同批的合法编辑也回滚），错误定位到该 id。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {
                "script": "episode_1.json",
                "edits": {
                    "E1S01": {"image_prompt.scene": "本应回滚"},
                    "E9": {"duration_seconds": 5},
                },
            },
        )
        assert out.get("is_error") is True
        text = _text(out)
        assert "E9" in text
        # 同批的合法编辑未落盘（all-or-nothing）
        assert _load(ctx)["segments"][0]["image_prompt"]["scene"] == "场景描述"

    async def test_invalid_value_rolls_back_whole_batch(self, ctx: ToolContext) -> None:
        """某条把合法剧本改非法（duration 越界）→ 写盘统一入口挡下，整批不落盘。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {
                "script": "episode_1.json",
                "edits": {
                    "E1S01": {"duration_seconds": 999},
                    "E1S02": {"image_prompt.scene": "本应回滚"},
                },
            },
        )
        assert out.get("is_error") is True
        saved = _load(ctx)["segments"]
        assert saved[0]["duration_seconds"] == 4  # 未落盘
        assert saved[1]["image_prompt"]["scene"] == "场景描述"  # 同批回滚

    async def test_error_localizes_scene_id_and_field(self, ctx: ToolContext) -> None:
        """字段路径不存在 → 错误精确指出触发的 scene_id + field。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {"image_prompt.nope.deep": "x"}}},
        )
        assert out.get("is_error") is True
        text = _text(out)
        assert "E1S01" in text
        assert "image_prompt.nope.deep" in text

    async def test_empty_edits_rejected(self, ctx: ToolContext) -> None:
        """空 edits map 被拒（对齐 patch_project 的非空映射校验）。"""
        out = await _call(patch_episode_script_tool(ctx), {"script": "episode_1.json", "edits": {}})
        assert out.get("is_error") is True

    async def test_empty_field_map_rejected(self, ctx: ToolContext) -> None:
        """某分镜的子映射为空 → 拒（禁止零信号成功）。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {}}},
        )
        assert out.get("is_error") is True
        assert _load(ctx)["segments"][0]["image_prompt"]["scene"] == "场景描述"

    async def test_reject_generated_assets(self, ctx: ToolContext) -> None:
        """禁改 generated_assets（逐字继承单编辑约束），整批不落盘。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {"generated_assets.status": "completed"}}},
        )
        assert out.get("is_error") is True

    async def test_reject_id_field(self, ctx: ToolContext) -> None:
        """禁改分镜 id 字段（逐字继承单编辑约束）。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {"segment_id": "E1S99"}}},
        )
        assert out.get("is_error") is True
        assert [s["segment_id"] for s in _load(ctx)["segments"]] == ["E1S01", "E1S02"]

    async def test_create_missing_optional_leaf(self, ctx: ToolContext) -> None:
        """叶子字段不存在可创建（补 LLM 漏写的 optional 字段 video_prompt.dialogue）。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {
                "script": "episode_1.json",
                "edits": {"E1S01": {"video_prompt.dialogue": [{"speaker": "甲", "line": "台词"}]}},
            },
        )
        assert out.get("is_error") is not True
        assert _load(ctx)["segments"][0]["video_prompt"]["dialogue"] == [{"speaker": "甲", "line": "台词"}]

    async def test_rejects_path_in_script_arg(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "../x.json", "edits": {"E1S01": {"duration_seconds": 5}}},
        )
        assert out.get("is_error") is True

    async def test_hallucinated_leaf_blocked_by_funnel(self, ctx: ToolContext) -> None:
        """中间路径存在、叶子被凭空创建的 hallucinated 字段（video_prompt.hallucinated_key）
        经写盘统一入口 extra='forbid' 结构校验拒写，不静默落盘。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {"video_prompt.hallucinated_key": "stray"}}},
        )
        assert out.get("is_error") is True
        assert "hallucinated_key" not in _load(ctx)["segments"][0]["video_prompt"]

    async def test_middle_path_typo_fail_loud(self, ctx: ToolContext) -> None:
        """中间路径拼错（image_prompt.scen 应为 .scene）→ fail-loud，错误定位到 id/field。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {"image_prompt.scen.x": "y"}}},
        )
        assert out.get("is_error") is True
        text = _text(out)
        assert "E1S01" in text and "image_prompt.scen.x" in text

    async def test_prompt_change_includes_regen_hint(self, ctx: ToolContext) -> None:
        """改了 image_prompt / video_prompt 后，返回文本聚合『须重新生成』提示。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {"image_prompt.scene": "新场景"}}},
        )
        assert out.get("is_error") is not True
        assert "重新生成" in _text(out)

    async def test_non_prompt_change_omits_regen_hint(self, ctx: ToolContext) -> None:
        """只改非 prompt 字段（duration_seconds）时不追加重生提示。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "edits": {"E1S01": {"duration_seconds": 5}}},
        )
        assert out.get("is_error") is not True
        assert "重新生成" not in _text(out)

    async def test_drama_mode_by_scene_id(self, drama_ctx: ToolContext) -> None:
        """drama 模式：按 scene_id 定位，批量改字段落盘。"""
        out = await _call(
            patch_episode_script_tool(drama_ctx),
            {"script": "episode_1.json", "edits": {"E1S02": {"image_prompt.scene": "剧集新场景"}}},
        )
        assert out.get("is_error") is not True
        assert _load(drama_ctx)["scenes"][1]["image_prompt"]["scene"] == "剧集新场景"

    async def test_reference_mode_by_unit_id(self, ref_ctx: ToolContext) -> None:
        """reference 模式：按 unit_id 定位，批量改字段落盘。"""
        out = await _call(
            patch_episode_script_tool(ref_ctx),
            {"script": "episode_1.json", "edits": {"E1U1": {"note": "单元备注"}}},
        )
        assert out.get("is_error") is not True
        assert _load(ref_ctx)["video_units"][0]["note"] == "单元备注"

    async def test_ad_mode_by_shot_id(self, ad_ctx: ToolContext) -> None:
        """ad 模式：按 shot_id 定位，批量改字段落盘。"""
        out = await _call(
            patch_episode_script_tool(ad_ctx),
            {"script": "episode_1.json", "edits": {"E1S02": {"voiceover_text": "新口播"}}},
        )
        assert out.get("is_error") is not True
        assert _load(ad_ctx)["shots"][1]["voiceover_text"] == "新口播"


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


class TestPatchEpisodeMeta:
    """patch_episode_meta：编辑剧本顶层 title，白名单兜底，写盘自动镜像到 project.json。"""

    async def test_set_title(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_meta_tool(ctx),
            {"script": "episode_1.json", "field": "title", "value": "新标题"},
        )
        assert out.get("is_error") is not True
        assert _load(ctx)["title"] == "新标题"
        # project.json 镜像同步（locked_script 退出经 sync_episode_from_script）
        episodes = ctx.pm.load_project("demo")["episodes"]
        entry = next(e for e in episodes if e["episode"] == 1)
        assert entry["title"] == "新标题"

    async def test_title_trimmed(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_meta_tool(ctx),
            {"script": "episode_1.json", "field": "title", "value": "  去空白  "},
        )
        assert out.get("is_error") is not True
        assert _load(ctx)["title"] == "去空白"

    async def test_empty_title_rejected(self, ctx: ToolContext) -> None:
        for blank in ("", "   ", "\t\n"):
            out = await _call(
                patch_episode_meta_tool(ctx),
                {"script": "episode_1.json", "field": "title", "value": blank},
            )
            assert out.get("is_error") is True
        assert _load(ctx)["title"] == "标题"  # 原值未改

    async def test_non_whitelist_field_rejected(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_meta_tool(ctx),
            {"script": "episode_1.json", "field": "episode", "value": 9},
        )
        assert out.get("is_error") is True
        assert _load(ctx)["episode"] == 1  # 未被改写

    async def test_non_string_value_rejected(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_meta_tool(ctx),
            {"script": "episode_1.json", "field": "title", "value": 123},
        )
        assert out.get("is_error") is True
        assert _load(ctx)["title"] == "标题"

    async def test_rejects_path_in_script_arg(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_episode_meta_tool(ctx),
            {"script": "../x.json", "field": "title", "value": "x"},
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

    async def test_invalid_entry_rejected_even_when_project_already_invalid(self, ctx: ToolContext) -> None:
        """「不更坏」error set diff 语义：项目本就脏（无关字段非法）时，本次 upsert 引入的
        新错误（如新 entry 缺 description）仍应被拒——单纯 `before_valid AND after.valid` 判定
        会让新错误 piggyback 通过，error set diff 才能堵这条旁路。"""
        # 让项目改前先脏（与资产无关的历史问题，如空 style）
        ctx.pm.update_project("demo", lambda p: p.update({"style": ""}))
        out = await _call(
            patch_project_tool(ctx),
            # 缺 description 的非法 entry，本次写入引入的「新错误」
            {"table": "scenes", "entries": {"空场景": {"voice_style": "x"}}},
        )
        assert out.get("is_error") is True
        # 不落盘：空场景没写入
        assert "空场景" not in ctx.pm.load_project("demo").get("scenes", {})

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

    async def test_entry_name_whitespace_normalized(self, ctx: ToolContext) -> None:
        """agent 传带前后空格的 name → strip 规范化后存储（避免按 name 查找因空格差异 mismatch）。"""
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"  李白  ": {"description": "白衣剑客"}}},
        )
        assert out.get("is_error") is not True
        chars = ctx.pm.load_project("demo")["characters"]
        assert "李白" in chars  # 规范化后存储
        assert "  李白  " not in chars

    async def test_blank_entry_name_rejected(self, ctx: ToolContext) -> None:
        """全空白或空 name fail-loud：避免把 \"\" / \"   \" 写成合法 entry key。"""
        for blank_name in ("", "   ", "\t\n"):
            out = await _call(
                patch_project_tool(ctx),
                {"table": "characters", "entries": {blank_name: {"description": "x"}}},
            )
            assert out.get("is_error") is True

    async def test_non_string_extra_field_rejected(self, ctx: ToolContext) -> None:
        """voice_style 等 extra_string_fields 须为字符串：agent 传 int / dict / list 会被守卫点拦下，
        否则下游把 reference_image 当路径拼接时会运行时崩。"""
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": 1}}},
        )
        assert out.get("is_error") is True
        assert "李白" not in ctx.pm.load_project("demo").get("characters", {})

    async def test_upsert_strips_sheet_and_unknown_fields(self, ctx: ToolContext) -> None:
        """least-privilege：agent 仅能改 description + spec.extra_string_fields。
        sheet 字段（系统生成的资产图路径）+ spec-undeclared key 均被静默丢弃，不让 agent
        覆写本不该碰的字段。"""
        # 先 upsert 一个干净 entry，再尝试用 patch 改 sheet（应被忽略）+ 加 unknown key
        await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": "豪放"}}},
        )
        # 模拟系统通过 _update_asset_sheet 写入 sheet 路径
        ctx.pm.update_project(
            "demo", lambda p: p["characters"]["李白"].update({"character_sheet": "characters/li_bai.png"})
        )

        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "改后描述",
                        "voice_style": "沉稳",
                        "character_sheet": "fake/agent_overwrite.png",  # 应被丢弃
                        "random_extra_field": "noise",  # 应被丢弃
                    }
                },
            },
        )
        assert out.get("is_error") is not True
        char = ctx.pm.load_project("demo")["characters"]["李白"]
        assert char["description"] == "改后描述"
        assert char["voice_style"] == "沉稳"
        assert char["character_sheet"] == "characters/li_bai.png"  # 系统字段未被 agent 覆写
        assert "random_extra_field" not in char  # spec 外字段不入库

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

    async def test_normalized_name_collision_fails_loud(self, ctx: ToolContext) -> None:
        """两个 raw key strip 后等价（如 "李白" 与 "  李白  "）→ fail-loud，避免后者
        silent overwrite 前者的 attrs；agent 应明确感知 collision 并去重。"""
        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "characters",
                "entries": {
                    "李白": {"description": "白衣剑客"},
                    "  李白  ": {"description": "白衣剑客v2"},
                },
            },
        )
        assert out.get("is_error") is True
        # 任何一个版本都不应入库（mutation 在校验阶段就 raise，不落盘）
        assert "李白" not in ctx.pm.load_project("demo").get("characters", {})

    async def test_upsert_strips_reference_image_field(self, ctx: ToolContext) -> None:
        """reference_image 是用户上传或系统生成的文件路径（与 sheet_field 同性质），
        agent_editable_extra_fields 不包含它——patch_project 应静默丢弃，不让 agent
        覆写用户已上传的角色参考图。更新走专用 API update_character_reference_image。
        validator 维度的 extra_string_fields 仍保留 reference_image 用于类型校验。"""
        # 先 upsert 一个干净 entry
        await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客", "voice_style": "豪放"}}},
        )
        # 模拟用户通过 WebUI 上传参考图
        ctx.pm.update_character_reference_image("demo", "李白", "characters/refs/li_bai.jpg")
        assert ctx.pm.load_project("demo")["characters"]["李白"]["reference_image"] == "characters/refs/li_bai.jpg"

        # agent 尝试改描述时顺带覆写 reference_image——应被丢弃
        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "改后描述",
                        "voice_style": "沉稳",
                        "reference_image": "",  # 应被白名单过滤掉
                    }
                },
            },
        )
        assert out.get("is_error") is not True
        char = ctx.pm.load_project("demo")["characters"]["李白"]
        assert char["description"] == "改后描述"
        assert char["voice_style"] == "沉稳"
        # 用户上传的 reference_image 不被 agent 覆写
        assert char["reference_image"] == "characters/refs/li_bai.jpg"

    async def test_product_upsert_selling_points_editable(self, ctx: ToolContext) -> None:
        """products 表对 agent 开放；selling_points 在可编辑白名单内（agent 起草、用户可改），
        新 entry 的列表字段按 spec 初始化。"""
        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "products",
                "entries": {"保温杯": {"description": "不锈钢保温杯", "selling_points": ["12 小时保温", "一键开盖"]}},
            },
        )
        assert out.get("is_error") is not True
        product = ctx.pm.load_project("demo")["products"]["保温杯"]
        assert product["description"] == "不锈钢保温杯"
        assert product["selling_points"] == ["12 小时保温", "一键开盖"]
        assert product["reference_images"] == []
        assert product["product_sheet"] == ""
        assert product["brand"] == ""

    async def test_product_upsert_strips_reference_images(self, ctx: ToolContext) -> None:
        """reference_images 是用户上传的原图路径列表（保真验收锚点），不在 agent 白名单——
        upsert 应静默丢弃且不覆写既有值，更新走专用上传 API。"""
        await _call(
            patch_project_tool(ctx),
            {"table": "products", "entries": {"保温杯": {"description": "不锈钢保温杯"}}},
        )
        ctx.pm.add_product_reference_image("demo", "保温杯", "products/refs/保温杯_1.jpg")

        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "products",
                "entries": {
                    "保温杯": {
                        "description": "改后描述",
                        "selling_points": ["双层真空"],
                        "reference_images": [],
                    }
                },
            },
        )
        assert out.get("is_error") is not True
        product = ctx.pm.load_project("demo")["products"]["保温杯"]
        assert product["description"] == "改后描述"
        assert product["selling_points"] == ["双层真空"]
        assert product["reference_images"] == ["products/refs/保温杯_1.jpg"]

    async def test_product_upsert_invalid_selling_points_blocked(self, ctx: ToolContext) -> None:
        """selling_points 须为字符串列表：非法类型被结构校验拦截，不落盘。"""
        out = await _call(
            patch_project_tool(ctx),
            {"table": "products", "entries": {"保温杯": {"description": "杯", "selling_points": "不是列表"}}},
        )
        assert out.get("is_error") is True
        assert "保温杯" not in ctx.pm.load_project("demo").get("products", {})

    async def test_response_distinguishes_added_and_merged(self, ctx: ToolContext) -> None:
        """工具返回文本应区分『新增 N 个 / 合并改字段 N 个』,让 agent 验证是否符合预期策略
        (如 analyze-assets subagent 应预期合并数=0,出现合并数说明遗漏了已存在过滤)。"""
        out1 = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        text1 = _text(out1)
        assert "新增" in text1 and "李白" in text1
        assert "合并" not in text1

        out2 = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "改后描述"}}},
        )
        text2 = _text(out2)
        assert "合并改字段" in text2 and "李白" in text2
        assert "新增" not in text2

    async def test_response_lists_dropped_non_allowed_fields(self, ctx: ToolContext) -> None:
        """工具返回文本应显式列出被白名单丢弃的字段(reference_image / sheet_field 等),
        让 LLM 知道为何这些字段没生效,不再重复尝试。"""
        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "白衣剑客",
                        "reference_image": "x.jpg",  # 系统管理,应被忽略
                        "character_sheet": "y.jpg",  # 资产流水线回写,应被忽略
                    }
                },
            },
        )
        text = _text(out)
        assert "reference_image" in text
        assert "character_sheet" in text
        assert "agent 可编辑范围" in text or "已忽略" in text

    async def test_existing_entry_with_only_dropped_fields_reports_noop(self, ctx: ToolContext) -> None:
        """已存在的 entry,agent 提交的全部字段都被白名单/legacy strip 丢空时,
        cleaned[name]={} → bucket.update({}) 是 no-op。工具应明确报『无可写字段已跳过』,
        不应误报『合并改字段 1 个』让 agent 以为有变更。"""
        # 先建一个干净 entry
        await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        # 再提交一个只有被丢字段的 patch(reference_image 系统管理 / type 历史字段)
        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "characters",
                "entries": {"李白": {"reference_image": "x.jpg", "type": "主角"}},
            },
        )
        assert out.get("is_error") is not True
        text = _text(out)
        # 不报 merged,应报 noop / 无可写字段
        assert "合并改字段" not in text
        assert "无可写字段已跳过" in text or "无变更" in text
        # 描述未被改写,仍为原值
        assert ctx.pm.load_project("demo")["characters"]["李白"]["description"] == "白衣剑客"

    async def test_response_lists_dropped_legacy_fields(self, ctx: ToolContext) -> None:
        """工具返回文本应显式列出被剔除的历史字段(type / importance),让 agent 不再发它们。"""
        out = await _call(
            patch_project_tool(ctx),
            {
                "table": "characters",
                "entries": {
                    "李白": {
                        "description": "白衣剑客",
                        "type": "主角",  # 历史字段,应被剔除
                        "importance": "high",  # 历史字段,应被剔除
                    }
                },
            },
        )
        text = _text(out)
        assert "type" in text
        assert "importance" in text
        assert "历史字段" in text or "已废弃" in text


class TestPatchProjectSettings:
    """patch_project 顶层 settings 分支:首期支持 episode_target_units 写入/清除/校验."""

    async def test_set_episode_target_units(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"episode_target_units": 1000}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["episode_target_units"] == 1000
        assert "已更新" in _text(out)

    async def test_clear_episode_target_units(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"settings": {"episode_target_units": 1000}})
        out = await _call(patch_project_tool(ctx), {"settings": {"episode_target_units": None}})
        assert out.get("is_error") is not True
        assert "episode_target_units" not in ctx.pm.load_project("demo")
        assert "已清除" in _text(out)

    async def test_noop_when_same_value(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"settings": {"episode_target_units": 800}})
        out = await _call(patch_project_tool(ctx), {"settings": {"episode_target_units": 800}})
        assert out.get("is_error") is not True
        assert "无变更" in _text(out)

    async def test_non_whitelist_field_rejected(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"arbitrary_field": 1}})
        assert out.get("is_error") is True
        assert "arbitrary_field" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("lang", ["zh", "en", "vi"])
    async def test_set_source_language_allowed_values(self, ctx: ToolContext, lang: str) -> None:
        """source_language 作为 user-confirmed 恢复通道(overview 失败/跳过时),enum 校验."""
        out = await _call(patch_project_tool(ctx), {"settings": {"source_language": lang}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["source_language"] == lang

    async def test_clear_source_language(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"settings": {"source_language": "en"}})
        out = await _call(patch_project_tool(ctx), {"settings": {"source_language": None}})
        assert out.get("is_error") is not True
        assert "source_language" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("bad", ["english", "ja", "ZH", "", 1, True, ["en"]])
    async def test_invalid_source_language_rejected(self, ctx: ToolContext, bad: Any) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"source_language": bad}})
        assert out.get("is_error") is True
        assert "source_language" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("bad_value", [0, -5, 1.5, True, "10.5", "10.0", "abc", ""])
    async def test_invalid_value_rejected(self, ctx: ToolContext, bad_value: Any) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"episode_target_units": bad_value}})
        assert out.get("is_error") is True
        assert "episode_target_units" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("key", ["episode_target_units", "planning_window_chars", "planning_max_episodes"])
    async def test_positive_int_setting_accepts_digit_string(self, ctx: ToolContext, key: str) -> None:
        """MCP object 入参无逐字段类型声明，模型常把数字加引号传入；数字字符串按落盘用 int 容忍。"""
        out = await _call(patch_project_tool(ctx), {"settings": {key: "10"}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")[key] == 10

    @pytest.mark.parametrize("key", ["planning_window_chars", "planning_max_episodes"])
    async def test_set_and_clear_planning_overrides(self, ctx: ToolContext, key: str) -> None:
        """分集规划的窗口字数 / 每批集数覆盖项：正整数写入，null 清除回内部默认。"""
        out = await _call(patch_project_tool(ctx), {"settings": {key: 12}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")[key] == 12
        out = await _call(patch_project_tool(ctx), {"settings": {key: None}})
        assert out.get("is_error") is not True
        assert key not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("key", ["planning_window_chars", "planning_max_episodes"])
    @pytest.mark.parametrize("bad_value", [0, -1, 2.5, True, "10.5", "10.0", "abc", ""])
    async def test_invalid_planning_override_rejected(self, ctx: ToolContext, key: str, bad_value: Any) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {key: bad_value}})
        assert out.get("is_error") is True
        assert key not in ctx.pm.load_project("demo")

    async def test_table_and_settings_together_rejected(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"x": {"description": "y"}}, "settings": {"episode_target_units": 1}},
        )
        assert out.get("is_error") is True

    async def test_neither_table_nor_settings_rejected(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {})
        assert out.get("is_error") is True

    async def test_empty_settings_rejected(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {}})
        assert out.get("is_error") is True

    async def test_legacy_upsert_path_still_works(self, ctx: ToolContext) -> None:
        """老 schema 回归:只传 table/entries 仍走 upsert 分支(向后兼容 8 处既有调用)."""
        out = await _call(
            patch_project_tool(ctx),
            {"table": "characters", "entries": {"李白": {"description": "白衣剑客"}}},
        )
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["characters"]["李白"]["description"] == "白衣剑客"


class TestPatchProjectNarrationSettings:
    """narration_voice / narration_speed 经 settings 白名单写入/清除/校验（项目级旁白覆盖）。"""

    async def test_set_narration_voice(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_voice": "Ethan"}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["narration_voice"] == "Ethan"
        assert "已更新" in _text(out)

    async def test_modify_narration_voice(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"settings": {"narration_voice": "Ethan"}})
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_voice": "Cherry"}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["narration_voice"] == "Cherry"

    async def test_clear_narration_voice(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"settings": {"narration_voice": "Ethan"}})
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_voice": None}})
        assert out.get("is_error") is not True
        assert "narration_voice" not in ctx.pm.load_project("demo")
        assert "已清除" in _text(out)

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n", 1, 1.5, True, ["Ethan"], {"id": "Ethan"}])
    async def test_invalid_narration_voice_rejected(self, ctx: ToolContext, bad: Any) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_voice": bad}})
        assert out.get("is_error") is True
        assert "narration_voice" not in ctx.pm.load_project("demo")

    @pytest.mark.parametrize("speed", [1.2, 0.5, 2, 1])
    async def test_set_narration_speed(self, ctx: ToolContext, speed: Any) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_speed": speed}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["narration_speed"] == speed

    async def test_narration_speed_accepts_numeric_string(self, ctx: ToolContext) -> None:
        """MCP object 入参无逐字段类型声明，模型常把数字加引号传入；有限数值字符串按落盘用 float 容忍。"""
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_speed": "1.5"}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["narration_speed"] == 1.5

    async def test_clear_narration_speed(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"settings": {"narration_speed": 1.2}})
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_speed": None}})
        assert out.get("is_error") is not True
        assert "narration_speed" not in ctx.pm.load_project("demo")
        assert "已清除" in _text(out)

    @pytest.mark.parametrize("bad", [0, -1.5, float("inf"), float("nan"), True, False, "fast", "", [1.2], 10**400])
    async def test_invalid_narration_speed_rejected(self, ctx: ToolContext, bad: Any) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"narration_speed": bad}})
        assert out.get("is_error") is True
        # 超出 float 范围的巨大整数同样收到清晰的校验文案，而非底层溢出信息
        assert "narration_speed 必须是正的有限数值" in _text(out)
        assert "narration_speed" not in ctx.pm.load_project("demo")

    async def test_one_invalid_field_rejects_whole_batch(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_project_tool(ctx),
            {"settings": {"narration_voice": "Ethan", "narration_speed": -1}},
        )
        assert out.get("is_error") is True
        project = ctx.pm.load_project("demo")
        assert "narration_voice" not in project
        assert "narration_speed" not in project

    async def test_resolver_uses_values_written_by_tool(self, ctx: ToolContext) -> None:
        """工具写入与生成端解析读的是同一份顶层字段:写入后 resolver 实际解析出覆盖值。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from lib.config.resolver import ConfigResolver
        from lib.db.base import Base

        out = await _call(
            patch_project_tool(ctx),
            {"settings": {"narration_voice": "Ethan", "narration_speed": 1.2}},
        )
        assert out.get("is_error") is not True
        project = ctx.pm.load_project("demo")

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            resolver = ConfigResolver(async_sessionmaker(engine, expire_on_commit=False))
            assert await resolver.resolve_narration_voice(project) == "Ethan"
            assert await resolver.resolve_narration_speed(project) == 1.2
        finally:
            await engine.dispose()


class TestPatchProjectOverview:
    """patch_project overview 分支：四字段白名单 merge 编辑，概述不存在时创建，三选一互斥。"""

    async def test_set_overview_fields(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_project_tool(ctx),
            {"overview": {"synopsis": "一句话", "genre": "悬疑", "theme": "复仇", "world_setting": "近未来"}},
        )
        assert out.get("is_error") is not True
        ov = ctx.pm.load_project("demo")["overview"]
        assert ov["synopsis"] == "一句话"
        assert ov["genre"] == "悬疑"
        assert ov["theme"] == "复仇"
        assert ov["world_setting"] == "近未来"
        assert "已更新" in _text(out)

    async def test_merge_preserves_untouched_fields(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"overview": {"synopsis": "原始梗概", "genre": "原题材"}})
        out = await _call(patch_project_tool(ctx), {"overview": {"genre": "悬疑"}})
        assert out.get("is_error") is not True
        ov = ctx.pm.load_project("demo")["overview"]
        assert ov["genre"] == "悬疑"
        assert ov["synopsis"] == "原始梗概"  # 未传字段保留

    async def test_creates_overview_when_absent(self, ctx: ToolContext) -> None:
        assert "overview" not in ctx.pm.load_project("demo")
        out = await _call(patch_project_tool(ctx), {"overview": {"synopsis": "新建概述"}})
        assert out.get("is_error") is not True
        assert ctx.pm.load_project("demo")["overview"]["synopsis"] == "新建概述"

    async def test_non_whitelist_key_rejected(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"overview": {"title": "x"}})
        assert out.get("is_error") is True
        assert "title" not in ctx.pm.load_project("demo").get("overview", {})

    async def test_non_string_value_rejected(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"overview": {"synopsis": 1}})
        assert out.get("is_error") is True
        assert "overview" not in ctx.pm.load_project("demo")

    async def test_empty_overview_rejected(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"overview": {}})
        assert out.get("is_error") is True

    async def test_noop_when_same_value(self, ctx: ToolContext) -> None:
        await _call(patch_project_tool(ctx), {"overview": {"synopsis": "同值"}})
        out = await _call(patch_project_tool(ctx), {"overview": {"synopsis": "同值"}})
        assert out.get("is_error") is not True
        assert "无变更" in _text(out)

    async def test_overview_with_settings_rejected(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_project_tool(ctx),
            {"overview": {"synopsis": "x"}, "settings": {"episode_target_units": 1}},
        )
        assert out.get("is_error") is True

    async def test_overview_with_table_rejected(self, ctx: ToolContext) -> None:
        out = await _call(
            patch_project_tool(ctx),
            {"overview": {"synopsis": "x"}, "table": "characters", "entries": {"a": {"description": "b"}}},
        )
        assert out.get("is_error") is True


class TestPatchProjectBriefSetting:
    """brief 是 ad 项目的创作诉求短文本，经 settings 白名单写入/清除；非 ad 项目拒绝。"""

    @pytest.fixture
    def ad_ctx(self, tmp_path: Path) -> ToolContext:
        pm = ProjectManager(str(tmp_path))
        pm.create_project("ad-demo", content_mode="ad")
        pm.create_project_metadata("ad-demo", "Ad Demo", "Realistic", "ad", target_duration=60)
        return ToolContext(project_name="ad-demo", projects_root=tmp_path, pm=pm)

    async def test_set_brief_on_ad_project(self, ad_ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ad_ctx), {"settings": {"brief": "突出 3 秒速干卖点"}})
        assert out.get("is_error") is not True
        assert ad_ctx.pm.load_project("ad-demo")["brief"] == "突出 3 秒速干卖点"

    async def test_clear_brief_on_ad_project(self, ad_ctx: ToolContext) -> None:
        await _call(patch_project_tool(ad_ctx), {"settings": {"brief": "x"}})
        out = await _call(patch_project_tool(ad_ctx), {"settings": {"brief": None}})
        assert out.get("is_error") is not True
        assert "brief" not in ad_ctx.pm.load_project("ad-demo")

    async def test_brief_rejected_on_non_ad_project(self, ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ctx), {"settings": {"brief": "x"}})
        assert out.get("is_error") is True
        assert "brief" not in ctx.pm.load_project("demo")

    async def test_non_string_brief_rejected(self, ad_ctx: ToolContext) -> None:
        out = await _call(patch_project_tool(ad_ctx), {"settings": {"brief": 42}})
        assert out.get("is_error") is True
        # 创建时写入的 brief=""（可空）不被非法写入污染
        assert ad_ctx.pm.load_project("ad-demo")["brief"] == ""
