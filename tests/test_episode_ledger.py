"""分集账本回填：归一化坐标系、精确子串锚定、消费状态判定、cursor 换算、幂等重跑。"""

import copy
import json
import unicodedata
from pathlib import Path

from lib.episode_ledger import backfill_episode_ledger, normalize_source_text

NOVEL = "第一章少年下山遇见老人。第二章城里起了大火人群四散。第三章一切归于平静少年远行。"
CUT_1 = NOVEL.index("第二章")
CUT_2 = NOVEL.index("第三章")


def _project(tmp_path: Path, *, novel: str | None = NOVEL) -> Path:
    d = tmp_path / "demo"
    (d / "source").mkdir(parents=True)
    if novel is not None:
        (d / "source" / "novel.txt").write_text(novel, encoding="utf-8")
    return d


def _write_episode(project_dir: Path, num: int, text: str) -> None:
    (project_dir / "source" / f"episode_{num}.txt").write_text(text, encoding="utf-8")


def _write_script(project_dir: Path, rel_path: str) -> None:
    path = project_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"episode": 1}), encoding="utf-8")


class TestNormalizeSourceText:
    def test_nfc_and_newlines(self):
        nfd_cafe = unicodedata.normalize("NFD", "café")
        assert normalize_source_text(nfd_cafe) == "café"
        assert normalize_source_text("a\r\nb\rc\nd") == "a\nb\nc\nd"


class TestBackfillAnchoring:
    def test_normal_backfill_adjacent_ranges(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        _write_episode(d, 2, NOVEL[CUT_1:CUT_2])
        _write_script(d, "scripts/episode_1.json")
        project = {
            "episodes": [
                {"episode": 1, "title": "下山", "script_file": "scripts/episode_1.json"},
            ]
        }
        result = backfill_episode_ledger(d, project)
        ep1, ep2 = result["episodes"]
        assert ep1["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": CUT_1}
        assert ep1["ledger_status"] == "consumed"
        assert ep2["source_range"] == {"source_file": "source/novel.txt", "start": CUT_1, "end": CUT_2}
        assert ep2["ledger_status"] == "planned"  # 无下游产物

    def test_consumed_via_step1_draft(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        draft = d / "drafts" / "episode_1" / "step1_normalized_script.md"
        draft.parent.mkdir(parents=True)
        draft.write_text("规范化剧本", encoding="utf-8")
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["episodes"][0]["ledger_status"] == "consumed"

    def test_consumed_via_entry_script_file_nonstandard_name(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        _write_script(d, "scripts/custom_name.json")
        project = {"episodes": [{"episode": 1, "title": "x", "script_file": "scripts/custom_name.json"}]}
        result = backfill_episode_ledger(d, project)
        assert result["episodes"][0]["ledger_status"] == "consumed"

    def test_unanchored_when_content_edited_even_with_script(self, tmp_path: Path):
        """内容对不上源文 → unanchored 锁定，即使已有剧本（锁定语义优先于 consumed）。"""
        d = _project(tmp_path)
        _write_episode(d, 1, "人工编辑过的内容，源文里不存在。")
        _write_script(d, "scripts/episode_1.json")
        result = backfill_episode_ledger(d, {"episodes": []})
        entry = result["episodes"][0]
        assert entry["ledger_status"] == "unanchored"
        assert entry["source_range"] is None

    def test_orphan_episode_file_creates_entry(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        result = backfill_episode_ledger(d, {"episodes": []})
        entry = result["episodes"][0]
        assert entry["episode"] == 1
        assert entry["title"] == ""
        assert entry["script_file"] == "scripts/episode_1.json"
        assert entry["ledger_status"] == "planned"

    def test_entry_without_episode_file_unanchored(self, tmp_path: Path):
        d = _project(tmp_path)
        project = {"episodes": [{"episode": 1, "title": "直建剧本", "script_file": "scripts/episode_1.json"}]}
        result = backfill_episode_ledger(d, project)
        entry = result["episodes"][0]
        assert entry["ledger_status"] == "unanchored"
        assert entry["source_range"] is None

    def test_empty_episode_file_unanchored(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, "")
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["episodes"][0]["ledger_status"] == "unanchored"

    def test_repeated_text_disambiguated_by_order(self, tmp_path: Path):
        """重复段落靠顺序先验消歧：第 3 集从第 2 集末尾起搜，不会撞回第 1 集的位置。"""
        d = _project(tmp_path, novel="甲段。乙段。甲段。乙段。")
        _write_episode(d, 1, "甲段。")
        _write_episode(d, 2, "乙段。")
        _write_episode(d, 3, "甲段。")
        _write_episode(d, 4, "乙段。")
        result = backfill_episode_ledger(d, {"episodes": []})
        starts = [e["source_range"]["start"] for e in result["episodes"]]
        assert starts == [0, 3, 6, 9]

    def test_resplit_overlap_falls_back_to_full_scan(self, tmp_path: Path):
        """重切后第 2 集起点早于第 1 集末尾：游标起搜落空，退化全文搜并如实记录重叠范围。"""
        d = _project(tmp_path, novel="一二三四五六七八九十")
        _write_episode(d, 1, "一二三四五六")
        _write_episode(d, 2, "四五六七八九")
        result = backfill_episode_ledger(d, {"episodes": []})
        ep1, ep2 = result["episodes"]
        assert (ep1["source_range"]["start"], ep1["source_range"]["end"]) == (0, 6)
        assert (ep2["source_range"]["start"], ep2["source_range"]["end"]) == (3, 9)

    def test_multi_source_files(self, tmp_path: Path):
        d = _project(tmp_path, novel=None)
        (d / "source" / "vol_a.txt").write_text("上卷甲乙丙丁。", encoding="utf-8")
        (d / "source" / "vol_b.txt").write_text("下卷戊己庚辛。", encoding="utf-8")
        _write_episode(d, 1, "上卷甲乙")
        _write_episode(d, 2, "下卷戊己")
        result = backfill_episode_ledger(d, {"episodes": []})
        ep1, ep2 = result["episodes"]
        assert ep1["source_range"]["source_file"] == "source/vol_a.txt"
        assert ep2["source_range"]["source_file"] == "source/vol_b.txt"

    def test_pre_cursor_duplicate_does_not_shadow_other_source(self, tmp_path: Path):
        """先验文件游标前的重复段不得遮蔽另一源文件的前向匹配（先全员游标搜，再全员全文搜）。"""
        d = _project(tmp_path, novel=None)
        (d / "source" / "vol_a.txt").write_text("甲段。乙段。", encoding="utf-8")
        (d / "source" / "vol_b.txt").write_text("丙段。甲段。", encoding="utf-8")
        _write_episode(d, 1, "甲段。乙段。")
        _write_episode(d, 2, "甲段。")
        result = backfill_episode_ledger(d, {"episodes": []})
        ep2 = result["episodes"][1]
        assert ep2["source_range"] == {"source_file": "source/vol_b.txt", "start": 3, "end": 6}

    def test_explicit_null_ledger_status_treated_as_absent(self, tmp_path: Path):
        """显式 ledger_status: null 视同缺失（与 data_validator 的放行语义一致），正常回填。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        project = {
            "episodes": [{"episode": 1, "title": "x", "script_file": "scripts/episode_1.json", "ledger_status": None}]
        }
        result = backfill_episode_ledger(d, project)
        entry = result["episodes"][0]
        assert entry["ledger_status"] == "planned"
        assert entry["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": CUT_1}

    def test_bool_episode_does_not_collide_with_episode_one(self, tmp_path: Path):
        """episode: true 不得与第 1 集同键碰撞（bool 是 int 子类）：bool 条目原样保留不回填。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        project = {
            "episodes": [
                {"episode": True, "title": "坏数据", "script_file": "scripts/episode_1.json"},
                {"episode": 1, "title": "真一集", "script_file": "scripts/episode_1.json"},
            ]
        }
        result = backfill_episode_ledger(d, project)
        bool_entry = next(e for e in result["episodes"] if e["title"] == "坏数据")
        int_entry = next(e for e in result["episodes"] if e["title"] == "真一集")
        assert "ledger_status" not in bool_entry
        assert int_entry["ledger_status"] == "planned"

    def test_string_episode_number_does_not_duplicate_entry(self, tmp_path: Path):
        """episode: "1"（历史手编数据）按数字解析，不为同一逻辑集额外新建孤儿条目。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        project = {"episodes": [{"episode": "1", "title": "旧条目", "script_file": "scripts/episode_1.json"}]}
        result = backfill_episode_ledger(d, project)
        assert len(result["episodes"]) == 1
        entry = result["episodes"][0]
        assert entry["episode"] == "1"  # 原值不改写
        assert entry["ledger_status"] == "planned"

    def test_nfd_source_crlf_episode_still_match(self, tmp_path: Path):
        """NFD 源文（macOS/越南语导入）+ CRLF 集文件双侧归一化后照常精确匹配。

        CRLF 一侧经 read_text 通用换行模式已被折叠，本测试端到端真正验证的是 NFC
        归一化；换行折叠对非 read_text 消费方的保障由 TestNormalizeSourceText 锚定。
        """
        d = _project(tmp_path, novel=unicodedata.normalize("NFD", "café au lait\r\nsecond line here"))
        _write_episode(d, 1, "café au lait\r\n")
        result = backfill_episode_ledger(d, {"episodes": []})
        entry = result["episodes"][0]
        # 归一化坐标系："café au lait\n" 共 13 字符（é 合成为单字符、CRLF 折为单 \n）
        assert entry["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": 13}

    def test_no_source_dir_all_unanchored_cursor_none(self, tmp_path: Path):
        d = tmp_path / "demo"
        d.mkdir()
        project = {"episodes": [{"episode": 1, "title": "x", "script_file": "scripts/episode_1.json"}]}
        result = backfill_episode_ledger(d, project)
        assert result["episodes"][0]["ledger_status"] == "unanchored"
        assert result["planning_cursor"] is None

    def test_remaining_and_raw_excluded_from_source_candidates(self, tmp_path: Path):
        """_remaining.txt 与 source/raw/ 内文件不得进入候选源（避免集文件被锚到余文上）。"""
        d = _project(tmp_path, novel=None)
        (d / "source" / "_remaining.txt").write_text(NOVEL, encoding="utf-8")
        (d / "source" / "raw").mkdir()
        (d / "source" / "raw" / "origin.txt").write_text(NOVEL, encoding="utf-8")
        _write_episode(d, 1, NOVEL[:CUT_1])
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["episodes"][0]["ledger_status"] == "unanchored"


class TestPlanningCursor:
    def test_cursor_from_remaining(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        (d / "source" / "_remaining.txt").write_text(NOVEL[CUT_1:], encoding="utf-8")
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["planning_cursor"] == {"source_file": "source/novel.txt", "offset": CUT_1}

    def test_cursor_fallback_to_last_anchored_end(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["planning_cursor"] == {"source_file": "source/novel.txt", "offset": CUT_1}

    def test_cursor_empty_remaining_uses_last_end(self, tmp_path: Path):
        """空余文无定位信息（find("") 恒为 0），必须走 fallback 而非错锚到文件头。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        (d / "source" / "_remaining.txt").write_text("", encoding="utf-8")
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["planning_cursor"] == {"source_file": "source/novel.txt", "offset": CUT_1}

    def test_cursor_none_when_no_evidence(self, tmp_path: Path):
        d = _project(tmp_path)
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["planning_cursor"] is None

    def test_cursor_existing_value_untouched(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        existing = {"source_file": "source/novel.txt", "offset": 5}
        result = backfill_episode_ledger(d, {"episodes": [], "planning_cursor": existing})
        assert result["planning_cursor"] == existing

    def test_cursor_null_value_rederived(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        result = backfill_episode_ledger(d, {"episodes": [], "planning_cursor": None})
        assert result["planning_cursor"] == {"source_file": "source/novel.txt", "offset": CUT_1}

    def test_cursor_remaining_later_than_last_end_wins(self, tmp_path: Path):
        """余文起点晚于最后锚定末尾（用户主动跳过一段）：以余文匹配为准，而非 fallback。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        (d / "source" / "_remaining.txt").write_text(NOVEL[CUT_2:], encoding="utf-8")
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["planning_cursor"] == {"source_file": "source/novel.txt", "offset": CUT_2}

    def test_cursor_stale_remaining_does_not_rewind_into_consumed(self, tmp_path: Path):
        """陈旧余文（崩溃残留，仍含已拆集内容）匹配位置早于最后锚定末尾：以锚定证据为准。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        _write_episode(d, 2, NOVEL[CUT_1:CUT_2])
        (d / "source" / "_remaining.txt").write_text(NOVEL[CUT_1:], encoding="utf-8")
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["planning_cursor"] == {"source_file": "source/novel.txt", "offset": CUT_2}

    def test_cursor_cross_file_remaining_not_blocked_by_guard(self, tmp_path: Path):
        """余文匹配在另一源文件时防回退守卫不干预（守卫只比较同文件偏移）。"""
        d = _project(tmp_path, novel=None)
        (d / "source" / "vol_a.txt").write_text("上卷甲乙丙丁。", encoding="utf-8")
        (d / "source" / "vol_b.txt").write_text("下卷戊己庚辛。", encoding="utf-8")
        _write_episode(d, 1, "上卷甲乙丙丁。")
        (d / "source" / "_remaining.txt").write_text("下卷戊己庚辛。", encoding="utf-8")
        result = backfill_episode_ledger(d, {"episodes": []})
        assert result["planning_cursor"] == {"source_file": "source/vol_b.txt", "offset": 0}

    def test_remaining_file_never_deleted(self, tmp_path: Path):
        d = _project(tmp_path)
        remaining = d / "source" / "_remaining.txt"
        remaining.write_text(NOVEL, encoding="utf-8")
        backfill_episode_ledger(d, {"episodes": []})
        assert remaining.read_text(encoding="utf-8") == NOVEL


class TestIdempotency:
    def test_double_run_identical(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        _write_episode(d, 2, NOVEL[CUT_1:CUT_2])
        _write_script(d, "scripts/episode_1.json")
        once = backfill_episode_ledger(d, {"episodes": []})
        twice = backfill_episode_ledger(d, once)
        assert twice == once

    def test_existing_ledger_entry_never_touched(self, tmp_path: Path):
        """已带 ledger_status 的条目（如规划器标的 stale）整条跳过，但其范围仍推进顺序先验。"""
        d = _project(tmp_path, novel="甲段。甲段。")
        _write_episode(d, 1, "甲段。")
        _write_episode(d, 2, "甲段。")
        stale_entry = {
            "episode": 1,
            "title": "已失效",
            "script_file": "scripts/episode_1.json",
            "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 3},
            "ledger_status": "stale",
        }
        result = backfill_episode_ledger(d, {"episodes": [copy.deepcopy(stale_entry)]})
        assert result["episodes"][0] == stale_entry
        # 第 2 集从 stale 条目的 end 起搜，锚到第二处"甲段。"而非撞回第一处
        assert result["episodes"][1]["source_range"]["start"] == 3

    def test_does_not_mutate_input(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        project = {"episodes": [{"episode": 1, "title": "x", "script_file": "scripts/episode_1.json"}]}
        snapshot = copy.deepcopy(project)
        backfill_episode_ledger(d, project)
        assert project == snapshot
