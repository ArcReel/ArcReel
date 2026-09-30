"""分集账本：归一化坐标系与源文指纹。"""

import unicodedata
from pathlib import Path

from lib.episode.episode_ledger import (
    compute_source_fingerprints,
    mismatched_source_fingerprints,
    normalize_source_text,
)
from lib.episode.episode_sources import discover_sources

#: 整本源文清单只登记 novel.txt 与 extra.txt 的项目。
_PROJECT = {"whole_source_files": [{"source_file": "source/novel.txt"}, {"source_file": "source/extra.txt"}]}

NOVEL = "第一章少年下山遇见老人。第二章城里起了大火人群四散。第三章一切归于平静少年远行。"


def _project(tmp_path: Path, *, novel: str | None = NOVEL) -> Path:
    d = tmp_path / "demo"
    (d / "source").mkdir(parents=True)
    if novel is not None:
        (d / "source" / "novel.txt").write_text(novel, encoding="utf-8")
    return d


class TestNormalizeSourceText:
    def test_nfc_and_newlines(self):
        nfd_cafe = unicodedata.normalize("NFD", "café")
        assert normalize_source_text(nfd_cafe) == "café"
        assert normalize_source_text("a\r\nb\rc\nd") == "a\nb\nc\nd"


class TestSourceFingerprints:
    def test_compute_ignores_newline_style(self, tmp_path: Path):
        d = _project(tmp_path, novel="第一行\r\n第二行\r第三行")
        sources = discover_sources(d, _PROJECT)
        fp_crlf = compute_source_fingerprints(sources)

        d2 = _project(tmp_path.with_name("demo2"), novel="第一行\n第二行\n第三行")
        fp_lf = compute_source_fingerprints(discover_sources(d2, _PROJECT))
        assert fp_crlf == fp_lf

    def test_mismatched_when_unrecorded_returns_empty(self, tmp_path: Path):
        """存量项目无记录字段：不比对，视同待补记，不阻塞规划。"""
        d = _project(tmp_path)
        sources = discover_sources(d, _PROJECT)
        assert mismatched_source_fingerprints(None, sources) == []
        assert mismatched_source_fingerprints({}, sources) == []

    def test_mismatched_when_content_changed(self, tmp_path: Path):
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d, _PROJECT))
        (d / "source" / "novel.txt").write_text(NOVEL + "追加内容", encoding="utf-8")
        mismatched = mismatched_source_fingerprints(recorded, discover_sources(d, _PROJECT))
        assert mismatched == ["source/novel.txt"]

    def test_mismatched_when_file_removed(self, tmp_path: Path):
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d, _PROJECT))
        (d / "source" / "novel.txt").unlink()
        mismatched = mismatched_source_fingerprints(recorded, discover_sources(d, _PROJECT))
        assert mismatched == ["source/novel.txt"]

    def test_matched_when_content_unchanged(self, tmp_path: Path):
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d, _PROJECT))
        assert mismatched_source_fingerprints(recorded, discover_sources(d, _PROJECT)) == []

    def test_unrecorded_new_file_not_flagged(self, tmp_path: Path):
        """记录中没有的新增源文件不参与比对（首次纳入规划时才补记）。"""
        d = _project(tmp_path)
        recorded = compute_source_fingerprints(discover_sources(d, _PROJECT))
        (d / "source" / "extra.txt").write_text("新增源文件", encoding="utf-8")
        assert mismatched_source_fingerprints(recorded, discover_sources(d, _PROJECT)) == []

    def test_non_string_recorded_value_treated_as_unrecorded(self, tmp_path: Path):
        d = _project(tmp_path)
        mismatched = mismatched_source_fingerprints({"source/novel.txt": 123}, discover_sources(d, _PROJECT))
        assert mismatched == []
