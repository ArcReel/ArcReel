"""
Unified image generation prompt builder functions.

All prompt templates are managed centrally in this file, ensuring the WebUI and Skills
use the same logic.

Module responsibilities:
- Character design image prompt building
- Clue design image prompt building (prop / location)
- Storyboard image prompt suffix

Consumers:
- webui/server/routers/generate.py
- .claude/skills/generate-characters/scripts/generate_character.py
- .claude/skills/generate-clues/scripts/generate_clue.py
"""


def build_character_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """
    Build a character design image prompt.

    Follows nano-banana best practices: use narrative paragraph descriptions
    rather than keyword lists.

    Args:
        name: Character name
        description: Character appearance description (should be a narrative paragraph)
        style: Project style
        style_description: AI-analysed style description

    Returns:
        Complete prompt string
    """
    style_part = f", {style}" if style else ""

    # Build style prefix
    style_prefix = ""
    if style_description:
        style_prefix = f"Visual style: {style_description}\n\n"

    return f"""{style_prefix}Character design reference image{style_part}.

Full-body illustration of "{name}".

{description}

Composition: single character full-body shot, natural pose, facing the camera.
Background: clean light grey, no decorative elements.
Lighting: soft, even studio lighting, no harsh shadows.
Quality: high resolution, sharp details, accurate colours."""


def build_clue_prompt(
    name: str, description: str, clue_type: str = "prop", style: str = "", style_description: str = ""
) -> str:
    """
    Build a clue design image prompt.

    Selects the appropriate template based on the clue type.

    Args:
        name: Clue name
        description: Clue description
        clue_type: Clue type ("prop" or "location")
        style: Project style
        style_description: AI-analysed style description

    Returns:
        Complete prompt string
    """
    if clue_type == "location":
        return build_location_prompt(name, description, style, style_description)
    else:
        return build_prop_prompt(name, description, style, style_description)


def build_prop_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """
    Build a prop-type clue prompt.

    Uses a three-view composition: front full view, 45-degree side view, and detail close-up.

    Args:
        name: Prop name
        description: Prop description
        style: Project style
        style_description: AI-analysed style description

    Returns:
        Complete prompt string
    """
    style_suffix = f", {style}" if style else ""

    # Build style prefix
    style_prefix = ""
    if style_description:
        style_prefix = f"Visual style: {style_description}\n\n"

    return f"""{style_prefix}A professional prop design reference image{style_suffix}.

Multi-angle showcase of the prop "{name}". {description}

Three views arranged horizontally on a clean light grey background: front full view on the left, 45-degree side view in the centre to show depth, key detail close-up on the right. Soft, even studio lighting, high-definition quality, accurate colours."""


def build_location_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """
    Build a location-type clue prompt.

    Uses a 3/4 main panel + bottom-right detail close-up composition.

    Args:
        name: Location name
        description: Location description
        style: Project style
        style_description: AI-analysed style description

    Returns:
        Complete prompt string
    """
    style_suffix = f", {style}" if style else ""

    # Build style prefix
    style_prefix = ""
    if style_description:
        style_prefix = f"Visual style: {style_description}\n\n"

    return f"""{style_prefix}A professional location design reference image{style_suffix}.

Visual reference for the iconic location "{name}". {description}

The main panel covers three-quarters of the image and shows the overall appearance and atmosphere of the environment; the small inset in the bottom right is a detail close-up. Soft, natural lighting."""


def build_storyboard_suffix(content_mode: str = "narration", *, aspect_ratio: str | None = None) -> str:
    """
    Get the storyboard image prompt suffix.

    Prefers the aspect_ratio parameter; falls back to inferring from content_mode for backward compatibility.
    """
    if aspect_ratio is None:
        ratio = "9:16" if content_mode == "narration" else "16:9"
    else:
        ratio = aspect_ratio
    if ratio == "9:16":
        return "Portrait composition."
    elif ratio == "16:9":
        return "Landscape composition."
    return ""


def build_style_prompt(project_data: dict) -> str:
    """
    Build a style description prompt fragment.

    Merges style (manually entered by the user) and style_description (AI-generated analysis).

    Args:
        project_data: project.json data

    Returns:
        Style description string for appending to a generation prompt
    """
    parts = []

    # Base style tag
    style = project_data.get("style", "")
    if style:
        parts.append(f"Style: {style}")

    # AI-analysed style description
    style_description = project_data.get("style_description", "")
    if style_description:
        parts.append(f"Visual style: {style_description}")

    return "\n".join(parts)
