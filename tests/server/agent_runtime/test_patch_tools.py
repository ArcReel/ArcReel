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


def _text(out: dict[str, Any]) -> str:
    """从 tool 返回的 ``{"content": [{"type": "text", "text": ...}]}`` 中抽出文本。"""
    blocks = out.get("content") or []
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))


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
        """把合法剧本改非法（duration 越界）→ 写盘统一入口「不更坏」语义当场挡下。"""
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

    async def test_patch_hallucinated_leaf_blocked_by_funnel(self, ctx: ToolContext) -> None:
        """_set_nested 单元层面允许任意叶子写入(为了让 agent 补 LLM 漏写的 optional 字段),
        但 lib/script_models.py 子模型(VideoPrompt / ImagePrompt / Composition 等)
        都加了 model_config = ConfigDict(extra="forbid"),写盘统一入口的「不更坏」校验
        会把 hallucinated 字段(如 video_prompt.hallucinated_key)列为 ValidationError 拒写。
        防止 LLM typo / hallucination 字段静默落盘 JSON 文件。
        """
        out = await _call(
            patch_episode_script_tool(ctx),
            {
                "script": "episode_1.json",
                "id": "E1S01",
                "field": "video_prompt.hallucinated_key",
                "value": "stray",
            },
        )
        assert out.get("is_error") is True
        # 校验未落盘:重新 load script 应不含 hallucinated_key
        assert "hallucinated_key" not in _load(ctx)["segments"][0]["video_prompt"]

    async def test_patch_image_prompt_scene_typo_blocked_by_funnel(self, ctx: ToolContext) -> None:
        """同款典型 typo 场景:agent 想写 image_prompt.scene 但拼成 .scen。
        _set_nested 在 dict 上加 'scen' 成功,_guard_no_worse 经 ImagePrompt 的
        extra="forbid" 拒写——agent 拿到结构错误明确知道是字段名错。"""
        out = await _call(
            patch_episode_script_tool(ctx),
            {"script": "episode_1.json", "id": "E1S01", "field": "image_prompt.scen", "value": "x"},
        )
        assert out.get("is_error") is True
        assert "scen" not in _load(ctx)["segments"][0]["image_prompt"]


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
