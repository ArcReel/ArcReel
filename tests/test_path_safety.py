"""safe_exists：项目目录内相对路径的存在性检查，防穿越 + 脏数据容错。"""

from pathlib import Path
from typing import Any, cast

from lib.path_safety import safe_exists


def test_existing_relative_path(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert safe_exists(tmp_path, "a.txt") is True


def test_missing_file_returns_false(tmp_path: Path):
    assert safe_exists(tmp_path, "nope.txt") is False


def test_traversal_rejected(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assert safe_exists(tmp_path, "../outside.txt") is False


def test_empty_rel_path_returns_false(tmp_path: Path):
    assert safe_exists(tmp_path, "") is False


def test_dirty_type_returns_false(tmp_path: Path):
    # rel_path 来自 project.json 原始字段，可能是任意 JSON 类型；脏数据按「不存在」处理
    assert safe_exists(tmp_path, cast(Any, {"oops": 1})) is False
    assert safe_exists(tmp_path, cast(Any, 42)) is False
