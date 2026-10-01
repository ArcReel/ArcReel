"""手工切分的服务命令：在真实的临时项目目录上只经公开命令验证账本、集文件与确认出口。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.episode.episode_ledger import SOURCE_FINGERPRINTS_KEY
from lib.episode.episode_manual_split import (
    ManualSplitConfirmationRequired,
    ManualSplitError,
    ManualSplitImpact,
    ManualSplitResult,
    clear_cuts_after,
    cut_unsplit_source,
    merge_with_next_episode,
    move_episode_boundary,
    render_manual_split_impact_text,
    split_episode,
)
from lib.i18n import _ as i18n_message
from lib.script.script_review import STALE_SCRIPT_PLAN_REVISION_FIELD

A = "第一章。少年下山。遇见老人。第二章。城里起火。人群四散。第三章。夜雨。"
B = "第四章。重逢。第五章。离别。"
CH2 = A.index("第二章")
CH3 = A.index("第三章")
CH5 = B.index("第五章")


def _cut(episode: int, source_file: str, start: int, end: int, **fields) -> dict:
    return {
        "episode": episode,
        "title": f"集{episode}",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "whole_source",
        "source_range": {"source_file": f"source/{source_file}", "start": start, "end": end},
        "ledger_status": "planned",
        **fields,
    }


def _own(episode: int) -> dict:
    return {
        "episode": episode,
        "title": f"番外{episode}",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "own",
    }


def _project_dir(tmp_path: Path, episodes: list[dict], **fields) -> Path:
    project_dir = tmp_path / "projects" / "demo"
    (project_dir / "source").mkdir(parents=True)
    (project_dir / "source" / "a.txt").write_text(A, encoding="utf-8")
    (project_dir / "source" / "b.txt").write_text(B, encoding="utf-8")
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "style": "国漫",
        "characters": {},
        "scenes": {},
        "props": {},
        "whole_source_files": [{"source_file": "source/a.txt"}, {"source_file": "source/b.txt"}],
        "episodes": episodes,
        "episode_id_high_water": max([e["episode"] for e in episodes], default=0),
        **fields,
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    texts = {"a.txt": A, "b.txt": B}
    for entry in episodes:
        source_range = entry.get("source_range")
        if source_range:
            name = source_range["source_file"].removeprefix("source/")
            text = texts[name][source_range["start"] : source_range["end"]]
            (project_dir / "source" / f"episode_{entry['episode']}.txt").write_text(text, encoding="utf-8")
    return project_dir


def _load(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _order(project_dir: Path) -> list[int]:
    return [entry["episode"] for entry in _load(project_dir)["episodes"]]


def _entry(project_dir: Path, episode: int) -> dict:
    return next(entry for entry in _load(project_dir)["episodes"] if entry["episode"] == episode)


def _range(project_dir: Path, episode: int) -> tuple[str, int, int]:
    source_range = _entry(project_dir, episode)["source_range"]
    return source_range["source_file"], source_range["start"], source_range["end"]


def _episode_text(project_dir: Path, episode: int) -> str:
    return (project_dir / "source" / f"episode_{episode}.txt").read_text(encoding="utf-8")


def _give_products(project_dir: Path, *episodes: int) -> None:
    (project_dir / "scripts").mkdir(exist_ok=True)
    for episode in episodes:
        (project_dir / "scripts" / f"episode_{episode}.json").write_text("{}", encoding="utf-8")


class TestCut:
    def test_cut_after_the_last_cut_episode_comes_before_episodes_of_other_origins(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2), _own(2)])

        result = cut_unsplit_source(project_dir, source_file="source/a.txt", end=CH3, title="  城里起火 ")

        assert isinstance(result, ManualSplitResult)
        assert result.episode == 3
        assert _order(project_dir) == [1, 3, 2]
        new = _entry(project_dir, 3)
        assert (new["title"], new["source_origin"], new["ledger_status"]) == ("城里起火", "whole_source", "planned")
        assert _range(project_dir, 3) == ("source/a.txt", CH2, CH3)
        assert _episode_text(project_dir, 3) == A[CH2:CH3]
        project = _load(project_dir)
        assert "source/a.txt" in project[SOURCE_FINGERPRINTS_KEY]
        assert (project_dir / "source" / "snapshots" / "a.txt").read_text(encoding="utf-8") == A

    def test_cut_in_a_gap_before_the_first_cut_episode_goes_right_before_it(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_own(1), _cut(2, "a.txt", CH2, CH3)])

        result = cut_unsplit_source(project_dir, source_file="source/a.txt", end=CH2)

        assert isinstance(result, ManualSplitResult)
        assert _order(project_dir) == [1, 3, 2]
        assert _range(project_dir, 3) == ("source/a.txt", 0, CH2)

    def test_cut_in_a_later_file_continues_from_the_previous_cut_across_files(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])

        cut_unsplit_source(project_dir, source_file="source/b.txt", end=CH5)

        assert _order(project_dir) == [1, 2]
        assert _entry(project_dir, 2)["source_range"] == {
            "source_file": "source/a.txt",
            "start": CH2,
            "end_file": "source/b.txt",
            "end": CH5,
        }
        assert _episode_text(project_dir, 2) == A[CH2:] + B[:CH5]
        snapshots = project_dir / "source" / "snapshots"
        assert sorted(path.name for path in snapshots.iterdir()) == ["a.txt", "b.txt"]

    def test_cut_does_not_cross_a_source_kind_switch(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)], content_mode="drama")
        project = _load(project_dir)
        project["whole_source_files"][1]["source_kind"] = "screenplay"
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

        cut_unsplit_source(project_dir, source_file="source/b.txt", end=CH5)

        assert _range(project_dir, 2) == ("source/b.txt", 0, CH5)

    def test_cut_ids_are_never_reused(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)], episode_id_high_water=7)

        result = cut_unsplit_source(project_dir, source_file="source/a.txt", end=CH3)

        assert isinstance(result, ManualSplitResult)
        assert result.episode == 8

    @pytest.mark.parametrize(
        ("end", "code"),
        [(3, "inside_episode"), (CH2, "empty_range"), (len(A) + 1, "position_invalid")],
    )
    def test_cut_is_refused_without_touching_the_ledger(self, tmp_path: Path, end: int, code: str):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        before = _load(project_dir)

        with pytest.raises(ManualSplitError) as excinfo:
            cut_unsplit_source(project_dir, source_file="source/a.txt", end=end)

        assert excinfo.value.code == code
        assert _load(project_dir) == before


class TestSplit:
    def test_front_keeps_the_id_and_the_back_follows_it_as_a_new_episode(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2), _own(2), _cut(3, "a.txt", CH2, CH3)])
        at = A.index("人群")

        result = split_episode(project_dir, 3, at=at)

        assert isinstance(result, ManualSplitResult)
        assert result.episode == 4
        assert result.impact == ManualSplitImpact()
        assert _order(project_dir) == [1, 2, 3, 4]
        assert _range(project_dir, 3) == ("source/a.txt", CH2, at)
        assert _range(project_dir, 4) == ("source/a.txt", at, CH3)
        assert _episode_text(project_dir, 3) == A[CH2:at]
        assert _episode_text(project_dir, 4) == A[at:CH3]
        assert _entry(project_dir, 3)["ledger_status"] == "planned"

    def test_episode_with_products_needs_confirmation_and_then_becomes_stale(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        _give_products(project_dir, 1)
        at = A.index("遇见")
        before = _load(project_dir)

        pending = split_episode(project_dir, 1, at=at)

        assert pending == ManualSplitConfirmationRequired(impact=ManualSplitImpact(restaled=[1]))
        assert _load(project_dir) == before

        result = split_episode(project_dir, 1, at=at, confirm_episodes=[1])

        assert isinstance(result, ManualSplitResult)
        entry = _entry(project_dir, 1)
        assert entry["ledger_status"] == "stale"
        assert STALE_SCRIPT_PLAN_REVISION_FIELD in entry
        assert (project_dir / "scripts" / "episode_1.json").is_file()
        assert _entry(project_dir, 2)["ledger_status"] == "planned"

    def test_split_needs_text_on_both_sides(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])

        with pytest.raises(ManualSplitError) as excinfo:
            split_episode(project_dir, 1, at=CH2)

        assert excinfo.value.code == "position_invalid"


class TestMoveBoundary:
    def test_both_episodes_keep_their_ids_and_only_those_with_products_become_stale(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2), _own(2), _cut(3, "a.txt", CH2, CH3)])
        _give_products(project_dir, 3)
        at = A.index("城里")

        pending = move_episode_boundary(project_dir, 1, at=at)
        assert isinstance(pending, ManualSplitConfirmationRequired)
        assert pending.impact.restaled == [3]

        result = move_episode_boundary(project_dir, 1, at=at, confirm_episodes=[3])

        assert isinstance(result, ManualSplitResult)
        assert _order(project_dir) == [1, 2, 3]
        assert _range(project_dir, 1) == ("source/a.txt", 0, at)
        assert _range(project_dir, 3) == ("source/a.txt", at, CH3)
        assert _episode_text(project_dir, 3) == A[at:CH3]
        assert _entry(project_dir, 1)["ledger_status"] == "planned"
        assert _entry(project_dir, 3)["ledger_status"] == "stale"

    def test_boundary_must_touch_the_next_episode(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2 - 3), _cut(2, "a.txt", CH2, CH3)])

        with pytest.raises(ManualSplitError) as excinfo:
            move_episode_boundary(project_dir, 1, at=5)

        assert excinfo.value.code == "no_adjacent_episode"


class TestMerge:
    def test_next_episode_with_products_retires_to_the_end_and_episodes_between_stay(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path,
            [_cut(1, "a.txt", 0, CH2), _own(2), _cut(3, "a.txt", CH2, CH3), _own(4)],
        )
        _give_products(project_dir, 3)

        pending = merge_with_next_episode(project_dir, 1)
        assert isinstance(pending, ManualSplitConfirmationRequired)
        assert pending.impact == ManualSplitImpact(retired=[3])

        result = merge_with_next_episode(project_dir, 1, confirm_episodes=[3])

        assert isinstance(result, ManualSplitResult)
        assert _order(project_dir) == [1, 2, 4, 3]
        assert _range(project_dir, 1) == ("source/a.txt", 0, CH3)
        assert _episode_text(project_dir, 1) == A[:CH3]
        retired = _entry(project_dir, 3)
        assert (retired["source_origin"], retired["ledger_status"]) == ("none", "stale")
        assert "source_range" not in retired
        assert not (project_dir / "source" / "episode_3.txt").exists()
        assert (project_dir / "scripts" / "episode_3.json").is_file()

    def test_next_episode_without_products_is_removed_and_its_id_is_not_reused(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, CH3)])

        result = merge_with_next_episode(project_dir, 1)

        assert isinstance(result, ManualSplitResult)
        assert result.impact == ManualSplitImpact(removed=[2])
        assert _order(project_dir) == [1]
        assert not (project_dir / "source" / "episode_2.txt").exists()
        cut = cut_unsplit_source(project_dir, source_file="source/a.txt", end=len(A))
        assert isinstance(cut, ManualSplitResult)
        assert cut.episode == 3

    def test_next_episode_with_a_zero_padded_script_counts_as_having_products(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, CH3)])
        (project_dir / "scripts").mkdir()
        (project_dir / "scripts" / "episode_02.json").write_text("{}", encoding="utf-8")

        pending = merge_with_next_episode(project_dir, 1)

        assert pending == ManualSplitConfirmationRequired(impact=ManualSplitImpact(retired=[2]))
        assert _order(project_dir) == [1, 2]

    def test_next_episode_in_another_file_is_merged_across_files(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", CH3, len(A)), _cut(2, "b.txt", 0, CH5)])

        result = merge_with_next_episode(project_dir, 1)

        assert isinstance(result, ManualSplitResult)
        assert _order(project_dir) == [1]
        assert _entry(project_dir, 1)["source_range"] == {
            "source_file": "source/a.txt",
            "start": CH3,
            "end_file": "source/b.txt",
            "end": CH5,
        }
        assert _episode_text(project_dir, 1) == A[CH3:] + B[:CH5]

    def test_next_episode_of_another_source_kind_cannot_be_merged(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path, [_cut(1, "a.txt", CH3, len(A)), _cut(2, "b.txt", 0, CH5)], content_mode="drama"
        )
        project = _load(project_dir)
        project["whole_source_files"][1]["source_kind"] = "screenplay"
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ManualSplitError) as excinfo:
            merge_with_next_episode(project_dir, 1)

        assert excinfo.value.code == "merge_across_kinds"

    def test_unsplit_text_between_the_two_episodes_needs_confirmation_of_its_volume(self, tmp_path: Path):
        meet = A.index("遇见老人")
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, meet), _cut(2, "a.txt", CH2, CH3)])

        pending = merge_with_next_episode(project_dir, 1)
        assert pending == ManualSplitConfirmationRequired(impact=ManualSplitImpact(removed=[2], merged_units=5))
        assert _range(project_dir, 1) == ("source/a.txt", 0, meet)

        stale = merge_with_next_episode(project_dir, 1, confirm_merged_units=3)
        assert isinstance(stale, ManualSplitConfirmationRequired)

        result = merge_with_next_episode(project_dir, 1, confirm_merged_units=5)

        assert result == ManualSplitResult(impact=ManualSplitImpact(removed=[2], merged_units=5))
        assert _range(project_dir, 1) == ("source/a.txt", 0, CH3)
        assert _episode_text(project_dir, 1) == A[:CH3]


class TestAcrossFiles:
    def test_split_a_crossing_episode_in_its_later_file(self, tmp_path: Path):
        crossing = _cut(1, "a.txt", CH3, CH5)
        crossing["source_range"]["end_file"] = "source/b.txt"
        project_dir = _project_dir(tmp_path, [crossing])
        (project_dir / "source" / "episode_1.txt").write_text(A[CH3:] + B[:CH5], encoding="utf-8")

        result = split_episode(project_dir, 1, at=3, source_file="source/b.txt")

        assert isinstance(result, ManualSplitResult)
        assert _entry(project_dir, 1)["source_range"] == {
            "source_file": "source/a.txt",
            "start": CH3,
            "end_file": "source/b.txt",
            "end": 3,
        }
        assert _range(project_dir, 2) == ("source/b.txt", 3, CH5)
        assert _episode_text(project_dir, 1) == A[CH3:] + B[:3]

    def test_move_a_boundary_back_into_the_previous_file(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, len(A)), _cut(2, "b.txt", 0, CH5)])

        move_episode_boundary(project_dir, 1, at=CH3, source_file="source/a.txt")

        assert _range(project_dir, 1) == ("source/a.txt", 0, CH3)
        assert _entry(project_dir, 2)["source_range"] == {
            "source_file": "source/a.txt",
            "start": CH3,
            "end_file": "source/b.txt",
            "end": CH5,
        }

    def test_unsplit_text_across_files_is_counted_in_the_merged_volume(self, tmp_path: Path):
        # 两集之间夹着 a.txt 的「第三章。夜雨。」与 b.txt 的「第四章。重逢。」，各 7 字
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH3), _cut(2, "b.txt", CH5, len(B))])

        pending = merge_with_next_episode(project_dir, 1)
        assert pending == ManualSplitConfirmationRequired(impact=ManualSplitImpact(removed=[2], merged_units=14))

        result = merge_with_next_episode(project_dir, 1, confirm_merged_units=14)

        assert isinstance(result, ManualSplitResult)
        assert _episode_text(project_dir, 1) == A + B


class TestClearAfter:
    def test_cuts_after_the_episode_retire_or_are_removed(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path,
            [
                _cut(1, "a.txt", 0, CH2),
                _cut(2, "a.txt", CH2, CH3),
                _own(3),
                _cut(4, "a.txt", CH3, len(A)),
                _cut(5, "b.txt", 0, CH5),
                _own(6),
            ],
        )
        _give_products(project_dir, 4, 5)
        (project_dir / "source" / "snapshots").mkdir()
        (project_dir / "source" / "snapshots" / "b.txt").write_text(B, encoding="utf-8")

        pending = clear_cuts_after(project_dir, 1, dry_run=True)
        assert pending == ManualSplitConfirmationRequired(impact=ManualSplitImpact(retired=[4, 5], removed=[2]))

        result = clear_cuts_after(project_dir, 1, confirm_episodes=[4, 5])

        assert isinstance(result, ManualSplitResult)
        assert _order(project_dir) == [1, 3, 6, 4, 5]
        assert _range(project_dir, 1) == ("source/a.txt", 0, CH2)
        assert [_entry(project_dir, n)["source_origin"] for n in (4, 5)] == ["none", "none"]
        assert not (project_dir / "source" / "episode_2.txt").exists()
        assert not (project_dir / "source" / "snapshots" / "b.txt").exists()

    def test_last_cut_episode_has_nothing_to_clear(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])

        with pytest.raises(ManualSplitError) as excinfo:
            clear_cuts_after(project_dir, 1)

        assert excinfo.value.code == "nothing_after"


class TestSourceChangedOutside:
    def test_splitting_a_changed_file_is_paused_but_clearing_still_works(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path,
            [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, CH3)],
            source_fingerprints={"source/a.txt": "0" * 64},
        )

        with pytest.raises(ManualSplitError) as excinfo:
            split_episode(project_dir, 1, at=3)
        assert excinfo.value.code == "source_changed"

        assert isinstance(clear_cuts_after(project_dir, 1), ManualSplitResult)
        assert _order(project_dir) == [1]

    def test_a_file_that_differs_from_its_snapshot_counts_as_changed(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, CH3)])
        (project_dir / "source" / "snapshots").mkdir()
        (project_dir / "source" / "snapshots" / "a.txt").write_text(A.replace("少年", "青年"), encoding="utf-8")

        with pytest.raises(ManualSplitError) as excinfo:
            split_episode(project_dir, 1, at=3)

        assert excinfo.value.code == "source_changed"


def test_confirmation_text_names_episodes_by_title_or_position():
    project = {"episodes": [{"episode": 4, "title": "雨夜"}, {"episode": 9, "title": ""}, {"episode": 2}]}
    impact = ManualSplitImpact(restaled=[4], retired=[9], removed=[2]).to_dict()

    text = render_manual_split_impact_text(impact, project, i18n_message)

    assert text.splitlines() == [
        "这次调整波及 2 个已开始制作的集，它们已有的产物都会保留。",
        "原文范围会改变，标为「原文已重新规划」：雨夜",
        "转为无原文的集，标为「原文已重新规划」，移到播出顺序末尾：第 2 集",
        "还没有产物，直接移除：第 3 集",
    ]


def test_confirmation_text_states_the_volume_of_unsplit_text_merged_in():
    project = {"source_language": "zh", "episodes": [{"episode": 2, "title": "雨夜"}]}
    impact = ManualSplitImpact(removed=[2], merged_units=120).to_dict()

    text = render_manual_split_impact_text(impact, project, i18n_message)

    assert text.splitlines() == [
        "两集之间有 120 字未切分的原文，合并后会并入这一集。",
        "还没有产物，直接移除：雨夜",
    ]


def test_confirmation_text_counts_words_for_projects_counted_by_words():
    project = {"source_language": "en", "episodes": [{"episode": 2, "title": "Rain"}]}
    impact = ManualSplitImpact(removed=[2], merged_units=1).to_dict()

    text = render_manual_split_impact_text(impact, project, i18n_message)

    assert text.splitlines()[0] == "两集之间有 1 词未切分的原文，合并后会并入这一集。"
