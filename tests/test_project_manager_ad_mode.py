"""广告/短片项目创建：project.json 专属字段与恒单集结构。

只断言外部行为：调用 create_project_metadata 后读 project.json 形状，
不触碰 ProjectManager 内部实现。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager, require_creation_type

pytestmark = pytest.mark.unit


def _pm(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path / "projects")


class TestCreateAdProjectMetadata:
    def test_ad_project_json_shape(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo-ad", creation_type="ad")
        project = pm.create_project_metadata(
            "demo-ad",
            "速干杯带货",
            "Realistic",
            "ad",
            target_duration=30,
            brief="突出 3 秒速干卖点",
        )

        assert project["creation_type"] == "ad"
        assert project["target_duration"] == 30
        assert project["brief"] == "突出 3 秒速干卖点"
        assert "default_duration" not in project
        assert project["episodes"] == [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}]

        # 落盘后的 project.json 与返回值一致
        on_disk = pm.load_project("demo-ad")
        assert on_disk["target_duration"] == 30
        assert on_disk["episodes"] == project["episodes"]

    def test_ad_defaults_target_duration_to_60_and_empty_brief(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo-ad", creation_type="ad")
        project = pm.create_project_metadata("demo-ad", "短片", "Realistic", "ad")

        assert project["target_duration"] == 60
        assert project["brief"] == ""

    def test_ad_rejects_default_duration(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo-ad", creation_type="ad")
        with pytest.raises(ValueError, match="default_duration"):
            pm.create_project_metadata("demo-ad", "短片", "Realistic", "ad", default_duration=8)

    def test_ad_rejects_non_string_brief(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo-ad", creation_type="ad")
        with pytest.raises(ValueError, match="brief"):
            pm.create_project_metadata("demo-ad", "短片", "Realistic", "ad", brief=123)  # type: ignore[arg-type]

    def test_rejects_unknown_creation_type(self, tmp_path):
        """非路由调用方传入未登记创作类型时不得落盘：写进去的项目无分派器能处理。"""
        pm = _pm(tmp_path)
        pm.create_project("demo-doc")
        with pytest.raises(ValueError, match="creation_type"):
            pm.create_project_metadata("demo-doc", "纪录片", "Realistic", "documentary")

    @pytest.mark.parametrize("project", [{}, {"creation_type": None}, {"creation_type": ""}, {"creation_type": "doc"}])
    def test_require_creation_type_rejects_missing_and_invalid(self, project):
        """写入与付费提交路径读创作类型时不得造默认值：缺失与非法一律 fail-loud。"""
        with pytest.raises(ValueError, match="creation_type"):
            require_creation_type(project)

    @pytest.mark.parametrize("value", ["narration", "drama", "ad"])
    def test_require_creation_type_accepts_declared_values(self, value):
        assert require_creation_type({"creation_type": value}) == value

    def test_rejects_unknown_source_file_type(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo-src")
        with pytest.raises(ValueError, match="source_file_type"):
            pm.create_project_metadata("demo-src", "小说", "Anime", "narration", source_file_type="screen_play")

    def test_extras_cannot_override_core_fields(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo-ad", creation_type="ad")
        with pytest.raises(ValueError, match="extras"):
            pm.create_project_metadata(
                "demo-ad",
                "短片",
                "Realistic",
                "ad",
                extras={"default_duration": 8, "video_backend": "vidu"},
            )
        with pytest.raises(ValueError, match="extras"):
            pm.create_project_metadata("demo-ad", "短片", "Realistic", "ad", extras={"creation_type": "drama"})

    def test_non_ad_rejects_target_duration_and_brief(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", creation_type="narration")
        with pytest.raises(ValueError, match="target_duration"):
            pm.create_project_metadata("demo", "Demo", "Anime", "narration", target_duration=60)
        with pytest.raises(ValueError, match="brief"):
            pm.create_project_metadata("demo", "Demo", "Anime", "narration", brief="x")

    def test_narration_metadata_unchanged(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", creation_type="narration")
        project = pm.create_project_metadata("demo", "Demo", "Anime", "narration", default_duration=4)

        assert "target_duration" not in project
        assert "brief" not in project
        assert project["default_duration"] == 4
        assert project["episodes"] == []
