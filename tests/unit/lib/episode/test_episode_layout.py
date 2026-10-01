"""「分集」视图的只读投影：按集分段、空段与尚未分集的原文、每集体量与首尾句、未登记文件。"""

from pathlib import Path

from lib.episode.episode_layout import build_episode_layout
from lib.episode.episode_sources import SourceOrigin

VOLUME_A = "第一章。少年下山。\n遇见老人。\n\n第二章。城里起火。"
VOLUME_B = "第三章。人群四散。"


def _cut(episode: int, source_file: str, start: int, end: int) -> dict:
    return {
        "episode": episode,
        "title": f"集 {episode}",
        "source_origin": "whole_source",
        "source_range": {"source_file": f"source/{source_file}", "start": start, "end": end},
    }


def _project_dir(tmp_path: Path, **files: str) -> Path:
    project_dir = tmp_path / "demo"
    (project_dir / "source").mkdir(parents=True)
    for name, text in files.items():
        (project_dir / "source" / name).write_text(text, encoding="utf-8")
    return project_dir


def _project(*names: str, episodes: list[dict] | None = None, **fields) -> dict:
    return {
        "whole_source_files": [{"source_file": f"source/{name}"} for name in names],
        "episodes": episodes or [],
        **fields,
    }


class TestSegments:
    def test_files_follow_the_list_and_split_into_episodes_and_unsplit_text(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": VOLUME_A, "b.txt": VOLUME_B})
        second_start = VOLUME_A.index("第二章")
        project = _project("b.txt", "a.txt", episodes=[_cut(7, "a.txt", 0, second_start)])

        layout = build_episode_layout(project_dir, project)

        assert [f.source_file for f in layout.files] == ["source/b.txt", "source/a.txt"]
        b_file, a_file = layout.files
        assert [(s.kind, s.gap) for s in b_file.segments] == [("unsplit", True)]
        assert [(s.kind, s.episode, s.start, s.end) for s in a_file.segments] == [
            ("episode", 7, 0, second_start),
            ("unsplit", None, second_start, len(VOLUME_A)),
        ]
        assert a_file.segments[1].gap is False
        assert a_file.segments[1].text == "第二章。城里起火。"

    def test_an_episode_crossing_files_shows_one_part_in_each_file(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": VOLUME_A, "b.txt": VOLUME_B})
        second_start = VOLUME_A.index("第二章")
        crossing = _cut(3, "a.txt", second_start, 4)
        crossing["source_range"]["end_file"] = "source/b.txt"
        project = _project("a.txt", "b.txt", episodes=[_cut(1, "a.txt", 0, second_start), crossing])

        layout = build_episode_layout(project_dir, project)

        a_file, b_file = layout.files
        assert [(s.episode, s.continued, s.continues) for s in a_file.segments] == [(1, False, False), (3, False, True)]
        assert [(s.kind, s.episode, s.start, s.end, s.continued) for s in b_file.segments] == [
            ("episode", 3, 0, 4, True),
            ("unsplit", None, 4, len(VOLUME_B), False),
        ]
        episode = next(e for e in layout.episodes if e.episode == 3)
        assert (episode.source_file, episode.end_file) == ("source/a.txt", "source/b.txt")
        assert episode.first_sentence.startswith("第二章")
        assert a_file.cut_units + b_file.cut_units == layout.cut_units

    def test_unsplit_text_between_cut_episodes_is_a_gap_and_blank_gaps_are_dropped(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": VOLUME_A})
        first_end = VOLUME_A.index("遇见")
        second_start = VOLUME_A.index("第二章")
        project = _project(
            "a.txt",
            episodes=[_cut(1, "a.txt", 0, first_end - 1), _cut(2, "a.txt", second_start, len(VOLUME_A))],
        )

        layout = build_episode_layout(project_dir, project)

        segments = layout.files[0].segments
        assert [(s.kind, s.gap) for s in segments] == [("episode", False), ("unsplit", True), ("episode", False)]
        assert segments[1].text == "\n遇见老人。\n\n"

        blank_gap = _project(
            "a.txt",
            episodes=[_cut(1, "a.txt", 0, second_start - 2), _cut(2, "a.txt", second_start, len(VOLUME_A))],
        )
        assert [s.kind for s in build_episode_layout(project_dir, blank_gap).files[0].segments] == [
            "episode",
            "episode",
        ]

    def test_ranges_that_cannot_be_placed_stay_out_of_the_text(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": VOLUME_A, "episode_4.txt": "旧拆分流程切出的原文。"})
        project = _project(
            "a.txt",
            episodes=[
                _cut(1, "a.txt", 0, 6),
                _cut(2, "a.txt", 3, 9),
                _cut(3, "gone.txt", 0, 5),
                {"episode": 4, "title": "旧集", "source_origin": "whole_source"},
            ],
        )

        layout = build_episode_layout(project_dir, project)

        assert [s.episode for s in layout.files[0].segments if s.kind == "episode"] == [1]
        placed = {e.episode: (e.placed, e.units) for e in layout.episodes}
        assert placed == {1: (True, 6), 2: (False, None), 3: (False, None), 4: (False, 11)}

    def test_missing_files_are_listed_without_segments(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": VOLUME_A})

        layout = build_episode_layout(project_dir, _project("gone.txt", "a.txt"))

        gone = layout.files[0]
        assert (gone.name, gone.missing, gone.segments) == ("gone.txt", True, [])
        assert layout.files[1].missing is False


class TestVolume:
    def test_units_and_cut_units_follow_the_project_language(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": "One two three. Four five."})
        project = _project(
            "a.txt", episodes=[_cut(1, "a.txt", 0, 14)], source_language="en", speech_rate_units_per_second=2.0
        )

        layout = build_episode_layout(project_dir, project)

        assert (layout.unit, layout.units, layout.cut_units) == ("words", 5, 3)
        assert (layout.files[0].units, layout.files[0].cut_units) == (5, 3)
        episode = layout.episodes[0]
        assert (episode.units, episode.spoken_seconds) == (3, 1.5)
        assert (episode.first_sentence, episode.last_sentence) == ("One two three.", "One two three.")

    def test_own_source_episodes_read_their_episode_file_and_no_source_episodes_have_no_volume(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"episode_2.txt": "番外开头。\n番外结尾！"})
        project = _project(
            episodes=[
                {"episode": 2, "title": "番外", "source_origin": "own"},
                {"episode": 3, "title": "空白", "source_origin": "none"},
            ]
        )

        layout = build_episode_layout(project_dir, project)

        own, blank = layout.episodes
        assert (own.origin, own.placed, own.units) == (SourceOrigin.OWN, False, 10)
        assert (own.first_sentence, own.last_sentence) == ("番外开头。", "番外结尾！")
        assert (blank.origin, blank.units, blank.spoken_seconds, blank.first_sentence) == (
            SourceOrigin.NONE,
            None,
            None,
            "",
        )
        assert layout.files == []


class TestUnregisteredFiles:
    def test_lists_text_files_that_are_neither_whole_source_nor_episode_files(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path,
            **{
                "a.txt": VOLUME_A,
                "episode_1.txt": "切出集的集文件",
                "episode_9.txt": "账本外的旧集文件",
                "notes.md": "笔记",
                "_remaining.txt": "旧拆分流程的剩余原文",
                ".hidden.txt": "隐藏",
                "cover.png": "不是文本",
                "_episode_2.txt.bak": "留底",
            },
        )
        (project_dir / "source" / "raw").mkdir()
        project = _project("a.txt", episodes=[_cut(1, "a.txt", 0, 5)])

        layout = build_episode_layout(project_dir, project)

        assert [(f.name, f.can_join_whole_source) for f in layout.unregistered] == [
            ("_remaining.txt", False),
            ("episode_9.txt", False),
            ("notes.md", True),
        ]

    def test_original_upload_name_comes_from_the_raw_backup(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, **{"a.txt": VOLUME_A})
        (project_dir / "source" / "raw").mkdir()
        (project_dir / "source" / "raw" / "a.docx").write_bytes(b"docx")

        layout = build_episode_layout(project_dir, _project("a.txt"))

        assert layout.files[0].original_filename == "a.docx"
