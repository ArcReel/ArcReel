"""剧本编辑核心（纯函数）测试。

只断言外部行为：喂入纯 dict + 操作，断言返回 dict 的数组/id/资产结果，或非法操作抛
`ScriptEditError`。逐条覆盖三种模式的 patch/insert/remove/split 与模式 dispatch，不 patch
私有方法。fixture 形态与 `tests/test_script_structure_validator.py` 一致。
"""

from __future__ import annotations

import pytest

from lib.script_editor import (
    ScriptEditError,
    insert_segment,
    patch_field,
    remove_segment,
    resolve_items,
    split_segment,
)


def _segment(segment_id: str = "E1S01", duration: int = 4) -> dict:
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
        "generated_assets": {"storyboard_image": "scripts/x.png", "status": "completed"},
    }


def _narration(segments: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "narration",
        "episode": 1,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": segments if segments is not None else [_segment("E1S01"), _segment("E1S02")],
    }


def _scene(scene_id: str = "E1S01", duration: int = 8) -> dict:
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
        "generated_assets": {"storyboard_image": "scripts/y.png"},
    }


def _drama(scenes: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "drama",
        "episode": 1,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": scenes if scenes is not None else [_scene("E1S01"), _scene("E1S02")],
    }


def _unit(unit_id: str = "E1U1", shots: list[dict] | None = None) -> dict:
    shots = shots if shots is not None else [{"duration": 3, "text": "镜头1"}, {"duration": 4, "text": "镜头2"}]
    return {
        "unit_id": unit_id,
        "shots": shots,
        "references": [],
        "duration_seconds": sum(s["duration"] for s in shots),
        "transition_to_next": "cut",  # 对齐 Pydantic 默认；剧本经 model_dump 后该字段总会出现
        "generated_assets": {"video_clip": "scripts/z.mp4"},
    }


def _reference(units: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "episode": 1,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": units if units is not None else [_unit("E1U1"), _unit("E1U2")],
    }


class TestResolveItems:
    def test_narration(self):
        items, id_field, kind = resolve_items(_narration())
        assert id_field == "segment_id"
        assert kind == "segments"
        assert len(items) == 2

    def test_drama(self):
        _items, id_field, kind = resolve_items(_drama())
        assert id_field == "scene_id"
        assert kind == "scenes"

    def test_reference_data_shape_picks_video_units(self):
        # video_units 唯一存在(无 segments/scenes)时走 video_units——按数据形状路由,
        # 与 generation_mode / content_mode 标记无关。
        _items, id_field, kind = resolve_items(_reference())
        assert id_field == "unit_id"
        assert kind == "video_units"

    def test_partial_migration_data_shape_wins_over_generation_mode(self):
        # partial migration 中间态:generation_mode 改成了 reference_video 但数据还在 segments,
        # 数据形状优先让 agent 仍能通过 MCP 工具编辑 segments(旧版让 generation_mode 单向赢
        # 会导致 resolve_items 返回 [],按 id 编辑都报"未找到",整集脚本对所有工具不可触达)。
        script = {
            "title": "标题",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episode": 1,
            "summary": "摘要",
            "novel": {"title": "小说", "chapter": "第一章"},
            "segments": [_segment("E1S01")],
        }
        items, id_field, kind = resolve_items(script)
        assert kind == "segments"
        assert id_field == "segment_id"
        assert len(items) == 1
        assert items[0]["segment_id"] == "E1S01"

    def test_returned_list_is_live_reference(self):
        script = _narration()
        items, _id, _kind = resolve_items(script)
        items.append(_segment("E1S03"))
        assert len(script["segments"]) == 3

    def test_stray_video_units_do_not_hijack_storyboard_script(self):
        # 历史脏数据：storyboard 脚本被误塞游离 video_units（无 generation_mode/content_mode）。
        # video_units 与 segments 并存时不认定为 reference，编辑/metadata 仍作用于真实 segments。
        script = {
            "segments": [_segment("E1S01"), _segment("E1S02")],
            "video_units": [{"unit_id": "E1U1", "generated_assets": {"status": "pending"}}],
        }
        items, id_field, kind = resolve_items(script)
        assert kind == "segments"
        assert id_field == "segment_id"
        assert len(items) == 2

    def test_bare_video_units_without_segments_is_reference(self):
        # video_units 为唯一结构（无 segments/scenes、无显式 mode）→ 仍判为 reference
        script = {"video_units": [{"unit_id": "E1U1"}]}
        _items, id_field, kind = resolve_items(script)
        assert kind == "video_units"
        assert id_field == "unit_id"

    def test_missing_key_is_empty_list(self):
        # 内容数组键缺失 → 空列表（合法的「空草稿」），不报错
        items, _id, kind = resolve_items({"content_mode": "narration"})
        assert kind == "segments"
        assert items == []

    def test_non_list_items_fail_loud(self):
        # 键存在但类型非 list（数据损坏）→ fail-loud，而非静默降级为 []
        with pytest.raises(ScriptEditError):
            resolve_items({"content_mode": "narration", "segments": "oops"})

    def test_present_but_null_fails_loud(self):
        # 键存在但值为 null（损坏数据）→ fail-loud，不与「键缺失」混为空草稿
        with pytest.raises(ScriptEditError):
            resolve_items({"content_mode": "narration", "segments": None})


class TestPatchField:
    def test_patch_top_level_field(self):
        script = patch_field(_narration(), "E1S02", "duration_seconds", 9)
        assert script["segments"][1]["duration_seconds"] == 9

    def test_patch_nested_field(self):
        script = patch_field(_narration(), "E1S01", "image_prompt.scene", "新场景")
        assert script["segments"][0]["image_prompt"]["scene"] == "新场景"

    def test_patch_drama_by_scene_id(self):
        script = patch_field(_drama(), "E1S02", "scene_type", "空镜")
        assert script["scenes"][1]["scene_type"] == "空镜"

    def test_patch_reference_unit_field(self):
        script = patch_field(_reference(), "E1U2", "transition_to_next", "fade")
        assert script["video_units"][1]["transition_to_next"] == "fade"

    def test_patch_unknown_leaf_field_succeeds_at_set_nested_layer(self):
        # _set_nested 单元层面允许叶子写入——dict 操作不查 schema。
        # 但这里写的是 video_prompt.note(VideoPrompt 实际无 note 字段),
        # 是个 hallucinated 字段——在 _set_nested 这层确实写进 dict,但经
        # _write_script_unlocked 的 _guard_no_worse 会被 Pydantic extra="forbid" 拒,
        # 真实工具链不允许落盘(见 test_patch_unknown_leaf_blocked_at_write_throat)。
        script = patch_field(_narration(), "E1S01", "video_prompt.note", "新增备注")
        assert script["segments"][0]["video_prompt"]["note"] == "新增备注"

    def test_patch_optional_leaf_present_in_schema_succeeds(self):
        # 合法的「补 LLM 漏写的 optional 字段」:NarrationSegment.note 是 schema 内的
        # SkipJsonSchema[str | None] 字段,允许通过 patch 补;此路径既在 _set_nested
        # 写入成功,也能通过 _guard_no_worse 校验(字段是合法 schema 字段)。
        # 但 _narration() fixture 默认带 note=None,需要先 strip 再测「补」语义——
        # 退而求其次,改测「写入 schema 合法字段」本身。
        script = _narration()
        # 删 segment.note 模拟 LLM 漏写
        for seg in script["segments"]:
            seg.pop("note", None)
        script = patch_field(script, "E1S01", "note", "补全的备注")
        assert script["segments"][0]["note"] == "补全的备注"

    def test_patch_unknown_id_raises(self):
        with pytest.raises(ScriptEditError):
            patch_field(_narration(), "E9S99", "duration_seconds", 9)

    def test_patch_generated_assets_rejected(self):
        with pytest.raises(ScriptEditError):
            patch_field(_narration(), "E1S01", "generated_assets.status", "completed")

    @pytest.mark.parametrize("id_field", ["segment_id", "scene_id", "unit_id"])
    def test_patch_id_field_rejected(self, id_field):
        # 三类 id 字段（segment/scene/unit）均不可直改：id 由 insert/split 派生，agent 改 id
        # 会让其他依赖 id 定位的 helper 回写到错误分镜，或产生重复 id 歧义。
        with pytest.raises(ScriptEditError, match="不可改分镜 id"):
            patch_field(_narration(), "E1S01", id_field, "X")

    def test_patch_does_not_touch_generated_assets(self):
        script = patch_field(_narration(), "E1S01", "duration_seconds", 7)
        assert script["segments"][0]["generated_assets"]["status"] == "completed"

    def test_patch_missing_parent_path_raises(self):
        with pytest.raises(ScriptEditError):
            patch_field(_narration(), "E1S01", "no_such.deep", 1)


class TestInsertSegment:
    def test_insert_after_assigns_unique_suffixed_id_at_right_position(self):
        script = insert_segment(_narration(), "E1S01", _segment("IGNORED"))
        ids = [s["segment_id"] for s in script["segments"]]
        assert ids == ["E1S01", "E1S01_1", "E1S02"]

    def test_insert_clears_generated_assets(self):
        script = insert_segment(_narration(), "E1S01", _segment("X"))
        assert script["segments"][1]["generated_assets"] == {}

    def test_insert_id_avoids_collision(self):
        seg = _segment("E1S01_1")
        script = insert_segment(_narration([_segment("E1S01"), seg]), "E1S01", _segment("X"))
        ids = [s["segment_id"] for s in script["segments"]]
        assert ids == ["E1S01", "E1S01_2", "E1S01_1"]

    def test_insert_anchor_already_suffixed_flattens_subindex(self):
        # 锚点本身已含子序号（E1S01_1）→ 新 id 取 stem `E1S01` + 下一个空闲子序号，
        # 不产生 `E1S01_1_1` 这种多层后缀（违反 data_validator.ID_PATTERN）。
        script = insert_segment(_narration([_segment("E1S01"), _segment("E1S01_1")]), "E1S01_1", _segment("X"))
        ids = [s["segment_id"] for s in script["segments"]]
        # 跳过已占用的 E1S01_1，得到 E1S01_2，仍是合法单层后缀
        assert ids == ["E1S01", "E1S01_1", "E1S01_2"]

    def test_insert_unknown_anchor_raises(self):
        with pytest.raises(ScriptEditError):
            insert_segment(_narration(), "E9S99", _segment("X"))

    def test_insert_reference_unit(self):
        script = insert_segment(_reference(), "E1U1", _unit("X"))
        ids = [u["unit_id"] for u in script["video_units"]]
        assert ids == ["E1U1", "E1U1_1", "E1U2"]


class TestRemoveSegment:
    def test_remove_by_id(self):
        script = remove_segment(_narration(), "E1S01")
        assert [s["segment_id"] for s in script["segments"]] == ["E1S02"]

    def test_remove_does_not_renumber_others(self):
        script = remove_segment(_narration([_segment("E1S01"), _segment("E1S02"), _segment("E1S03")]), "E1S02")
        assert [s["segment_id"] for s in script["segments"]] == ["E1S01", "E1S03"]

    def test_remove_unknown_id_raises(self):
        with pytest.raises(ScriptEditError):
            remove_segment(_narration(), "E9S99")


class TestSplitSegment:
    def test_split_keeps_first_id_and_suffixes_rest(self):
        parts = [_segment("a"), _segment("b"), _segment("c")]
        script = split_segment(_narration(), "E1S01", parts)
        ids = [s["segment_id"] for s in script["segments"]]
        assert ids == ["E1S01", "E1S01_1", "E1S01_2", "E1S02"]

    def test_split_keeps_anchor_assets_clears_new_parts(self):
        # 锚点(parts[0],保留原 id)的 generated_assets 不动,与 insert_segment 的「锚点资产
        # 不动」语义对齐;其余 parts(新派生 id)清空 generated_assets 退回 pending 待重生。
        # 即便 agent 在 parts[0] 自带了 generated_assets 也以原分镜实际值为准,不让 agent
        # 凭空写资产路径。
        anchor_assets = _narration()["segments"][0]["generated_assets"]
        script = split_segment(_narration(), "E1S01", [_segment("a"), _segment("b")])
        assert script["segments"][0]["generated_assets"] == anchor_assets
        assert script["segments"][1]["generated_assets"] == {}

    def test_split_requires_at_least_two_parts(self):
        with pytest.raises(ScriptEditError):
            split_segment(_narration(), "E1S01", [_segment("a")])

    def test_split_unknown_id_raises(self):
        with pytest.raises(ScriptEditError):
            split_segment(_narration(), "E9S99", [_segment("a"), _segment("b")])

    def test_split_reference_units_act_on_video_units(self):
        anchor_assets = _reference()["video_units"][0]["generated_assets"]
        script = split_segment(_reference(), "E1U1", [_unit("a"), _unit("b")])
        ids = [u["unit_id"] for u in script["video_units"]]
        assert ids == ["E1U1", "E1U1_1", "E1U2"]
        # 锚点 video_unit 保留原 generated_assets,新派生 unit 清空
        assert script["video_units"][0]["generated_assets"] == anchor_assets
        assert script["video_units"][1]["generated_assets"] == {}

    def test_split_warns_when_anchor_assets_dirty_non_dict(self, caplog):
        """锚点 generated_assets 形态异常(非 dict 的脏数据)时退化为空 dict,
        但必须 warning——符合 ADR-0003 增补「禁止零信号成功」原则。anchor_assets is None
        视为合法初始态不 warn,只对 list / str / int 等真正脏数据 warn。"""
        import logging

        # 脏数据:generated_assets 是 list 而非 dict
        script_dirty = _narration([_segment("E1S01"), _segment("E1S02")])
        script_dirty["segments"][0]["generated_assets"] = ["unexpected_list_form"]

        with caplog.at_level(logging.WARNING, logger="lib.script_editor"):
            split_segment(script_dirty, "E1S01", [_segment("a"), _segment("b")])

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("E1S01" in m and "list" in m for m in warnings), warnings

    def test_split_no_warn_when_anchor_assets_none(self, caplog):
        """anchor generated_assets is None / 缺失是合法初始态(未生成),不应 warning。"""
        import logging

        script_clean = _narration([_segment("E1S01"), _segment("E1S02")])
        script_clean["segments"][0].pop("generated_assets", None)  # 模拟初始态缺失

        with caplog.at_level(logging.WARNING, logger="lib.script_editor"):
            split_segment(script_clean, "E1S01", [_segment("a"), _segment("b")])

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert not warnings, f"unexpected warnings: {warnings}"
