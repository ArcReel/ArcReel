"""项目创建写入源文件性质（source_kind）：持久化、缺省 novel、非法值拒绝。

只断言外部行为：调用 create_project_metadata 后读 project.json 形状。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager, resolve_source_kind


def _pm(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path / "projects")


class TestResolveSourceKind:
    """统一回退入口：合法值原样返回，缺失 / 非法 / 脏数据一律回退 novel 不抛异常。"""

    def test_valid_values_pass_through(self):
        assert resolve_source_kind({"source_kind": "novel"}) == "novel"
        assert resolve_source_kind({"source_kind": "screenplay"}) == "screenplay"

    def test_missing_key_falls_back_to_novel(self):
        assert resolve_source_kind({}) == "novel"

    def test_invalid_string_falls_back_to_novel(self):
        assert resolve_source_kind({"source_kind": "screen_play"}) == "novel"
        assert resolve_source_kind({"source_kind": ""}) == "novel"

    def test_unhashable_dirty_value_falls_back_without_raising(self):
        # list / dict 等不可哈希脏值不得在成员判断时抛 TypeError，须回退 novel
        assert resolve_source_kind({"source_kind": ["novel"]}) == "novel"
        assert resolve_source_kind({"source_kind": {"k": "v"}}) == "novel"
        assert resolve_source_kind({"source_kind": 123}) == "novel"


class TestCreateSourceKind:
    def test_screenplay_persisted_to_project_json_top_level(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", content_mode="drama")
        project = pm.create_project_metadata("demo", "剧本项目", "Anime", "drama", source_kind="screenplay")

        assert project["source_kind"] == "screenplay"
        assert pm.load_project("demo")["source_kind"] == "screenplay"

    def test_defaults_to_novel_when_omitted(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", content_mode="drama")
        project = pm.create_project_metadata("demo", "默认项目", "Anime", "drama")

        assert project["source_kind"] == "novel"
        assert pm.load_project("demo")["source_kind"] == "novel"

    def test_invalid_source_kind_rejected(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", content_mode="drama")
        with pytest.raises(ValueError, match="source_kind"):
            pm.create_project_metadata("demo", "X", "Anime", "drama", source_kind="screen_play")

    def test_empty_string_source_kind_rejected(self, tmp_path):
        # 空字符串是非法值，不得被当作"未传入"而静默回退到 novel
        pm = _pm(tmp_path)
        pm.create_project("demo", content_mode="drama")
        with pytest.raises(ValueError, match="source_kind"):
            pm.create_project_metadata("demo", "X", "Anime", "drama", source_kind="")
