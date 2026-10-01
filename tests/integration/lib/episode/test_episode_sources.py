"""集原文来源与整本源文清单：来源判定、文件顺序、规划起点推导与快照。"""

import unicodedata
from pathlib import Path

from lib.episode.episode_sources import (
    SourceOrigin,
    append_whole_source_file,
    cut_episode_placements,
    discover_sources,
    episode_source_origin,
    legacy_cut_episode_ids,
    planning_start,
    source_snapshot_path,
    sync_source_snapshots,
    unplanned_text_remains,
    whole_source_files,
)

CHAPTER_A = "第一章少年下山遇见老人。"
CHAPTER_B = "第二章城里起了大火人群四散。"


def _files(*names: str) -> list[dict[str, str]]:
    return [{"source_file": f"source/{name}"} for name in names]


def _cut(episode: int, source_file: str, start: int, end: int) -> dict:
    return {
        "episode": episode,
        "source_origin": "whole_source",
        "source_range": {"source_file": f"source/{source_file}", "start": start, "end": end},
    }


def _project_dir(tmp_path: Path, **files: str) -> Path:
    project_dir = tmp_path / "demo"
    (project_dir / "source").mkdir(parents=True)
    for name, text in files.items():
        (project_dir / "source" / name).write_text(text, encoding="utf-8")
    return project_dir


class TestEpisodeSourceOrigin:
    def test_recorded_origin_wins_over_disk(self):
        assert episode_source_origin({"episode": 1, "source_origin": "own"}) is SourceOrigin.OWN
        assert episode_source_origin({"episode": 1, "source_origin": "none"}) is SourceOrigin.NONE

    def test_missing_origin_follows_source_range_only(self):
        assert episode_source_origin(_cut(1, "a.txt", 0, 3) | {"source_origin": None}) is SourceOrigin.WHOLE_SOURCE
        assert episode_source_origin({"episode": 1}) is SourceOrigin.NONE

    def test_only_cut_episodes_without_range_block_planning(self):
        project = {
            "episodes": [
                _cut(1, "a.txt", 0, 3),
                {"episode": 2, "source_origin": "whole_source"},
                {"episode": 3, "source_origin": "own"},
                {"episode": 4, "source_origin": "none"},
            ]
        }
        assert legacy_cut_episode_ids(project) == [2]


class TestWholeSourceFiles:
    def test_list_order_decides_file_order_not_file_name(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"b.txt": CHAPTER_B, "a.txt": CHAPTER_A})
        project = {"whole_source_files": _files("b.txt", "a.txt")}

        assert [doc.rel_path for doc in discover_sources(project_dir, project)] == ["source/b.txt", "source/a.txt"]

    def test_unregistered_files_are_not_whole_source(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": CHAPTER_A, "stray.txt": CHAPTER_B, "episode_1.txt": "x"})

        assert [doc.rel_path for doc in discover_sources(project_dir, {"whole_source_files": _files("a.txt")})] == [
            "source/a.txt"
        ]

    def test_episode_file_names_and_malformed_items_are_skipped(self):
        project = {
            "whole_source_files": [
                *_files("a.txt", "episode_2.txt", "_hidden.txt", "a.txt"),
                {"source_file": "../outside.txt"},
                "source/b.txt",
            ]
        }
        assert whole_source_files(project) == ["source/a.txt"]

    def test_symlinked_or_missing_files_are_skipped(self, tmp_path: Path):
        outside = tmp_path / "outside.txt"
        outside.write_text(CHAPTER_B, encoding="utf-8")
        project_dir = _project_dir(tmp_path, **{"a.txt": CHAPTER_A})
        (project_dir / "source" / "linked.txt").symlink_to(outside)
        project = {"whole_source_files": _files("linked.txt", "missing.txt", "a.txt")}

        assert [doc.rel_path for doc in discover_sources(project_dir, project)] == ["source/a.txt"]


class TestAppendWholeSourceFile:
    def test_new_files_go_to_the_end_by_default(self):
        project = {"whole_source_files": _files("a.txt")}

        assert append_whole_source_file(project, "source/b.txt") is True
        assert append_whole_source_file(project, "source/a.txt") is False
        assert whole_source_files(project) == ["source/a.txt", "source/b.txt"]

    def test_index_places_the_file_among_valid_items(self):
        malformed = {"source_file": "../outside.txt"}
        project = {"whole_source_files": [*_files("a.txt"), malformed, *_files("c.txt")]}

        append_whole_source_file(project, "source/b.txt", index=1)
        append_whole_source_file(project, "source/first.txt", index=0)
        append_whole_source_file(project, "source/last.txt", index=99)

        assert whole_source_files(project) == [
            "source/first.txt",
            "source/a.txt",
            "source/b.txt",
            "source/c.txt",
            "source/last.txt",
        ]
        assert malformed in project["whole_source_files"]

    def test_inserting_before_a_cut_file_does_not_move_the_planning_start(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": CHAPTER_A, "b.txt": CHAPTER_B})
        project = {"whole_source_files": _files("b.txt"), "episodes": [_cut(1, "b.txt", 0, 5)]}

        append_whole_source_file(project, "source/a.txt", index=0)

        assert planning_start(project, discover_sources(project_dir, project)) == ("source/b.txt", 5)


class TestPlanningStart:
    def _docs(self, tmp_path: Path, project: dict):
        project_dir = _project_dir(tmp_path, **{"a.txt": CHAPTER_A, "b.txt": CHAPTER_B})
        return discover_sources(project_dir, project)

    def test_starts_at_first_file_without_cut_episodes(self, tmp_path: Path):
        project = {"whole_source_files": _files("a.txt", "b.txt"), "episodes": [{"episode": 1, "source_origin": "own"}]}
        assert planning_start(project, self._docs(tmp_path, project)) == ("source/a.txt", 0)

    def test_last_cut_episode_by_source_position_not_by_ledger_order(self, tmp_path: Path):
        project = {
            "whole_source_files": _files("a.txt", "b.txt"),
            "episodes": [_cut(3, "b.txt", 0, 4), _cut(1, "a.txt", 0, len(CHAPTER_A)), {"episode": 2}],
        }
        assert planning_start(project, self._docs(tmp_path, project)) == ("source/b.txt", 4)

    def test_reordering_files_moves_the_start(self, tmp_path: Path):
        project = {
            "whole_source_files": _files("b.txt", "a.txt"),
            "episodes": [_cut(1, "b.txt", 0, 4), _cut(2, "a.txt", 0, 2)],
        }
        assert planning_start(project, self._docs(tmp_path, project)) == ("source/a.txt", 2)

    def test_cut_placements_match_planning_start_across_unicode_normalization(self, tmp_path: Path):
        nfc = unicodedata.normalize("NFC", "café.txt")
        nfd = unicodedata.normalize("NFD", "café.txt")
        project_dir = _project_dir(tmp_path, **{nfc: CHAPTER_A})
        project = {"whole_source_files": _files(nfc), "episodes": [_cut(1, nfd, 0, 4)]}
        docs = discover_sources(project_dir, project)

        assert planning_start(project, docs) == (f"source/{nfc}", 4)
        placement = cut_episode_placements(project, docs)[1]
        assert (placement.file_index, placement.start, placement.end) == (0, 0, 4)

    def test_unplanned_text_remains_across_files(self, tmp_path: Path):
        project = {"whole_source_files": _files("a.txt", "b.txt"), "episodes": [_cut(1, "a.txt", 0, len(CHAPTER_A))]}
        docs = self._docs(tmp_path, project)
        assert unplanned_text_remains(project, docs) is True

        project["episodes"].append(_cut(2, "b.txt", 0, len(CHAPTER_B)))
        assert unplanned_text_remains(project, docs) is False


class TestSnapshots:
    def test_snapshots_follow_files_with_cut_episodes(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": CHAPTER_A, "b.txt": CHAPTER_B})
        project = {"whole_source_files": _files("a.txt", "b.txt"), "episodes": [_cut(1, "a.txt", 0, 3)]}

        sync_source_snapshots(project_dir, project, {"source/a.txt": CHAPTER_A, "source/b.txt": CHAPTER_B})

        assert source_snapshot_path(project_dir, "source/a.txt").read_text(encoding="utf-8") == CHAPTER_A
        assert not source_snapshot_path(project_dir, "source/b.txt").exists()

        project["episodes"] = [_cut(2, "b.txt", 0, 3)]
        sync_source_snapshots(project_dir, project, {"source/b.txt": CHAPTER_B})

        assert not source_snapshot_path(project_dir, "source/a.txt").exists()
        assert source_snapshot_path(project_dir, "source/b.txt").read_text(encoding="utf-8") == CHAPTER_B
