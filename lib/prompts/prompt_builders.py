"""Single source of truth for image / video / asset prompts.

Both WebUI (server/services/tasks/generation_tasks.py) and the Skill
(agent_runtime_profile/.claude/skills/generate-assets) obtain final prompt text here,
keeping entry points consistent without drift.

Built-in templates render system wrappers for asset and storyboard images. This module
provides the storyboard rendering entry point, assembling projections and 图N numbering
as slot values. Negative constraints use the trailing Avoid key in the body, never the
backend negative_prompt parameter channel.
"""

from __future__ import annotations

from collections.abc import Sequence

from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.prompts.prompt_utils import (
    image_prompt_to_yaml,
    project_storyboard_image_prompt,
    yaml_section,
)
from lib.prompts.reference_image_numbering import (
    REFERENCE_IMAGES_KEY,
    ReferenceImageSlot,
    reference_images_declaration,
    render_reference_mentions,
)

# Persisted plain-text prompts can contain this former built-in declaration.
_LEGACY_IMAGE_AVOID_LINE = "Avoid: 水印、多余文字、Logo"


def _asset_prompt(asset_type: str, name: str, description: str, style: str = "", style_description: str = "") -> str:
    return builtin_templates.render(
        "asset/sheet",
        asset_type=asset_type,
        name=name,
        description=description,
        style=normalize_style_value(style),
        style_description=normalize_style_value(style_description),
    )


def build_character_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """Character asset image (three views, 16:9)."""
    return _asset_prompt("character", name, description, style, style_description)


def build_character_derivative_prompt(description: str) -> str:
    """Change only the described appearance; preserve the base asset image layout and all other appearance."""
    return _asset_prompt("character_derivative", "", description)


def build_scene_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """Unoccupied scene asset image."""
    return _asset_prompt("scene", name, description, style, style_description)


def build_prop_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """Prop asset image."""
    return _asset_prompt("prop", name, description, style, style_description)


def build_product_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """Product asset image faithful to photographic references, with an empty style variant."""
    return _asset_prompt("product", name, description, style, style_description)


# ---------------------------------------------------------------------------
# Storyboard image prompt rendering entry point
# ---------------------------------------------------------------------------


def render_storyboard_image_prompt(
    image_prompt: object,
    *,
    style: str = "",
    style_description: str = "",
    references: Sequence[ReferenceImageSlot] = (),
) -> str:
    """Sole entry point for final storyboard image prompt text.

    Shared by execution, Skill enqueue validation and preview endpoints: project structured
    prompts through the project style into YAML, and use text prompts unchanged as the body.
    Both inject project style, reference-image type declarations and ``Avoid`` constraints.
    ``references`` is the image list actually sent with the request, in final orchestration
    order; positions supply 图N numbers. Insert the ``Reference_Images`` declaration between
    the style block and ``Scene``. Replace body ``@[名称]`` mentions with their numbers, or bare
    names when unmatched. Omit the declaration when no references exist. Include product
    reference fidelity requirements in the declaration.
    """

    projected, projected_style = project_storyboard_image_prompt(image_prompt, style)
    normalized_style = normalize_style_value(projected_style)
    normalized_description = normalize_style_value(style_description)
    declaration = reference_images_declaration(references)

    if isinstance(projected, dict):
        projected["scene"] = render_reference_mentions(projected["scene"], references)
        return image_prompt_to_yaml(
            projected,
            normalized_style,
            reference_images=declaration,
            style_description=normalized_description,
        ).rstrip()
    text_body = render_reference_mentions(projected, references)
    if _LEGACY_IMAGE_AVOID_LINE in text_body.split("\n"):
        current_declaration = builtin_templates.render(
            "storyboard/image",
            style="",
            style_description="",
            reference_images="",
            structured_body="",
            text_body="",
        )
        text_body = "\n".join(
            current_declaration if line == _LEGACY_IMAGE_AVOID_LINE else line for line in text_body.split("\n")
        )
    return builtin_templates.render(
        "storyboard/image",
        style=normalized_style,
        style_description=normalized_description,
        reference_images=yaml_section({REFERENCE_IMAGES_KEY: declaration}) if declaration else "",
        structured_body="",
        text_body=text_body,
    )
