"""Tests for validate_script_filename (split from test_sdk_tools.py)."""

from __future__ import annotations

import pytest

from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.enqueue_storyboards import generate_storyboards_tool
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _call,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")

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
    from server.agent_runtime.sdk_tools._context import validate_script_filename

    with pytest.raises(ValueError):
        validate_script_filename(bad)


def test_validate_script_filename_accepts_basename() -> None:
    from server.agent_runtime.sdk_tools._context import validate_script_filename

    assert validate_script_filename("episode_1.json") == "episode_1.json"


async def test_generate_storyboards_rejects_path_in_script_arg(fake_ctx: ToolContext) -> None:
    """Agent 传带路径分隔符的 script 名必须被 handler 拒绝（共享 validate_script_filename 防御）。"""
    tool_obj = generate_storyboards_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "../etc/passwd"})
    assert out.get("is_error") is True
    assert "路径分隔符" in out["content"][0]["text"]
