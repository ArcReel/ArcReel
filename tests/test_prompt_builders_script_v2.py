"""验证节奏 section 的 v2 灰度开关接入两个 builder。

visual_dynamic / asset_anti_break / asset_layout 三个 rule 模块在本次重构中已删除，
相关断言移除；只保留 episode_pacing 的灰度行为。
"""

import pytest

from lib.prompt_builders_script import build_drama_prompt, build_narration_prompt
from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
)


def _normalize(text: str) -> str:
    """去除全部空白字符，用于跨缩进比较。"""
    return "".join(text.split())


def _kwargs() -> dict:
    return dict(
        project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
        style="动漫",
        style_description="日漫半厚涂",
        characters={"主角": {"description": "X"}},
        scenes={"庙宇": {"description": "Y"}},
        props={"玉佩": {"description": "Z"}},
        supported_durations=[4, 5, 6, 7, 8],
        default_duration=4,
    )


def test_drama_v2_on_injects_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert _normalize(DRAMA_PACING_RULES) in _normalize(text)


def test_drama_v2_off_omits_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert _normalize(DRAMA_PACING_RULES) not in _normalize(text)


def test_narration_v2_on_injects_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_narration_prompt(segments_md="| G01 | xxx | 25 | 4s | 否 | - |", **_kwargs())
    assert _normalize(NARRATION_PACING_RULES) in _normalize(text)


def test_narration_v2_off_omits_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    text = build_narration_prompt(segments_md="| G01 | xxx | 25 | 4s | 否 | - |", **_kwargs())
    assert _normalize(NARRATION_PACING_RULES) not in _normalize(text)


def test_drama_no_enum_dump_in_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """schema 已声明的枚举不再在 prompt 中重复列举（节省 token + 防漂移）。"""
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    # 枚举值不再以"全枚举列表"形式出现
    assert "Tracking Shot" not in text
    assert "Pan Left, Pan Right" not in text


def test_drama_no_hard_char_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 无法精确数字数，硬性"≤200 字"约束已移除。"""
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert "200 字以内" not in text
    assert "150 字以内" not in text
