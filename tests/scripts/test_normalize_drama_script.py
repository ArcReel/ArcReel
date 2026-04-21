"""`normalize_drama_script.py` 的 prompt builder 纯函数测试。

验证锚点已彻底参数化：
- 不再出现 "4、6 或 8 秒" / "默认 8 秒（简单画面可用 4 或 6 秒）" 老文本。
- 传入不同 (default_duration, supported_durations) 产出对应指令。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO_ROOT
    / "agent_runtime_profile"
    / ".claude"
    / "skills"
    / "generate-script"
    / "scripts"
    / "normalize_drama_script.py"
)


@pytest.fixture(scope="module")
def module():
    """动态加载脚本，只取 `build_normalize_prompt`，不触发 amain。"""
    spec = importlib.util.spec_from_file_location("normalize_drama_script_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["normalize_drama_script_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_OVERVIEW = {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"}


def _build(module, default_duration, supported_durations):
    return module.build_normalize_prompt(
        novel_text="novel",
        project_overview=_OVERVIEW,
        style="国漫",
        characters={"A": {}},
        scenes={"酒馆": {}},
        props={},
        default_duration=default_duration,
        supported_durations=supported_durations,
    )


def test_prompt_uses_placeholder_in_example_table(module):
    """example 表的"时长"列必须是 `<duration>` 占位，不能硬编码具体秒数。"""
    prompt = _build(module, default_duration=8, supported_durations=[4, 6, 8])
    assert "<duration>" in prompt
    # 老锚点彻底消失
    assert "4、6 或 8 秒" not in prompt
    assert "默认 8 秒（简单画面可用 4 或 6 秒）" not in prompt


def test_prompt_with_grok_wide_durations_injects_default_and_max(module):
    """Grok (default=8, supported=1..15) → prompt 明示默认 8、上限 15。"""
    prompt = _build(module, default_duration=8, supported_durations=list(range(1, 16)))
    assert "每场景默认 8 秒" in prompt
    assert "上限 15 秒" in prompt
    # 允许列表里所有值都应出现
    assert "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15" in prompt


def test_prompt_with_veo_narrow_durations(module):
    """Veo (default=8, supported=[4,6,8]) → prompt 含 "每场景默认 8 秒" 和允许列表 4, 6, 8。"""
    prompt = _build(module, default_duration=8, supported_durations=[4, 6, 8])
    assert "每场景默认 8 秒" in prompt
    assert "4, 6, 8" in prompt
    assert "上限 8 秒" in prompt


def test_prompt_without_default_duration_skips_default_hint(module):
    """default_duration=None → prompt 不含"每场景默认"强锚点，只给允许列表 + 上限。"""
    prompt = _build(module, default_duration=None, supported_durations=[4, 6, 8, 10, 12])
    assert "每场景默认" not in prompt
    assert "4, 6, 8, 10, 12" in prompt
    assert "最长 12 秒" in prompt


def test_prompt_with_empty_supported_durations_degrades_gracefully(module):
    """边界：supported_durations 为空时 prompt 不炸，允许列表显示占位符。"""
    prompt = _build(module, default_duration=None, supported_durations=[])
    # 不应有明确数值锚点
    assert "4、6 或 8 秒" not in prompt
    assert "默认 8 秒" not in prompt
