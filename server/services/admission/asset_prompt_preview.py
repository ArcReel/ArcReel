"""资产图草稿提示词预览：复用执行期渲染，不提交生成或保存描述。"""

from lib.prompts.prompt_builders import (
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
)
from server.services.admission.prompt_preview import UNAVAILABLE_INVALID, UNAVAILABLE_MISSING, RenderedPrompt
from server.services.tasks.derivative_sheet_tasks import build_derivative_sheet_instruction


def render_asset_prompt(
    asset_type: str,
    name: str,
    description: str,
    style: str = "",
    style_description: str = "",
) -> RenderedPrompt:
    """按资产执行口径渲染：衍生只编辑本体外观，画风由本体图承载。"""
    if not description.strip():
        return RenderedPrompt(unavailable=UNAVAILABLE_MISSING, is_text_form=True)
    try:
        if asset_type == "character_derivative":
            text = build_derivative_sheet_instruction(description.strip())
        else:
            builder = {
                "character": build_character_prompt,
                "scene": build_scene_prompt,
                "prop": build_prop_prompt,
                "product": build_product_prompt,
            }[asset_type]
            text = builder(name, description.strip(), style, style_description)
        return RenderedPrompt(text=text, is_text_form=True)
    except (ValueError, TypeError, KeyError):
        return RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=True)
