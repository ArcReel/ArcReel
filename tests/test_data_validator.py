import json
from pathlib import Path

from lib.data_validator import DataValidator, validate_episode, validate_project


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_payload(content_mode: str = "narration") -> dict:
    return {
        "title": "Demo",
        "content_mode": content_mode,
        "style": "Anime",
        "characters": {
            "姜月茴": {"description": "女主"},
        },
        "clues": {
            "玉佩": {
                "type": "prop",
                "description": "关键线索",
                "importance": "major",
            }
        },
    }


class TestDataValidator:
    def test_validate_project_success(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload())

        validator = DataValidator(projects_root=str(tmp_path / "projects"))
        result = validator.validate_project("demo")

        assert result.valid
        assert result.errors == []
        assert "验证通过" in str(result)

    def test_validate_project_reports_missing_and_invalid_fields(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "",
                "content_mode": "invalid",
                "style": "",
                "characters": {"A": []},
                "clues": {
                    "X": {
                        "type": "bad",
                        "description": "",
                        "importance": "wrong",
                    }
                },
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("title" in error for error in result.errors)
        assert any("content_mode" in error for error in result.errors)
        assert any("角色 'A' 数据格式错误" in error for error in result.errors)
        assert any("type 值无效" in error for error in result.errors)

    def test_validate_episode_narration_success_with_warnings(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "characters_in_episode": ["姜月茴"],
                "clues_in_episode": ["玉佩"],
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "clues_in_segment": ["玉佩"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode(
            "demo", "episode_1.json"
        )

        assert result.valid
        assert any("characters_in_episode 字段已废弃" in w for w in result.warnings)
        assert any("clues_in_episode 字段已废弃" in w for w in result.warnings)
        assert any("缺少 duration_seconds" in w for w in result.warnings)

    def test_validate_episode_accepts_split_segment_ids_and_missing_clues_warning(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S03_1",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode(
            "demo", "episode_1.json"
        )

        assert result.valid
        assert any("缺少 clues_in_segment" in warning for warning in result.warnings)

    def test_validate_episode_reports_invalid_references_and_fields(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": "bad",
                "title": "",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "bad-id",
                        "duration_seconds": 5,
                        "novel_text": "",
                        "characters_in_segment": ["未知角色"],
                        "clues_in_segment": ["未知线索"],
                        "image_prompt": "",
                        "video_prompt": "",
                    }
                ],
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode(
            "demo", "episode_1.json"
        )

        assert not result.valid
        assert any("episode (整数)" in error for error in result.errors)
        assert any("segment_id 格式错误" in error for error in result.errors)
        assert any("duration_seconds 值无效" in error for error in result.errors)
        assert any("不存在于 project.json 的角色" in error for error in result.errors)
        assert any("不存在于 project.json 的线索" in error for error in result.errors)

    def test_validate_episode_drama_mode(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("drama"))
        _write_json(
            project_dir / "scripts" / "episode_2.json",
            {
                "episode": 2,
                "title": "第二集",
                "content_mode": "drama",
                "scenes": [
                    {
                        "scene_id": "E2S01",
                        "scene_type": "剧情",
                        "duration_seconds": 8,
                        "characters_in_scene": ["姜月茴"],
                        "clues_in_scene": ["玉佩"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = validate_episode("demo", "episode_2.json", projects_root=str(tmp_path / "projects"))
        assert result.valid

    def test_validate_helpers_on_missing_files(self, tmp_path):
        result = validate_project("missing", projects_root=str(tmp_path / "projects"))
        assert not result.valid
        assert any("无法加载 project.json" in error for error in result.errors)
