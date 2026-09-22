"""各生成入口对项目风格字段的归一化口径一致：首尾空白去掉，非字符串按空处理。"""

from collections.abc import Callable
from typing import Any

import pytest

from lib.prompts.prompt_builders import build_character_prompt, render_storyboard_image_prompt
from lib.script.grid.prompt_builder import build_grid_prompt
from lib.script.reference_video.prompt_render import render_unit_prompt
from lib.script.reference_video.voice_settings import VoiceRenderSettings


def _asset(style: Any, style_description: Any) -> str:
    return build_character_prompt("林清", "黑发，冷静神态。", style, style_description)


def _storyboard_text(style: Any, style_description: Any) -> str:
    return render_storyboard_image_prompt("林清坐在窗边", style=style, style_description=style_description)


def _storyboard_structured(style: Any, style_description: Any) -> str:
    return render_storyboard_image_prompt({"scene": "林清坐在窗边"}, style=style, style_description=style_description)


def _grid(style: Any, style_description: Any) -> str:
    return build_grid_prompt(
        scenes=[{"scene_id": "S1", "image_prompt": "林清坐在窗边", "video_prompt": "起身"}],
        id_field="scene_id",
        rows=2,
        cols=2,
        style=style,
        style_description=style_description,
    )


def _reference_video(style: Any, style_description: Any) -> str:
    project = {"style": style, "style_description": style_description}
    return render_unit_prompt("林清走进酒馆", project, [], VoiceRenderSettings()).prompt


_ENTRIES: dict[str, Callable[[Any, Any], str]] = {
    "asset": _asset,
    "storyboard-text": _storyboard_text,
    "storyboard-structured": _storyboard_structured,
    "grid": _grid,
    "reference-video": _reference_video,
}


@pytest.mark.parametrize("render", _ENTRIES.values(), ids=_ENTRIES.keys())
@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ((" 水墨 ", "  留白写意\n"), ("水墨", "留白写意")),
        (("  ", "\t\n"), ("", "")),
        ((123, None), ("", "")),
        ((None, ["留白写意"]), ("", "")),
    ],
    ids=["padded", "blank", "non-string", "null-and-list"],
)
def test_style_values_render_like_their_normalized_form(render, raw, normalized):
    rendered = render(*raw)
    assert rendered == render(*normalized)
    for line in rendered.split("\n"):
        if line.startswith(("Style:", "Visual style:")):
            assert line.split(":", 1)[1].strip()
            assert line == line.rstrip()
