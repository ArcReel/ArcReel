"""Check ``@[登记名]`` bindings in storyboard visual descriptions.

Reference fields (``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot``) determine storyboard images;
``image_prompt.scene`` ``@[名称]`` mentions identify, never derive, references. Replacement with “图N” requires both
asset registration and presence in this entry's reference fields. If either is missing, render the bare name and collect
locale-neutral ``{"key", "params"}`` warnings here for script-writing tool receipts.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.references.reference_catalog import build_reference_catalog
from lib.script.reference_video.text_parser import extract_mentions
from lib.script.storyboard_sequence import get_storyboard_items

#: Visual ``@[名称]`` absent from registration or this shot's reference fields is sent as a bare name.
WARN_STORYBOARD_MENTION_UNBOUND = "storyboard_warn_mention_unbound"

_REFERENCE_LIST_FIELDS: tuple[str, ...] = tuple(
    field for spec in ASSET_SPECS.values() for field in spec.reference_list_fields
)


def _scene_text(item: dict[str, Any]) -> str | None:
    image_prompt = item.get("image_prompt")
    if isinstance(image_prompt, dict):
        image_prompt = image_prompt.get("scene")
    return image_prompt if isinstance(image_prompt, str) else None


def _declared_names(item: dict[str, Any]) -> set[str]:
    declared: set[str] = set()
    for field in _REFERENCE_LIST_FIELDS:
        names = item.get(field)
        if not isinstance(names, list):
            continue
        declared.update(asset_name_comparison_key(name) for name in names if isinstance(name, str))
    return declared


def storyboard_mention_warnings(
    project: object,
    script: dict[str, Any],
    *,
    unit_ids: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    """Return unbound visual-description mention warnings in entry order, then first-mention order within entries.

    If ``unit_ids`` is supplied, check only those entries (write tools own only entries changed in this call).
    Skeletons without storyboards (reference-to-video) return an empty list.
    """
    items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
    catalog = build_reference_catalog(project)
    warnings: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get(id_field) or "")
        if unit_ids is not None and unit_id not in unit_ids:
            continue
        text = _scene_text(item)
        if text is None:
            continue
        declared = _declared_names(item)
        for name in extract_mentions(text):
            if name in declared and catalog.resolve(name) is not None:
                continue
            warnings.append({"key": WARN_STORYBOARD_MENTION_UNBOUND, "params": {"unit_id": unit_id, "name": name}})
    return warnings


def render_storyboard_mention_warnings(
    warnings: Sequence[Mapping[str, Any]], translate: Callable[..., str]
) -> list[str]:
    """Render :func:`storyboard_mention_warnings` entries as receipt text lines through ``translate``.

    Both script-writing tool boundaries (``generate_episode_script`` / ``patch_episode_script``) share this renderer;
    copy exists only in i18n key tables.
    """
    return [translate(str(warning["key"]), **warning["params"]) for warning in warnings]


__all__ = [
    "WARN_STORYBOARD_MENTION_UNBOUND",
    "render_storyboard_mention_warnings",
    "storyboard_mention_warnings",
]
