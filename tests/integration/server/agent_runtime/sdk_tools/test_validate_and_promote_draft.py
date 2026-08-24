"""Tests for validate_and_promote_draft."""

from __future__ import annotations

import copy
import json

import pytest

from lib import script_review
from lib.draft_quarantine import (
    QUARANTINE_KIND_NARRATION_STEP1,
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    quarantine_path,
    write_quarantine,
)
from lib.reference_video.draft_validation import DraftViolation
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.text_generation import (
    _generate_drama_step1_tool as normalize_drama_script_tool,
)
from server.agent_runtime.sdk_tools.text_generation import (
    _generate_narration_step1_tool as split_narration_segments_tool,
)
from server.agent_runtime.sdk_tools.text_generation import (
    generate_episode_script_tool,
)
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _RV_NOVEL,
    _call,
    _drama_project,
    _drama_quarantine_path,
    _drama_scene,
    _drama_step1_path,
    _nr_generator_returning,
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
    _use_fake_caps,
    _write_drama_step1,
    _write_nr_step1,
    _write_rv_step1,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")

# ---------------------------------------------------------------------------
# 草稿与修复晋升闭环（step1）
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
    assert not _rv_step1_path(fake_ctx).exists()

    envelope = _read_rv_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_STEP1
    assert [v["code"] for v in envelope["violations"]] == [code]
    assert envelope["violations"][0]["label"] == "unit E1U01"
    # 草稿装的是扁平草稿结构（Agent 要改的是正文 / 原文锚 / 时长），不是派生后的落盘形状
    assert envelope["content"]["units"][0]["text"] == unit["text"]
    assert "shots" not in envelope["content"]["units"][0]

    report = out["content"][0]["text"]
    assert f"[{code}]" in report
    assert "unit E1U01" in report
    assert str(_rv_quarantine_path(fake_ctx)) in report
    assert "validate_and_promote_draft" in report


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


async def test_validate_and_promote_draft_promotes_after_repair(fake_ctx: ToolContext, monkeypatch) -> None:
    """Agent 修好草稿后晋升：正式 step1 落盘、草稿清除、结构由正文机械派生。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 在 @[村口] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["unit_id"] == "E1U01"
    assert saved["units"][0]["text"] == "@[张三] 在 @[村口] 出场"


async def test_validate_and_promote_draft_reports_again_without_round_limit(fake_ctx: ToolContext, monkeypatch) -> None:
    """再违约则再返回刷新后的报告、草稿留在原地，可反复晋升——无收敛轮次上限。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])

    for _round in range(3):
        out = await _promote(fake_ctx)
        assert out.get("is_error") is True
        assert "unregistered_asset" in out["content"][0]["text"]
        assert _rv_quarantine_path(fake_ctx).exists()
        assert not _rv_step1_path(fake_ctx).exists()

    # 改成另一类违约后报告随之刷新，不是上一轮的陈旧快照
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 推门，音量 {}"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    await _promote(fake_ctx)
    assert [v["code"] for v in _read_rv_quarantine(fake_ctx)["violations"]] == ["braces_in_description"]


# ---------------------------------------------------------------------------
# step1 乐观并发控制（取回时记基线指纹，晋升前锁内比对）
# ---------------------------------------------------------------------------


async def test_promote_conflicts_when_official_changed_after_open(fake_ctx: ToolContext) -> None:
    """「用户在内容确认界面编辑 + Agent 改草稿并晋升」的双端并发：取回后正式文件被另一写入方
    改过时，晋升中止并返回冲突报告（含最新内容与合并指引），不静默覆盖对方的修改；草稿
    留在原地。按报告把 meta.base_fingerprint 更新为现值（显式确认已合并）后方可重新晋升。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")

    # 模拟取回之后 Web 端保存改写了正式文件
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 在 @[村口] 等候")])
    web_version = _rv_step1_path(fake_ctx).read_text(encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert "并发冲突" in report
    assert "base_fingerprint" in report
    # 冲突报告附上盘上现值的扁平草稿单元，供 Agent 对照合并
    assert "在 @[村口] 等候" in report
    # 正式文件未被覆盖，草稿仍在场
    assert _rv_step1_path(fake_ctx).read_text(encoding="utf-8") == web_version
    assert _rv_quarantine_path(fake_ctx).exists()

    # 按报告指引更新基线指纹（显式确认已合并对方修改）后重新晋升即放行
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["meta"]["base_fingerprint"] = script_review.content_fingerprint(_rv_step1_path(fake_ctx))
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    out = await _promote(fake_ctx)
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_promote_conflict_report_renders_missing_fingerprint_as_json_null(fake_ctx: ToolContext) -> None:
    """取回后正式文件被删除：现值指纹是 null，报告须按 JSON 字面量给出而非字符串 "None"。
    照报告把 meta.base_fingerprint 设为 null 后重晋升即放行——写成字符串则永远比对不上、冲突解不掉。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    _rv_step1_path(fake_ctx).unlink()

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert "null" in report
    assert "None" not in report

    envelope = _read_rv_quarantine(fake_ctx)
    envelope["meta"]["base_fingerprint"] = None
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    out = await _promote(fake_ctx)
    assert out.get("is_error") is not True, out
    assert not _rv_quarantine_path(fake_ctx).exists()


async def test_promote_without_base_fingerprint_meta_promotes_unchecked(fake_ctx: ToolContext) -> None:
    """基线机制引入前产出的存量草稿缺 meta.base_fingerprint 键：按无基线晋升，不被新校验卡死。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_rv_quarantine(fake_ctx)
    del envelope["meta"]["base_fingerprint"]
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    # 取回后正式文件又被改过——存量草稿无基线可比，照旧覆盖（维持引入前语义）
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 在 @[村口] 等候")])

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
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 出场"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is True
    assert "并发冲突" in out["content"][0]["text"]


@pytest.mark.parametrize(
    ("mutate", "hint"),
    [
        (lambda u: u.update(duration_seconds=7), "7"),
        (lambda u: u.pop("duration_seconds"), "duration_seconds"),
        (lambda u: u.update(source_text=""), "source_text"),
    ],
    ids=["off_slot_duration", "duration_removed", "blank_source_text"],
)
async def test_validate_and_promote_draft_rejects_schema_breach(
    fake_ctx: ToolContext, monkeypatch, mutate, hint: str
) -> None:
    """草稿改坏 schema 层字段同样只回报告：晋升与产出走同一份 schema，正式文件不被污染。

    时长枚举在产出侧由 response_schema 卡死；晋升侧若只判内容约束，Agent 把 duration_seconds
    改成非档位值或整个删掉（收成 0 秒）就能一路进正式 step1。
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
async def test_validate_and_promote_draft_reports_broken_outer_shape(
    fake_ctx: ToolContext, monkeypatch, mutate_content
) -> None:
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
    assert not _rv_step1_path(fake_ctx).exists()
    refreshed = _read_rv_quarantine(fake_ctx)
    assert [v["code"] for v in refreshed["violations"]] == ["schema_invalid"]
    # 草稿留在原地且原样保留 Agent 写的那份内容：做收编会把它的原稿改形，它照着报告回看时
    # 反而对不上自己写的东西，改完再晋升这条路就断了
    assert _rv_quarantine_path(fake_ctx).exists()
    assert refreshed["content"] == edited_content


async def test_validate_and_promote_draft_requires_source_provenance(fake_ctx: ToolContext, monkeypatch) -> None:
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
    assert "meta.source 缺失" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_validate_and_promote_draft_reports_promotion_not_split(fake_ctx: ToolContext, monkeypatch) -> None:
    """晋升成功的摘要要说「晋升」：说成「拆分」会让 Agent 以为自己的修改被一次重抽覆盖了。"""
    _rv_source(fake_ctx)
    await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    envelope = _read_rv_quarantine(fake_ctx)
    envelope["content"]["units"][0]["text"] = "@[张三] 起身"
    _rv_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert "晋升" in out["content"][0]["text"]


async def test_writing_reference_step1_clears_stale_step2_quarantine(fake_ctx: ToolContext, monkeypatch) -> None:
    """step1 一变即清掉在场的 step2 草稿：它以旧 step1 为 diff 基底，留着就永远晋升不了。"""
    _rv_source(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    step2_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP2)
    assert step2_path.exists()

    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 起身")])

    assert out.get("is_error") is not True, out
    assert not step2_path.exists()


async def test_promote_reference_step1_preserves_step2_draft_when_content_unchanged(fake_ctx: ToolContext) -> None:
    """情况 B 中途放弃、原样晋升：取回草稿未改动即晋升，写回的 step1 与盘上原值逐字相同，
    此时不该清在场的 step2 草稿——它的保结构 diff 仍然对得上这份没变的基底，Agent
    放弃 step1 修改不该连带销毁一份仍然有效的 step2 修复草稿。"""
    _rv_source(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("@[张三] 起身")])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    step2_path = quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP2)
    assert step2_path.exists()

    await _open_for_edit(fake_ctx, source="source/episode_1.txt")
    out = await _promote(fake_ctx)

    assert out.get("is_error") is not True, out
    assert step2_path.exists()


async def test_validate_and_promote_draft_step2_uses_async_factory(fake_ctx: ToolContext, monkeypatch) -> None:
    """step2 晋升走 ``ScriptGenerator.create``：晋升同样经 _add_metadata 落盘，裸构造会把
    metadata.generator 记成 "unknown"，与直接生成路径的同一份产物对不上。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )

    class _FakeGenerator:
        def __init__(self, _path) -> None:
            raise AssertionError("晋升不得裸构造 ScriptGenerator")

        @classmethod
        async def create(cls, project_path, **_kwargs):
            obj = cls.__new__(cls)
            obj.project_path = project_path
            return obj

        async def promote_reference_step2_draft(self, episode: int):
            return self.project_path / "scripts" / f"episode_{episode}.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    out = await _promote(fake_ctx)
    assert out.get("is_error") is not True, out
    assert "episode_1.json" in out["content"][0]["text"]


async def test_validate_and_promote_draft_refuses_after_mode_switch(fake_ctx: ToolContext) -> None:
    """切走参考路径后不再晋升残留草稿：晋升会按参考路径的形状覆盖该集正式剧本。"""
    _rv_project(fake_ctx, generation_mode="storyboard")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )

    out = await _promote(fake_ctx)
    assert out.get("is_error") is True
    # 项目已是 narration + storyboard，晋升按该变体的草稿位分派：残留的参考路径 step2 草稿不在
    # 它的视野内，故报「本集没有待处置的草稿」而不是去晋升它。
    assert "没有待处置的草稿" in out["content"][0]["text"]
    assert quarantine_path(fake_ctx.project_path, 1, QUARANTINE_KIND_STEP2).exists(), (
        "残留草稿应原样留在盘上，不被这条路径消费"
    )


async def test_validate_and_promote_draft_step2_blocked_by_review_gate(fake_ctx: ToolContext, monkeypatch) -> None:
    """step1 未经确认时 step2 草稿不晋升：常规生成路径在工具入口就被内容确认拦下，两条路不该分叉。

    草稿在场期间用户在 Web 端改过 step1 会让确认指纹失效，该集回到 pending_review——此时晋升等于
    拿一份用户没确认过的 step1 合成正式剧本。
    """
    from server import text_generation as mod

    _rv_project(fake_ctx)
    step1 = _rv_step1_path(fake_ctx)
    step1.parent.mkdir(parents=True, exist_ok=True)
    step1.write_text(json.dumps({"units": []}, ensure_ascii=False), encoding="utf-8")
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_STEP2,
        content={"title": "第1集", "units": [{"text": "@[张三] 起身"}]},
        violations=[],
    )
    monkeypatch.setattr(mod.script_review, "gate_blocks_step2", lambda *_args, **_kw: True)

    out = await _promote(fake_ctx)
    assert out.get("is_error") is True
    assert "尚未完成内容确认" in out["content"][0]["text"]


async def test_validate_and_promote_draft_without_draft(fake_ctx: ToolContext) -> None:
    out = await _promote(fake_ctx)
    assert out.get("is_error") is True
    assert "没有待处置的草稿" in out["content"][0]["text"]


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
        QUARANTINE_KIND_STEP1,
        content={"units": []},
        violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
    )


async def test_generate_episode_script_blocked_by_quarantine(fake_ctx: ToolContext) -> None:
    """草稿在场时 step2 入口阻塞，且给出「改草稿再晋升」而非「去 Web 端确认」的出路。"""
    _rv_project(fake_ctx)
    step1 = _rv_step1_path(fake_ctx)
    step1.parent.mkdir(parents=True, exist_ok=True)
    step1.write_text(json.dumps({"units": []}, ensure_ascii=False), encoding="utf-8")
    _write_rv_quarantine(fake_ctx)

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    assert "草稿待处置" in out["content"][0]["text"]
    assert "validate_and_promote_draft" in out["content"][0]["text"]


async def test_generate_episode_script_preserves_editable_draft_without_violations(fake_ctx: ToolContext) -> None:
    """可编辑草稿没有违约报告，step2 入口应引导校验晋升而不是要求凭空修改。"""
    _rv_project(fake_ctx)
    _write_rv_step1(fake_ctx, [_rv_saved_unit("原始内容")])
    await _open_for_edit(fake_ctx)

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "这是可编辑草稿" in text
    assert "保留已有修改" in text
    assert "按草稿内 violations" not in text


async def test_generate_episode_script_quarantine_precedes_missing_step1(fake_ctx: ToolContext) -> None:
    """首次拆分就违约时正式 step1 本就不存在——先报缺文件会把 Agent 引回重跑拆分（丢弃重抽）。"""
    _rv_project(fake_ctx)
    _write_rv_quarantine(fake_ctx)
    assert not _rv_step1_path(fake_ctx).exists()

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
    # 卡在「缺 narration step1」这道常规校验上，而不是参考路径的草稿
    assert "草稿待处置" not in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# drama step1 的取回编辑与晋升闭环
# ---------------------------------------------------------------------------


async def test_promote_drama_step1_rederives_needs_replan(fake_ctx: ToolContext) -> None:
    """needs_replan 由晋升侧按台词准入重新派生，不取草稿里的值——它是机器判据，
    手写值一旦被采信，后续重规划会漏掉真正需要重排的场景。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])

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
    saved = json.loads(_drama_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["needs_replan"] is True


async def test_promote_drama_step1_reports_schema_breach_without_writing(fake_ctx: ToolContext) -> None:
    """草稿被改坏时正式文件不写：草稿留在场、按 violations 继续改再晋升，不丢内容也不污染正式文件。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])
    before = _drama_step1_path(fake_ctx).read_text(encoding="utf-8")

    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    envelope = _read_drama_quarantine(fake_ctx)
    envelope["content"]["scenes"][0]["duration_seconds"] = 7  # 不在档位内
    _drama_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_drama(fake_ctx)

    assert out.get("is_error") is True
    assert _drama_step1_path(fake_ctx).read_text(encoding="utf-8") == before
    assert _drama_quarantine_path(fake_ctx).exists()
    assert "content.scenes[i]" in out["content"][0]["text"]


async def test_promote_drama_step1_aborts_on_concurrent_write(fake_ctx: ToolContext) -> None:
    """取回与晋升之间正式文件被别的写入方改过 → 中止并报冲突，不静默覆盖对方的保存。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])

    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")
    _write_drama_step1(fake_ctx, [_drama_scene(scene_description="别处改过的描述。")])
    concurrent = _drama_step1_path(fake_ctx).read_text(encoding="utf-8")

    out = await _promote_drama(fake_ctx)

    assert out.get("is_error") is True
    assert _drama_step1_path(fake_ctx).read_text(encoding="utf-8") == concurrent
    assert _drama_quarantine_path(fake_ctx).exists()


async def test_generate_episode_script_blocked_by_drama_quarantine(fake_ctx: ToolContext) -> None:
    """drama 的 step2 与参考生视频同口径：草稿在场即拒绝生成，
    否则会拿正式文件那份上一版内容静默顶替待处置的正文。"""
    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])
    await _open_drama_for_edit(fake_ctx, source="source/episode_1.txt")

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})

    assert out.get("is_error") is True
    assert "草稿待处置" in out["content"][0]["text"]


async def test_normalize_drama_script_clears_quarantine_on_regeneration(fake_ctx: ToolContext, monkeypatch) -> None:
    """重新规范化是刻意的整份重建，与参考生视频的重拆分同口径：正式文件换成新产物的同一临界区内
    清掉上一轮草稿。留着它会让内容确认与 step2 一直阻塞在一份已被取代的内容上，而草稿记下的基线
    指纹此刻也对不上，晋升只会反复报冲突——Agent 没有第二条出路。"""
    from server import text_generation as mod

    _drama_project(fake_ctx)
    _write_drama_step1(fake_ctx, [_drama_scene()])
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

    out = await _call(normalize_drama_script_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})

    assert out.get("is_error") is not True, out
    assert not _drama_quarantine_path(fake_ctx).exists()
    saved = json.loads(_drama_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["scenes"][0]["scene_description"] == "重新规范化后的描述。"


# ---------------------------------------------------------------------------
# narration step1 的取回编辑与晋升闭环
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

    out = await _call(split_narration_segments_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})

    assert out.get("is_error") is True
    report = out["content"][0]["text"]
    assert str(_nr_quarantine_path(fake_ctx)) in report
    assert "[duration_off_tier]" in report
    assert "[unregistered_asset]" in report
    assert "segment E1S01" in report
    assert not _nr_step1_path(fake_ctx).exists(), "正式文件一步不动"
    envelope = _read_nr_quarantine(fake_ctx)
    assert envelope["kind"] == QUARANTINE_KIND_NARRATION_STEP1
    assert envelope["content"]["segments"][0]["segment_id"] == "E1S01"
    assert envelope["meta"]["source"] == "source/episode_1.txt"
    # 正式文件此刻不存在，基线即 null——这份草稿修好晋升时若已被别的写入方建出来，按基线比对报冲突。
    assert envelope["meta"]["base_fingerprint"] is None


async def test_split_narration_segments_clears_quarantine_on_regeneration(fake_ctx: ToolContext, monkeypatch) -> None:
    """重跑拆分成功后清掉上一轮的草稿：正式文件已是新产物，旧草稿留着只会让内容确认与 step2 继续
    阻塞在一份已被取代的内容上。"""
    from server import text_generation as mod

    _nr_source(fake_ctx)
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_STEP1,
        content={"segments": [_nr_segment("E1S01", 5)]},
        violations=[],
    )
    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", _nr_generator_returning([_nr_segment("E1S01", 4, _RV_NOVEL)]))

    out = await _call(split_narration_segments_tool(fake_ctx), {"episode": 1, "source": "source/episode_1.txt"})

    assert out.get("is_error") is not True, out
    assert not _nr_quarantine_path(fake_ctx).exists()
    assert json.loads(_nr_step1_path(fake_ctx).read_text(encoding="utf-8"))["segments"][0]["duration_seconds"] == 4


async def test_promote_narration_step1_reports_schema_breach_without_writing(fake_ctx: ToolContext) -> None:
    """草稿被改到过不了产出时那份 schema：报告刷新、正式文件不写，草稿保留 Agent 手里那份原样内容。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    before = _nr_step1_path(fake_ctx).read_text(encoding="utf-8")

    envelope = _read_nr_quarantine(fake_ctx)
    del envelope["content"]["segments"][0]["novel_text"]
    _nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is True
    assert "[schema_invalid]" in out["content"][0]["text"]
    assert _nr_step1_path(fake_ctx).read_text(encoding="utf-8") == before
    assert "novel_text" not in _read_nr_quarantine(fake_ctx)["content"]["segments"][0]


async def test_promote_narration_step1_aborts_on_concurrent_write(fake_ctx: ToolContext) -> None:
    """取回后正式文件被别的写入方改过：晋升中止、报冲突让 Agent 合并，不静默覆盖。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 6, _RV_NOVEL)])

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is True
    assert "并发冲突" in out["content"][0]["text"]
    assert "content.segments" in out["content"][0]["text"]
    assert _nr_quarantine_path(fake_ctx).exists(), "冲突时草稿仍在场，合并后可重试"
    assert json.loads(_nr_step1_path(fake_ctx).read_text(encoding="utf-8"))["segments"][0]["duration_seconds"] == 6


async def test_promote_narration_step1_revalidates_against_current_source(fake_ctx: ToolContext) -> None:
    """晋升按现值重判原文覆盖：草稿里改写过的正文即便结构合法也拒，正式文件不被污染。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx, source="source/episode_1.txt")
    before = _nr_step1_path(fake_ctx).read_text(encoding="utf-8")

    envelope = _read_nr_quarantine(fake_ctx)
    envelope["content"]["segments"][0]["novel_text"] = "张三在村口等候"
    _nr_quarantine_path(fake_ctx).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    out = await _promote_nr(fake_ctx)

    assert out.get("is_error") is True
    assert "[novel_text_coverage]" in out["content"][0]["text"]
    assert _nr_step1_path(fake_ctx).read_text(encoding="utf-8") == before


async def test_promote_narration_step1_names_source_scope_on_coverage_violation(fake_ctx: ToolContext) -> None:
    """取回时未指定 source、而 source/ 下不止一集：一字未改的草稿也判不过，报告须指名范围与出路。

    草稿在场时不能重新取回，改 meta.source 是 Agent 唯一的出路；报告只说「分镜正文须原样复制
    原文」的话，它只会去改一份本来就正确的分镜表。
    """
    _nr_source(fake_ctx)
    (fake_ctx.project_path / "source" / "episode_2.txt").write_text("李四走进院子", encoding="utf-8")
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    await _open_nr_for_edit(fake_ctx)
    assert _read_nr_quarantine(fake_ctx)["meta"]["source"] is None

    out = await _promote_nr(fake_ctx)

    text = out["content"][0]["text"]
    assert out.get("is_error") is True
    assert "[novel_text_coverage]" in text
    assert "整个 source/ 目录" in text
    assert "meta.source" in text


async def test_generate_episode_script_blocked_by_narration_quarantine(fake_ctx: ToolContext) -> None:
    """narration 草稿在场时 step2 生成被拦：正式文件此刻仍是上一版，拿它跑 step2 等于静默换回旧内容。"""
    _nr_source(fake_ctx)
    _write_nr_step1(fake_ctx, [_nr_segment("E1S01", 4, _RV_NOVEL)])
    write_quarantine(
        fake_ctx.project_path,
        1,
        QUARANTINE_KIND_NARRATION_STEP1,
        content={"segments": [_nr_segment("E1S01", 4, _RV_NOVEL)]},
        violations=[],
    )

    out = await _call(generate_episode_script_tool(fake_ctx), {"episode": 1})

    assert out.get("is_error") is True
    assert "草稿待处置" in out["content"][0]["text"]
