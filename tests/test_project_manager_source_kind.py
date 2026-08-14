"""项目创建写入源文件性质（source_file_type）：持久化、缺省 novel、非法值拒绝。

只断言外部行为：调用 create_project_metadata 后读 project.json 形状。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager, require_source_file_type

pytestmark = pytest.mark.unit


def _pm(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path / "projects")


class TestRequireSourceFileType:
    """统一入口：合法值原样返回，缺失 / 非法 / 脏数据一律 fail-loud。

    消费方全是付费文本生成的 prompt 装配，静默落 novel 会让剧本项目按小说改编规则处理。
    """

    def test_valid_values_pass_through(self):
        assert require_source_file_type({"source_file_type": "novel"}) == "novel"
        assert require_source_file_type({"source_file_type": "screenplay"}) == "screenplay"

    @pytest.mark.parametrize(
        "value",
        [{}, {"source_file_type": None}, {"source_file_type": "screen_play"}, {"source_file_type": ""}],
    )
    def test_missing_or_invalid_raises(self, value):
        with pytest.raises(ValueError, match="source_file_type"):
            require_source_file_type(value)

    @pytest.mark.parametrize("dirty", [["novel"], {"k": "v"}, 123])
    def test_unhashable_dirty_value_raises_without_type_error(self, dirty):
        # list / dict 等不可哈希脏值不得在成员判断时抛 TypeError，须报成 ValueError
        with pytest.raises(ValueError, match="source_file_type"):
            require_source_file_type({"source_file_type": dirty})


class TestCreateSourceKind:
    def test_screenplay_persisted_to_project_json_top_level(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", creation_type="drama")
        project = pm.create_project_metadata("demo", "剧本项目", "Anime", "drama", source_file_type="screenplay")

        assert project["source_file_type"] == "screenplay"
        assert pm.load_project("demo")["source_file_type"] == "screenplay"

    def test_defaults_to_novel_when_omitted(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", creation_type="drama")
        project = pm.create_project_metadata("demo", "默认项目", "Anime", "drama")

        assert project["source_file_type"] == "novel"
        assert pm.load_project("demo")["source_file_type"] == "novel"

    def test_invalid_source_kind_rejected(self, tmp_path):
        pm = _pm(tmp_path)
        pm.create_project("demo", creation_type="drama")
        with pytest.raises(ValueError, match="source_file_type"):
            pm.create_project_metadata("demo", "X", "Anime", "drama", source_file_type="screen_play")

    def test_empty_string_source_kind_rejected(self, tmp_path):
        # 空字符串是非法值，不得被当作"未传入"而静默回退到 novel
        pm = _pm(tmp_path)
        pm.create_project("demo", creation_type="drama")
        with pytest.raises(ValueError, match="source_file_type"):
            pm.create_project_metadata("demo", "X", "Anime", "drama", source_file_type="")
