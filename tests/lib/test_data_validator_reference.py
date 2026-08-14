import json
from pathlib import Path

import pytest

from lib.data_validator import DataValidator
from lib.script_models import REFERENCE_UNIT_DURATION_RANGE


def _write(dir: Path, path: str, data: dict) -> Path:
    full = dir / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return full


def _valid_reference_script(episode: int = 1) -> dict:
    return {
        "episode": episode,
        "title": "E1",
        "creation_type": "narration",
        "generation_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": f"E{episode}U1",
                "shots": [
                    {"text": "Shot 1 (3s): @张三 推门"},
                    {"text": "Shot 2 (5s): @酒馆 全景"},
                ],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 8,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            },
        ],
    }


def _reference_project(*, with_assets: bool = True) -> dict:
    project = {
        "title": "T",
        "creation_type": "narration",
        "generation_mode": "reference_video",
        "style": "s",
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
        "characters": {},
        "scenes": {},
        "props": {},
    }
    if with_assets:
        project["characters"]["张三"] = {"description": "x"}
        project["scenes"]["酒馆"] = {"description": "x"}
    return project


@pytest.mark.unit
def test_validator_accepts_reference_video_generation_mode(tmp_path: Path):
    _write(tmp_path, "project.json", _reference_project())
    _write(tmp_path, "scripts/episode_1.json", _valid_reference_script())

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert result.valid, result.errors


@pytest.mark.integration
def test_validator_accepts_nfc_reference_for_nfd_registered_character(tmp_path: Path):
    import unicodedata

    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    assert name_nfd != name_nfc

    project = _reference_project(with_assets=False)
    project["characters"][name_nfd] = {"description": "x"}
    script = _valid_reference_script()
    script["video_units"][0]["shots"] = [{"text": f"Shot 1 (3s): @{name_nfc} 推门"}]
    script["video_units"][0]["references"] = [{"type": "character", "name": f" {name_nfc} "}]

    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", script)

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert result.valid, result.errors


@pytest.mark.unit
def test_validator_rejects_unknown_mention(tmp_path: Path):
    _write(tmp_path, "project.json", _reference_project(with_assets=False))
    _write(tmp_path, "scripts/episode_1.json", _valid_reference_script())

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert not result.valid
    assert any("张三" in e for e in result.errors)
    assert any("酒馆" in e for e in result.errors)


@pytest.mark.unit
def test_validator_allows_reference_videos_dir(tmp_path: Path):
    project = _reference_project(with_assets=False)
    project["episodes"] = []
    _write(tmp_path, "project.json", project)
    (tmp_path / "reference_videos").mkdir()
    (tmp_path / "reference_videos" / "E1U1.mp4").write_bytes(b"\x00")

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert result.valid, result.errors


@pytest.mark.unit
def test_validator_rejects_non_string_reference_name(tmp_path: Path):
    project = _reference_project(with_assets=False)
    script = _valid_reference_script()
    script["video_units"][0]["references"] = [{"type": "character", "name": {"bad": "dict"}}]
    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", script)

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert not result.valid
    assert any("reference.name 必须是非空字符串" in e for e in result.errors)


@pytest.mark.unit
def test_validator_rejects_invalid_unit_duration(tmp_path: Path):
    project = _reference_project()
    script = _valid_reference_script()
    script["video_units"][0]["duration_seconds"] = 9999  # 超出结构合理性区间
    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", script)

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert not result.valid
    low, high = REFERENCE_UNIT_DURATION_RANGE
    assert any(f"duration_seconds 必须是 {low}-{high}" in e for e in result.errors)


@pytest.mark.integration
def test_validator_rejects_duplicate_reference_video_unit_ids(tmp_path: Path):
    project = _reference_project()
    script = _valid_reference_script()
    script["video_units"].append({**script["video_units"][0]})
    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", script)

    result = DataValidator().validate_project_tree(tmp_path)

    assert not result.valid
    assert any("unit_id 重复 'E1U1'" in error for error in result.errors)


@pytest.mark.integration
def test_validator_rejects_duplicate_ad_reference_unit_ids(tmp_path: Path):
    project = _reference_project()
    project.update({"creation_type": "ad", "target_duration": 10})
    script = _valid_reference_script()
    script.update({"title": "Ad", "creation_type": "ad"})
    script["video_units"].append({**script["video_units"][0]})
    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", script)

    result = DataValidator().validate_project_tree(tmp_path)

    assert not result.valid
    assert any("unit_id 重复 'E1U1'" in error for error in result.errors)


@pytest.mark.unit
def test_validator_rejects_reference_video_in_creation_type(tmp_path: Path):
    """creation_type 严格只允许 narration / drama / ad；reference_video 属于 generation_mode
    维度。UI 不可达该值，无需兼容迁移，直接拒绝即可。
    """
    project = {
        "title": "T",
        "creation_type": "reference_video",
        "style": "s",
        "episodes": [],
        "characters": {},
        "scenes": {},
        "props": {},
    }
    _write(tmp_path, "project.json", project)

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert not result.valid
    assert any("reference_video" in e for e in result.errors)
