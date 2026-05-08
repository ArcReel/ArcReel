"""验证 generate_asset 的 description 包装行为。

测试只覆盖纯函数 _wrap_prompt，避开 ProjectManager / queue 副作用。
"""

import importlib
import sys
from pathlib import Path

import pytest

# 把 skill 脚本目录加入 sys.path（脚本不是 lib 包，需要这样导）
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "agent_runtime_profile/.claude/skills/generate-assets/scripts"
sys.path.insert(0, str(SCRIPT_DIR))

generate_asset = importlib.import_module("generate_asset")


def test_wrap_v2_on_appends_layout_and_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    prompt, neg = generate_asset._wrap_prompt("character", "二十岁青年，杏眼柳眉")
    assert "二十岁青年" in prompt
    assert "正面" in prompt  # layout
    assert "五指" in prompt  # positive 防崩
    assert neg is not None
    assert "畸形" in neg


def test_wrap_v2_off_returns_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    prompt, neg = generate_asset._wrap_prompt("character", "原始描述")
    assert prompt == "原始描述"
    assert neg is None


def test_wrap_each_type_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    char_p, _ = generate_asset._wrap_prompt("character", "X")
    scene_p, _ = generate_asset._wrap_prompt("scene", "X")
    prop_p, _ = generate_asset._wrap_prompt("prop", "X")
    # 每种 type 的 layout + positive 都不同
    assert char_p != scene_p != prop_p != char_p
