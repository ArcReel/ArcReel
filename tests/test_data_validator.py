import json
from pathlib import Path

import pytest

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
        "scenes": {
            "古宅": {"description": "废弃古宅，阴暗潮湿"},
        },
        "props": {
            "玉佩": {"description": "关键道具"},
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
                "scenes": {
                    "X": {"description": ""},
                },
                "props": {
                    "Y": {"description": ""},
                },
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("title" in error for error in result.errors)
        assert any("content_mode" in error for error in result.errors)
        assert any("角色 'A' 数据格式错误" in error for error in result.errors)
        # scenes/props 缺少 description 也应报错
        assert any("场景 'X'" in error for error in result.errors)
        assert any("道具 'Y'" in error for error in result.errors)

    def test_validate_episode_narration_success_with_warnings(self, tmp_path):
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
                        "segment_id": "E1S01",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "scenes": ["古宅"],
                        "props": ["玉佩"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert result.valid
        assert any("缺少 duration_seconds" in w for w in result.warnings)

    def test_validate_episode_accepts_split_segment_ids_and_missing_scenes_props_warning(self, tmp_path):
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert result.valid
        # scenes/props 都是 optional，缺少时应有警告
        assert any("缺少 scenes" in warning for warning in result.warnings)
        assert any("缺少 props" in warning for warning in result.warnings)

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
                        "scenes": ["未知场景"],
                        "props": ["未知道具"],
                        "image_prompt": "",
                        "video_prompt": "",
                    }
                ],
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid
        assert any("episode (整数)" in error for error in result.errors)
        assert any("segment_id 格式错误" in error for error in result.errors)
        # 5 是正整数 → 合法，不应再报 duration_seconds 错误
        assert not any("duration_seconds 值无效" in error for error in result.errors)
        assert any("不存在于 project.json 的角色" in error for error in result.errors)
        assert any("不存在于 project.json 的场景" in error for error in result.errors)
        assert any("不存在于 project.json 的道具" in error for error in result.errors)

    @pytest.mark.parametrize("bad_duration", [0, -1, "5", 4.5, True])
    def test_validate_episode_rejects_non_positive_integer_duration(self, tmp_path, bad_duration):
        """非正整数的 duration_seconds 仍应报错（0 / 负数 / 字符串 / 浮点 / bool）。"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "x",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": bad_duration,
                        "novel_text": "x",
                        "image_prompt": "x",
                        "video_prompt": "x",
                    }
                ],
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid, f"bad={bad_duration}"
        assert any("duration_seconds 值无效" in e for e in result.errors), f"bad={bad_duration}; errors={result.errors}"

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
                        "scenes": ["古宅"],
                        "props": ["玉佩"],
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

    # ── 新增测试 ──────────────────────────────────────────────

    def test_project_json_validates_scenes_and_props(self, tmp_path):
        """新 schema：scenes + props 两个字典都通过校验"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "style": "Anime",
                "characters": {},
                "scenes": {
                    "书房": {"description": "昏暗的古代书房"},
                    "庭院": {"description": "月下庭院"},
                },
                "props": {
                    "长剑": {"description": "寒光闪闪的长剑"},
                },
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert result.valid
        assert result.errors == []

    def test_project_json_rejects_legacy_clues(self, tmp_path):
        """顶层 clues 字段应报废弃错误"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "style": "Anime",
                "characters": {},
                "clues": {"玉佩": {"type": "prop", "description": "xxx", "importance": "major"}},
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("已废弃字段 clues" in error for error in result.errors)

    def test_validate_scenes_dict_missing_description(self, tmp_path):
        """scenes 字典中某个场景缺少 description 应报错"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "style": "Anime",
                "characters": {},
                "scenes": {
                    "书房": {"description": ""},  # 空字符串视为缺失
                },
                "props": {},
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("场景 '书房'" in error and "description" in error for error in result.errors)

    def test_validate_props_dict_missing_description(self, tmp_path):
        """props 字典中某个道具缺少 description 应报错"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "style": "Anime",
                "characters": {},
                "scenes": {},
                "props": {
                    "玉佩": {},  # 完全缺少 description 键
                },
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("道具 '玉佩'" in error and "description" in error for error in result.errors)

    def test_validate_episode_drama_invalid_scene_prop_refs(self, tmp_path):
        """drama 模式：引用未定义的 scenes/props 应报错"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("drama"))
        _write_json(
            project_dir / "scripts" / "episode_3.json",
            {
                "episode": 3,
                "title": "第三集",
                "content_mode": "drama",
                "scenes": [
                    {
                        "scene_id": "E3S01",
                        "scene_type": "剧情",
                        "duration_seconds": 8,
                        "characters_in_scene": ["姜月茴"],
                        "scenes": ["未知场景"],
                        "props": ["未知道具"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_3.json")
        assert not result.valid
        assert any("不存在于 project.json 的场景" in error for error in result.errors)
        assert any("不存在于 project.json 的道具" in error for error in result.errors)
