"""Tests for validate_script_filename."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# validate_script_filename — shared guard for all enqueue tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "scripts/episode_1.json",  # 任何分隔符都拒（包括 scripts/ 前缀）
        "../etc/passwd",
        "sub/dir/file.json",
        "a\\b.json",
        ".",
        "..",
    ],
)
def test_validate_script_filename_rejects_paths(bad: str) -> None:
    from server.media_tools.context import validate_script_filename

    with pytest.raises(ValueError, match=r"script (文件名不能为空|必须是纯文件名，禁止路径分隔符)"):
        validate_script_filename(bad)


@pytest.mark.parametrize("bad", [1, None, ["episode_1.json"], {"name": "episode_1.json"}])
def test_validate_script_filename_rejects_non_string_agent_args(bad: object) -> None:
    """Agent 传来的是原始 JSON 值，非字符串同样要落成 ValueError。"""

    from server.media_tools.context import validate_script_filename

    with pytest.raises(ValueError, match="script 文件名不能为空"):
        validate_script_filename(bad)


def test_validate_script_filename_accepts_basename() -> None:
    from server.media_tools.context import validate_script_filename

    assert validate_script_filename("episode_1.json") == "episode_1.json"
