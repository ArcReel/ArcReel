"""Traverse name-reference sites in script/draft payloads, sharing locations for scans and rewrites.

Reference names (assets or character ``本体名/衍生名``) occur in five places: skeleton reference arrays
(``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot``), ``speaker`` fields,
unit-body ``@[名称]`` markers, visual-description ``@[名称]`` markers, and merge/derivative ``target``
fields in plan new-assets lists. Cascade rename/merge must update all; reference checks must count all.
They differ only in how names are processed, never the site set; separate traversals would let new sites
be recognized by only one side, missing renames or usage reports.

Visual-description markers only identify assets already declared in reference fields, never derive images
(:mod:`lib.script.storyboard_mentions`); rename them, but do not count them as independent references.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any, Literal

from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, asset_name_comparison_key
from lib.references.reference_catalog import derivative_reference
from lib.script.reference_video.text_parser import mention_names
from lib.script.script_models import NEW_ASSETS_FIELD

#: List fields containing unit bodies. Reference mentions live in ``video_units[].text`` (draft
#: ``units[].text``); ad shots additionally contain reference arrays and ``video_prompt.dialogue``.
_TEXT_ITEM_FIELDS: frozenset[str] = frozenset({"shots", "units", "video_units"})

#: Reference arrays for all asset types. Scans are type-independent (name occurrence); rewrites select
#: type-specific subsets (character renames must not alter ``scenes`` arrays).
_ALL_REFERENCE_LIST_FIELDS: frozenset[str] = frozenset(
    field for spec in ASSET_SPECS.values() for field in spec.reference_list_fields
)

#: Five site shapes: ``name`` is a full array reference, ``speaker`` holds a base-character name,
#: ``text`` is a unit body containing ``@[名称]``, ``scene`` is a visual description containing ``@[名称]``
#: (``image_prompt.scene`` or text-form ``image_prompt``), and ``target`` names merge targets or derivative
#: bases in new-asset lists. Like speakers, targets use table-entry names, never ``本体名/衍生名``.
SiteKind = Literal["name", "speaker", "text", "scene", "target"]


def _dict_writer(node: dict[str, Any], key: str) -> Callable[[str], None]:
    def write(value: str) -> None:
        node[key] = value

    return write


def _list_writer(items: list[Any], index: int) -> Callable[[str], None]:
    def write(value: str) -> None:
        items[index] = value

    return write


def _new_asset_target_sites(
    items: list[Any], list_fields: frozenset[str]
) -> Iterator[tuple[SiteKind, str, Callable[[str], None]]]:
    """New-asset ``target`` sites whose asset type belongs to ``list_fields``.

    Merge ``target`` names same-type assets; derivative targets name base characters. Other dispositions
    leave ``target`` empty and create no site. ``list_fields`` defines concerned types: character renames
    supply only character array fields, never considering scene-merge targets as character sites.
    """
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("target"), str):
            continue
        decision = item.get("decision")
        target_type = item.get("type") if decision == "merge" else "character" if decision == "derivative" else None
        spec = ASSET_SPECS.get(target_type) if isinstance(target_type, str) else None
        if spec is not None and not list_fields.isdisjoint(spec.reference_list_fields):
            yield "target", item["target"], _dict_writer(item, "target")


def iter_reference_sites(
    payload: object,
    list_fields: frozenset[str],
    *,
    with_speaker: bool,
) -> Iterator[tuple[SiteKind, str, Callable[[str], None]]]:
    """Traverse name-bearing payload locations, yielding ``(类别, 当前值, 写回)``.

    Recognize skeleton structure, not semantics; centralized persistence no-worse validation enforces structure.
    Writes replace existing keys/indices without adding/removing entries, so in-place changes during traversal are safe.

    ``with_speaker`` controls inclusion of ``speaker`` fields, which carry only base-character names
    (derivatives share base voices, ``docs/adr/0072``); exclude them for scene/prop/product renames.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in list_fields and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, str):
                        yield "name", item, _list_writer(value, index)
                    else:
                        yield from iter_reference_sites(item, list_fields, with_speaker=with_speaker)
                continue
            if key == NEW_ASSETS_FIELD and isinstance(value, list):
                yield from _new_asset_target_sites(value, list_fields)
                continue
            if key == "speaker" and with_speaker and isinstance(value, str):
                yield "speaker", value, _dict_writer(payload, key)
                continue
            if key == "image_prompt":
                if isinstance(value, str):
                    yield "scene", value, _dict_writer(payload, key)
                elif isinstance(value, dict) and isinstance(value.get("scene"), str):
                    yield "scene", value["scene"], _dict_writer(value, "scene")
                continue
            if key in _TEXT_ITEM_FIELDS and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        yield "text", item["text"], _dict_writer(item, "text")
                    yield from iter_reference_sites(item, list_fields, with_speaker=with_speaker)
                continue
            yield from iter_reference_sites(value, list_fields, with_speaker=with_speaker)
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_reference_sites(item, list_fields, with_speaker=with_speaker)


def payload_reference_names(payload: object) -> set[str]:
    """All payload reference names in normalized comparison space, merged across asset types without ownership.

    Answers whether a name appears in scripts, without checking type or registration;
    :class:`lib.references.reference_catalog.ReferenceCatalog` answers ownership/registration.
    """
    names: set[str] = set()
    for kind, value, _write in iter_reference_sites(payload, _ALL_REFERENCE_LIST_FIELDS, with_speaker=True):
        if kind == "text":
            names.update(mention_names(value))
        elif kind != "scene":
            names.add(asset_name_comparison_key(value))
    return names


def iter_reference_lists(payload: object, list_fields: frozenset[str]) -> Iterator[list[Any]]:
    """Traverse reference arrays themselves for callers adding/removing entries (such as post-merge deduplication).

    Recognize the same arrays as :func:`iter_reference_sites`: keys in ``list_fields`` with list values.
    Yielded lists may mutate in place; traversal does not descend into them afterward.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in list_fields and isinstance(value, list):
                yield value
                for item in value:
                    if not isinstance(item, str):
                        yield from iter_reference_lists(item, list_fields)
                continue
            yield from iter_reference_lists(value, list_fields)
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_reference_lists(item, list_fields)


def annotate_derivative_references(project: object, payloads: Iterable[object]) -> dict[str, Any]:
    """Return a project copy with each derivative's ``referenced`` flag: whether scripts contain ``本体名/衍生名``.

    Compute on read, never persist: derivative storage contains only ``description`` and image paths
    (``docs/adr/0072``). Read APIs expose this for appearance-use overlays. Never mutate inputs, since
    shared read/write payloads would otherwise persist the derived field into project.json.

    Share the cascade-rename site set (:func:`iter_reference_sites`): any author-written reference
    is also renamed, keeping usage checks and rewriting consistent.
    """
    payload: dict[str, Any] = dict(project) if isinstance(project, dict) else {}
    referenced: set[str] = set()
    for item in payloads:
        referenced |= payload_reference_names(item)
    for spec in ASSET_SPECS.values():
        if not spec.supports_derivatives:
            continue
        bucket = payload.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        payload[spec.bucket_key] = {
            name: _annotated_entry(asset, asset_name_comparison_key(str(name)), referenced)
            for name, asset in bucket.items()
        }
    return payload


def _annotated_entry(asset: object, base_name: str, referenced: set[str]) -> object:
    """Copy an asset with ``referenced`` on each derivative; return unchanged when no derivative table exists."""
    if not isinstance(asset, dict):
        return asset
    table = asset.get(DERIVATIVES_FIELD)
    if not isinstance(table, dict):
        return asset
    return {
        **asset,
        DERIVATIVES_FIELD: {
            derivative_name: (
                {
                    **derivative,
                    "referenced": derivative_reference(base_name, asset_name_comparison_key(str(derivative_name)))
                    in referenced,
                }
                if isinstance(derivative, dict)
                else derivative
            )
            for derivative_name, derivative in table.items()
        },
    }


__all__ = [
    "SiteKind",
    "annotate_derivative_references",
    "iter_reference_lists",
    "iter_reference_sites",
    "payload_reference_names",
]
