"""Tests for promote_draft."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from contextlib import asynccontextmanager
from typing import Any

import pytest

from lib import script_review
from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.draft_quarantine import (
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    draft_revision,
    quarantine_path,
    read_quarantine,
    write_quarantine,
)
from lib.project_manager import ProjectManager
from lib.reference_video.draft_validation import DraftViolation
from server.agent_runtime.sdk_tools.text_generation import (
    generate_episode_script_tool,
    generate_script_plan_tool,
    open_draft_tool,
    patch_draft_tool,
    promote_draft_tool,
)
from server.draft_workflow import DraftContext, DraftWorkflow
from server.media_tools.context import ToolContext
from server.text_generation import TextGenerationError, TextGenerationRequest, generate_reference_script_plan
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _RV_NOVEL,
    _call,
    _drama_project,
    _drama_quarantine_path,
    _drama_scene,
    _drama_script_plan_path,
    _nr_generator_returning,
    _nr_quarantine_path,
    _nr_script_plan_path,
    _nr_segment,
    _nr_source,
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
    _rv_generator_returning,
    _rv_project,
    _rv_quarantine_path,
    _rv_saved_unit,
    _rv_script_plan_path,
    _rv_source,
    _rv_unit,
    _use_fake_caps,
    _write_drama_script_plan,
    _write_nr_script_plan,
    _write_rv_script_plan,
)

# ---------------------------------------------------------------------------
# 草稿与修复晋升闭环（script_plan）
# ---------------------------------------------------------------------------


#: 六类阻断违约的最小触发样例（违约类 → 扁平 unit），共 7 条：「``@[X]`` 未登记」一类按出现位置
#: 拆成描述位（unregistered_asset）与台词记号 speaker 位（unregistered_speaker）两条，两处走不同入口，
#: 合测会漏掉其中一处。逐类断言「落草稿 + 正式文件干净 + 报告按类定位」，而不是只验其中
#: 一两类——各类共用同一次遍历，漏测哪一类都可能在该类上退回「丢弃重抽」。
#: ``duration_off_tier``（时长不在该 unit 引用状态的生效档位内）需要另一套 caps 才触发，
#: 单列在 ``test_split_reference_video_units_rejects_duration_off_reference_tier``。
_RV_VIOLATION_CASES = [
    ("unclosed_brace", _rv_unit("@[张三] 起身，喊了一句 {我来了")),
    ("dialogue_line_syntax", _rv_unit("门开了\n@[张三]：我来了。")),
    ("unregistered_asset", _rv_unit("@[不存在的人] 出场")),
    ("unregistered_speaker", _rv_unit("门开了\n@[无名氏]：{我来了。}")),
    ("braces_in_description", _rv_unit("@[张三] 推门，音量 {}，转身离开")),
    ("source_text_not_verbatim", _rv_unit("@[张三] 起身", source_text="张三在城里等人")),
    ("dialogue_overload", _rv_unit("@[张三] 起身\n@[张三]：{" + "这是一段非常长的台词" * 6 + "}", duration=4)),
]


@pytest.mark.parametrize(("code", "unit"), _RV_VIOLATION_CASES, ids=[c for c, _ in _RV_VIOLATION_CASES])
async def test_split_reference_video_units_quarantines_each_violation_class(
    fake_ctx: ToolContext, monkeypatch, code: str, unit: dict
) -> None:
    """六类阻断违约逐类：产物落草稿、正式文件不被写出、报告按违约类逐条定位。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [unit])

    assert out.get("is_error") is True
    assert not _rv_script_plan_path(fake_ctx).exists()

    envelope = _read_rv_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_SCRIPT_PLAN
    assert [v["code"] for v in envelope["violations"]] == [code]
    assert envelope["violations"][0]["label"] == "unit E1U01"
    # 草稿装的是扁平草稿结构（Agent 要改的是正文 / 原文锚 / 时长），不是派生后的落盘形状
    assert envelope["content"]["units"][0]["text"] == unit["text"]
    assert "shots" not in envelope["content"]["units"][0]

    report = out["content"][0]["text"]
    assert f"[{code}]" in report
    assert "unit E1U01" in report
    assert str(_rv_quarantine_path(fake_ctx)) in report
    assert "promote_draft" in report


async def test_split_reference_video_units_reports_all_bad_units_in_one_round(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """报告逐条覆盖所有坏 unit，不停在第一个——否则 Agent 每修一处就要再跑一轮付费拆分。"""
    _rv_source(fake_ctx)
    units = [
        _rv_unit("@[张三] 起身"),
        _rv_unit("@[不存在的人] 出场"),
        _rv_unit("@[张三] 推门，音量 {}"),
    ]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)

    assert out.get("is_error") is True
    envelope = _read_rv_quarantine(fake_ctx)
    assert [v["label"] for v in envelope["violations"]] == ["unit E1U02", "unit E1U03"]
    assert [v["code"] for v in envelope["violations"]] == ["unregistered_asset", "braces_in_description"]
    # 合法的 unit 也原样留在草稿里：Agent 只需改坏的那些
    assert len(envelope["content"]["units"]) == 3


async def test_reference_script_plan_write_transaction_does_not_block_event_loop(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    _rv_source(fake_ctx)
    resolver = _use_fake_caps(fake_ctx)
    from server import text_generation as mod

    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning([_rv_unit("@[张三] 起身")]))
    started = threading.Event()
    release = threading.Event()
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def before_commit() -> None:
        worker_threads.append(threading.get_ident())
        started.set()
        release.wait()

    generation = asyncio.create_task(
        generate_reference_script_plan(
            TextGenerationRequest(episode=1),
            project_name=fake_ctx.project_name,
            projects=fake_ctx.pm,
            config_resolver=resolver,
            before_commit=before_commit,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        ticked = asyncio.Event()
        asyncio.get_running_loop().call_soon(ticked.set)
        await asyncio.wait_for(ticked.wait(), timeout=1)
    finally:
        release.set()

    result = await generation

    assert result.message.startswith("✅")
    assert worker_threads and all(thread != caller_thread for thread in worker_threads)


async def test_cancelled_reference_script_plan_commit_restores_files_and_manifest(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    _rv_source(fake_ctx)
    resolver = _use_fake_caps(fake_ctx)
    from server import text_generation as mod

    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 等待")])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_SCRIPT_PLAN,
        content={"units": [_rv_unit("@[张三] 等待")]},
        violations=[],
    )
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "旧草稿", "units": [{"text": "旧内容"}]},
        violations=[],
    )
    paths = (
        _rv_script_plan_path(fake_ctx),
        _rv_quarantine_path(fake_ctx),
        quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING),
    )
    before = {path: path.read_bytes() for path in paths}
    adapter = ProjectArtifactManifestAdapter(fake_ctx.project_path)
    key = ArtifactKey.episode_script_plan(1)
    manifest_before = adapter.get_entry(key)
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning([_rv_unit("@[张三] 起身")]))
    started = threading.Event()
    release = threading.Event()

    def before_commit() -> None:
        started.set()
        release.wait()

    generation = asyncio.create_task(
        generate_reference_script_plan(
            TextGenerationRequest(episode=1),
            project_name=fake_ctx.project_name,
            projects=fake_ctx.pm,
            config_resolver=resolver,
            before_commit=before_commit,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        generation.cancel()
        await asyncio.sleep(0)
        assert not generation.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(generation, timeout=1)
    assert {path: path.read_bytes() for path in paths} == before
    assert adapter.get_entry(key) == manifest_before


async def test_promote_draft_promotes_after_repair(fake_ctx: ToolContext, monkeypatch) -> None:
    """Agent 修好草稿后晋升：正式 script_plan 落盘、草稿清除、结构由正文机械派生。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(_rv_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["unit_id"] == "E1U01"
    assert saved["units"][0]["text"] == "@[张三] 在 @[村口] 出场"


async def test_promote_draft_reports_again_without_round_limit(fake_ctx: ToolContext, monkeypatch) -> None:
    """再违约则再返回刷新后的报告、草稿留在原地，可反复晋升——无收敛轮次上限。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    for _round in range(3):
        out = await _promote(fake_ctx)
        assert out.get("is_error") is True
        assert "unregistered_asset" in out["content"][0]["text"]
        assert _rv_quarantine_path(fake_ctx).exists()
        assert not _rv_script_plan_path(fake_ctx).exists()

    # 改成另一类违约后报告随之刷新，不是上一轮的陈旧快照
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 推门，音量 {}"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    await _promote(fake_ctx)
    assert [v["code"] for v in _read_rv_quarantine(fake_ctx)["violations"]] == ["braces_in_description"]


async def test_promote_draft_rejects_stale_draft_revision(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    args = {"episode": 1, "doc_type": "reference_script_plan"}
    opened = json.loads((await _call(open_draft_tool(fake_ctx), args))["content"][0]["text"])["draft"]
    updated = copy.deepcopy(opened["content"])
    updated["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    patched = await _call(
        patch_draft_tool(fake_ctx),
        {**args, "content": updated, "base_revision": opened["revision"]},
    )
    assert patched.get("is_error") is not True, patched

    out = await _call(promote_draft_tool(fake_ctx), {**args, "base_revision": opened["revision"]})

    assert out.get("is_error") is True
    assert "revision_conflict" in out["content"][0]["text"]
    assert _rv_quarantine_path(fake_ctx).exists()
    assert not _rv_script_plan_path(fake_ctx).exists()


# ---------------------------------------------------------------------------
# script_plan 乐观并发控制（取回时记基线指纹，晋升前锁内比对）
# ---------------------------------------------------------------------------


async def test_promote_conflicts_when_official_changed_after_open(fake_ctx: ToolContext) -> None:
    """「用户在内容确认界面编辑 + Agent 改草稿并晋升」的双端并发：取回后正式文件被另一写入方
    改过时，晋升中止并返回冲突报告（含最新内容与合并指引），不静默覆盖对方的修改；草稿
    留在原地。按报告经 open_draft / patch_draft 显式接受 formal_revision 后方可重新晋升。"""
    _rv_source(fake_ctx)
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")

    # 模拟取回之后 Web 端保存改写了正式文件
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 在 @[村口] 等候")])
    web_version = _rv_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert "并发冲突" in report
    assert "accept_formal_revision" in report
    assert "patch_draft" in report
    assert "base_revision" in report
    assert 'doc_type\\": \\"reference_script_plan' in report
    # 冲突报告附上盘上现值的扁平草稿单元，供 Agent 对照合并
    assert "在 @[村口] 等候" in report
    # 正式文件未被覆盖，草稿仍在场
    assert _rv_script_plan_path(fake_ctx).read_text(encoding="utf-8") == web_version
    assert _rv_quarantine_path(fake_ctx).exists()

    # 按报告指引通过工具合并并显式接受正式版本后，重新晋升即放行。
    refreshed = json.loads((await _open_for_edit(fake_ctx))["content"][0]["text"])["draft"]
    refreshed["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 等候"
    patched = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "reference_script_plan",
            "content": refreshed["content"],
            "base_revision": refreshed["revision"],
            "accept_formal_revision": refreshed["formal_revision"],
        },
    )
    assert patched.get("is_error") is not True, patched
    out = await _promote(fake_ctx)
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_promote_conflict_report_renders_missing_fingerprint_as_json_null(fake_ctx: ToolContext) -> None:
    """取回后正式文件被删除：现值指纹是 null，报告须按 JSON 字面量给出而非字符串 "None"。
    照报告用 patch_draft 显式接受 null 后重晋升即放行——写成字符串则永远比对不上。"""
    _rv_source(fake_ctx)
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    _rv_script_plan_path(fake_ctx).unlink()

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert "null" in report
    assert "None" not in report
    assert "accepts_formal_revision=true" in report

    refreshed = json.loads((await _open_for_edit(fake_ctx))["content"][0]["text"])["draft"]
    assert refreshed["formal_revision"] is None
    patched = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "reference_script_plan",
            "content": refreshed["content"],
            "base_revision": refreshed["revision"],
            "accept_formal_revision": None,
        },
    )
    assert patched.get("is_error") is not True, patched
    out = await _promote(fake_ctx)
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_promote_without_base_fingerprint_meta_promotes_unchecked(fake_ctx: ToolContext) -> None:
    """基线机制引入前产出的存量草稿缺 meta.base_fingerprint 键：按无基线晋升，不被新校验卡死。"""
    _rv_source(fake_ctx)
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_rv_quarantine(fake_ctx)
    del envelope["meta"]["base_fingerprint"]
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    # 取回后正式文件又被改过——存量草稿无基线可比，照旧覆盖（维持引入前语义）
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 在 @[村口] 等候")])

    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_split_violation_quarantine_records_base_fingerprint(fake_ctx: ToolContext, monkeypatch) -> None:
    """拆分违约落草稿时同样记基线：修好晋升前正式文件被并发改写的话按基线中止。
    首拆时正式文件不存在，基线为 null——晋升时若正式文件已被另一次拆分写出，同样判冲突。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    meta = _read_rv_quarantine(fake_ctx)["meta"]
    assert "base_fingerprint" in meta
    assert meta["base_fingerprint"] is None

    # 草稿在场期间正式文件被写出（另一路径），修好草稿后晋升应报冲突而非覆盖
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    formal_fingerprint = script_review.content_fingerprint(_rv_script_plan_path(fake_ctx))

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    assert "并发冲突" in out["content"][0]["text"]
    assert "base_revision" in out["content"][0]["text"]
    assert script_review.content_fingerprint(_rv_script_plan_path(fake_ctx)) == formal_fingerprint


async def test_split_violation_keeps_pre_generation_formal_baseline(fake_ctx: ToolContext, monkeypatch) -> None:
    from server import text_generation as mod

    _rv_source(fake_ctx)
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    expected = script_review.content_fingerprint(_rv_script_plan_path(fake_ctx))
    started = asyncio.Event()
    release = asyncio.Event()

    class _Generator:
        async def generate(self, _request, project_name=None):
            started.set()
            await release.wait()

            class _Result:
                text = json.dumps({"units": [_rv_unit("@[不存在的人] 出场")]}, ensure_ascii=False)

            return _Result()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    resolver = _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)
    generation = asyncio.create_task(
        generate_reference_script_plan(
            TextGenerationRequest(episode=1),
            project_name=fake_ctx.project_name,
            projects=fake_ctx.pm,
            config_resolver=resolver,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    except TimeoutError:
        generation.cancel()
        await asyncio.gather(generation, return_exceptions=True)
        raise
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 在 @[村口] 等候")])
    release.set()

    with pytest.raises(TextGenerationError):
        await asyncio.wait_for(generation, timeout=1)

    current = script_review.content_fingerprint(_rv_script_plan_path(fake_ctx))
    assert current != expected
    assert _read_rv_quarantine(fake_ctx)["meta"]["base_fingerprint"] == expected


@pytest.mark.parametrize(
    ("mutate", "hint"),
    [
        (lambda u: u.update(duration_seconds=7), "7"),
        (lambda u: u.pop("duration_seconds"), "duration_seconds"),
        (lambda u: u.update(source_text=""), "source_text"),
    ],
    ids=["off_slot_duration", "duration_removed", "blank_source_text"],
)
async def test_promote_draft_rejects_schema_breach(fake_ctx: ToolContext, monkeypatch, mutate, hint: str) -> None:
    """草稿改坏 schema 层字段同样只回报告：晋升与产出走同一份 schema，正式文件不被污染。

    时长枚举在产出侧由 response_schema 卡死；晋升侧若只判内容约束，Agent 把 duration_seconds
    改成非档位值或整个删掉（收成 0 秒）就能一路进正式 script_plan。
    """
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    mutate(envelope["content"]["units"][0])
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    assert hint in out["content"][0]["text"]
    assert not _rv_script_plan_path(fake_ctx).exists()
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
async def test_promote_draft_reports_broken_outer_shape(fake_ctx: ToolContext, monkeypatch, mutate_content) -> None:
    """外层形状被改坏同样刷新报告，而不是抛一句裸错误。

    units 整个删掉 / 改成非数组 / 清空都是 Agent 编辑草稿时会犯的错。只有逐 unit 的字段违约
    刷新报告的话，这几种就被甩出了「按报告改完再晋升」的循环。
    """
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    mutate_content(envelope["content"])
    edited_content = copy.deepcopy(envelope["content"])
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    assert "content.units" in out["content"][0]["text"]
    assert not _rv_script_plan_path(fake_ctx).exists()
    refreshed = _read_rv_quarantine(fake_ctx)
    assert [v["code"] for v in refreshed["violations"]] == ["schema_invalid"]
    # 草稿留在原地且原样保留 Agent 写的那份内容：做收编会把它的原稿改形，它照着报告回看时
    # 反而对不上自己写的东西，改完再晋升这条路就断了
    assert _rv_quarantine_path(fake_ctx).exists()
    assert refreshed["content"] == edited_content


async def test_promote_draft_requires_source_provenance(fake_ctx: ToolContext, monkeypatch) -> None:
    """meta.source 被改掉后不晋升：按整个 source/ 重解析比产出时更松，别集的原文锚会恰好命中。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    assert "source" in envelope["meta"], "拆分侧须一律写出 source 键（未指定源文时为 null）"
    envelope["meta"] = {}
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)
    assert out.get("is_error") is True
    assert json.loads(out["content"][0]["text"])["problem"]["code"] == "draft_invalid"
    assert "meta.source 缺失" in out["content"][0]["text"]
    assert not _rv_script_plan_path(fake_ctx).exists()


async def test_promote_draft_reports_promotion_not_split(fake_ctx: ToolContext, monkeypatch) -> None:
    """晋升成功的摘要要说「晋升」：说成「拆分」会让 Agent 以为自己的修改被一次重抽覆盖了。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert "晋升" in out["content"][0]["text"]


async def test_cancelled_reference_script_plan_promotion_finishes_commit_and_cleanup(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    started = threading.Event()
    release = threading.Event()

    def before_commit() -> None:
        started.set()
        release.wait()

    draft = read_quarantine(fake_ctx.project_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
    assert draft is not None
    _use_fake_caps(fake_ctx)
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            projects_root=fake_ctx.projects_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )
    promotion = asyncio.create_task(
        workflow.promote(
            1,
            "reference_script_plan",
            draft_revision(draft),
            before_commit=before_commit,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        promotion.cancel()
        await asyncio.sleep(0)
        assert not promotion.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(promotion, timeout=1)
    assert _rv_script_plan_path(fake_ctx).exists()
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_writing_reference_script_plan_clears_stale_prompt_authoring_quarantine(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """script_plan 一变即清掉在场的 prompt_authoring 草稿：它以旧 script_plan 为 diff 基底，留着就永远晋升不了。"""
    _rv_source(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    prompt_authoring_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    assert prompt_authoring_path.exists()

    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 起身")])

    assert out.get("is_error") is not True, out
    assert not prompt_authoring_path.exists()


async def test_promote_reference_script_plan_preserves_prompt_authoring_draft_when_content_unchanged(
    fake_ctx: ToolContext,
) -> None:
    """情况 B 中途放弃、原样晋升：取回草稿未改动即晋升，写回的 script_plan 与盘上原值逐字相同，
    此时不该清在场的 prompt_authoring 草稿——它的保结构 diff 仍然对得上这份没变的基底，Agent
    放弃 script_plan 修改不该连带销毁一份仍然有效的 prompt_authoring 修复草稿。"""
    _rv_source(fake_ctx)
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    prompt_authoring_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    assert prompt_authoring_path.exists()

    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert prompt_authoring_path.exists()


async def test_promote_draft_prompt_authoring_uses_async_factory(fake_ctx: ToolContext, monkeypatch) -> None:
    """prompt_authoring 晋升走 ``ScriptGenerator.create``：晋升同样经 _add_metadata 落盘，裸构造会把
    metadata.generator 记成 "unknown"，与直接生成路径的同一份产物对不上。"""
    from lib.text_generator import TextGenerator

    _rv_source(fake_ctx)
    split = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 在 @[村口] 等候")])
    assert split.get("is_error") is not True, split
    project = fake_ctx.pm.project_payload  # pyright: ignore[reportAttributeAccessIssue]
    project["episodes"][0]["script_plan_review"] = {
        "fingerprint": script_review.content_fingerprint(_rv_script_plan_path(fake_ctx)),
        "confirmed_at": "2026-08-24T00:00:00Z",
    }
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(project, ensure_ascii=False),
        encoding="utf-8",
    )
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "镜头1：中景，平视。@[张三] 在 @[村口] 等候。"}]},
        violations=[],
        meta={"base_fingerprint": None},
    )
    seen: dict[str, object] = {}

    class _TextBoundary:
        model = "review-factory"

    async def create_text_generator(task_type, project_name=None):
        seen["task_type"] = task_type
        seen["project_name"] = project_name
        return _TextBoundary()

    monkeypatch.setattr(TextGenerator, "create", create_text_generator)
    out = await _promote(fake_ctx)
    assert out.get("is_error") is not True, out
    assert "episode_1.json" in out["content"][0]["text"]
    saved = json.loads((fake_ctx.project_path / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    assert saved["metadata"]["generator"] == "review-factory"
    assert seen["project_name"] == fake_ctx.project_name


async def test_promote_draft_waits_for_file_lock_without_blocking_event_loop(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.text_generator import TextGenerator

    _rv_source(fake_ctx)
    split = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 起身")])
    assert split.get("is_error") is not True, split
    project = fake_ctx.pm.project_payload  # pyright: ignore[reportAttributeAccessIssue]
    project["episodes"][0]["script_plan_review"] = {
        "fingerprint": script_review.content_fingerprint(_rv_script_plan_path(fake_ctx)),
        "confirmed_at": "2026-08-24T00:00:00Z",
    }
    (fake_ctx.project_path / "project.json").write_text(
        json.dumps(project, ensure_ascii=False),
        encoding="utf-8",
    )
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "镜头1：中景，平视。@[张三] 起身。"}]},
        violations=[],
        meta={"base_fingerprint": None},
    )
    path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    draft = read_quarantine(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    assert draft is not None
    _use_fake_caps(fake_ctx)
    workflow = DraftWorkflow(
        DraftContext(
            project_name=fake_ctx.project_name,
            projects_root=fake_ctx.projects_root,
            pm=fake_ctx.pm,
            config_resolver=fake_ctx.config_resolver,
        )
    )

    class _TextBoundary:
        model = "async-lock"

    async def create_text_generator(_task_type, _project_name=None):
        return _TextBoundary()

    monkeypatch.setattr(TextGenerator, "create", create_text_generator)
    pm = ProjectManager(str(fake_ctx.project_path.parent))
    held = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with pm.file_lock(path):
            held.set()
            release.wait()

    holder = asyncio.create_task(asyncio.to_thread(hold_lock))
    promotion: asyncio.Task[dict[str, Any]] | None = None
    try:
        assert await asyncio.to_thread(held.wait, 1)
        attempted = asyncio.Event()
        promotion = asyncio.create_task(
            workflow.promote(
                1,
                "reference_prompt_authoring",
                draft_revision(draft),
                before_lock=attempted.set,
            )
        )
        await asyncio.wait_for(attempted.wait(), 0.3)
        assert not promotion.done()
    finally:
        release.set()
        assert await asyncio.wait_for(holder, timeout=1) is None

    assert promotion is not None
    out = await asyncio.wait_for(promotion, timeout=1)
    assert out["promoted"] is True
    assert (fake_ctx.project_path / "scripts" / "episode_1.json").exists()


async def test_open_script_plan_draft_waits_for_quarantine_lock(fake_ctx: ToolContext, monkeypatch) -> None:
    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])
    target = _drama_quarantine_path(fake_ctx)
    pm = ProjectManager(str(fake_ctx.project_path.parent))
    attempted = asyncio.Event()
    original_async_lock = ProjectManager.async_file_lock

    async with pm.async_file_lock(target):

        @asynccontextmanager
        async def observed_async_lock(self, path, **kwargs):
            if path == target:
                attempted.set()
            async with original_async_lock(self, path, **kwargs):
                yield

        monkeypatch.setattr(ProjectManager, "async_file_lock", observed_async_lock)
        opening = asyncio.create_task(_open_drama_for_edit(fake_ctx, source="source/episode_1.txt"))
        await asyncio.wait_for(attempted.wait(), timeout=1)
        assert not opening.done()

    out = await opening
    assert out.get("is_error") is not True, out
    assert target.exists()


async def test_promote_draft_refuses_after_mode_switch(fake_ctx: ToolContext) -> None:
    """切走参考路径后不再晋升残留草稿：晋升会按参考路径的形状覆盖该集正式剧本。"""
    _rv_project(fake_ctx, generation_mode="storyboard")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )

    out = await _promote(fake_ctx)
    assert out.get("is_error") is True
    assert "doc_type_not_applicable" in out["content"][0]["text"]
    assert quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists(), (
        "残留草稿应原样留在盘上，不被这条路径消费"
    )


async def test_promote_draft_prompt_authoring_blocked_by_review_gate(fake_ctx: ToolContext) -> None:
    """script_plan 未经确认时 prompt_authoring 草稿不晋升：常规生成路径在工具入口就被内容确认拦下，两条路不该分叉。

    草稿在场期间用户在 Web 端改过 script_plan 会让确认指纹失效，该集回到 pending_review——此时晋升等于
    拿一份用户没确认过的 script_plan 合成正式剧本。
    """
    _rv_project(fake_ctx)
    script_plan = _rv_script_plan_path(fake_ctx)
    script_plan.parent.mkdir(parents=True, exist_ok=True)
    script_plan.write_text(json.dumps({"units": []}, ensure_ascii=False), encoding="utf-8")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    project = json.loads((fake_ctx.project_path / "project.json").read_text(encoding="utf-8"))
    assert script_review.review_status(fake_ctx.project_path, project, 1) == "pending_review"

    out = await _promote(fake_ctx)
    assert out.get("is_error") is True
    assert "review_required" in out["content"][0]["text"]


async def test_promote_draft_without_draft(fake_ctx: ToolContext) -> None:
    out = await _promote(fake_ctx)
    assert out.get("is_error") is True
    assert "draft_not_found" in out["content"][0]["text"]


async def test_split_reference_video_units_clears_stale_quarantine_on_success(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """重拆分成功即清掉上一轮的草稿——留着会让内容确认与生成侧继续阻塞在已被取代的产物上。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    assert _rv_quarantine_path(fake_ctx).exists()

    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 起身")])
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


def _write_rv_quarantine(fake_ctx: ToolContext) -> None:
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_SCRIPT_PLAN,
        content={"units": []},
        violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
    )


async def test_generate_episode_script_blocked_by_quarantine(fake_ctx: ToolContext) -> None:
    """草稿在场时 prompt_authoring 入口阻塞，且给出「改草稿再晋升」而非「去 Web 端确认」的出路。"""
    _rv_project(fake_ctx)
    script_plan = _rv_script_plan_path(fake_ctx)
    script_plan.parent.mkdir(parents=True, exist_ok=True)
    script_plan.write_text(json.dumps({"units": []}, ensure_ascii=False), encoding="utf-8")
    _write_rv_quarantine(fake_ctx)

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    assert "草稿待处置" in out["content"][0]["text"]
    assert "promote_draft" in out["content"][0]["text"]


async def test_generate_episode_script_preserves_editable_draft_without_violations(fake_ctx: ToolContext) -> None:
    """可编辑草稿没有违约报告，prompt_authoring 入口应引导校验晋升而不是要求凭空修改。"""
    _rv_project(fake_ctx)
    _write_rv_script_plan(fake_ctx, [_rv_saved_unit("原始内容")])
    await _open_for_edit(fake_ctx)

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "这是可编辑草稿" in text
    assert "保留已有修改" in text
    assert "按草稿内 violations" not in text


async def test_generate_episode_script_quarantine_precedes_missing_script_plan(fake_ctx: ToolContext) -> None:
    """首次拆分就违约时正式 script_plan 本就不存在——先报缺文件会把 Agent 引回重跑拆分（丢弃重抽）。"""
    _rv_project(fake_ctx)
    _write_rv_quarantine(fake_ctx)
    assert not _rv_script_plan_path(fake_ctx).exists()

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "草稿待处置" in text
    assert "未找到 Step 1 文件" not in text


async def test_generate_episode_script_ignores_quarantine_after_mode_switch(fake_ctx: ToolContext) -> None:
    """切走参考路径后残留的草稿与新路径无关：非参考路径不清它们，仍判会把该集永久卡死。"""
    _rv_project(fake_ctx, generation_mode="storyboard")
    _write_rv_quarantine(fake_ctx)

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    # 卡在「缺 narration script_plan」这道常规校验上，而不是参考路径的草稿
    assert "草稿待处置" not in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# drama script_plan 的取回编辑与晋升闭环
# ---------------------------------------------------------------------------


async def test_promote_drama_script_plan_rederives_needs_replan(fake_ctx: ToolContext) -> None:
    """needs_replan 由晋升侧按台词准入重新派生，不取草稿里的值——它是机器判据，
    手写值一旦被采信，后续重规划会漏掉真正需要重排的场景。"""
    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])

    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_drama_quarantine(fake_ctx)
    scene = envelope["content"]["scenes"][0]
    scene["utterances"] = [
        {"kind": "dialogue", "speaker": "阿离", "text": "我回来了。"},
        {"kind": "voiceover", "speaker": None, "text": "三年后。"},
    ]
    scene["needs_replan"] = False
    _drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_drama(fake_ctx)

    assert out.get("is_error") is not True, out
    saved = json.loads(_drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["needs_replan"] is True


async def test_promote_drama_script_plan_reports_schema_breach_without_writing(fake_ctx: ToolContext) -> None:
    """草稿被改坏时正式文件不写：草稿留在场、按 violations 继续改再晋升，不丢内容也不污染正式文件。"""
    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])
    before = _drama_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["duration_seconds"] = 7  # 不在档位内
    _drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_drama(fake_ctx)

    assert out.get("is_error") is True
    assert _drama_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before
    assert _drama_quarantine_path(fake_ctx).exists()
    assert "content.scenes[i]" in out["content"][0]["text"]


async def test_promote_drama_script_plan_aborts_on_concurrent_write(fake_ctx: ToolContext) -> None:
    """取回与晋升之间正式文件被别的写入方改过 → 中止并报冲突，不静默覆盖对方的保存。"""
    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])

    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    _write_drama_script_plan(fake_ctx, [_drama_scene(scene_description="别处改过的描述。")])
    concurrent = _drama_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    out = await _promote_drama(fake_ctx)

    assert out.get("is_error") is True
    assert _drama_script_plan_path(fake_ctx).read_text(encoding="utf-8") == concurrent
    assert _drama_quarantine_path(fake_ctx).exists()


async def test_generate_episode_script_blocked_by_drama_quarantine(fake_ctx: ToolContext) -> None:
    """drama 的 prompt_authoring 与参考生视频同口径：草稿在场即拒绝生成，
    否则会拿正式文件那份上一版内容静默顶替待处置的正文。"""
    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])
    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})

    assert out.get("is_error") is True
    assert "草稿待处置" in out["content"][0]["text"]


async def test_normalize_drama_script_clears_quarantine_on_regeneration(fake_ctx: ToolContext, monkeypatch) -> None:
    """重新规范化是刻意的整份重建，与参考生视频的重拆分同口径：正式文件换成新产物的同一临界区内
    清掉上一轮草稿。留着它会让内容确认与 prompt_authoring 一直阻塞在一份已被取代的内容上，而草稿记下的基线
    指纹此刻也对不上，晋升只会反复报冲突——Agent 没有第二条出路。"""
    from server import text_generation as mod

    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])
    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    assert _drama_quarantine_path(fake_ctx).exists()

    regenerated = {"title": "第一集", "scenes": [_drama_scene(scene_description="重新规范化后的描述。")]}

    class _Generator:
        async def generate(self, _request, project_name=None):
            class _R:
                text = json.dumps(regenerated, ensure_ascii=False)

            return _R()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    out = await _call(generate_script_plan_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})

    assert out.get("is_error") is not True, out
    assert not _drama_quarantine_path(fake_ctx).exists()
    saved = json.loads(_drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["scene_description"] == "重新规范化后的描述。"


async def test_normalize_drama_script_serializes_commit_with_draft_edits(fake_ctx: ToolContext, monkeypatch) -> None:
    """重生成的正式文件提交与草稿 patch/promote 共用草稿锁，避免交叉覆盖。"""
    from server import text_generation as mod

    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])
    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    regenerated = {"title": "第一集", "scenes": [_drama_scene(scene_description="重新规范化后的描述。")]}

    class _Generator:
        async def generate(self, _request, project_name=None):
            class _R:
                text = json.dumps(regenerated, ensure_ascii=False)

            return _R()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)
    pm = ProjectManager(str(fake_ctx.project_path.parent))
    target = _drama_quarantine_path(fake_ctx)
    attempted = asyncio.Event()
    original_async_file_lock = ProjectManager.async_file_lock
    async with pm.async_file_lock(target):

        @asynccontextmanager
        async def observed_async_file_lock(self, path, **kwargs):
            if path == target:
                attempted.set()
            async with original_async_file_lock(self, path, **kwargs):
                yield

        monkeypatch.setattr(ProjectManager, "async_file_lock", observed_async_file_lock)
        task = asyncio.create_task(
            _call(generate_script_plan_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})
        )
        await asyncio.wait_for(attempted.wait(), timeout=1)
        assert not task.done(), "generation commit must wait for the draft lock"

    out = await task
    assert out.get("is_error") is not True, out


async def test_normalize_drama_script_preserves_draft_edited_during_model_call(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from server import text_generation as mod

    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene()])
    opened_out = await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    opened = json.loads(opened_out["content"][0]["text"])["draft"]
    started = asyncio.Event()
    release = asyncio.Event()
    regenerated = {"title": "第一集", "scenes": [_drama_scene(scene_description="重生成内容")]}

    class _Generator:
        async def generate(self, _request, project_name=None):
            started.set()
            await release.wait()

            class _R:
                text = json.dumps(regenerated, ensure_ascii=False)

            return _R()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)
    generation = asyncio.create_task(
        _call(generate_script_plan_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})
    )
    await started.wait()
    edited = copy.deepcopy(opened["content"])
    edited["scenes"][0]["scene_description"] = "并发编辑内容"
    patched = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "drama_script_plan",
            "content": edited,
            "base_revision": opened["revision"],
        },
    )
    assert patched.get("is_error") is not True, patched
    release.set()

    out = await generation

    assert out.get("is_error") is True
    assert "draft_revision_conflict" in out["content"][0]["text"]
    assert _read_drama_quarantine(fake_ctx)["content"]["scenes"][0]["scene_description"] == "并发编辑内容"
    formal = json.loads(_drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert formal["scenes"][0]["scene_description"] != "重生成内容"


async def test_normalize_drama_script_preserves_output_when_formal_changes_during_model_call(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from server import text_generation as mod

    _drama_project(fake_ctx)
    _write_drama_script_plan(fake_ctx, [_drama_scene(scene_description="生成前内容")])
    started = asyncio.Event()
    release = asyncio.Event()
    regenerated = {"title": "第一集", "scenes": [_drama_scene(scene_description="本次生成内容")]}

    class _Generator:
        async def generate(self, _request, project_name=None):
            started.set()
            await release.wait()

            class _R:
                text = json.dumps(regenerated, ensure_ascii=False)

            return _R()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)
    generation = asyncio.create_task(
        _call(generate_script_plan_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})
    )
    await started.wait()
    _write_drama_script_plan(fake_ctx, [_drama_scene(scene_description="并发正式内容")])
    release.set()

    out = await generation

    assert out.get("is_error") is True
    assert "formal_revision_conflict" in out["content"][0]["text"]
    formal = json.loads(_drama_script_plan_path(fake_ctx).read_text(encoding="utf-8"))
    assert formal["scenes"][0]["scene_description"] == "并发正式内容"
    assert _read_drama_quarantine(fake_ctx)["content"]["scenes"][0]["scene_description"] == "本次生成内容"


# ---------------------------------------------------------------------------
# narration script_plan 的取回编辑与晋升闭环
# ---------------------------------------------------------------------------


async def test_split_narration_segments_quarantines_violation_instead_of_discarding(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """违约产出落草稿而非丢弃：正式文件不写，报告带违约类与分镜定位，草稿里是这次的产出。

    丢弃重抽既烧钱又不收敛（同一模型对同一份原文大概率再犯同一类错），本机制的全部要点就是
    让 Agent 就地改这份已付费的产出。
    """
    from server import text_generation as mod

    _nr_source(fake_ctx)
    segments = [_nr_segment("E1S01", 5, _RV_NOVEL, characters_in_segment=["王五"])]
    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning(segments))

    out = await _call(generate_script_plan_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert str(_nr_quarantine_path(fake_ctx)) in report
    assert "[duration_off_tier]" in report
    assert "[unregistered_asset]" in report
    assert "segment E1S01" in report
    assert not _nr_script_plan_path(fake_ctx).exists(), "正式文件一步不动"
    envelope = _read_nr_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_NARRATION_SCRIPT_PLAN
    assert envelope["content"]["segments"][0]["segment_id"] == "E1S01"
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    # 正式文件此刻不存在，基线即 null——这份草稿修好晋升时若已被别的写入方建出来，按基线比对报冲突。
    assert envelope["meta"]["base_fingerprint"] is None


async def test_split_narration_segments_clears_quarantine_on_regeneration(fake_ctx: ToolContext, monkeypatch) -> None:
    """重跑拆分成功后清掉上一轮的草稿：正式文件已是新产物，旧草稿留着只会让内容确认与 prompt_authoring 继续
    阻塞在一份已被取代的内容上。"""
    from server import text_generation as mod

    _nr_source(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [_nr_segment("E1S01", 5)]},
        violations=[],
    )
    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([_nr_segment("E1S01", 4, _RV_NOVEL)]))

    out = await _call(generate_script_plan_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})

    assert out.get("is_error") is not True, out
    assert not _nr_quarantine_path(fake_ctx).exists()
    assert (
        json.loads(_nr_script_plan_path(fake_ctx).read_text(encoding="utf-8"))["segments"][0]["duration_seconds"] == 4
    )


@pytest.mark.parametrize("segment_id", [" ", "items[0]", "E1S1", "E2S01"])
async def test_split_narration_segments_rejects_malformed_segment_id(
    fake_ctx: ToolContext, monkeypatch, segment_id: str
) -> None:
    from server import text_generation as mod

    _nr_source(fake_ctx)
    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(
        mod.TextGenerator,
        "create",
        _nr_generator_returning([_nr_segment(segment_id, 4, _RV_NOVEL)]),
    )

    out = await _call(generate_script_plan_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})

    assert out.get("is_error") is True
    assert not _nr_script_plan_path(fake_ctx).exists()


async def test_promote_narration_script_plan_rejects_segment_id_from_another_episode(fake_ctx: ToolContext) -> None:
    _nr_source(fake_ctx)
    _write_nr_script_plan(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["segment_id"] = "E2S01"
    _nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is True
    assert "[invalid_segment_id]" in out["content"][0]["text"]
    assert _nr_quarantine_path(fake_ctx).exists()


async def test_promote_narration_script_plan_reports_schema_breach_without_writing(fake_ctx: ToolContext) -> None:
    """草稿被改到过不了产出时那份 schema：报告刷新、正式文件不写，草稿保留 Agent 手里那份原样内容。"""
    _nr_source(fake_ctx)
    _write_nr_script_plan(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    before = _nr_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    envelope = _read_nr_quarantine(fake_ctx)
    del envelope["content"]["segments"][0]["novel_text"]
    _nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is True
    assert "[schema_invalid]" in out["content"][0]["text"]
    assert _nr_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before
    assert "novel_text" not in _read_nr_quarantine(fake_ctx)["content"]["segments"][0]


async def test_promote_narration_script_plan_aborts_on_concurrent_write(fake_ctx: ToolContext) -> None:
    """取回后正式文件被别的写入方改过：晋升中止、报冲突让 Agent 合并，不静默覆盖。"""
    _nr_source(fake_ctx)
    _write_nr_script_plan(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    _write_nr_script_plan(fake_ctx, [_nr_segment("E1S01", 6, _RV_NOVEL)])

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is True
    assert "并发冲突" in out["content"][0]["text"]
    assert "content.segments" in out["content"][0]["text"]
    assert _nr_quarantine_path(fake_ctx).exists(), "冲突时草稿仍在场，合并后可重试"
    assert (
        json.loads(_nr_script_plan_path(fake_ctx).read_text(encoding="utf-8"))["segments"][0]["duration_seconds"] == 6
    )


async def test_promote_narration_script_plan_revalidates_against_current_source(fake_ctx: ToolContext) -> None:
    """晋升按现值重判原文覆盖：草稿里改写过的正文即便结构合法也拒，正式文件不被污染。"""
    _nr_source(fake_ctx)
    _write_nr_script_plan(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    before = _nr_script_plan_path(fake_ctx).read_text(encoding="utf-8")

    envelope = _read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["novel_text"] = "张三在村口等候"
    _nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is True
    assert "[novel_text_coverage]" in out["content"][0]["text"]
    assert _nr_script_plan_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_promote_narration_script_plan_names_source_scope_on_coverage_violation(fake_ctx: ToolContext) -> None:
    """取回时未指定 source、而 source/ 下不止一集：一字未改的草稿也判不过，报告须指名范围与出路。

    草稿在场时不能重新取回，报告须指引 Agent 通过 patch_draft 更新源文范围。
    """
    _nr_source(fake_ctx)
    (fake_ctx.project_path / "source" / "episode_2.txt").write_text("李四走进院子", encoding="utf-8")
    _write_nr_script_plan(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx)
    assert _read_nr_quarantine(fake_ctx)["meta"]["source"] is None

    out = await _promote_nr(fake_ctx)

    text = out["content"][0]["text"]
    assert out.get("is_error") is True
    assert "[novel_text_coverage]" in text
    assert "整个 source/ 目录" in text
    assert "patch_draft" in text

    refreshed = json.loads((await _open_nr_for_edit(fake_ctx))["content"][0]["text"])["draft"]
    patched = await _call(
        patch_draft_tool(fake_ctx),
        {
            "episode": 1,
            "doc_type": "narration_script_plan",
            "content": refreshed["content"],
            "base_revision": refreshed["revision"],
            "source": "source/episode_1.txt",
        },
    )
    assert patched.get("is_error") is not True, patched
    promoted = await _promote_nr(fake_ctx)
    assert promoted.get("is_error") is not True, promoted


async def test_generate_episode_script_blocked_by_narration_quarantine(fake_ctx: ToolContext) -> None:
    """narration 草稿在场时 prompt_authoring 生成被拦：正式文件此刻仍是上一版，拿它跑 prompt_authoring 等于静默换回旧内容。"""
    _nr_source(fake_ctx)
    _write_nr_script_plan(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [_nr_segment("E1S01", 4, _RV_NOVEL)]},
        violations=[],
    )

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})

    assert out.get("is_error") is True
    assert "草稿待处置" in out["content"][0]["text"]
