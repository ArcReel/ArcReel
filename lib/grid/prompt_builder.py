"""Grid prompt builder for grid-image-to-video feature.

整段提示词由 ``storyboard/grid`` 模版渲染，本模块产出各格槽位值。
参考图与分镜图同一口径：prompt 首行为 ``Reference_Images`` 类型声明，各格正文里的 ``@[登记名]``
按最终参考图列表的序位换成「图N」（见 :mod:`lib.reference_image_numbering`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import gcd

from lib.prompt_style import normalize_style_value
from lib.prompt_templates.builtin import builtin_templates
from lib.reference_image_numbering import (
    ReferenceImageSlot,
    reference_images_declaration,
    render_reference_mentions,
)


def pending_grid_prompt_ids(scenes: Sequence[Mapping[str, object]], id_field: str) -> list[str]:
    """一张联合图里提示词待生成（``None``）或为空的格子 id，按剧本顺序。

    联合图的提示词由每格的 ``image_prompt`` 拼成，任一格缺失整张图都出不了；REST 路由与
    Agent 工具在入队计费前共用这一判定。
    """

    return [str(scene.get(id_field)) for scene in scenes if not scene.get("image_prompt")]


def project_grid_image_prompt(image_prompt: object) -> str | dict[str, object]:
    """Project grid image semantics into the canonical provider/basis shape.

    ``None`` 是机械转换后的待生成态，没有可渲染、可取证的内容，与
    :func:`lib.prompt_utils.project_storyboard_image_prompt` 同样拒绝，不能变成字面量 ``"None"``。
    """

    if image_prompt is None:
        raise ValueError("grid image_prompt is pending; the cell has no prompt to render")
    if not isinstance(image_prompt, Mapping):
        return str(image_prompt)
    scene = image_prompt.get("scene")
    if scene is None:
        scene = ""
    if not isinstance(scene, str):
        raise ValueError("grid image_prompt.scene must be a string")
    raw_composition = image_prompt.get("composition")
    if raw_composition is None:
        raw_composition = {}
    if not isinstance(raw_composition, Mapping):
        raise ValueError("grid image_prompt.composition must be an object")
    if any(not isinstance(key, str) for key in raw_composition):
        raise ValueError("grid image_prompt.composition keys must be strings")
    composition: dict[str, str] = {}
    for key in sorted(raw_composition):
        raw_value = raw_composition[key]
        if raw_value is None:
            continue
        value = str(raw_value)
        if value:
            composition[key] = value
    return {"scene": scene, "composition": composition}


def _extract_image_desc(scene: dict, references: Sequence[ReferenceImageSlot] = ()) -> str:
    """Extract image description from a scene.

    If image_prompt is a dict, join scene + composition fields.
    If string, return as-is. ``@[登记名]`` in the scene text is rendered against *references*.
    """
    image_prompt = project_grid_image_prompt(scene.get("image_prompt", ""))
    if isinstance(image_prompt, str):
        return render_reference_mentions(image_prompt, references)
    parts: list[str] = []
    scene_text = image_prompt["scene"]
    if scene_text:
        parts.append(render_reference_mentions(str(scene_text), references))
    composition = image_prompt["composition"]
    if isinstance(composition, Mapping):
        comp_parts = [f"{key}: {value}" for key, value in composition.items()]
        if comp_parts:
            parts.append("，".join(comp_parts))
    return "；".join(parts) if parts else ""


def _extract_action(scene: dict) -> str:
    """Extract closing action from video_prompt.

    If dict, return action field. If string, return as-is.
    """
    video_prompt = scene.get("video_prompt")
    if video_prompt is None:
        return ""
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
    style_description: str,
    aspect_ratio: str = "16:9",
    grid_aspect_ratio: str | None = None,
    references: Sequence[ReferenceImageSlot] = (),
) -> str:
    """Render the grid image prompt with first-last frame chain structure.

    Args:
        scenes: List of scene dicts with image_prompt and video_prompt fields.
        id_field: Key in each scene dict for the scene ID.
        rows: Number of rows in the grid.
        cols: Number of columns in the grid.
        style: Project style.
        style_description: Project style description.
        aspect_ratio: Aspect ratio for each cell (default "16:9").
        references: The reference images sent with the request, in array order; they are
            declared as 图N on the first line and addressed as such in the cell texts.

    Returns:
        The prompt rendered from the ``storyboard/grid`` template.
    """
    total = rows * cols
    n_scenes = len(scenes)
    if n_scenes > total:
        # 超员场景在成图中没有对应画格，切格回填会按位置错配——调用方应先按
        # max_cell_count 切块（见 lib.grid.layout.plan_grid_chunks），此处 fail loud。
        raise ValueError(f"分镜数 {n_scenes} 超过 {rows}×{cols} 宫格的画格数 {total}，分组应先切块再构建 prompt")

    # 格0 是首个分镜的开场，格1..n-1 是相邻分镜的过渡，其余格为占位。
    effective_grid_ar = grid_aspect_ratio or aspect_ratio
    first = scenes[0]
    transitions = [
        {
            **_cell_position(idx, cols),
            "from_id": str(scenes[idx - 1].get(id_field, "")),
            "to_id": str(scenes[idx].get(id_field, "")),
            "action": _extract_action(scenes[idx - 1]),
            "description": _extract_image_desc(scenes[idx], references),
        }
        for idx in range(1, n_scenes)
    ]
    placeholders = [_cell_position(idx, cols) for idx in range(n_scenes, total)]
    return builtin_templates.render(
        "storyboard/grid",
        reference_images=reference_images_declaration(references) or None,
        rows=rows,
        cols=cols,
        cell_count=total,
        grid_aspect_ratio=effective_grid_ar,
        panel_aspect_ratio=_compute_panel_aspect(effective_grid_ar, rows, cols),
        last_chain_cell=n_scenes - 1,
        opening={
            "scene_id": str(first.get(id_field, "")),
            "description": _extract_image_desc(first, references),
        },
        transitions=transitions,
        placeholders=placeholders,
        style=normalize_style_value(style),
        style_description=normalize_style_value(style_description),
    )


def _cell_position(index: int, cols: int) -> dict[str, int]:
    return {"index": index, "row": index // cols + 1, "col": index % cols + 1}
