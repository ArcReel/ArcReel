"""图像 / 视频 / 资产 prompt 的统一真相源。

WebUI（server/services/generation_tasks.py）和 Skill（agent_runtime_profile/.claude/skills/generate-assets）
都从这里取最终 prompt 文本，确保入口一致、不漂移。

资产图的整段提示词由内置模版渲染；分镜与视频的投影、编号和包装由本模块提供。
反向提示词写在正文末尾的 Avoid 键，不使用 backend 的 negative_prompt 参数通道。
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.prompt_templates.builtin import builtin_templates
from lib.prompt_utils import (
    AVOID_KEY,
    STORYBOARD_AVOID_ITEMS,
    VIDEO_AVOID_ITEMS,
    image_prompt_to_yaml,
    project_storyboard_image_prompt,
    yaml_section,
)
from lib.reference_image_numbering import (
    REFERENCE_IMAGES_KEY,
    ReferenceImageSlot,
    reference_images_declaration,
    render_reference_mentions,
)
from lib.schema_guards import is_str

# 商品保真核心句由资产图模版与参考视频渲染入口共用，正文只存在于共享片段这一份；
# 「参考图中的出镜人物一律不保留」只对资产图成立，留在 product 守卫变体里，不并进这一句。
_, _ASSET_SHEET_PARTIALS = builtin_templates.read_source("asset/sheet")
PRODUCT_FIDELITY_CORE = _ASSET_SHEET_PARTIALS["shared/product_fidelity"].rstrip("\n")

_NEGATIVE_TAIL_STORYBOARD = yaml_section({AVOID_KEY: STORYBOARD_AVOID_ITEMS})
_NEGATIVE_TAIL_VIDEO = yaml_section({AVOID_KEY: VIDEO_AVOID_ITEMS})


def _asset_prompt(asset_type: str, name: str, description: str, style: str = "", style_description: str = "") -> str:
    return builtin_templates.render(
        "asset/sheet",
        asset_type=asset_type,
        name=name,
        description=description,
        style=style,
        style_description=style_description,
    )


def build_character_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """角色资产图（三视图 16:9）。"""
    return _asset_prompt("character", name, description, style, style_description)


def build_character_derivative_prompt(description: str) -> str:
    """只改描述到的外观，其余版式与外观保留本体资产图。"""
    return _asset_prompt("character_derivative", "", description)


def build_scene_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """无人场景资产图。"""
    return _asset_prompt("scene", name, description, style, style_description)


def build_prop_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """道具资产图。"""
    return _asset_prompt("prop", name, description, style, style_description)


def build_product_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """忠实于实拍参考的商品资产图，风格变体为空。"""
    return _asset_prompt("product", name, description, style, style_description)


# ---------------------------------------------------------------------------
# 分镜 / 视频 prompt 末尾增强
# ---------------------------------------------------------------------------


def render_storyboard_image_prompt(
    image_prompt: object,
    *,
    style: str = "",
    style_description: str = "",
    references: Sequence[ReferenceImageSlot] = (),
) -> str:
    """分镜图最终提示词文本的唯一出口。

    执行路径、Skill 入队校验与预览接口共用本函数：结构形态经项目风格投影为 YAML、文本形态原样
    作提示词主体，两者同样注入项目风格、参考图类型声明与 ``Avoid`` 反向约束。``references``
    是实际随请求发出的参考图列表（编排层最终装配序），其位置即「图N」编号：类型声明行
    ``Reference_Images`` 插在 ``Style`` 与 ``Scene`` 之间，正文的 ``@[名称]`` 换成对应编号、对不上
    的渲染为裸名；没有参考图就没有声明行。商品参考图的保真要求并入声明行。
    """

    if not is_str(style_description):
        raise TypeError("style_description must be a string")
    projected, normalized_style = project_storyboard_image_prompt(image_prompt, style)
    declaration = reference_images_declaration(references)

    style_parts: list[str] = []
    if isinstance(projected, dict):
        projected["scene"] = render_reference_mentions(projected["scene"], references)
        rendered = image_prompt_to_yaml(projected, normalized_style, reference_images=declaration).rstrip()
    else:
        rendered = render_reference_mentions(projected, references)
        if normalized_style:
            style_parts.append(f"Style: {normalized_style}")
    normalized_description = style_description.strip()
    if normalized_description:
        style_parts.append(f"Visual style: {normalized_description}")
    if isinstance(projected, str):
        if declaration:
            style_parts.append(yaml_section({REFERENCE_IMAGES_KEY: declaration}))
        # 文本形态才按内容判重：它以「当前渲染结果」为初值，正文本身就带着这些声明，按前缀相等
        # 判定会漏判而叠出第二份。结构形态的正文是本函数刚渲染出的 YAML，一律注入。
        style_parts = [part for part in style_parts if part not in rendered]
    if style_parts:
        rendered = "\n".join(style_parts) + "\n\n" + rendered
    return append_image_negative_tail(rendered)


def append_image_negative_tail(prompt: str) -> str:
    """给分镜图生成 prompt 追加统一的图像反向提示词。

    资产图在各 build_*_prompt 内已拼接各自的反向提示词；分镜图 prompt 由 LLM 产出、
    经归一化后交 image backend，在归一化出口过一遍此函数，保持各图像路径一致。
    """
    if not prompt or not prompt.strip():
        return _NEGATIVE_TAIL_STORYBOARD
    if _NEGATIVE_TAIL_STORYBOARD in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{_NEGATIVE_TAIL_STORYBOARD}"


def append_video_negative_tail(prompt: str) -> str:
    """给视频生成 prompt 追加统一的反向提示词。

    调用方拿到分镜 video_prompt 文本后，在交给 video backend 之前过一遍此函数；
    避免在每个 caller 各自拼接、导致漂移。
    """
    if not prompt or not prompt.strip():
        return _NEGATIVE_TAIL_VIDEO
    if _NEGATIVE_TAIL_VIDEO in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{_NEGATIVE_TAIL_VIDEO}"
