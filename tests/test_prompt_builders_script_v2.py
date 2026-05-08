"""验证 prompt_rules v2 注入是否正确接入两个 builder。"""

import pytest

from lib.prompt_builders_script import build_drama_prompt, build_narration_prompt
from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
)
from lib.prompt_rules.visual_dynamic import (
    IMAGE_DYNAMIC_PATCH,
    VIDEO_DYNAMIC_PATCH,
)


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


def test_drama_v2_on_injects_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert DRAMA_PACING_RULES in text
    assert IMAGE_DYNAMIC_PATCH in text
    assert VIDEO_DYNAMIC_PATCH in text


def test_drama_v2_off_omits_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert DRAMA_PACING_RULES not in text
    assert IMAGE_DYNAMIC_PATCH not in text
    assert VIDEO_DYNAMIC_PATCH not in text


def test_narration_v2_on_injects_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_narration_prompt(segments_md="| G01 | xxx | 25 | 4s | 否 | - |", **_kwargs())
    assert NARRATION_PACING_RULES in text
    assert IMAGE_DYNAMIC_PATCH in text
    assert VIDEO_DYNAMIC_PATCH in text


def test_narration_v2_off_omits_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    text = build_narration_prompt(segments_md="| G01 | xxx | 25 | 4s | 否 | - |", **_kwargs())
    assert NARRATION_PACING_RULES not in text
    assert IMAGE_DYNAMIC_PATCH not in text
    assert VIDEO_DYNAMIC_PATCH not in text


def test_drama_v2_on_keeps_camera_motion_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §4.6: 保留'每个片段仅选择一种镜头运动'约束不动。"""
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert "每个片段仅选择一种镜头运动" in text


def test_drama_v2_on_length_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """新版 prompt 不应膨胀超过旧版 + 3000 字符。"""
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    old = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    new = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert len(new) - len(old) < 3000
