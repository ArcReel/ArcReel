"""Tests for open_step1_for_edit."""

from __future__ import annotations

import json

import pytest

from lib import script_review
from lib.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_STEP1,
    QUARANTINE_KIND_NARRATION_STEP1,
    QUARANTINE_KIND_STEP1,
    write_quarantine,
)
from server.agent_runtime.sdk_tools._context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _RV_NOVEL,
    _derived_reference_names,
    _drama_project,
    _drama_quarantine_path,
    _drama_scene,
    _drama_step1_path,
    _nr_quarantine_path,
    _nr_segment,
    _nr_source,
    _nr_step1_path,
    _open_drama_for_edit,
    _open_for_edit,
    _open_nr_for_edit,
    _promote,
    _promote_drama,
    _promote_nr,
    _read_drama_quarantine,
    _read_nr_quarantine,
    _read_rv_quarantine,
    _run_rv_split,
    _rv_project,
    _rv_quarantine_path,
    _rv_saved_unit,
    _rv_source,
    _rv_step1_path,
    _rv_unit,
    _write_drama_step1,
    _write_nr_step1,
    _write_rv_step1,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")

# ---------------------------------------------------------------------------
# open_step1_for_edit
# ---------------------------------------------------------------------------


async def test_open_step1_for_edit_returns_flat_draft_structure(fake_ctx: ToolContext) -> None:
    """取回的是扁平草稿结构，不装派生物：Agent 改的是引用语法正文 / 原文锚 / 时长，
    unit_id 由晋升时按数组序号重新派生，放进草稿等于给漂移开口子。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身\n@[张三] 走向 @[村口]")])

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
    assert unit["text"] == "@[张三] 起身\n@[张三] 走向 @[村口]"


async def test_open_step1_for_edit_leaves_official_file_untouched(fake_ctx: ToolContext) -> None:
    """取回只是开编辑工位，正式文件一步不动——改动落回正式文件只发生在持锁的晋升侧。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    before = _rv_step1_path(fake_ctx).read_text(encoding="utf-8")

    await _open_for_edit(fake_ctx)

    assert _rv_step1_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_open_step1_for_edit_round_trips_through_promote(fake_ctx: ToolContext, monkeypatch) -> None:
    """情况 B 的完整闭环：取回 → 改草稿 → 晋升。改动经晋升侧的持锁写盘落回正式文件。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["text"] == "@[张三] 在 @[村口] 出场"
    assert _derived_reference_names(fake_ctx, saved["units"][0]["text"]) == ["张三", "村口"]


async def test_open_step1_for_edit_refuses_to_clobber_existing_draft(fake_ctx: ToolContext, monkeypatch) -> None:
    """已有草稿在场时不覆盖：那份草稿可能已含 Agent 未晋升的修改（或本就是待修复草稿），
    拿正式文件盖过去等于抹掉它手上的工作。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    before = _rv_quarantine_path(fake_ctx).read_text(encoding="utf-8")
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert _rv_quarantine_path(fake_ctx).read_text(encoding="utf-8") == before
    assert "validate_and_promote_draft" in out["content"][0]["text"]


async def test_open_step1_for_edit_without_official_file(fake_ctx: ToolContext) -> None:
    """没有正式 step1 时指回首次拆分工具，而不是开一份空草稿让 Agent 手写整集。"""
    _rv_source(fake_ctx)

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert "split_reference_video_units" in out["content"][0]["text"]
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_open_step1_for_edit_keeps_malformed_duration_verbatim(fake_ctx: ToolContext) -> None:
    """盘上 unit 的字段类型不符时原样带进草稿，不归一化成合法值：``8.0`` 被改写成 ``0``
    后，Agent 从草稿里看到的是一个它没写过的时长，晋升报告说「时长不在档位内」也对不上
    盘上的原值。原样带过则由晋升侧 schema 逐条报告，Agent 看得见错在哪。"""
    _rv_source(fake_ctx)
    unit = _rv_saved_unit("@[张三] 起身")
    unit["duration_seconds"] = 8.0
    _write_rv_step1(fake_ctx, [unit])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    assert _read_rv_quarantine(fake_ctx)["content"]["units"][0]["duration_seconds"] == 8.0


async def test_open_step1_for_edit_keeps_malformed_non_dict_unit_slot(fake_ctx: ToolContext) -> None:
    """盘上 units 混入非 dict 元素时不能直接丢弃：跳过会让草稿数组比正式文件短一个，若剩余
    unit 都能过校验，晋升会悄悄覆盖正式文件、丢失这个 unit 而无人知晓。留空占位在原数组
    位置，让晋升侧 schema 判它结构非法、逐条报出。"""
    _rv_source(fake_ctx)
    good_unit = _rv_saved_unit("@[张三] 起身")
    path = _rv_step1_path(fake_ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"units": [good_unit, "不是对象"]}, ensure_ascii=False), encoding="utf-8")

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    units = _read_rv_quarantine(fake_ctx)["content"]["units"]
    assert len(units) == 2
    assert units[1] == {"duration_seconds": None, "source_text": "", "text": ""}


async def test_open_step1_for_edit_rejects_missing_source_without_side_effect(
    fake_ctx: ToolContext,
) -> None:
    """`source` 指向不存在的文件时不落盘草稿：草稿一旦创建就把这个坏路径记进 meta.source，
    晋升时 `_load_novel_source` 会反复报错，而草稿在场又挡住重新取回改正 source，Agent
    会卡在一个自己改不动的死角。校验失败时不产生持久副作用，Agent 改对参数重试即可。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    out = await _open_for_edit(fake_ctx, source="source/episode_不存在.txt")

    assert out.get("is_error") is True
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_open_step1_for_edit_rejects_non_reference_episode(fake_ctx: ToolContext) -> None:
    """切走参考路径的集不给编辑：盘上的 step1 与该集此刻的生成路径无关。与晋升工具同一判据。"""
    _rv_source(fake_ctx)
    _rv_project(fake_ctx, generation_mode="image_to_video")
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_open_step1_for_edit_records_base_fingerprint(fake_ctx: ToolContext) -> None:
    """取回时把正式文件此刻的内容指纹记进 meta.base_fingerprint，供晋升前基线比对。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    meta = _read_rv_quarantine(fake_ctx)["meta"]
    assert meta["base_fingerprint"] == script_review.content_fingerprint(_rv_step1_path(fake_ctx))


async def test_open_step1_for_edit_returns_drama_scenes(fake_ctx: ToolContext) -> None:
    """drama 取回的草稿装分镜内容表，正式文件一步不动——写盘只发生在持锁的晋升侧。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene(needs_replan=True)])
    before = _drama_step1_path(fake_ctx).read_text(encoding="utf-8")

    out = await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.get("is_error") is not True, out
    envelope = _read_drama_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_DRAMA_STEP1
    assert envelope["violations"] == []
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    assert envelope["meta"]["base_fingerprint"]
    scene = envelope["content"]["scenes"][0]
    # needs_replan 按台词准入派生，取回时剥掉：留在草稿里 Agent 会当成可手写字段去改，
    # 而晋升侧无论如何都按现值重派生，两者不一致只会误导。
    assert "needs_replan" not in scene
    assert scene["scene_description"] == "阿离站在山门前。"
    assert _drama_step1_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_open_step1_for_edit_drama_round_trips_through_promote(fake_ctx: ToolContext, monkeypatch) -> None:
    """完整闭环：取回 → 改草稿 → 晋升。改动经持锁写盘落回正式文件，派生字段按新内容重算。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])

    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["scene_description"] = "阿离推开山门。"
    _drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_drama(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert not _drama_quarantine_path(fake_ctx).exists()
    saved = json.loads(_drama_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["scene_description"] == "阿离推开山门。"


async def test_open_step1_for_edit_refuses_to_clobber_existing_drama_draft(fake_ctx: ToolContext) -> None:
    """已有草稿在场时不覆盖：那份草稿可能已含未晋升的修改，出路是继续改它再晋升。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])
    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["scene_description"] = "未晋升的修改。"
    _drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.get("is_error") is True
    assert "validate_and_promote_draft" in out["content"][0]["text"]
    assert _read_drama_quarantine(fake_ctx)["content"]["scenes"][0]["scene_description"] == "未晋升的修改。"


async def test_open_step1_for_edit_rejects_variant_without_draft_channel(fake_ctx: ToolContext) -> None:
    """ad 没有结构化 step1，也就没有草稿通道：报错要点名这一点，不能让 Agent 以为工具坏了反复重试。"""
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps({"content_mode": "ad", "generation_mode": "storyboard"}, ensure_ascii=False),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["content_mode"] = "ad"  # pyright: ignore[reportAttributeAccessIssue]
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # pyright: ignore[reportAttributeAccessIssue]

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert "没有草稿编辑通道" in out["content"][0]["text"]


async def test_open_step1_for_edit_returns_narration_segments(fake_ctx: ToolContext) -> None:
    """narration 取回的草稿装分镜表，正式文件一步不动——写盘只发生在持锁的晋升侧。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    before = _nr_step1_path(fake_ctx).read_text(encoding="utf-8")

    out = await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.get("is_error") is not True, out
    envelope = _read_nr_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_NARRATION_STEP1
    assert envelope["content"]["segments"][0]["novel_text"] == _RV_NOVEL
    assert envelope["violations"] == [], "取回是编辑工位，不是待修复草稿"
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    assert envelope["meta"]["base_fingerprint"] is not None
    assert _nr_step1_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_open_step1_for_edit_narration_round_trips_through_promote(fake_ctx: ToolContext, monkeypatch) -> None:
    """取回 → 改草稿 → 晋升写回正式文件、草稿清除：与 drama / 参考生视频同一条晋升通道。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    envelope = _read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["duration_seconds"] = 8
    _nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_nr(fake_ctx, monkeypatch)

    assert out.get("is_error") is not True, out
    assert not _nr_quarantine_path(fake_ctx).exists()
    saved = json.loads(_nr_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["segments"][0]["duration_seconds"] == 8
    assert saved["segments"][0]["novel_text"] == _RV_NOVEL


async def test_open_step1_for_edit_refuses_to_clobber_existing_narration_draft(fake_ctx: ToolContext) -> None:
    """已有草稿在场时不覆盖：那份草稿可能已含未晋升的修改，拿正式文件盖过去等于抹掉它的工作。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_STEP1,
        content={"segments": [_nr_segment("E1S01", 8, "改到一半的正文")]},
        violations=[],
    )

    out = await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.get("is_error") is True
    assert "已有 step1 草稿在场" in out["content"][0]["text"]
    assert _read_nr_quarantine(fake_ctx)["content"]["segments"][0]["novel_text"] == "改到一半的正文"
