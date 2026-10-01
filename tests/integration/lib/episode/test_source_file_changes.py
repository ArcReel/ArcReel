"""整本源文文件的插入、替换、编辑、删除与调序：在真实的临时项目目录上只经公开命令验证账本、集文件与确认握手。"""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from lib.episode.episode_ledger import SOURCE_FINGERPRINTS_KEY, SourceDoc, compute_source_fingerprints
from lib.episode.episode_paths import SCRIPT_PLAN_FILENAMES
from lib.episode.source_file_changes import (
    SourceFileChangeError,
    accept_external_source_change,
    delete_whole_source_file,
    edit_whole_source_file,
    insert_whole_source_file,
    move_whole_source_file,
    render_source_file_impact_text,
    replace_whole_source_file,
)
from lib.i18n import _ as i18n_message
from lib.project.project_manager import ProjectManager

A = "第一章。少年下山。\n第二章。城里起火。\n"
B = "第三章。夜雨。\n第四章。重逢。\n"
C = "第五章。离别。\n"
CH2 = A.index("第二章")
CH4 = B.index("第四章")


def _cut(episode: int, source_file: str, start: int, end: int, *, end_file: str | None = None, **fields) -> dict:
    source_range = {"source_file": f"source/{source_file}", "start": start, "end": end}
    if end_file is not None:
        source_range["end_file"] = f"source/{end_file}"
    return {
        "episode": episode,
        "title": f"集{episode}",
        "script_file": f"scripts/episode_{episode}.json",
        "source_origin": "whole_source",
        "source_range": source_range,
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


TEXTS = {"a.txt": A, "b.txt": B, "c.txt": C}


def _project_dir(tmp_path: Path, episodes: list[dict], *, files=("a.txt", "b.txt", "c.txt"), **fields) -> Path:
    project_dir = tmp_path / "projects" / "demo"
    (project_dir / "source").mkdir(parents=True)
    for name in files:
        (project_dir / "source" / name).write_text(TEXTS[name], encoding="utf-8")
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "style": "国漫",
        "characters": {},
        "scenes": {},
        "props": {},
        "whole_source_files": [{"source_file": f"source/{name}"} for name in files],
        "episodes": episodes,
        "episode_id_high_water": max([e["episode"] for e in episodes], default=0),
        SOURCE_FINGERPRINTS_KEY: compute_source_fingerprints(
            [SourceDoc(f"source/{name}", TEXTS[name]) for name in files]
        ),
        **fields,
    }
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return project_dir


def _pm(project_dir: Path) -> ProjectManager:
    return ProjectManager.for_project_dir(project_dir)


def _load(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _order(project_dir: Path) -> list[int]:
    return [entry["episode"] for entry in _load(project_dir)["episodes"]]


def _entry(project_dir: Path, episode: int) -> dict:
    return next(entry for entry in _load(project_dir)["episodes"] if entry["episode"] == episode)


def _episode_text(project_dir: Path, episode: int) -> str:
    return (project_dir / "source" / f"episode_{episode}.txt").read_text(encoding="utf-8")


def _give_products(project_dir: Path, *episodes: int) -> None:
    (project_dir / "scripts").mkdir(exist_ok=True)
    for episode in episodes:
        (project_dir / "scripts" / f"episode_{episode}.json").write_text("{}", encoding="utf-8")


class TestEdit:
    def test_fixing_a_typo_only_restales_the_episode_that_contains_it(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path, [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, len(A)), _cut(3, "b.txt", 0, CH4)]
        )
        _give_products(project_dir, 2)
        new_text = A.replace("少年下山", "少年走下山")

        preview = edit_whole_source_file(_pm(project_dir), "demo", "a.txt", new_text)

        assert preview.applied is False
        assert preview.impact.changed_without_products == [1]
        assert preview.impact.shifted == [2]
        assert preview.impact.changed_with_products == []
        assert _load(project_dir)["episodes"][0]["source_range"]["end"] == CH2

        result = edit_whole_source_file(_pm(project_dir), "demo", "a.txt", new_text, revision=preview.revision)

        assert result.applied is True
        assert _entry(project_dir, 1)["ledger_status"] == "stale"
        assert _entry(project_dir, 1)["source_range"] == {"source_file": "source/a.txt", "start": 0, "end": CH2 + 1}
        assert _entry(project_dir, 2)["ledger_status"] == "planned"
        assert _entry(project_dir, 2)["source_range"]["start"] == CH2 + 1
        assert _entry(project_dir, 3)["source_range"] == {"source_file": "source/b.txt", "start": 0, "end": CH4}
        assert _episode_text(project_dir, 1) == new_text[: CH2 + 1]
        assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == new_text
        assert (
            _load(project_dir)[SOURCE_FINGERPRINTS_KEY]["source/a.txt"]
            == (compute_source_fingerprints([SourceDoc("source/a.txt", new_text)])["source/a.txt"])
        )
        assert (project_dir / "source" / "snapshots" / "a.txt").read_text(encoding="utf-8") == new_text

    def test_an_edit_that_touches_no_episode_is_applied_directly(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])

        result = edit_whole_source_file(_pm(project_dir), "demo", "a.txt", A + "第二章补记。\n")

        assert result.applied is True
        assert result.impact.is_empty
        assert _entry(project_dir, 1)["ledger_status"] == "planned"

    def test_a_stale_revision_returns_the_current_impact_without_writing(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        new_text = A.replace("少年", "青年")

        result = edit_whole_source_file(_pm(project_dir), "demo", "a.txt", new_text, revision="outdated")

        assert result.applied is False
        assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == A

    def test_an_episode_whose_text_was_deleted_is_retired_or_removed(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path, [_cut(1, "a.txt", 0, CH2), _own(9), _cut(2, "a.txt", CH2, len(A)), _cut(3, "b.txt", 0, CH4)]
        )
        _give_products(project_dir, 1)
        new_text = A[CH2:]

        preview = edit_whole_source_file(_pm(project_dir), "demo", "a.txt", new_text)
        assert (preview.impact.retired, preview.impact.shifted) == ([1], [2])
        edit_whole_source_file(_pm(project_dir), "demo", "a.txt", new_text, revision=preview.revision)

        assert _order(project_dir) == [9, 2, 3, 1]
        retired = _entry(project_dir, 1)
        assert (retired["source_origin"], retired["ledger_status"]) == ("none", "stale")
        assert "source_range" not in retired
        assert not (project_dir / "source" / "episode_1.txt").exists()

    def test_a_file_changed_outside_the_service_is_not_edited(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        (project_dir / "source" / "a.txt").write_text(A + "外部追加。\n", encoding="utf-8")

        with pytest.raises(SourceFileChangeError) as exc:
            edit_whole_source_file(_pm(project_dir), "demo", "a.txt", A)

        assert exc.value.code == "source_changed"

    def test_empty_text_is_rejected(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [])

        with pytest.raises(SourceFileChangeError) as exc:
            edit_whole_source_file(_pm(project_dir), "demo", "a.txt", " \n")

        assert exc.value.code == "source_text_empty"


class TestReplace:
    def test_replacing_keeps_position_name_and_kind(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "b.txt", 0, CH4)], content_mode="drama")
        project = _load(project_dir)
        project["whole_source_files"][1]["source_kind"] = "screenplay"
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        raw = project_dir / "source" / "raw"
        raw.mkdir()
        (raw / "b.docx").write_bytes(b"old")
        new_text = B.replace("夜雨", "大雨")

        preview = replace_whole_source_file(_pm(project_dir), "demo", "b.txt", new_text)
        replace_whole_source_file(_pm(project_dir), "demo", "b.txt", new_text, revision=preview.revision)

        project = _load(project_dir)
        assert project["whole_source_files"][1] == {"source_file": "source/b.txt", "source_kind": "screenplay"}
        assert (project_dir / "source" / "b.txt").read_text(encoding="utf-8") == new_text
        assert not (raw / "b.docx").exists()

    def test_changing_the_kind_lists_script_plans_that_go_stale(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path, [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, len(A))], content_mode="drama"
        )
        drafts = project_dir / "drafts" / "episode_2"
        drafts.mkdir(parents=True)
        (drafts / SCRIPT_PLAN_FILENAMES["drama"]).write_text("{}", encoding="utf-8")

        preview = replace_whole_source_file(_pm(project_dir), "demo", "a.txt", A, source_kind="screenplay")

        assert preview.applied is False
        assert preview.impact.kind_stale == [2]
        replace_whole_source_file(
            _pm(project_dir), "demo", "a.txt", A, source_kind="screenplay", revision=preview.revision
        )
        assert _load(project_dir)["whole_source_files"][0]["source_kind"] == "screenplay"


class TestDelete:
    def test_an_episode_crossing_the_deleted_file_keeps_the_rest(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path,
            [
                _cut(1, "a.txt", CH2, CH4, end_file="b.txt"),
                _cut(2, "b.txt", CH4, len(B)),
                _cut(3, "c.txt", 0, len(C)),
            ],
        )
        _give_products(project_dir, 2)

        preview = delete_whole_source_file(_pm(project_dir), "demo", "b.txt")

        assert preview.impact.changed_without_products == [1]
        assert preview.impact.retired == [2]
        delete_whole_source_file(_pm(project_dir), "demo", "b.txt", revision=preview.revision)

        project = _load(project_dir)
        assert [item["source_file"] for item in project["whole_source_files"]] == ["source/a.txt", "source/c.txt"]
        assert not (project_dir / "source" / "b.txt").exists()
        assert _entry(project_dir, 1)["source_range"] == {"source_file": "source/a.txt", "start": CH2, "end": len(A)}
        assert _entry(project_dir, 1)["ledger_status"] == "stale"
        assert _episode_text(project_dir, 1) == A[CH2:]
        assert _order(project_dir) == [1, 3, 2]
        assert "source/b.txt" not in project[SOURCE_FINGERPRINTS_KEY]

    def test_deleting_an_unplanned_file_is_applied_directly(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])

        result = delete_whole_source_file(_pm(project_dir), "demo", "c.txt")

        assert result.applied is True
        assert not (project_dir / "source" / "c.txt").exists()

    def test_a_file_changed_outside_the_service_can_still_be_deleted(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2), _cut(2, "b.txt", 0, CH4)])
        (project_dir / "source" / "b.txt").write_text("外部改过。\n", encoding="utf-8")

        preview = delete_whole_source_file(_pm(project_dir), "demo", "b.txt")
        result = delete_whole_source_file(_pm(project_dir), "demo", "b.txt", revision=preview.revision)

        assert result.applied is True
        assert _order(project_dir) == [1]


class TestMove:
    def test_cut_episodes_move_with_their_file_as_a_block_without_going_stale(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path,
            [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, len(A)), _own(9), _cut(3, "b.txt", 0, CH4)],
        )

        preview = move_whole_source_file(_pm(project_dir), "demo", "b.txt", direction="up")

        assert preview.applied is False
        assert sorted(preview.impact.shifted) == [1, 2, 3, 9]
        assert preview.impact.changed_with_products == preview.impact.changed_without_products == []
        move_whole_source_file(_pm(project_dir), "demo", "b.txt", direction="up", revision=preview.revision)

        project = _load(project_dir)
        assert [item["source_file"] for item in project["whole_source_files"]][:2] == ["source/b.txt", "source/a.txt"]
        assert _order(project_dir) == [3, 1, 2, 9]
        assert {entry["ledger_status"] for entry in project["episodes"] if "ledger_status" in entry} == {"planned"}

    def test_an_episode_split_apart_by_the_move_keeps_its_start_file_part(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path, [_cut(1, "a.txt", CH2, CH4, end_file="b.txt"), _cut(2, "c.txt", 0, len(C))]
        )

        preview = move_whole_source_file(_pm(project_dir), "demo", "b.txt", direction="down")
        assert preview.impact.changed_without_products == [1]
        move_whole_source_file(_pm(project_dir), "demo", "b.txt", direction="down", revision=preview.revision)

        assert _entry(project_dir, 1)["source_range"] == {"source_file": "source/a.txt", "start": CH2, "end": len(A)}
        assert _entry(project_dir, 1)["ledger_status"] == "stale"
        assert _order(project_dir) == [1, 2]

    def test_the_first_file_cannot_move_up(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [])

        with pytest.raises(SourceFileChangeError) as exc:
            move_whole_source_file(_pm(project_dir), "demo", "a.txt", direction="up")

        assert exc.value.code == "move_out_of_range"


def _writer(name: str, text: str):
    def write(source_dir: Path, undo: ExitStack) -> str:
        path = source_dir / name
        path.write_text(text, encoding="utf-8")
        undo.callback(path.unlink, missing_ok=True)
        return f"source/{name}"

    return write


class TestInsert:
    def test_a_file_inserted_between_episodes_changes_nothing(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, len(A)), _cut(2, "b.txt", 0, CH4)])

        outcome, rel = insert_whole_source_file(_pm(project_dir), "demo", _writer("新.txt", "插叙。\n"), index=1)

        assert (outcome.applied, rel) == (True, "source/新.txt")
        files = [item["source_file"] for item in _load(project_dir)["whole_source_files"]]
        assert files[:3] == ["source/a.txt", "source/新.txt", "source/b.txt"]

    def test_a_file_inserted_inside_a_crossing_episode_belongs_to_it(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", CH2, CH4, end_file="b.txt")])

        preview, rel = insert_whole_source_file(_pm(project_dir), "demo", _writer("新.txt", "插叙。\n"), index=1)
        assert (preview.applied, rel, preview.impact.changed_without_products) == (False, None, [1])
        assert not (project_dir / "source" / "新.txt").exists()

        insert_whole_source_file(
            _pm(project_dir), "demo", _writer("新.txt", "插叙。\n"), index=1, revision=preview.revision
        )

        assert _entry(project_dir, 1)["ledger_status"] == "stale"
        assert _episode_text(project_dir, 1) == A[CH2:] + "插叙。\n" + B[:CH4]


def _snapshot(project_dir: Path, name: str, text: str) -> None:
    (project_dir / "source" / "snapshots").mkdir(parents=True, exist_ok=True)
    (project_dir / "source" / "snapshots" / name).write_text(text, encoding="utf-8")


class TestExternalChange:
    def test_a_typo_fixed_outside_only_restales_the_episode_that_contains_it(self, tmp_path: Path):
        project_dir = _project_dir(
            tmp_path, [_cut(1, "a.txt", 0, CH2), _cut(2, "a.txt", CH2, len(A)), _cut(3, "b.txt", 0, CH4)]
        )
        _snapshot(project_dir, "a.txt", A)
        new_text = A.replace("少年下山", "少年走下山")
        (project_dir / "source" / "a.txt").write_text(new_text, encoding="utf-8")

        preview = accept_external_source_change(_pm(project_dir), "demo", "a.txt")

        assert preview.applied is False
        assert (preview.impact.changed_without_products, preview.impact.shifted) == ([1], [2])
        assert _entry(project_dir, 1)["source_range"]["end"] == CH2

        result = accept_external_source_change(_pm(project_dir), "demo", "a.txt", revision=preview.revision)

        assert result.applied is True
        assert _entry(project_dir, 1)["ledger_status"] == "stale"
        assert _entry(project_dir, 1)["source_range"] == {"source_file": "source/a.txt", "start": 0, "end": CH2 + 1}
        assert _entry(project_dir, 2)["source_range"] == {
            "source_file": "source/a.txt",
            "start": CH2 + 1,
            "end": len(new_text),
        }
        assert _entry(project_dir, 2)["ledger_status"] == "planned"
        assert _episode_text(project_dir, 1) == new_text[: CH2 + 1]
        assert (
            _load(project_dir)[SOURCE_FINGERPRINTS_KEY]["source/a.txt"]
            == (compute_source_fingerprints([SourceDoc("source/a.txt", new_text)])["source/a.txt"])
        )
        assert (project_dir / "source" / "snapshots" / "a.txt").read_text(encoding="utf-8") == new_text
        assert (project_dir / "source" / "a.txt").read_text(encoding="utf-8") == new_text

    def test_after_accepting_the_file_can_be_edited_again(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        _snapshot(project_dir, "a.txt", A)
        (project_dir / "source" / "a.txt").write_text(A + "外部追加。\n", encoding="utf-8")

        accepted = accept_external_source_change(_pm(project_dir), "demo", "a.txt")
        edited = edit_whole_source_file(_pm(project_dir), "demo", "a.txt", A + "外部追加。\n再追加。\n")

        assert (accepted.applied, accepted.impact.is_empty) == (True, True)
        assert edited.applied is True

    def test_a_snapshot_that_differs_counts_as_changed_without_a_recorded_fingerprint(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        project = _load(project_dir)
        del project[SOURCE_FINGERPRINTS_KEY]
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        _snapshot(project_dir, "a.txt", A)
        (project_dir / "source" / "a.txt").write_text(A.replace("少年", "青年"), encoding="utf-8")

        with pytest.raises(SourceFileChangeError) as exc:
            edit_whole_source_file(_pm(project_dir), "demo", "a.txt", A)
        preview = accept_external_source_change(_pm(project_dir), "demo", "a.txt")

        assert exc.value.code == "source_changed"
        assert preview.impact.changed_without_products == [1]

    def test_an_unchanged_file_has_nothing_to_accept(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        _snapshot(project_dir, "a.txt", A)

        with pytest.raises(SourceFileChangeError) as exc:
            accept_external_source_change(_pm(project_dir), "demo", "a.txt")

        assert exc.value.code == "source_not_changed"

    def test_without_a_snapshot_a_file_holding_cut_episodes_cannot_be_aligned(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        (project_dir / "source" / "a.txt").write_text(A + "外部追加。\n", encoding="utf-8")

        with pytest.raises(SourceFileChangeError) as exc:
            accept_external_source_change(_pm(project_dir), "demo", "a.txt")

        assert exc.value.code == "source_snapshot_missing"

    def test_a_file_without_cut_episodes_is_accepted_directly(self, tmp_path: Path):
        project_dir = _project_dir(tmp_path, [_cut(1, "a.txt", 0, CH2)])
        (project_dir / "source" / "c.txt").write_text("外部重写。\n", encoding="utf-8")

        result = accept_external_source_change(_pm(project_dir), "demo", "c.txt")

        assert (result.applied, result.impact.is_empty) == (True, True)
        assert (
            _load(project_dir)[SOURCE_FINGERPRINTS_KEY]["source/c.txt"]
            == (compute_source_fingerprints([SourceDoc("source/c.txt", "外部重写。\n")])["source/c.txt"])
        )


class TestImpactText:
    def test_groups_are_rendered_with_episode_names(self, tmp_path: Path):
        project = {"episodes": [{"episode": 1, "title": "下山"}, {"episode": 2, "title": ""}]}

        text = render_source_file_impact_text(
            {"shifted": [2], "changed_with_products": [1]}, project, lambda key, **kw: i18n_message(key, "zh", **kw)
        )

        lines = text.split("\n")
        assert len(lines) == 2
        assert "第 2 集" in lines[0]
        assert "下山" in lines[1]
