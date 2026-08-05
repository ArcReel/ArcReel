"""分集账本：归一化坐标系、源文指纹、孤儿条目登记、位置记录检查。"""

import copy
import json
import unicodedata
from pathlib import Path

import pytest

from lib.episode_ledger import (
    compute_source_fingerprints,
    discover_sources,
    episodes_without_source_range,
    mismatched_source_fingerprints,
    normalize_source_text,
    register_orphan_episode_entries,
)

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
    @pytest.mark.unit
    def test_nfc_and_newlines(self):
        nfd_cafe = unicodedata.normalize("NFD", "café")
        assert normalize_source_text(nfd_cafe) == "café"
        assert normalize_source_text("a\r\nb\rc\nd") == "a\nb\nc\nd"


@pytest.mark.integration
class TestRegisterOrphanEpisodeEntries:
    def test_orphan_episode_file_creates_entry_without_source_range(self, tmp_path: Path):
        """孤儿派生文件补建条目：只做登记，不写位置记录 source_range。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        result = register_orphan_episode_entries(d, {"episodes": []})
        entry = result["episodes"][0]
        assert entry["episode"] == 1
        assert entry["title"] == ""
        assert entry["script_file"] == "scripts/episode_1.json"
        assert entry["ledger_status"] == "planned"
        assert "source_range" not in entry

    def test_orphan_with_downstream_products_marked_consumed(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        _write_script(d, "scripts/episode_1.json")
        result = register_orphan_episode_entries(d, {"episodes": []})
        assert result["episodes"][0]["ledger_status"] == "consumed"

    def test_existing_entry_never_touched(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        entry = {
            "episode": 1,
            "title": "已规划",
            "script_file": "scripts/episode_1.json",
            "source_range": {"source_file": "source/novel.txt", "start": 0, "end": CUT_1},
            "ledger_status": "planned",
        }
        result = register_orphan_episode_entries(d, {"episodes": [copy.deepcopy(entry)]})
        assert result["episodes"] == [entry]

    def test_string_episode_number_does_not_duplicate_entry(self, tmp_path: Path):
        """episode: "1"（历史手编数据）按数字解析，不为同一逻辑集额外新建孤儿条目。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        project = {"episodes": [{"episode": "1", "title": "旧条目", "script_file": "scripts/episode_1.json"}]}
        result = register_orphan_episode_entries(d, project)
        assert len(result["episodes"]) == 1
        assert result["episodes"][0]["episode"] == "1"  # 原值不改写

    def test_bool_episode_does_not_collide_with_episode_one(self, tmp_path: Path):
        """episode: true 不得与第 1 集同键碰撞（bool 是 int 子类）：第 1 集照常补建。"""
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        project = {"episodes": [{"episode": True, "title": "坏数据", "script_file": "scripts/episode_1.json"}]}
        result = register_orphan_episode_entries(d, project)
        assert len(result["episodes"]) == 2
        assert any(e.get("episode") == 1 for e in result["episodes"])

    def test_double_run_identical(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        _write_episode(d, 2, NOVEL[CUT_1:CUT_2])
        once = register_orphan_episode_entries(d, {"episodes": []})
        twice = register_orphan_episode_entries(d, once)
        assert twice == once

    def test_does_not_mutate_input(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        project = {"episodes": []}
        snapshot = copy.deepcopy(project)
        register_orphan_episode_entries(d, project)
        assert project == snapshot

    def test_broken_episodes_container_returned_as_is(self, tmp_path: Path):
        d = _project(tmp_path)
        _write_episode(d, 1, NOVEL[:CUT_1])
        result = register_orphan_episode_entries(d, {"episodes": "坏数据"})
        assert result["episodes"] == "坏数据"

    def test_remaining_file_never_deleted(self, tmp_path: Path):
        d = _project(tmp_path)
        remaining = d / "source" / "_remaining.txt"
        remaining.write_text(NOVEL, encoding="utf-8")
        register_orphan_episode_entries(d, {"episodes": []})
        assert remaining.read_text(encoding="utf-8") == NOVEL


@pytest.mark.unit
class TestEpisodesWithoutSourceRange:
    def _entry(self, num, source_range="skip"):
        entry = {"episode": num, "title": "x", "script_file": f"scripts/episode_{num}.json"}
        if source_range != "skip":
            entry["source_range"] = source_range
        return entry

    def test_missing_and_null_and_broken_all_listed(self):
        project = {
            "episodes": [
                self._entry(1, {"source_file": "source/novel.txt", "start": 0, "end": 10}),
                self._entry(2),
                self._entry(3, None),
                self._entry(4, {}),
                self._entry(5, {"source_file": "source/novel.txt", "start": "0", "end": 10}),
            ]
        }
        assert episodes_without_source_range(project) == [2, 3, 4, 5]

    def test_bool_coordinates_rejected(self):
        project = {"episodes": [self._entry(1, {"source_file": "source/novel.txt", "start": True, "end": 10})]}
        assert episodes_without_source_range(project) == [1]

    def test_unparseable_episode_num_skipped(self):
        project = {"episodes": [{"episode": "第一集", "title": "x"}, "坏条目"]}
        assert episodes_without_source_range(project) == []

    def test_broken_container_and_empty_ledger(self):
        assert episodes_without_source_range({"episodes": "坏数据"}) == []
        assert episodes_without_source_range({}) == []


@pytest.mark.integration
class TestSourceFingerprints:
    def test_compute_ignores_newline_style(self, tmp_path: Path):
        d = _project(tmp_path, novel="第一行\r\n第二行\r第三行")
        sources = discover_sources(d)
        fp_crlf = compute_source_fingerprints(sources)

        d2 = _project(tmp_path.with_name("demo2"), novel="第一行\n第二行\n第三行")
        fp_lf = compute_source_fingerprints(discover_sources(d2))
        assert fp_crlf == fp_lf

    def test_mismatched_when_unrecorded_returns_empty(self, tmp_path: Path):
        """存量项目无记录字段：不比对，视同待补记，不阻塞规划。"""
        d = _project(tmp_path)
        sources = discover_sources(d)
        assert mismatched_source_fingerprints(None, sources) == []
        assert mismatched_source_fingerprints({}, sources) == []

    def test_mismatched_when_content_changed(self, tmp_path: Path):
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d))
        (d / "source" / "novel.txt").write_text(NOVEL + "追加内容", encoding="utf-8")
        mismatched = mismatched_source_fingerprints(recorded, discover_sources(d))
        assert mismatched == ["source/novel.txt"]

    def test_mismatched_when_file_removed(self, tmp_path: Path):
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d))
        (d / "source" / "novel.txt").unlink()
        mismatched = mismatched_source_fingerprints(recorded, discover_sources(d))
        assert mismatched == ["source/novel.txt"]

    def test_matched_when_content_unchanged(self, tmp_path: Path):
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d))
        assert mismatched_source_fingerprints(recorded, discover_sources(d)) == []

    def test_unrecorded_new_file_not_flagged(self, tmp_path: Path):
        """记录中没有的新增源文件不参与比对（首次纳入规划时才补记）。"""
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d))
        (d / "source" / "extra.txt").write_text("新增源文件", encoding="utf-8")
        assert mismatched_source_fingerprints(recorded, discover_sources(d)) == []

    def test_non_string_recorded_value_treated_as_unrecorded(self, tmp_path: Path):
        d = _project(tmp_path)
        mismatched = mismatched_source_fingerprints({"source/novel.txt": 123}, discover_sources(d))
        assert mismatched == []
