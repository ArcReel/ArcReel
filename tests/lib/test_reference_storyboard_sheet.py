from __future__ import annotations

import pytest

from server.services.reference_storyboard_sheet_tasks import (
    StoryboardSheetGateError,
    _sheet_aspect_ratio,
    build_storyboard_sheet_prompt,
    reference_storyboard_sheet_task_specs,
    require_keyframe_plan,
)

pytestmark = pytest.mark.unit


def test_sheet_outer_canvas_is_auto_laid_out_from_panel_ratio() -> None:
    assert _sheet_aspect_ratio("9:16", 6) == "27:32"
    assert _sheet_aspect_ratio("16:9", 6) == "8:3"


def test_sheet_prompt_preserves_panel_ratio_and_action_progression() -> None:
    prompt = build_storyboard_sheet_prompt(
        {"style": "田园动画", "style_description": "暖色自然光"},
        {
            "unit_id": "E1U01",
            "text": "妹妹追弟弟，弟弟绊倒摔进桂花堆。",
            "keyframes": [{"description": "妹妹开始追弟弟"}],
        },
        panel_ratio="9:16",
        panel_count=6,
        reference_roster="- Picture 1 = @[鳄鱼妹妹]",
    )

    assert "每个单独 panel 的画面比例必须是 9:16" in prompt
    assert "入口 panel 是妹妹开始追、弟弟开始逃" in prompt
    assert "Picture 1 = @[鳄鱼妹妹]" in prompt


def test_sheet_specs_keep_request_scoped_model_override() -> None:
    specs = reference_storyboard_sheet_task_specs(
        {"video_units": [{"unit_id": "E1U01", "text": "庭院开场"}]},
        "episode_1.json",
        image_override={"image_provider": "runware", "image_model": "openai:gpt-image@2"},
    )

    assert specs[0].task_type == "reference_storyboard_sheet"
    assert specs[0].payload["image_provider"] == "runware"
    assert specs[0].payload["image_model"] == "openai:gpt-image@2"


def test_sheet_confirmation_gate_rejects_an_empty_keyframe_plan() -> None:
    with pytest.raises(StoryboardSheetGateError) as exc_info:
        require_keyframe_plan({"unit_id": "E1U01", "keyframes": []})

    assert exc_info.value.code == "reference_keyframe_plan_required"
