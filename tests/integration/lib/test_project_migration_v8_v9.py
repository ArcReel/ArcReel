"""v8→v9 参考生视频单元正文收敛迁移。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.episode_paths import REFERENCE_VIDEO_STEP1_FILENAME, episode_drafts_dir
from lib.project_migration_failure import MIGRATION_FAILURE_FILENAME, load_migration_failure
from lib.project_migrations.runner import MIGRATORS, migrate_project_dir, migrate_project_with_verdict
from lib.project_migrations.v8_to_v9_reference_unit_text import migrate_v8_to_v9
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot(project_dir: Path) -> dict[str, tuple[bytes, int]]:
    """项目目录里每个文件的内容与 mtime，用于断言「零写入」。"""
    return {
        str(path.relative_to(project_dir)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(project_dir.rglob("*"))
        if path.is_file()
    }


def _project(
    tmp_path: Path,
    *,
    content_mode: str = "narration",
    generation_mode: str = "reference_video",
    episodes: int = 1,
) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 8,
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "style": "写实",
            "aspect_ratio": "9:16",
            "characters": {"阿离": {}},
            "scenes": {"雨巷": {}},
            "props": {"伞": {}},
            "products": {},
            "episodes": [
                {"episode": number, "title": f"第 {number} 集", "script_file": f"scripts/episode_{number}.json"}
                for number in range(1, episodes + 1)
            ],
        },
    )
    return project_dir


def _unit(
    unit_id: str,
    *,
    texts: list[str],
    duration_seconds: object = 8,
    shot_durations: list[int] | None = None,
    references: object = None,
    requires_replan: object = None,
) -> dict:
    shots: list[dict] = []
    for index, text in enumerate(texts):
        shot: dict = {"shot_id": f"{unit_id}S{index + 1}", "text": text}
        if shot_durations is not None:
            shot["duration"] = shot_durations[index]
        shots.append(shot)
    unit: dict = {"unit_id": unit_id, "shots": shots, "generated_assets": {}}
    if duration_seconds is not None:
        unit["duration_seconds"] = duration_seconds
    if references is not None:
        unit["references"] = references
    if requires_replan is not None:
        unit["migration_requires_content_replan"] = requires_replan
    return unit


def _script(content_mode: str, units: list[dict], *, episode: int = 1) -> dict:
    return {
        "episode": episode,
        "title": f"第 {episode} 集",
        "content_mode": content_mode,
        "video_units": units,
    }


def _draft_path(project_dir: Path, episode: int = 1) -> Path:
    return episode_drafts_dir(project_dir, episode) / REFERENCE_VIDEO_STEP1_FILENAME


@pytest.mark.parametrize("content_mode", ["narration", "drama", "ad"])
def test_shot_text_joins_back_into_one_body_for_every_content_mode(tmp_path: Path, content_mode: str) -> None:
    project_dir = _project(tmp_path, content_mode=content_mode)
    _write_json(
        project_dir / "scripts/episode_1.json",
        _script(
            content_mode,
            [
                _unit("E1U1", texts=["她推开门", "雨落在石板上", "@[阿离]抬头"]),
                _unit("E1U2", texts=["伞收起来"]),
            ],
        ),
    )

    migrate_v8_to_v9(project_dir)

    units = _read_json(project_dir / "scripts/episode_1.json")["video_units"]
    assert [unit["text"] for unit in units] == ["她推开门\n雨落在石板上\n@[阿离]抬头", "伞收起来"]
    assert all("shots" not in unit for unit in units)
    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION


def test_literal_shot_prefixes_stay_verbatim_in_the_body(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    _write_json(
        project_dir / "scripts/episode_1.json",
        _script("narration", [_unit("E1U1", texts=["镜头1：她推开门", "镜头2：雨落在石板上"])]),
    )
    _write_json(
        _draft_path(project_dir),
        {"units": [{"unit_id": "E1U1", "duration_seconds": 8, "shots": [{"text": "镜头1：她推开门"}]}]},
    )

    migrate_v8_to_v9(project_dir)

    assert _read_json(project_dir / "scripts/episode_1.json")["video_units"][0]["text"] == (
        "镜头1：她推开门\n镜头2：雨落在石板上"
    )
    assert _read_json(_draft_path(project_dir))["units"][0]["text"] == "镜头1：她推开门"


def test_references_and_replan_provenance_leave_only_the_replan_flag(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    _write_json(
        project_dir / "scripts/episode_1.json",
        _script(
            "ad",
            [
                _unit(
                    "E1U1",
                    texts=["她推开门"],
                    references=[{"type": "character", "name": "阿离"}],
                    requires_replan=True,
                ),
                _unit(
                    "E1U2",
                    texts=["雨落在石板上"],
                    references=[{"type": "scene", "name": "雨巷"}],
                    requires_replan=False,
                ),
            ],
        ),
    )

    migrate_v8_to_v9(project_dir)

    units = _read_json(project_dir / "scripts/episode_1.json")["video_units"]
    assert all("references" not in unit and "migration_requires_content_replan" not in unit for unit in units)
    assert units[0]["needs_replan"] is True
    assert units[1].get("needs_replan", False) is False


def test_step1_draft_migrates_alongside_the_episode_script(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, episodes=2)
    for episode in (1, 2):
        _write_json(
            project_dir / f"scripts/episode_{episode}.json",
            _script("narration", [_unit(f"E{episode}U1", texts=["剧本上半", "剧本下半"])], episode=episode),
        )
        _write_json(
            _draft_path(project_dir, episode),
            {
                "units": [
                    {
                        "unit_id": f"E{episode}U1",
                        "duration_seconds": 8,
                        "shots": [{"text": "草稿上半"}, {"text": "草稿下半"}],
                        "references": [{"type": "prop", "name": "伞"}],
                    }
                ]
            },
        )

    migrate_v8_to_v9(project_dir)

    for episode in (1, 2):
        script_unit = _read_json(project_dir / f"scripts/episode_{episode}.json")["video_units"][0]
        assert script_unit["text"] == "剧本上半\n剧本下半"
        draft_unit = _read_json(_draft_path(project_dir, episode))["units"][0]
        assert draft_unit["text"] == "草稿上半\n草稿下半"
        assert "shots" not in draft_unit and "references" not in draft_unit


def test_legacy_shot_durations_land_on_the_unit(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    _write_json(
        project_dir / "scripts/episode_1.json",
        _script(
            "narration",
            [
                _unit(
                    "E1U1",
                    texts=["她推开门", "雨落在石板上"],
                    duration_seconds=None,
                    shot_durations=[4, 5],
                )
            ],
        ),
    )

    migrate_v8_to_v9(project_dir)

    unit = _read_json(project_dir / "scripts/episode_1.json")["video_units"][0]
    assert unit["duration_seconds"] == 9
    assert unit["text"] == "她推开门\n雨落在石板上"


@pytest.mark.parametrize(
    "broken_units",
    [
        pytest.param([{"unit_id": "E2U1", "duration_seconds": 8, "shots": "她推开门"}], id="shots-not-a-list"),
        pytest.param(
            [{"unit_id": "E2U1", "duration_seconds": 8, "shots": [{"text": 42}]}],
            id="shot-text-not-a-string",
        ),
    ],
)
def test_a_broken_script_leaves_the_project_dir_byte_identical(tmp_path: Path, broken_units: list[dict]) -> None:
    project_dir = _project(tmp_path, episodes=2)
    _write_json(
        project_dir / "scripts/episode_1.json",
        _script("narration", [_unit("E1U1", texts=["她推开门"])]),
    )
    _write_json(_draft_path(project_dir), {"units": [{"unit_id": "E1U1", "duration_seconds": 8, "shots": []}]})
    _write_json(project_dir / "scripts/episode_2.json", _script("narration", broken_units, episode=2))
    before = _snapshot(project_dir)

    with pytest.raises(ValueError):
        migrate_v8_to_v9(project_dir)

    assert _snapshot(project_dir) == before
    assert not list(project_dir.rglob("*.bak.v8-*"))


def test_a_broken_script_records_a_repair_verdict_through_the_runner(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    _write_json(
        project_dir / "scripts/episode_1.json",
        _script("narration", [{"unit_id": "E1U1", "duration_seconds": 8, "shots": "她推开门"}]),
    )
    business_files = [project_dir / "project.json", project_dir / "scripts/episode_1.json"]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in business_files]

    record = migrate_project_with_verdict(project_dir)

    assert record is not None
    assert record.schema_version == 8
    assert (project_dir / MIGRATION_FAILURE_FILENAME).is_file()
    assert load_migration_failure(project_dir) == record
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in business_files] == before


def test_storyboard_project_only_gets_the_version_bump(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, generation_mode="storyboard")
    script = {
        "episode": 1,
        "title": "第 1 集",
        "content_mode": "narration",
        "segments": [{"segment_id": "E1S01", "image_prompt": "雨巷", "video_prompt": "转身"}],
    }
    _write_json(project_dir / "scripts/episode_1.json", script)
    script_before = (project_dir / "scripts/episode_1.json").read_bytes()

    migrate_v8_to_v9(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert (project_dir / "scripts/episode_1.json").read_bytes() == script_before
    # 分镜图生视频只改 project.json，备份也只有它那一份。
    backups = sorted(path.name.split(".bak.v8-")[0] for path in project_dir.rglob("*.bak.v8-*"))
    assert backups == ["project.json"]


def test_rerunning_on_a_migrated_project_is_a_no_op(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    _write_json(
        project_dir / "scripts/episode_1.json",
        _script("narration", [_unit("E1U1", texts=["她推开门", "雨落在石板上"])]),
    )
    _write_json(
        _draft_path(project_dir),
        {"units": [{"unit_id": "E1U1", "duration_seconds": 8, "shots": [{"text": "草稿"}]}]},
    )
    assert migrate_project_dir(project_dir) is True
    after_first = _snapshot(project_dir)

    migrate_v8_to_v9(project_dir)
    assert migrate_project_dir(project_dir) is False

    assert _snapshot(project_dir) == after_first


def test_startup_scan_and_archive_import_share_one_migration_entry() -> None:
    from server.services import project_archive

    assert MIGRATORS[8] is migrate_v8_to_v9
    assert project_archive.migrate_project_dir is migrate_project_dir
