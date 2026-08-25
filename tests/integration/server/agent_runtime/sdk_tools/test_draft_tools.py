"""Integration tests for the revisioned draft workflow tools."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import threading

import pytest

from lib import script_review
from lib.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_STEP1,
    QUARANTINE_KIND_NARRATION_STEP1,
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    quarantine_path,
    write_quarantine,
)
from server.agent_runtime.sdk_tools.text_generation import (
    discard_draft_tool,
    open_draft_tool,
    patch_draft_tool,
    promote_draft_tool,
)
from server.draft_workflow import DraftContext, DraftWorkflow, DraftWorkflowError
from server.media_tools.context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _RV_NOVEL,
    _call,
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


def _draft_result(out: dict) -> dict:
    return json.loads(out["content"][0]["text"])["draft"]


@pytest.mark.parametrize("factory", [open_draft_tool, patch_draft_tool, promote_draft_tool, discard_draft_tool])
def test_draft_tools_share_strict_locator_schema(fake_ctx: ToolContext, factory) -> None:
    schema = factory(fake_ctx).input_schema
    assert schema["properties"]["episode"]["minimum"] == 1
    assert schema["properties"]["doc_type"]["enum"] == [
        "drama_step1",
        "narration_step1",
        "reference_step1",
        "reference_step2",
    ]


def _write_reference_step2(fake_ctx: ToolContext, script: dict) -> None:
    fake_ctx.pm.script_payload = script  # pyright: ignore[reportAttributeAccessIssue]
    scripts = fake_ctx.project_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# open_draft
# ---------------------------------------------------------------------------


async def test_open_draft_returns_flat_draft_structure(fake_ctx: ToolContext) -> None:
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


async def test_open_draft_leaves_official_file_untouched(fake_ctx: ToolContext) -> None:
    """取回只是开编辑工位，正式文件一步不动——改动落回正式文件只发生在持锁的晋升侧。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    before = _rv_step1_path(fake_ctx).read_text(encoding="utf-8")

    await _open_for_edit(fake_ctx)

    assert _rv_step1_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_open_draft_round_trips_through_promote(fake_ctx: ToolContext) -> None:
    """情况 B 的完整闭环：取回 → 改草稿 → 晋升。改动经晋升侧的持锁写盘落回正式文件。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["text"] == "@[张三] 在 @[村口] 出场"
    assert _derived_reference_names(fake_ctx, saved["units"][0]["text"]) == ["张三", "村口"]


async def test_open_draft_returns_existing_draft_without_clobbering(fake_ctx: ToolContext, monkeypatch) -> None:
    """已有草稿在场时不覆盖：那份草稿可能已含 Agent 未晋升的修改（或本就是待修复草稿），
    拿正式文件盖过去等于抹掉它手上的工作。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    before = _rv_quarantine_path(fake_ctx).read_text(encoding="utf-8")
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True
    assert _rv_quarantine_path(fake_ctx).read_text(encoding="utf-8") == before
    assert "reference_step1" in out["content"][0]["text"]


async def test_open_draft_without_official_file(fake_ctx: ToolContext) -> None:
    """没有正式 step1 时指回首次拆分工具，而不是开一份空草稿让 Agent 手写整集。"""
    _rv_source(fake_ctx)

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert "generate_step1" in out["content"][0]["text"]
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_open_draft_keeps_malformed_duration_verbatim(fake_ctx: ToolContext) -> None:
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


async def test_open_draft_keeps_malformed_non_dict_unit_slot(fake_ctx: ToolContext) -> None:
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


async def test_open_draft_rejects_missing_source_without_side_effect(
    fake_ctx: ToolContext,
) -> None:
    """`source` 指向不存在的文件时不落盘草稿：草稿一旦创建就把这个坏路径记进 meta.source，
    晋升时 `_load_novel_source` 会反复报错，而草稿在场又挡住重新取回改正 source，Agent
    会卡在一个自己改不动的死角。校验失败时不产生持久副作用，Agent 改对参数重试即可。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            projects_root=fake_ctx.projects_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )

    with pytest.raises(DraftWorkflowError) as excinfo:
        await workflow.open(
            1,
            "reference_step1",
            source="source/episode_不存在.txt",
        )

    assert excinfo.value.code == "draft_open_failed"
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_step1_write_cannot_race_a_step2_draft_patch(fake_ctx: ToolContext) -> None:
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    _write_reference_step2(
        fake_ctx,
        {
            "title": "第一集",
            "content_mode": "narration",
            "episode": 1,
            "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
        },
    )
    opened = _draft_result(await _open_for_edit(fake_ctx, doc_type="reference_step2"))
    changed = copy.deepcopy(opened["content"])
    changed["units"][0]["text"] = "并发编辑"
    target = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP2)
    patch_reached_write = threading.Event()
    allow_patch_write = threading.Event()
    writer_attempted_draft_lock = threading.Event()
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            projects_root=fake_ctx.projects_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )

    def pause_before_patch_commit() -> None:
        patch_reached_write.set()
        _ = allow_patch_write.wait()

    def patch() -> dict:
        return asyncio.run(
            workflow.patch(
                1,
                "reference_step2",
                changed,
                opened["revision"],
                before_commit=pause_before_patch_commit,
            )
        )

    def rewrite_step1() -> None:
        script_review.write_step1(
            fake_ctx.project_path,
            1,
            {"units": [_rv_saved_unit("@[张三] 修改 step1")]},
            before_lock=writer_attempted_draft_lock.set,
        )

    patch_task = asyncio.create_task(asyncio.to_thread(patch))
    writer_task: asyncio.Task[None] | None = None
    patch_result: dict | None = None
    try:
        assert await asyncio.to_thread(patch_reached_write.wait, 1)
        writer_task = asyncio.create_task(asyncio.to_thread(rewrite_step1))
        attempted = await asyncio.to_thread(writer_attempted_draft_lock.wait, 1)
        assert attempted
    finally:
        allow_patch_write.set()
        if writer_task is not None:
            patch_result, _ = await asyncio.wait_for(asyncio.gather(patch_task, writer_task), timeout=2)

    assert patch_result is not None
    assert not target.exists()


async def test_open_draft_rejects_non_reference_episode(fake_ctx: ToolContext) -> None:
    """切走参考路径的集不给编辑：盘上的 step1 与该集此刻的生成路径无关。与晋升工具同一判据。"""
    _rv_source(fake_ctx)
    _rv_project(fake_ctx, generation_mode="image_to_video")
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_open_draft_records_base_fingerprint(fake_ctx: ToolContext) -> None:
    """取回时把正式文件此刻的内容指纹记进 meta.base_fingerprint，供晋升前基线比对。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is not True, out
    meta = _read_rv_quarantine(fake_ctx)["meta"]
    assert meta["base_fingerprint"] == script_review.content_fingerprint(_rv_step1_path(fake_ctx))


async def test_open_draft_returns_drama_scenes(fake_ctx: ToolContext) -> None:
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


async def test_open_draft_drama_round_trips_through_promote(fake_ctx: ToolContext) -> None:
    """完整闭环：取回 → 改草稿 → 晋升。改动经持锁写盘落回正式文件，派生字段按新内容重算。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])

    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["scene_description"] = "阿离推开山门。"
    _drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_drama(fake_ctx)

    assert out.get("is_error") is not True, out
    assert not _drama_quarantine_path(fake_ctx).exists()
    saved = json.loads(_drama_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["scene_description"] == "阿离推开山门。"


async def test_open_draft_returns_existing_drama_draft(fake_ctx: ToolContext) -> None:
    """已有草稿在场时不覆盖：那份草稿可能已含未晋升的修改，出路是继续改它再晋升。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])
    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["scene_description"] = "未晋升的修改。"
    _drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    assert out.get("is_error") is not True
    assert _read_drama_quarantine(fake_ctx)["content"]["scenes"][0]["scene_description"] == "未晋升的修改。"


async def test_open_draft_rejects_variant_without_draft_channel(fake_ctx: ToolContext) -> None:
    """ad 没有结构化 step1，也就没有草稿通道：报错要点名这一点，不能让 Agent 以为工具坏了反复重试。"""
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps({"content_mode": "ad", "generation_mode": "storyboard"}, ensure_ascii=False),
        encoding="utf-8",
    )
    fake_ctx.pm.project_payload["content_mode"] = "ad"  # pyright: ignore[reportAttributeAccessIssue]
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # pyright: ignore[reportAttributeAccessIssue]

    out = await _open_for_edit(fake_ctx)

    assert out.get("is_error") is True
    assert "doc_type_not_applicable" in out["content"][0]["text"]


async def test_open_draft_returns_narration_segments(fake_ctx: ToolContext) -> None:
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


async def test_open_draft_narration_round_trips_through_promote(fake_ctx: ToolContext) -> None:
    """取回 → 改草稿 → 晋升写回正式文件、草稿清除：与 drama / 参考生视频同一条晋升通道。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")

    envelope = _read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["duration_seconds"] = 8
    _nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is not True, out
    assert not _nr_quarantine_path(fake_ctx).exists()
    saved = json.loads(_nr_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["segments"][0]["duration_seconds"] == 8
    assert saved["segments"][0]["novel_text"] == _RV_NOVEL


async def test_open_draft_returns_existing_narration_draft(fake_ctx: ToolContext) -> None:
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

    assert out.get("is_error") is not True
    assert _read_nr_quarantine(fake_ctx)["content"]["segments"][0]["novel_text"] == "改到一半的正文"


async def test_patch_draft_supports_multiple_rounds_and_rejects_stale_revision(fake_ctx: ToolContext) -> None:
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    opened = _draft_result(await _open_for_edit(fake_ctx, source="source/episode_1.txt"))

    first_content = opened["content"]
    first_content["units"][0]["text"] = "@[张三] 走向 @[村口]"
    first = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "reference_step1",
            "content": first_content,
            "base_revision": opened["revision"],
        },
    )
    first_result = _draft_result(first)
    assert first_result["revision"] != opened["revision"]

    stale = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "reference_step1",
            "content": {"units": []},
            "base_revision": opened["revision"],
        },
    )
    assert stale.get("is_error") is True
    assert "revision_conflict" in stale["content"][0]["text"]

    second_content = first_result["content"]
    second_content["units"][0]["text"] = "@[张三] 在 @[村口] 停下"
    second = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "reference_step1",
            "content": second_content,
            "base_revision": first_result["revision"],
        },
    )
    assert _draft_result(second)["content"]["units"][0]["text"] == "@[张三] 在 @[村口] 停下"


@pytest.mark.parametrize(
    "doc_type",
    ["drama_step1", "narration_step1", "reference_step1", "reference_step2"],
)
async def test_each_doc_type_completes_multi_patch_then_promote_or_discard(
    fake_ctx: ToolContext, doc_type: str
) -> None:
    if doc_type == "drama_step1":
        _drama_project(fake_ctx)
        _write_drama_step1(fake_ctx, [_drama_scene()])
    elif doc_type == "narration_step1":
        _nr_source(fake_ctx)
        _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    elif doc_type == "reference_step1":
        _rv_source(fake_ctx)
        _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    else:
        _rv_project(fake_ctx)
        _write_reference_step2(
            fake_ctx,
            {
                "title": "第一集",
                "content_mode": "narration",
                "episode": 1,
                "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
            },
        )

    args = {"episode": 1, "doc_type": doc_type}
    opened = _draft_result(await _call(open_draft_tool(fake_ctx), args))

    def edit(content: dict, marker: str) -> None:
        if doc_type == "drama_step1":
            content["scenes"][0]["scene_description"] = marker
        elif doc_type == "narration_step1":
            content["segments"][0]["duration_seconds"] = 8 if marker == "second" else 6
        elif doc_type == "reference_step1":
            content["units"][0]["text"] = "@[张三] 在 @[村口] 等候" if marker == "second" else "@[张三] 走向 @[村口]"
        else:
            content["units"][0]["text"] = marker

    first_content = opened["content"]
    edit(first_content, "first")
    first = _draft_result(
        await _call(
            patch_draft_tool(fake_ctx),
            {**args, "content": first_content, "base_revision": opened["revision"]},
        )
    )
    second_content = first["content"]
    edit(second_content, "second")
    second = _draft_result(
        await _call(
            patch_draft_tool(fake_ctx),
            {**args, "content": second_content, "base_revision": first["revision"]},
        )
    )
    assert second["revision"] != first["revision"]

    if doc_type == "reference_step2":
        discarded = _draft_result(
            await _call(discard_draft_tool(fake_ctx), {**args, "base_revision": second["revision"]})
        )
        assert discarded["discarded"] is True
    else:
        if doc_type == "drama_step1":
            result = await _promote_drama(fake_ctx)
        elif doc_type == "narration_step1":
            result = await _promote_nr(fake_ctx)
        else:
            result = await _promote(fake_ctx)
        assert result.get("is_error") is not True, result


async def test_patch_draft_can_accept_a_merged_formal_revision(fake_ctx: ToolContext) -> None:
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    opened = _draft_result(await _open_for_edit(fake_ctx, source="source/episode_1.txt"))

    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 走向 @[村口]")])
    refreshed = _draft_result(await _open_for_edit(fake_ctx))
    merged = refreshed["content"]
    merged["units"][0]["text"] = "@[张三] 在 @[村口] 停下"
    patched = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "reference_step1",
            "content": merged,
            "base_revision": opened["revision"],
            "accept_formal_revision": refreshed["formal_revision"],
        },
    )

    assert patched.get("is_error") is not True, patched
    assert _read_rv_quarantine(fake_ctx)["meta"]["base_fingerprint"] == refreshed["formal_revision"]
    promoted = await _promote(fake_ctx)
    assert promoted.get("is_error") is not True, promoted


@pytest.mark.skipif(os.name != "posix", reason="FIFO-backed filesystem snapshot requires POSIX")
async def test_patch_draft_revision_covers_source_metadata_without_blocking_event_loop(
    fake_ctx: ToolContext,
) -> None:
    _rv_source(fake_ctx)
    (fake_ctx.project_path / "source" / "episode_2.txt").write_text(_RV_NOVEL, encoding="utf-8")
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    opened = _draft_result(await _open_for_edit(fake_ctx, source="source/episode_1.txt"))
    args = {
        "episode": 1,
        "doc_type": "reference_step1",
        "content": opened["content"],
        "base_revision": opened["revision"],
    }
    path = _rv_quarantine_path(fake_ctx)
    snapshot = path.read_text(encoding="utf-8")
    path.unlink()
    os.mkfifo(path)
    snapshot_writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import select
import sys

with open(sys.argv[1], "w", encoding="utf-8") as fifo:
    print("snapshot_started", flush=True)
    if not select.select([sys.stdin], [], [], 10)[0]:
        print("event_loop_blocked", flush=True)
    fifo.write(sys.argv[2])
""",
            os.fspath(path),
            snapshot,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert snapshot_writer.stdin is not None
    assert snapshot_writer.stdout is not None

    def release_fifo() -> None:
        try:
            snapshot_writer.stdin.write("\n")
            snapshot_writer.stdin.flush()
        except BrokenPipeError:
            pass

    patching = asyncio.create_task(_call(patch_draft_tool(fake_ctx), {**args, "source": "source/episode_2.txt"}))
    try:
        started = await asyncio.wait_for(asyncio.to_thread(snapshot_writer.stdout.readline), timeout=20)
        assert started == "snapshot_started\n"
        asyncio.get_running_loop().call_soon(release_fifo)
        first = _draft_result(await patching)
        writer_output = await asyncio.to_thread(snapshot_writer.stdout.read)
    finally:
        release_fifo()
        if snapshot_writer.poll() is None:
            snapshot_writer.terminate()
        await asyncio.to_thread(snapshot_writer.wait, 10)
        try:
            snapshot_writer.stdin.close()
        except BrokenPipeError:
            pass
        snapshot_writer.stdout.close()
        if not patching.done():
            patching.cancel()
        await asyncio.gather(patching, return_exceptions=True)

    stale = await _call(patch_draft_tool(fake_ctx), {**args, "source": "source/episode_1.txt"})

    assert "event_loop_blocked" not in writer_output
    assert first["revision"] != opened["revision"]
    assert _read_rv_quarantine(fake_ctx)["meta"]["source"] == "source/episode_2.txt"
    assert stale.get("is_error") is True
    assert "revision_conflict" in stale["content"][0]["text"]


async def test_discard_draft_rejects_stale_revision(fake_ctx: ToolContext) -> None:
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    opened = _draft_result(await _open_for_edit(fake_ctx))
    changed = copy.deepcopy(opened["content"])
    changed["units"][0]["text"] = "@[张三] 走向 @[村口]"
    patched = _draft_result(
        await _call(
            patch_draft_tool(fake_ctx),
            {
                "episode": 1,
                "doc_type": "reference_step1",
                "content": changed,
                "base_revision": opened["revision"],
            },
        )
    )

    stale = await _call(
        discard_draft_tool(fake_ctx),
        {"episode": 1, "doc_type": "reference_step1", "base_revision": opened["revision"]},
    )

    assert stale.get("is_error") is True
    assert "revision_conflict" in stale["content"][0]["text"]
    assert _rv_quarantine_path(fake_ctx).exists()
    discarded = _draft_result(
        await _call(
            discard_draft_tool(fake_ctx),
            {"episode": 1, "doc_type": "reference_step1", "base_revision": patched["revision"]},
        )
    )
    assert discarded["discarded"] is True


async def test_discard_draft_keeps_formal_content_and_is_idempotent(fake_ctx: ToolContext) -> None:
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    formal_before = _rv_step1_path(fake_ctx).read_text(encoding="utf-8")
    opened = _draft_result(await _open_for_edit(fake_ctx))

    args = {"episode": 1, "doc_type": "reference_step1", "base_revision": opened["revision"]}
    first = _draft_result(await _call(discard_draft_tool(fake_ctx), args))
    second = _draft_result(await _call(discard_draft_tool(fake_ctx), args))

    assert first["discarded"] is True
    assert second["discarded"] is False
    assert not _rv_quarantine_path(fake_ctx).exists()
    assert _rv_step1_path(fake_ctx).read_text(encoding="utf-8") == formal_before


async def test_open_reference_step2_returns_flat_editable_content(fake_ctx: ToolContext) -> None:
    _rv_project(fake_ctx)
    _write_reference_step2(
        fake_ctx,
        {
            "title": "第一集",
            "content_mode": "narration",
            "episode": 1,
            "video_units": [{"unit_id": "E1U01", "text": "@[张三] 起身", "duration_seconds": 4}],
        },
    )

    out = await _call(open_draft_tool(fake_ctx), {"episode": 1, "doc_type": "reference_step2"})

    draft = _draft_result(out)
    assert draft["content"] == {"title": "第一集", "units": [{"text": "@[张三] 起身"}]}
    assert draft["revision"].startswith("sha256-v1:")
    envelope = json.loads(quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP2).read_text(encoding="utf-8"))
    assert envelope["meta"]["base_fingerprint"] == draft["formal_revision"]
