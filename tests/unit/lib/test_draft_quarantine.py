"""草稿信封与违约收集的单元测试。

覆盖的是「产物不丢弃」这条机制的底座：信封读写往返、坏 JSON 的降级口径、多条违约的收集与
报告渲染。上层闭环（拆分 / 晋升 / gate 阻塞）的测试在 ``tests/server/agent_runtime/
test_sdk_tools.py``、``tests/lib/test_script_generator_reference_branch.py`` 与
``tests/test_script_review.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    QUARANTINE_SCHEMA_VERSION,
    clear_quarantine,
    draft_revision,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
    render_report,
    resolve_schema_version,
    violation_entries,
    write_quarantine,
)
from lib.draft_violation import (
    DraftViolation,
    DraftViolations,
    collect_violations,
    render_violation_report,
    violation_items,
)


def _violation(code: str = "unregistered_asset", label: str = "unit E1U01") -> DraftViolation:
    return DraftViolation(f"{label} 引用了未登记的资产名", code=code, label=label)


class TestEnvelope:
    def test_write_read_roundtrip_preserves_content_violations_and_meta(self, tmp_path: Path):
        path = write_quarantine(
            tmp_path,
            3,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"text": "镜头1：门开了"}]},
            violations=[_violation()],
            meta={"source": "source/episode_3.txt"},
        )
        assert path == quarantine_path(tmp_path, 3, QUARANTINE_KIND_SCRIPT_PLAN)

        draft = read_quarantine(tmp_path, 3, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.kind == QUARANTINE_KIND_SCRIPT_PLAN
        assert draft.episode == 3
        assert draft.content == {"units": [{"text": "镜头1：门开了"}]}
        assert draft.violations == [
            {
                "code": "unregistered_asset",
                "label": "unit E1U01",
                "message": "unit E1U01 引用了未登记的资产名",
                "line": None,
            }
        ]
        assert draft.meta == {"source": "source/episode_3.txt"}

    def test_write_creates_missing_drafts_dir(self, tmp_path: Path):
        """该集从未产出过 script_plan 时目录还不存在——首次拆分就违约是常态，不能因此写不下去。"""
        assert not (tmp_path / "drafts").exists()
        write_quarantine(
            tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": []}, violations=[_violation()]
        )
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)

    def test_script_plan_and_prompt_authoring_drafts_are_separate_files(self, tmp_path: Path):
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN, content={"units": []}, violations=[])
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": []}, violations=[])
        assert quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) != quarantine_path(
            tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING
        )

    def test_broken_json_reads_as_none_but_still_counts_as_present(self, tmp_path: Path):
        """Agent 手改草稿改坏 JSON 是可预期的中间态：读不出内容，但不能因此被当成「无草稿」
        而放行 gate 与 prompt_authoring。"""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True)
        path.write_text("{不是 JSON", encoding="utf-8")

        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is True

    def test_envelope_without_content_object_reads_as_none(self, tmp_path: Path):
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"kind": QUARANTINE_KIND_SCRIPT_PLAN, "content": []}), encoding="utf-8")
        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None

    def test_envelope_with_non_numeric_episode_reads_as_none(self, tmp_path: Path):
        """episode 被手改成非数字与 content 形状坏同口径：返回 None 而非抛出，exists 仍为真。"""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {"kind": QUARANTINE_KIND_SCRIPT_PLAN, "episode": "一", "content": {"units": []}}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is True

    @pytest.mark.parametrize(
        "envelope",
        [
            {"kind": QUARANTINE_KIND_PROMPT_AUTHORING, "episode": 1, "content": {"units": []}},
            {"kind": QUARANTINE_KIND_SCRIPT_PLAN, "episode": 2, "content": {"units": []}},
            {"episode": 1, "content": {"units": []}},
            {"kind": QUARANTINE_KIND_SCRIPT_PLAN, "content": {"units": []}},
        ],
        ids=["kind_mismatch", "episode_mismatch", "kind_missing", "episode_missing"],
    )
    def test_envelope_identity_must_match_requested_draft(self, tmp_path: Path, envelope: dict):
        """kind / episode 对不上或缺失按形状坏处理，不退回请求值。

        不校验就等于把这两个字段解析出来又丢掉：一份从别集拷过来的信封会带着它自己的
        meta.source 过原文锚校验，再按本集的 unit_id 重建、覆盖本集的正式 script_plan。
        """
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is True

    def test_clear_is_idempotent(self, tmp_path: Path):
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN, content={"units": []}, violations=[])
        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert not quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)


class TestSchemaVersion:
    """信封的版本位：写侧固定盖章、读侧缺失即 v1，且不改变任何现有键的语义。"""

    def _envelope_with_meta(self, tmp_path: Path, meta: dict) -> Path:
        """按给定的 meta 原值写一份草稿信封，绕开写侧的盖章。"""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "kind": QUARANTINE_KIND_SCRIPT_PLAN,
                    "episode": 1,
                    "meta": meta,
                    "violations": [],
                    "content": {"units": [{"text": "镜头1：门开了"}]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_written_draft_carries_current_schema_version_on_disk(self, tmp_path: Path):
        path = write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )
        envelope = json.loads(path.read_text(encoding="utf-8"))
        assert envelope["meta"]["schema_version"] == QUARANTINE_SCHEMA_VERSION
        assert envelope["meta"]["source"] == "source/episode_1.txt"

    def test_write_stamps_current_version_over_a_stale_one_carried_in_meta(self, tmp_path: Path):
        """从读回的草稿改一改再写回时，写出去的这份就是本代码这一版；沿用读到的旧值等于给旧版本贴新语义。"""
        path = write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[],
            meta={"schema_version": 99},
        )
        assert json.loads(path.read_text(encoding="utf-8"))["meta"]["schema_version"] == QUARANTINE_SCHEMA_VERSION

    def test_roundtrip_exposes_version_as_field_not_as_meta_key(self, tmp_path: Path):
        """消费方读解析结果，不去 meta 里摸裸键——版本位不留在 meta 上，meta 只剩重判上下文。"""
        write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == QUARANTINE_SCHEMA_VERSION
        assert draft.meta == {"source": "source/episode_1.txt"}

    def test_legacy_envelope_without_version_reads_as_v1_unchanged(self, tmp_path: Path):
        """不带版本位的信封解析为 v1，其余字段照常读出：它与盖过章的信封走同一条读路。"""
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt"})
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == 1
        assert draft.meta == {"source": "source/episode_1.txt"}
        assert draft.content == {"units": [{"text": "镜头1：门开了"}]}
        assert draft.violations == []

    def test_version_bit_does_not_shift_the_optimistic_concurrency_token(self, tmp_path: Path):
        """同一份内容盖章前后 revision 相同：版本位若进入令牌，持有未盖章草稿 revision 的 Agent
        会被判 revision_conflict。"""
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt"})
        legacy = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"text": "镜头1：门开了"}]},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )
        stamped = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert legacy is not None
        assert stamped is not None
        assert draft_revision(legacy) == draft_revision(stamped)

    @pytest.mark.parametrize(
        "raw",
        ["1", 1.0, True, None, [1], {"v": 1}, 0, -3],
        ids=["str", "float", "bool", "null", "list", "dict", "zero", "negative"],
    )
    def test_unusable_version_reads_as_v1(self, tmp_path: Path, raw: object):
        """版本位不可信时唯一确定的事实是「它不是本代码写出来的」，而只有 v1 一种语义：
        为此拒读会把一份仍能被 Agent 改好再晋升的草稿变成「无草稿」。"""
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt", "schema_version": raw})
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == 1
        assert resolve_schema_version({"schema_version": raw}) == 1

    def test_future_version_is_returned_as_is_and_still_readable(self, tmp_path: Path):
        """未来版本原值返回、不夹取也不拒读：本模块不做迁移框架，夹取会把新版草稿伪装成当前版本。"""
        future = QUARANTINE_SCHEMA_VERSION + 7
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt", "schema_version": future})
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == future
        assert draft.content == {"units": [{"text": "镜头1：门开了"}]}

    def test_missing_meta_object_reads_as_v1_with_empty_context(self, tmp_path: Path):
        """meta 整个缺失（或被改成非对象）与版本位缺失同口径，不抛错。"""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"kind": QUARANTINE_KIND_SCRIPT_PLAN, "episode": 1, "meta": [], "content": {"units": []}}),
            encoding="utf-8",
        )
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == 1
        assert draft.meta == {}


class TestReport:
    def test_report_names_draft_field_and_promote_tool(self, tmp_path: Path):
        """处置指引要写「改哪个文件的哪个字段、改完调什么」——Agent 不知道产物还在盘上就会重抽。"""
        path = quarantine_path(tmp_path, 2, QUARANTINE_KIND_SCRIPT_PLAN)
        text = render_report(path, QUARANTINE_KIND_SCRIPT_PLAN, [_violation()], episode=2)
        assert str(path) in text
        assert "按报告字段路径修复" in text
        assert "content.units[i]" in text
        assert 'promote_draft({"episode": 2, "doc_type": "reference_script_plan", "base_revision":' in text
        assert "无轮次上限" in text

    def test_report_numbers_each_violation_with_its_class(self):
        text = render_violation_report([_violation("unregistered_asset"), _violation("too_many_shots", "unit E1U02")])
        assert text.splitlines()[0].startswith("1. [unregistered_asset] ")
        assert text.splitlines()[1].startswith("2. [too_many_shots] ")

    def test_drama_script_plan_report_points_at_scene_fields(self, tmp_path: Path):
        """drama 草稿改的是分镜内容表，不是参考生视频的 units——指引里报错字段路径写错，
        Agent 会照着改一个不存在的字段再晋升，白跑一轮。"""
        path = quarantine_path(tmp_path, 3, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        text = render_report(path, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN, [_violation()], episode=3)

        assert path.name == "script_plan_normalized_script.invalid.json"
        assert "content.scenes[i]" in text
        assert "units[i]" not in text
        assert 'promote_draft({"episode": 3, "doc_type": "drama_script_plan", "base_revision":' in text

    def test_narration_script_plan_report_points_at_segment_fields(self, tmp_path: Path):
        """narration 草稿改的是分镜表：指引里的字段路径写错，Agent 会照着改一个不存在的字段
        再晋升，白跑一轮。"""
        path = quarantine_path(tmp_path, 4, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        text = render_report(path, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, [_violation()], episode=4)

        assert path.name == "script_plan_segments.invalid.json"
        assert "content.segments[i]" in text
        assert "units[i]" not in text
        assert "scenes[i]" not in text
        assert 'promote_draft({"episode": 4, "doc_type": "narration_script_plan", "base_revision":' in text

    def test_each_script_plan_variant_has_its_own_draft_file(self, tmp_path: Path):
        """三条路线的 script_plan 草稿同目录并存而不互相覆盖：共用一个文件名会让换过路线的项目上
        残留的草稿被当成本路线的待处置件读进来。"""
        names = {
            quarantine_path(tmp_path, 1, kind).name
            for kind in (
                QUARANTINE_KIND_SCRIPT_PLAN,
                QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            )
        }
        assert len(names) == 3

    def test_prompt_authoring_report_only_points_at_text(self, tmp_path: Path):
        """prompt_authoring 的 unit 只有正文可改：时长与原文锚是 script_plan 已确认的内容契约，不在这一层修。"""
        text = render_report(tmp_path / "d.json", QUARANTINE_KIND_PROMPT_AUTHORING, [_violation()], episode=1)
        assert "content.units[i].text" in text
        assert "source_text" not in text

    def test_entries_carry_class_and_locator(self):
        assert violation_entries([_violation("blank_shot", "unit E2U07")]) == [
            {"code": "blank_shot", "label": "unit E2U07", "message": "unit E2U07 引用了未登记的资产名", "line": None}
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
        assert "[a]" in str(aggregate)
        assert "[b]" in str(aggregate)
        # 聚合体仍是 DraftViolation：调用方不必在「一条」与「多条」之间分叉出两套处置路径
        assert isinstance(aggregate, DraftViolation)

    def test_single_violation_flattens_to_itself(self):
        single = _violation("a")
        assert violation_items(single) == [single]
