"""path_safety：项目目录内路径包含校验，防穿越 + 脏数据容错。"""

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from lib.infra.path_safety import (
    PathTraversalError,
    safe_exists,
    safe_join,
    try_safe_join,
)


def test_existing_relative_path(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert safe_exists(tmp_path, "a.txt") is True


def test_missing_file_returns_false(tmp_path: Path):
    assert safe_exists(tmp_path, "nope.txt") is False


def test_directory_returns_false(tmp_path: Path):
    (tmp_path / "subdir").mkdir()
    assert safe_exists(tmp_path, "subdir") is False


def test_traversal_rejected(tmp_path: Path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assert safe_exists(tmp_path, "../outside.txt") is False


def test_empty_rel_path_returns_false(tmp_path: Path):
    assert safe_exists(tmp_path, "") is False


def test_dirty_type_returns_false(tmp_path: Path):
    assert safe_exists(tmp_path, cast(Any, {"oops": 1})) is False
    assert safe_exists(tmp_path, cast(Any, 42)) is False


def test_safe_join_returns_absolute_within_base(tmp_path: Path):
    result = safe_join(tmp_path, "sub", "a.txt")
    assert result == Path(os.path.realpath(tmp_path)) / "sub" / "a.txt"


def test_safe_join_rejects_dotdot(tmp_path: Path):
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "../outside.txt")


def test_safe_join_rejects_absolute_escape(tmp_path: Path):
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "/etc/passwd")


def test_safe_join_absolute_within_base_allowed(tmp_path: Path):
    inside = Path(os.path.realpath(tmp_path)) / "inside.txt"
    assert safe_join(tmp_path, str(inside)) == inside


def test_safe_join_rejects_malformed_path(tmp_path: Path):
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "bad\x00name.png")


def test_safe_join_base_itself_rejected_by_default(tmp_path: Path):
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "")


def test_safe_join_base_itself_allowed_with_flag(tmp_path: Path):
    assert safe_join(tmp_path, "", allow_base=True) == Path(os.path.realpath(tmp_path))


def test_safe_join_root_base_itself_rejected_by_default():
    root = Path(os.path.abspath(os.sep))
    with pytest.raises(PathTraversalError):
        safe_join(root, "")


def test_safe_join_must_exist(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        safe_join(tmp_path, "nope.txt", must_exist=True)
    (tmp_path / "yes.txt").write_text("x", encoding="utf-8")
    assert safe_join(tmp_path, "yes.txt", must_exist=True).name == "yes.txt"


def test_safe_join_require_file_rejects_dir(tmp_path: Path):
    (tmp_path / "d").mkdir()
    with pytest.raises(FileNotFoundError):
        safe_join(tmp_path, "d", require_file=True)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows")
def test_safe_join_rejects_symlink_escape(tmp_path: Path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = base / "link.txt"
    os.symlink(outside, link)
    with pytest.raises(PathTraversalError):
        safe_join(base, "link.txt")


def test_try_safe_join_returns_none_on_escape(tmp_path: Path):
    assert try_safe_join(tmp_path, "../x") is None
    assert try_safe_join(tmp_path, cast(Any, 42)) is None
    assert try_safe_join(tmp_path, "ok.txt") == Path(os.path.realpath(tmp_path)) / "ok.txt"
