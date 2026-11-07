"""Script-editing core (pure functions).

Centralize safe id-based edits to script dictionaries: given a script dict and an operation, mutate
and return the dict, or raise `ScriptEditError` for invalid operations (missing ids, array bounds,
insufficient split parts, or nonexistent field paths). **No disk reads, project-state dependencies,
or structural well-formedness checks**: the unified `_write_script_unlocked` write entry enforces
structure with its no-worse rule and Pydantic models. This module handles only array surgery,
id allocation, and asset invalidation. MCP tools and tests both reuse it.

Delegate item-array and id-field selection for the four skeletons (segments/scenes/shots/video_units)
to `script_skeleton.resolve_kind_items`, the sole skeleton-item access entry. `resolve_items` adds
editing-specific fail-loud validation. Share the same evidence-based resolution with
`script_structure_validator._select_model` and write-entry metadata recalculation to prevent drift.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from lib.script.item_ids import ITEM_ID_RE, ItemIdIssuer, max_item_number
from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_skeleton import resolve_kind_items

logger = logging.getLogger(__name__)


class ScriptEditError(ValueError):
    """Invalid script edit: missing id, array bounds, insufficient split parts, nonexistent field path, etc.

    ``key``/``params`` provide translation coordinates for user-facing paths. Raise sites returned to
    end users through HTTP routes must explicitly provide specific ``key`` values, rendered server-side
    by request ``Accept-Language`` rather than embedding fixed-Chinese ``str(self)`` in translated
    response templates. Raise sites consumed only by MCP tools/logs address the Agent, exempt from
    i18n under CLAUDE.md, and may retain the default key (rendered as generic fallback copy).
    """

    def __init__(self, message: str, *, key: str = "script_edit_error", **params: Any) -> None:
        super().__init__(message)
        self.key = key
        self.params = params


def resolve_items(script: dict[str, Any], *, kind: str | None = None) -> tuple[list[Any], str, str]:
    """Select the script's current item array, id-field name, and kind by skeleton.

    Return ``(items, id_field, kind)``, with ``kind`` ∈ {"segments", "scenes", "shots", "video_units"}.
    Delegate key/id-field lookup to `script_skeleton.resolve_kind_items`, the sole item-access entry
    sharing evidence-based resolution. An explicitly supplied ``kind`` (such as the generation mode
    fixed at task start) bypasses inference and selects that kind directly. Add editing-specific
    validation to otherwise unchanged returns: **missing keys** mean empty arrays; **present keys
    with non-list values, including null**, fail loudly with `ScriptEditError`, never silently becoming
    [] and disguising corruption as missing ids. Corruption such as `"segments": null` is exposed
    rather than treated as an empty draft. For existing keys, the returned list is the actual script
    reference, so in-place editing takes effect.
    """
    raw_items, id_field, kind = resolve_kind_items(script, kind=kind)
    if kind not in script:
        return [], id_field, kind
    if not isinstance(raw_items, list):
        type_name = type(raw_items).__name__
        raise ScriptEditError(
            f"{kind} 必须是列表，当前为 {type_name}",
            key="script_edit_items_not_list",
            kind=kind,
            type_name=type_name,
        )
    return raw_items, id_field, kind


def _find_index(items: list[Any], id_field: str, item_id: str) -> int:
    for idx, item in enumerate(items):
        if isinstance(item, dict) and str(item.get(id_field)) == str(item_id):
            return idx
    raise ScriptEditError(f"未找到 id={item_id!r} 的分镜（{id_field}）")


def _set_nested(obj: dict[str, Any], field_path: str, value: Any) -> None:
    parts = field_path.split(".")
    if not parts or any(not p for p in parts):
        raise ScriptEditError(f"非法字段路径: {field_path!r}")
    if parts[0] == "generated_assets":
        # patch is a pure field setter; asset lifecycle is separate from script editing (ADR-0003).
        raise ScriptEditError("patch_episode_script 不可改 generated_assets；资产的生成/重生是独立的显式动作")
    if parts[0] == "needs_replan":
        raise ScriptEditError("patch_episode_script 不可直接改重规划标记；修改 unit 规划内容后由系统重算")
    if parts[0] == PENDING_AUTHORING_FIELD:
        # The pending-authoring flag tracks visual-layer completeness; allowing patch would disguise missing visuals as authored.
        raise ScriptEditError(
            f"patch_episode_script 不可改 {PENDING_AUTHORING_FIELD}；它由新增条目置位，写齐视觉层或提示词编写写回时清除"
        )
    if parts[0] == "end_frame_image":
        # The last-frame field holds service-created relative snapshot paths, written only by set/clear endpoints.
        # Allowing patch would bypass snapshot copying with arbitrary verbatim strings, reintroducing dangling
        # references and out-of-bounds paths.
        raise ScriptEditError("patch_episode_script 不可改 end_frame_image；尾帧的设置/清除是独立的显式动作")
    if value is None and parts in (["image_prompt"], ["video_prompt"]):
        # None prompts mean pending generation, written only by mechanical script-plan conversion.
        # Once script models accept None, this is the sole barrier against patch clearing existing prompts.
        # Supply new values to rewrite, or use generate_episode_script(entry_ids=…) for model completion.
        raise ScriptEditError(
            f"patch_episode_script 不可把 {parts[0]} 清成 null；待生成态只由脚本规划机械转换写入，"
            "要重写请给出新的提示词"
        )
    if parts[0] in {"segment_id", "scene_id", "unit_id", "shot_id"}:
        # patch cannot change storyboard ids: insert/split allocate them, and structural validation does not
        # check uniqueness. Agent id edits would send id-based helpers (update_scene_asset, etc.) to wrong
        # items or create duplicate-id ambiguity. Add/remove with patch_episode_script insert / split / remove operations.
        raise ScriptEditError(
            f"patch_episode_script 不可改分镜 id 字段 ({parts[0]})；id 由 insert/split 分配，不允许直接修改"
        )
    cur: Any = obj
    # Report three exception categories separately for precise Agent errors: typos, types, and missing intermediate nodes.
    for p in parts[:-1]:
        if not isinstance(cur, dict):
            raise ScriptEditError(f"父节点非对象 (类型 {type(cur).__name__}): {field_path!r}")
        if p not in cur:
            raise ScriptEditError(f"字段路径不存在: {field_path!r}")
        if not isinstance(cur[p], dict):
            raise ScriptEditError(f"父节点非对象 (键 {p!r} 类型为 {type(cur[p]).__name__}): {field_path!r}")
        cur = cur[p]
    if not isinstance(cur, dict):
        raise ScriptEditError(f"父节点非对象: {field_path!r}")
    # The final leaf may be absent: Agents should fill optional fields omitted by LLMs (video_prompt.dialogue /
    # note, etc., with Pydantic defaults/default_factory and possible JSON omission), rather than remove+insert
    # an entire storyboard item. Missing parent/intermediate nodes still fail loudly: actual typos such as
    # image_prompt.scen instead of image_prompt.scene must not create intermediate dict nodes from nothing.
    # The unified write entry's no-worse structural validation ultimately catches structural errors.
    cur[parts[-1]] = value


def patch_field(script: dict[str, Any], item_id: str, field_path: str, value: Any) -> dict[str, Any]:
    """Locate a storyboard item by id and set its potentially nested field. Pure setter, leaving generated_assets untouched."""
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    _set_nested(items[idx], field_path, value)
    return script


def _script_episode(script: dict[str, Any], ids: list[str]) -> int:
    episode = script.get("episode")
    if isinstance(episode, int) and not isinstance(episode, bool) and episode >= 1:
        return episode
    for item_id in ids:
        if (match := ITEM_ID_RE.match(item_id)) is not None:
            return int(match.group(1))
    return 1


def new_item_ids(script: dict[str, Any], count: int, *, floor: int = 0) -> list[str]:
    """Allocate ``count`` new ids after this episode's highest used main sequence; Web and Agent insertions both use this.

    ``floor`` is the episode's highest used main sequence (``ProjectManager.item_id_floor`` takes the
    maximum of the ledger's highest number and current script ids). Allocate without reuse: deleted
    items' numbers never return to new items. Never fill gaps or derive ids from anchors; array position
    alone determines item order. Storyboard-family ids are ``E{集}S{序号:02d}``, reference-to-video units
    ``E{集}U{序号}``.
    """
    items, id_field, kind = resolve_items(script)
    ids = [str(item.get(id_field)) for item in items if isinstance(item, dict)]
    episode = _script_episode(script, ids)
    issuer = ItemIdIssuer(episode, kind, max(floor, max_item_number(ids, episode)))
    taken = set(ids)
    issued: list[str] = []
    while len(issued) < count:
        if (item_id := issuer.issue()) not in taken:
            issued.append(item_id)
    return issued


def new_item_id(script: dict[str, Any], *, floor: int = 0) -> str:
    """Allocate one new item id; see :func:`new_item_ids`."""
    return new_item_ids(script, 1, floor=floor)[0]


def insert_segment(
    script: dict[str, Any], after_id: str | None, new_item: dict[str, Any], *, floor: int = 0
) -> dict[str, Any]:
    """Insert a new item after ``after_id``, or at the beginning when ``after_id`` is ``None``.

    Always allocate its id through ``new_item_id``, clearing ``generated_assets`` and ``end_frame_image``.
    Callers supply other fields; the unified write entry validates their structure.
    """
    items, id_field, _ = resolve_items(script)
    idx = -1 if after_id is None else _find_index(items, id_field, after_id)
    item = deepcopy(new_item)
    item[id_field] = new_item_id(script, floor=floor)
    item["generated_assets"] = {}
    # Last-frame snapshots use storyboard ids. New ids have none; Agent values would point elsewhere or to empty paths.
    item.pop("end_frame_image", None)
    items.insert(idx + 1, item)
    return script


def remove_segment(script: dict[str, Any], item_id: str) -> dict[str, Any]:
    """Delete a storyboard item by id, removing its assets too without changing other items' ids."""
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    items.pop(idx)
    return script


def move_segment(script: dict[str, Any], item_id: str, after_id: str | None) -> dict[str, Any]:
    """Move ``item_id`` after ``after_id``, or to the beginning if ``after_id`` is ``None``. Content/artifacts stay unchanged."""
    if after_id == item_id:
        raise ScriptEditError(f"id={item_id!r} 不能移到自己之后")
    items, id_field, _ = resolve_items(script)
    item = items.pop(_find_index(items, id_field, item_id))
    idx = -1 if after_id is None else _find_index(items, id_field, after_id)
    items.insert(idx + 1, item)
    return script


def split_segment(
    script: dict[str, Any], item_id: str, parts: list[dict[str, Any]], *, floor: int = 0
) -> dict[str, Any]:
    """Split ``item_id`` into multiple storyboard items using Agent-supplied parts.

    The first part keeps the original id and **retains** ``generated_assets`` and ``end_frame_image``
    without clearing: anchor continuation, matching ``insert_segment``'s unchanged anchor assets and
    consistent invalidation granularity across related structural operations. Other parts each get a
    new main sequence through :func:`new_item_ids` (same ``floor`` meaning), clearing ``generated_assets``
    and ``end_frame_image`` because changed identity leaves old assets unowned and pending regeneration.
    Use ``patch_episode_script`` for small field edits to the original item. Retaining anchor assets
    prevents a misused split from invalidating all generated images/videos.

    Reference-to-video splits top-level ``video_units`` (``item_id`` is unit_id). Each part is a complete
    new unit with its own ``shots``, not a finer split of the original unit's ``shots``.
    ``duration_seconds`` is independent of ``shots``, so each new part must provide its own value;
    this function does not calculate it.
    """
    if len(parts) < 2:
        raise ScriptEditError("split 至少需要 2 个部分")
    items, id_field, _ = resolve_items(script)
    idx = _find_index(items, id_field, item_id)
    anchor_assets = items[idx].get("generated_assets")
    anchor_end_frame = items[idx].get("end_frame_image")

    fresh_ids = iter(new_item_ids(script, len(parts) - 1, floor=floor))
    new_parts: list[dict[str, Any]] = []
    for offset, raw in enumerate(parts):
        part = deepcopy(raw)
        if offset == 0:
            part[id_field] = str(item_id)
            # Anchor continuation retains actual original generated_assets, overriding any generated_assets
            # supplied by the Agent in parts[0] so it cannot invent asset paths.
            if isinstance(anchor_assets, dict):
                part["generated_assets"] = deepcopy(anchor_assets)
            else:
                # Malformed anchor generated_assets (non-dict dirty data such as list/str) degrades to an empty dict.
                # This also overrides/discards Agent-supplied generated_assets in parts[0], already copied into
                # part by deepcopy(raw). Warn operators per ADR-0003's added ban on zero-signal success.
                # anchor_assets is None means ordinarily absent, so do not warn.
                if anchor_assets is not None:
                    logger.warning(
                        "split_segment: 锚点 %r generated_assets 形态异常(%s),退化为空 dict",
                        item_id,
                        type(anchor_assets).__name__,
                    )
                part["generated_assets"] = {}
            # Last frames also continue the anchor: retain actual original values, preventing invented paths in parts[0].
            if anchor_end_frame is None:
                part.pop("end_frame_image", None)
            else:
                part["end_frame_image"] = anchor_end_frame
        else:
            part[id_field] = next(fresh_ids)
            part["generated_assets"] = {}
            # New ids have no snapshot; Agent-supplied values would only point to the anchor snapshot.
            part.pop("end_frame_image", None)
        new_parts.append(part)

    items[idx : idx + 1] = new_parts
    return script
