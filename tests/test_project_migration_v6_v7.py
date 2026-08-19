"""v6→v7 广告参考路线自包含 video_units 迁移。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from lib.project_migrations.v6_to_v7_ad_reference_video_units import migrate_v6_to_v7
from lib.reference_video.text_parser import extract_mentions
from lib.script_models import ReferenceVideoScript

pytestmark = pytest.mark.integration


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _project(tmp_path: Path, *, mode: str = "reference_video", episodes: int = 1) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 6,
            "content_mode": "ad",
            "generation_mode": mode,
            "characters": {"演员": {}},
            "scenes": {"厨房": {}},
            "props": {"杯子": {}},
            "products": {"咖啡": {}},
            "episodes": [
                {"episode": number, "title": f"第 {number} 集", "script_file": f"scripts/episode_{number}.json"}
                for number in range(1, episodes + 1)
            ],
        },
    )
    return project_dir


def _shot(shot_id: str, *, duration: object = 4, voiceover: str = "", transition: str = "cut") -> dict:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": voiceover,
        "characters_in_shot": ["演员"],
        "scenes": ["厨房"],
        "props": ["杯子"],
        "products_in_shot": ["咖啡"],
        "image_prompt": {
            "shot_type": "Medium Shot",
            "composition": "产品位于画面中央",
            "lighting": "晨光",
            "color_tone": "暖色",
            "ambiance": "清新",
            "scene": "演员端起咖啡",
        },
        "video_prompt": {
            "action": "演员缓慢转动杯子",
            "camera_motion": "Push In",
            "ambiance_audio": "轻柔音乐",
            "dialogue": [],
        },
        "transition_to_next": transition,
        "generated_assets": {"status": "pending"},
    }


def _script(*, units: object = None) -> dict:
    payload = {
        "episode": 1,
        "title": "咖啡广告",
        "content_mode": "ad",
        "duration_seconds": 8,
        "shots": [_shot("E1S1", voiceover="醒来的第一口"), _shot("E1S2")],
    }
    if units is not None:
        payload["reference_units"] = units
    return payload


def test_existing_index_preserves_identity_boundaries_assets_and_content(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    assets = {
        "video_clip": "reference_videos/E1U9.mp4",
        "status": "completed",
        "source_signature": "legacy-signature",
        "video_uri": "provider://paid-job",
    }
    script = _script(
        units=[
            {
                "unit_id": "paid-unit",
                "shot_ids": ["E1S2", "E1S1"],
                "references": [{"type": "scene", "name": "old-cache"}],
                "generated_assets": assets,
                "stale": True,
            }
        ]
    )
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    migrated = _read_json(project_dir / "scripts/episode_1.json")
    assert "shots" not in migrated
    assert "reference_units" not in migrated
    assert [unit["unit_id"] for unit in migrated["video_units"]] == ["paid-unit"]
    unit = migrated["video_units"][0]
    assert unit["duration_seconds"] == 8
    assert unit["generated_assets"] == assets
    # 引用不再落盘：正文里的 `@[名称]` 记号按旧字段顺序写入，读时派生。
    text = unit["text"]
    assert extract_mentions(text)[:4] == ["咖啡", "演员", "厨房", "杯子"]
    assert text.index("缓慢转动") < text.index("醒来的第一口")
    assert "@[咖啡]" in text
    assert "{醒来的第一口}" in text
    assert "section" not in json.dumps(migrated, ensure_ascii=False)
    assert list((project_dir / "scripts").glob("episode_1.json.bak.v6-*"))
    assert _read_json(project_dir / "project.json")["schema_version"] == 7


def test_unscripted_episode_advances_schema_without_creating_script(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)

    migrate_v6_to_v7(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 7
    assert not (project_dir / "scripts/episode_1.json").exists()
    assert not list(project_dir.glob("scripts/episode_1.json.bak.v6-*"))


@pytest.mark.parametrize("missing_value", [pytest.param("absent", id="absent"), pytest.param(None, id="null")])
def test_missing_index_creates_one_unit_per_shot_in_order(tmp_path: Path, missing_value: object) -> None:
    project_dir = _project(tmp_path)
    script = _script()
    if missing_value is None:
        script["reference_units"] = None
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    units = _read_json(project_dir / "scripts/episode_1.json")["video_units"]
    assert [unit["unit_id"] for unit in units] == ["E1U1", "E1U2"]
    assert [unit["duration_seconds"] for unit in units] == [4, 4]
    assert "{醒来的第一口}" in units[0]["text"]
    assert "{醒来的第一口}" not in units[1]["text"]


def test_empty_index_creates_one_unit_per_shot_without_data_loss(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    _write_json(project_dir / "scripts/episode_1.json", _script(units=[]))

    migrate_v6_to_v7(project_dir)

    migrated = _read_json(project_dir / "scripts/episode_1.json")
    assert [unit["unit_id"] for unit in migrated["video_units"]] == ["E1U1", "E1U2"]
    assert all(unit["text"].strip() for unit in migrated["video_units"])


def test_partial_index_preserves_uncovered_shots_as_replan_units(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(units=[{"unit_id": "E1U1", "shot_ids": ["E1S1"], "generated_assets": {}}])
    script["shots"][1]["image_prompt"]["scene"] = "未索引镜头仍须保留"
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    existing, recovered = _read_json(project_dir / "scripts/episode_1.json")["video_units"]
    assert existing["unit_id"] == "E1U1"
    assert recovered["unit_id"] == "E1U2"
    assert recovered["needs_replan"] is True
    assert "未索引镜头仍须保留" in recovered["text"]


def test_existing_index_preserves_final_member_transition(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(units=[{"unit_id": "E1U1", "shot_ids": ["E1S1", "E1S2"], "generated_assets": {}}])
    script["shots"][1]["transition_to_next"] = "dissolve"
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    unit = _read_json(project_dir / "scripts/episode_1.json")["video_units"][0]
    assert unit["transition_to_next"] == "dissolve"


def test_many_member_legacy_unit_keeps_all_text_in_one_body(tmp_path: Path) -> None:
    """成员数不再有上限：多成员旧 unit 的全部画面文本拼进同一段正文，不因数量判问题壳。"""
    project_dir = _project(tmp_path)
    script = _script()
    for ordinal in range(3, 6):
        script["shots"].append(_shot(f"E1S{ordinal}"))
    for ordinal, shot in enumerate(script["shots"], start=1):
        shot["image_prompt"]["scene"] = f"保留镜头{ordinal}"
    script["reference_units"] = [
        {
            "unit_id": "paid-unit",
            "shot_ids": [f"E1S{ordinal}" for ordinal in range(1, 6)],
            "generated_assets": {"video_uri": "provider://paid-job"},
        }
    ]
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    migrated = _read_json(project_dir / "scripts/episode_1.json")
    unit = migrated["video_units"][0]
    assert all(f"保留镜头{ordinal}" in unit["text"] for ordinal in range(1, 6))
    assert "needs_replan" not in unit
    assert unit["generated_assets"] == {"video_uri": "provider://paid-job"}
    ReferenceVideoScript.model_validate(migrated)


def test_nonempty_zero_duration_unit_remains_readable_and_requires_replan(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(units=[{"unit_id": "E1U1", "shot_ids": ["E1S1", "E1S2"], "generated_assets": {}}])
    script["shots"][0]["duration_seconds"] = 0
    script["shots"][1]["duration_seconds"] = "bad"
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    migrated = _read_json(project_dir / "scripts/episode_1.json")
    unit = migrated["video_units"][0]
    assert unit["duration_seconds"] == 1
    assert unit["needs_replan"] is True
    # 两个成员镜头的画面文本都留在同一段正文里。
    assert unit["text"].count("演员端起咖啡") == 2
    ReferenceVideoScript.model_validate(migrated)


def test_dangling_and_mixed_speech_preserve_unit_as_replan_shell(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(
        units=[
            {
                "unit_id": "E1U7",
                "shot_ids": ["E1S404"],
                "references": [],
                "generated_assets": {"video_clip": "reference_videos/E1U7.mp4", "status": "completed"},
            },
            {
                "unit_id": "E1U8",
                "shot_ids": ["E1S1"],
                "references": [],
                "generated_assets": {},
            },
        ]
    )
    script["shots"][0]["video_prompt"]["dialogue"] = [{"speaker": "演员", "line": "试试这一杯"}]
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    first, second = _read_json(project_dir / "scripts/episode_1.json")["video_units"][:2]
    assert (first["unit_id"], first["text"], first["duration_seconds"], first["needs_replan"]) == (
        "E1U7",
        "",
        0,
        True,
    )
    assert first["generated_assets"]["video_clip"].endswith("E1U7.mp4")
    assert json.loads(first["note"]) == {"unresolved_legacy_shot_ids": ["E1S404"]}
    assert second["needs_replan"] is True
    text = second["text"]
    assert "@[演员]：{试试这一杯}" in text
    assert "{醒来的第一口}" in text


def test_mixed_valid_and_dangling_members_keep_content_and_missing_id_history(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(
        units=[
            {
                "unit_id": "E1U7",
                "shot_ids": ["E1S1", "E1S404"],
                "generated_assets": {"video_uri": "provider://paid-job"},
            }
        ]
    )
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    migrated = _read_json(project_dir / "scripts/episode_1.json")
    unit = migrated["video_units"][0]
    assert unit["unit_id"] == "E1U7"
    assert "醒来的第一口" in unit["text"]
    assert unit["generated_assets"] == {"video_uri": "provider://paid-job"}
    assert unit["needs_replan"] is True
    assert json.loads(unit["note"]) == {"unresolved_legacy_shot_ids": ["E1S404"]}
    ReferenceVideoScript.model_validate(migrated)


def test_overlapping_legacy_members_mark_every_affected_unit_for_replanning(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(
        units=[
            {"unit_id": "E1U1", "shot_ids": ["E1S1"], "generated_assets": {}},
            {"unit_id": "E1U2", "shot_ids": ["E1S1", "E1S2"], "generated_assets": {}},
        ]
    )
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    migrated = _read_json(project_dir / "scripts/episode_1.json")
    first, second = migrated["video_units"]
    assert first["needs_replan"] is True
    assert second["needs_replan"] is True
    assert json.loads(first["note"]) == {"overlapping_legacy_shot_ids": ["E1S1"]}
    assert json.loads(second["note"]) == {"overlapping_legacy_shot_ids": ["E1S1"]}
    ReferenceVideoScript.model_validate(migrated)


def test_duplicate_legacy_unit_ids_fail_preflight_without_writes(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(
        units=[
            {"unit_id": "E1U1", "shot_ids": ["E1S1"], "generated_assets": {}},
            {"unit_id": "E1U1", "shot_ids": ["E1S2"], "generated_assets": {}},
        ]
    )
    _write_json(project_dir / "scripts/episode_1.json", script)
    project_before = (project_dir / "project.json").read_bytes()
    script_before = (project_dir / "scripts/episode_1.json").read_bytes()

    with pytest.raises(ValueError, match=r"reference_units\[1\]\.unit_id 重复: E1U1"):
        migrate_v6_to_v7(project_dir)

    assert (project_dir / "project.json").read_bytes() == project_before
    assert (project_dir / "scripts/episode_1.json").read_bytes() == script_before
    assert not list((project_dir / "scripts").glob("*.bak.v6-*"))


def test_empty_legacy_members_become_replan_shell_and_same_name_uses_product_priority(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(
        units=[
            {"unit_id": "empty-paid", "shot_ids": [], "generated_assets": {"video_uri": "provider://job"}},
            {"unit_id": "collision", "shot_ids": ["E1S1"], "generated_assets": {}},
        ]
    )
    script["shots"][0]["products_in_shot"] = ["同名"]
    script["shots"][0]["characters_in_shot"] = ["同名"]
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    empty, collision = _read_json(project_dir / "scripts/episode_1.json")["video_units"][:2]
    assert (empty["text"], empty["duration_seconds"], empty["needs_replan"]) == ("", 0, True)
    assert empty["generated_assets"] == {"video_uri": "provider://job"}
    # 同名的商品与角色只写一个 `@[同名]` 记号，类型归属交读时派生（商品优先）。
    assert collision["text"].count("@[同名]") == 1
    assert extract_mentions(collision["text"])[0] == "同名"


def test_duration_sums_only_positive_integer_member_durations(tmp_path: Path) -> None:
    project_dir = _project(tmp_path)
    script = _script(
        units=[{"unit_id": "E1U1", "shot_ids": ["E1S1", "E1S2"], "references": [], "generated_assets": {}}]
    )
    script["shots"][0]["duration_seconds"] = True
    script["shots"][1]["duration_seconds"] = 7
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    unit = _read_json(project_dir / "scripts/episode_1.json")["video_units"][0]
    assert unit["duration_seconds"] == 7


def test_preflight_failure_writes_nothing(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, episodes=2)
    first = _script()
    second = _script()
    second["episode"] = 2
    _write_json(project_dir / "scripts/episode_1.json", first)
    (project_dir / "scripts/episode_2.json").write_text("{broken", encoding="utf-8")
    project_before = (project_dir / "project.json").read_bytes()
    script_before = (project_dir / "scripts/episode_1.json").read_bytes()

    with pytest.raises((ValueError, json.JSONDecodeError)):
        migrate_v6_to_v7(project_dir)

    assert (project_dir / "project.json").read_bytes() == project_before
    assert (project_dir / "scripts/episode_1.json").read_bytes() == script_before
    assert not list((project_dir / "scripts").glob("*.bak.v6-*"))


def test_interruption_is_rerunnable_and_project_version_commits_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = _project(tmp_path, episodes=2)
    first = _script()
    second = copy.deepcopy(first)
    second["episode"] = 2
    second["shots"] = [_shot("E2S1")]
    _write_json(project_dir / "scripts/episode_1.json", first)
    _write_json(project_dir / "scripts/episode_2.json", second)

    from lib.project_migrations import v6_to_v7_ad_reference_video_units as migration

    real_atomic_write = migration.atomic_write_json
    writes = 0

    def interrupt(path: Path, payload: object) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated interruption")
        real_atomic_write(path, payload)

    monkeypatch.setattr(migration, "atomic_write_json", interrupt)
    with pytest.raises(OSError, match="simulated interruption"):
        migrate_v6_to_v7(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 6
    monkeypatch.setattr(migration, "atomic_write_json", real_atomic_write)
    migrate_v6_to_v7(project_dir)
    assert _read_json(project_dir / "project.json")["schema_version"] == 7
    assert "video_units" in _read_json(project_dir / "scripts/episode_1.json")
    assert "video_units" in _read_json(project_dir / "scripts/episode_2.json")


def test_other_project_routes_are_untouched_except_version(tmp_path: Path) -> None:
    project_dir = _project(tmp_path, mode="storyboard")
    script = _script()
    _write_json(project_dir / "scripts/episode_1.json", script)

    migrate_v6_to_v7(project_dir)

    assert _read_json(project_dir / "scripts/episode_1.json") == script
    assert _read_json(project_dir / "project.json")["schema_version"] == 7
