"""Grid prompt builder for grid-image-to-video feature."""

from __future__ import annotations

from math import gcd


def _extract_image_desc(scene: dict) -> str:
    """Extract image description from a scene.

    If image_prompt is a dict, join scene + composition fields.
    If string, return as-is.
    """
    image_prompt = scene.get("image_prompt", "")
    if isinstance(image_prompt, dict):
        parts = []
        scene_text = image_prompt.get("scene", "")
        if scene_text:
            parts.append(scene_text)
        composition = image_prompt.get("composition", {})
        if isinstance(composition, dict):
            comp_parts = [f"{k}: {v}" for k, v in composition.items() if v]
            if comp_parts:
                parts.append("，".join(comp_parts))
        return "；".join(parts) if parts else ""
    return str(image_prompt)


def _extract_action(scene: dict) -> str:
    """Extract closing action from video_prompt.

    If dict, return action field. If string, return as-is.
    """
    video_prompt = scene.get("video_prompt", "")
    if isinstance(video_prompt, dict):
        return str(video_prompt.get("action", ""))
    return str(video_prompt)


def _compute_panel_aspect(grid_aspect_ratio: str, rows: int, cols: int) -> str:
    """从整体宫格比例推算单格比例。

    例：grid 4:3, 3行2列 → panel (4/2):(3/3) = 2:1
    """
    gw, gh = (int(x) for x in grid_aspect_ratio.split(":"))
    pw = gw * rows  # 交叉相乘避免浮点
    ph = gh * cols
    g = gcd(pw, ph)
    return f"{pw // g}:{ph // g}"


def build_grid_prompt(
    *,
    scenes: list[dict],
    id_field: str,
    rows: int,
    cols: int,
    style: str,
    aspect_ratio: str = "16:9",
    grid_aspect_ratio: str | None = None,
    reference_image_mapping: dict[str, str] | None = None,
) -> str:
    """Assemble a grid image generation prompt with first-last frame chain structure.

    Args:
        scenes: List of scene dicts with image_prompt and video_prompt fields.
        id_field: Key in each scene dict for the scene ID.
        rows: Number of rows in the grid.
        cols: Number of columns in the grid.
        style: Style description for the grid.
        aspect_ratio: Aspect ratio for each cell (default "16:9").
        reference_image_mapping: Optional mapping of image labels to character names.

    Returns:
        Assembled prompt string.
    """
    total = rows * cols
    n_scenes = len(scenes)

    # Number of content cells: first frame + (n_scenes - 1) transitions + last first frame
    # Cell 0: first scene opening
    # Cells 1..n_scenes-2: transitions between consecutive scenes
    # Cell n_scenes-1: last scene opening
    # Remaining cells: placeholders
    n_content = n_scenes  # 1 first + (n-2) transitions + 1 last = n

    effective_grid_ar = grid_aspect_ratio or aspect_ratio
    panel_ar = _compute_panel_aspect(effective_grid_ar, rows, cols)

    lines: list[str] = []

    # Header
    lines.append(f"你是一位专业的分镜画师。请严格按照 {rows}×{cols} 宫格布局生成一张包含恰好 {total} 个等大画格的联合图。")
    lines.append("")

    # Layout requirements
    lines.append("【布局要求】")
    lines.append(f"- 恰好 {rows} 行 {cols} 列，共 {total} 个画格，阅读顺序：从左到右，从上到下")
    lines.append(f"- 整体图片比例：{effective_grid_ar}")
    lines.append(f"- 每个画格比例：{panel_ar}，所有画格大小完全相同")
    lines.append("- 画格之间无边框、无间隙、无留白，紧密排列")
    lines.append("- 不得合并画格、不得遗漏画格、不得错位排列")
    lines.append("- 所有画格保持一致的角色外观、光线和色彩风格")
    lines.append("")

    # Frame chain rhythm
    lines.append("【帧链节奏】")
    lines.append("本宫格采用首尾帧链式结构：")
    lines.append("- 格0 是第一个场景的开场画面")
    lines.append(f"- 格1~格{n_content - 2} 是相邻场景的过渡帧（前一场景的结束 = 后一场景的开始）")
    lines.append(f"- 格{n_content - 1} 是最后一个场景的开场画面")
    lines.append("- 相邻格之间应体现画面的自然过渡和动作延续")
    lines.append("")

    # Reference images (optional)
    if reference_image_mapping:
        lines.append("【参考图说明】")
        for label, character in reference_image_mapping.items():
            lines.append(f"- {label}：{character}")
        lines.append("")

    # Cell contents
    lines.append("【各格内容】")

    for cell_idx in range(total):
        row_num = cell_idx // cols + 1
        col_num = cell_idx % cols + 1
        position = f"row{row_num} col{col_num}"

        if cell_idx == 0:
            # First scene opening
            scene = scenes[0]
            scene_id = scene.get(id_field, "")
            image_desc = _extract_image_desc(scene)
            lines.append(f"格{cell_idx}（{position}）— {scene_id}开场：")
            lines.append(f"  {image_desc}")

        elif cell_idx < n_scenes - 1:
            # Transition between scenes[cell_idx-1] and scenes[cell_idx]
            prev_scene = scenes[cell_idx - 1]
            next_scene = scenes[cell_idx]
            prev_scene_id = prev_scene.get(id_field, "")
            next_scene_id = next_scene.get(id_field, "")
            prev_action = _extract_action(prev_scene)
            next_image_desc = _extract_image_desc(next_scene)
            lines.append(f"格{cell_idx}（{position}）— {prev_scene_id}→{next_scene_id}过渡：")
            lines.append(f"  {prev_action}，过渡到 {next_image_desc}")

        elif cell_idx == n_scenes - 1:
            # Last scene opening
            scene = scenes[-1]
            scene_id = scene.get(id_field, "")
            image_desc = _extract_image_desc(scene)
            lines.append(f"格{cell_idx}（{position}）— {scene_id}开场：")
            lines.append(f"  {image_desc}")

        else:
            # Placeholder
            lines.append(f"格{cell_idx}（{position}）— 空占位：纯灰色背景，无任何内容")

    lines.append("")

    # Style requirements
    lines.append("【风格要求】")
    lines.append(style)
    lines.append("")

    # Negative constraints
    lines.append("【负面约束】")
    lines.append("禁止出现以下任何元素：")
    lines.append("- 文字、字幕、标签、标题、数字编号、时间戳")
    lines.append("- 水印、logo、签名")
    lines.append("- 白色边框、黑色边框、粗边框、装饰性边框")
    lines.append("- 分隔线、间隙、间距、留白、padding、margin")
    lines.append("- 白色背景、纯色背景条")
    lines.append("- 合并的画格、缺失的画格、错位的画格")
    lines.append("- 连续全景图（非分格）、单张大图")
    lines.append("- 模糊、低画质、噪点")
    lines.append("- 拼贴感、蒙太奇拼接感")
    lines.append("- 画格大小不一致、画格比例不一致")

    return "\n".join(lines)
