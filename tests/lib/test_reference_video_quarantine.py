"""隔离草稿信封与违约收集的单元测试。

覆盖的是「产物不丢弃」这条机制的底座：信封读写往返、坏 JSON 的降级口径、多条违约的收集与
报告渲染。上层闭环（拆分 / 晋升 / gate 阻塞）的测试在 ``tests/server/agent_runtime/
test_sdk_tools.py``、``tests/lib/test_script_generator_reference_branch.py`` 与
``tests/test_script_review.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.reference_video.draft_validation import (
    DraftViolation,
    DraftViolations,
    collect_violations,
    render_violation_report,
    violation_items,
)
from lib.reference_video.quarantine import (
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    clear_quarantine,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
    render_report,
    violation_entries,
    write_quarantine,
)

pytestmark = pytest.mark.unit


def _violation(code: str = "unregistered_asset", label: str = "unit E1U01") -> DraftViolation:
    return DraftViolation(f"{label} 引用了未登记的资产名", code=code, label=label)


class TestEnvelope:
    def test_write_read_roundtrip_preserves_content_violations_and_meta(self, tmp_path: Path):
        path = write_quarantine(
            tmp_path,
            3,
            QUARANTINE_KIND_STEP1,
            content={"units": [{"text": "镜头1：门开了"}]},
            violations=[_violation()],
            meta={"source": "source/episode_3.txt"},
        )
        assert path == quarantine_path(tmp_path, 3, QUARANTINE_KIND_STEP1)

        draft = read_quarantine(tmp_path, 3, QUARANTINE_KIND_STEP1)
        assert draft is not None
        assert draft.kind == QUARANTINE_KIND_STEP1
        assert draft.episode == 3
        assert draft.content == {"units": [{"text": "镜头1：门开了"}]}
        assert draft.violations == [
            {"code": "unregistered_asset", "label": "unit E1U01", "message": "unit E1U01 引用了未登记的资产名"}
        ]
        assert draft.meta == {"source": "source/episode_3.txt"}

    def test_write_creates_missing_drafts_dir(self, tmp_path: Path):
        """该集从未产出过 step1 时目录还不存在——首次拆分就违约是常态，不能因此写不下去。"""
        assert not (tmp_path / "drafts").exists()
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP2, content={"units": []}, violations=[_violation()])
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_STEP2)

    def test_step1_and_step2_drafts_are_separate_files(self, tmp_path: Path):
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP1, content={"units": []}, violations=[])
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP2, content={"units": []}, violations=[])
        assert quarantine_path(tmp_path, 1, QUARANTINE_KIND_STEP1) != quarantine_path(
            tmp_path, 1, QUARANTINE_KIND_STEP2
        )

    def test_broken_json_reads_as_none_but_still_counts_as_present(self, tmp_path: Path):
        """agent 手改草稿改坏 JSON 是可预期的中间态：读不出内容，但不能因此被当成「没有隔离」
        而放行 gate 与 step2。"""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_STEP1)
        path.parent.mkdir(parents=True)
        path.write_text("{不是 JSON", encoding="utf-8")

        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP1) is None
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_STEP1) is True

    def test_envelope_without_content_object_reads_as_none(self, tmp_path: Path):
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_STEP1)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"kind": QUARANTINE_KIND_STEP1, "content": []}), encoding="utf-8")
        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP1) is None

    def test_clear_is_idempotent(self, tmp_path: Path):
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP1, content={"units": []}, violations=[])
        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP1)
        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_STEP1)
        assert not quarantine_exists(tmp_path, 1, QUARANTINE_KIND_STEP1)


class TestReport:
    def test_report_names_draft_field_and_promote_tool(self, tmp_path: Path):
        """处置指引要写「改哪个文件的哪个字段、改完调什么」——agent 不知道产物还在盘上就会重抽。"""
        path = quarantine_path(tmp_path, 2, QUARANTINE_KIND_STEP1)
        text = render_report(path, QUARANTINE_KIND_STEP1, [_violation()], episode=2)
        assert str(path) in text
        assert "content.units[i].text" in text
        assert 'validate_and_promote_reference_draft({"episode": 2})' in text
        assert "无轮次上限" in text

    def test_report_numbers_each_violation_with_its_class(self):
        text = render_violation_report([_violation("unregistered_asset"), _violation("too_many_shots", "unit E1U02")])
        assert text.splitlines()[0].startswith("1. [unregistered_asset] ")
        assert text.splitlines()[1].startswith("2. [too_many_shots] ")

    def test_step2_report_only_points_at_text(self, tmp_path: Path):
        """step2 的 unit 只有正文可改：时长与原文锚是 step1 已确认的内容契约，不在这一层修。"""
        text = render_report(tmp_path / "d.json", QUARANTINE_KIND_STEP2, [_violation()], episode=1)
        assert "content.units[i].text" in text
        assert "source_text" not in text

    def test_entries_carry_class_and_locator(self):
        assert violation_entries([_violation("blank_shot", "unit E2U07")]) == [
            {"code": "blank_shot", "label": "unit E2U07", "message": "unit E2U07 引用了未登记的资产名"}
        ]


class TestCollectViolations:
    def test_collects_all_instead_of_stopping_at_first(self):
        def bad(code: str):
            def _check():
                raise _violation(code)

            return _check

        found = collect_violations([bad("a"), lambda: None, bad("b")])
        assert [v.code for v in found] == ["a", "b"]

    def test_non_violation_errors_are_not_swallowed(self):
        """解析器内部错误 / 脏数据引发的类型错误照常上抛，不被伪装成一条内容违约。"""

        def boom():
            raise TypeError("脏数据")

        with pytest.raises(TypeError):
            collect_violations([boom])

    def test_aggregate_flattens_and_renders_as_report(self):
        aggregate = DraftViolations([_violation("a"), _violation("b")])
        assert [v.code for v in violation_items(aggregate)] == ["a", "b"]
        assert "[a]" in str(aggregate) and "[b]" in str(aggregate)
        # 聚合体仍是 DraftViolation：调用方不必在「一条」与「多条」之间分叉出两套处置路径
        assert isinstance(aggregate, DraftViolation)

    def test_single_violation_flattens_to_itself(self):
        single = _violation("a")
        assert violation_items(single) == [single]
