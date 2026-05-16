import json
from pathlib import Path

from lib.data_validator import DataValidator


def _write(dir: Path, path: str, data: dict) -> Path:
    full = dir / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return full


def _valid_reference_script(episode: int = 1) -> dict:
    return {
        "episode": episode,
        "title": "E1",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": f"E{episode}U1",
                "shots": [
                    {"duration": 3, "text": "Shot 1 (3s): @张三 推门"},
                    {"duration": 5, "text": "Shot 2 (5s): @酒馆 全景"},
                ],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 8,
                "duration_override": False,
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
        "content_mode": "narration",
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


def test_validator_accepts_reference_video_generation_mode(tmp_path: Path):
    _write(tmp_path, "project.json", _reference_project())
    _write(tmp_path, "scripts/episode_1.json", _valid_reference_script())

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert result.valid, result.errors


def test_validator_rejects_unknown_mention(tmp_path: Path):
    _write(tmp_path, "project.json", _reference_project(with_assets=False))
    _write(tmp_path, "scripts/episode_1.json", _valid_reference_script())

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert not result.valid
    assert any("张三" in e for e in result.errors)
    assert any("酒馆" in e for e in result.errors)


def test_validator_allows_reference_videos_dir(tmp_path: Path):
    project = _reference_project(with_assets=False)
    project["episodes"] = []
    _write(tmp_path, "project.json", project)
    (tmp_path / "reference_videos").mkdir()
    (tmp_path / "reference_videos" / "E1U1.mp4").write_bytes(b"\x00")

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert result.valid, result.errors


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


def test_validator_rejects_invalid_shot_duration(tmp_path: Path):
    project = _reference_project()
    script = _valid_reference_script()
    script["video_units"][0]["shots"][0]["duration"] = 99  # 超出 [1,15]
    _write(tmp_path, "project.json", project)
    _write(tmp_path, "scripts/episode_1.json", script)

    v = DataValidator()
    result = v.validate_project_tree(tmp_path)
    assert not result.valid
    assert any("duration 必须是 1-15" in e for e in result.errors)


def test_validator_rejects_reference_video_in_content_mode(tmp_path: Path):
    """新规则：content_mode 不再允许 reference_video（issue #542）。"""
    project = {
        "title": "T",
        "content_mode": "reference_video",
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
    assert any("content_mode" in e for e in result.errors)
