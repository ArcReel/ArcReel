"""Sole specification source for project assets (character / scene / prop / product).

``AssetSpec`` defines buckets, sheet fields, directories, labels, references, and extra fields, shared
by ProjectManager APIs, namespaces, and server/routers/_asset_router_factory. ASSET_TYPES / BUCKET_KEY /
SHEET_KEY compatibility constants derive from ASSET_SPECS.

User-visible labels are outside specs: ``localize_asset_type`` maps internal types to ``lib/i18n``
``asset_type_*`` through injected translation, avoiding reverse i18n dependencies."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssetSpec:
    """All structural properties of one asset type.

    ``extra_string_fields`` is a schema dimension: validators require strings when present,
    `_build_asset_entry` initializes empty strings, and REST PATCH expands writable fields.
    ``extra_list_fields`` similarly requires string lists and initializes empty lists through
    `_build_asset_entry`, also expanding PATCH. ``agent_editable_extra_fields`` is a permission dimension,
    constructing :attr:`agent_writable_fields` **without reusing schema dimensions**. ``reference_image`` /
    ``reference_images`` are uploaded/system-generated paths, valid schema fields but outside
    ``agent_editable_extra_fields``: Agents must not overwrite uploads; dedicated APIs update them,
    as with sheet_field.

    ``original_image_fields`` declares user-original fields, single/list alike. Reference-to-video uses
    sheets if available, otherwise all originals in declaration order. Types without original fields
    (scenes/props) yield no image candidates without sheets.

    ``in_global_library`` controls cross-project assets-table inclusion. Its single-image-column shape
    supports one-image-per-asset types only, excluding multi-image product lists for now.

    ``supports_derivatives`` optionally adds ``DERIVATIVES_FIELD`` tables (name →
    ``{description, <sheet_field>}``), sharing base identity/names and unique only within bases, referenced
    as ``本体名/衍生名``. Generic by asset type, currently character-only (``docs/adr/0072``);
    other types omit the field.

    ``label_zh`` serves logger/Agent strings, both exempt under i18n rules; user-visible types use
    ``lib/i18n`` ``asset_type_*`` instead."""

    asset_type: str
    bucket_key: str
    sheet_field: str
    subdir: str
    label_zh: str
    namespace_priority: int
    reference_list_fields: tuple[str, ...]
    original_image_fields: tuple[str, ...] = ()
    extra_string_fields: tuple[str, ...] = ()
    extra_list_fields: tuple[str, ...] = ()
    agent_editable_extra_fields: tuple[str, ...] = ()
    in_global_library: bool = True
    supports_derivatives: bool = False

    @property
    def agent_writable_fields(self) -> frozenset[str]:
        """Agent-write allowlist for `upsert_assets` and atomic asset-extraction commits.

        `description` is shared and may be empty, required only for sheet generation.
        `agent_editable_extra_fields` supplies string extras; derivative-capable types additionally accept
        nested ``DERIVATIVES_FIELD``, outside that string tuple. Both Agent entry points share this source;
        add writable fields here only."""
        fields = {"description", *self.agent_editable_extra_fields}
        if self.supports_derivatives:
            fields.add(DERIVATIVES_FIELD)
        return frozenset(fields)


#: Character/scene/prop aliases: source alternative names, old names, merged names, used only by AI
#: planning/script generation to recognize identities, never programmatic matching or references (``docs/adr/0092``).
ALIASES_FIELD = "aliases"

ASSET_SPECS: dict[str, AssetSpec] = {
    "character": AssetSpec(
        asset_type="character",
        bucket_key="characters",
        sheet_field="character_sheet",
        subdir="characters",
        label_zh="角色",
        namespace_priority=0,
        reference_list_fields=("characters_in_segment", "characters_in_scene", "characters_in_shot"),
        original_image_fields=("reference_image",),
        extra_string_fields=("voice_style", "reference_image", "reference_audio", "voice_notice_dismissed_at"),
        # voice_style is LLM-generated, Agent-editable. reference_image / reference_audio
        # are system-managed upload paths outside Agent allowlists, updated through
        # update_character_reference_image / update_character_reference_audio。
        # voice_notice_dismissed_at records acknowledged voice_updated_at via frontend dismissal
        # through generic PATCH; Agents need not see this UI state and cannot write it.
        # voice_updated_at is excluded: systems mechanically stamp only actual reference_audio changes
        # (update_character_reference_audio/library imports), never arbitrary PATCH
        # that would let clients bypass comparisons.
        extra_list_fields=(ALIASES_FIELD,),
        agent_editable_extra_fields=("voice_style", ALIASES_FIELD),
        supports_derivatives=True,
    ),
    "scene": AssetSpec(
        asset_type="scene",
        bucket_key="scenes",
        sheet_field="scene_sheet",
        subdir="scenes",
        label_zh="场景",
        namespace_priority=1,
        reference_list_fields=("scenes",),
        extra_string_fields=(),
        extra_list_fields=(ALIASES_FIELD,),
        agent_editable_extra_fields=(ALIASES_FIELD,),
    ),
    "prop": AssetSpec(
        asset_type="prop",
        bucket_key="props",
        sheet_field="prop_sheet",
        subdir="props",
        label_zh="道具",
        namespace_priority=2,
        reference_list_fields=("props",),
        extra_string_fields=(),
        extra_list_fields=(ALIASES_FIELD,),
        agent_editable_extra_fields=(ALIASES_FIELD,),
    ),
    "product": AssetSpec(
        asset_type="product",
        bucket_key="products",
        sheet_field="product_sheet",
        subdir="products",
        label_zh="商品",
        namespace_priority=3,
        reference_list_fields=("products_in_shot",),
        original_image_fields=("reference_images",),
        # brand is user free text; reference_images are system-managed uploaded product
        # original paths/fidelity anchors; selling_points are Agent-drafted and
        # user-editable.
        extra_string_fields=("brand",),
        extra_list_fields=("reference_images", "selling_points"),
        # selling_points is Agent-editable; reference_images are uploaded paths like
        # reference_image, outside allowlists and updated by dedicated upload APIs.
        agent_editable_extra_fields=("selling_points",),
        # Global-library single-image columns exclude multi-image products.
        in_global_library=False,
    ),
}


ASSET_TYPES: frozenset[str] = frozenset(ASSET_SPECS.keys())

BUCKET_KEY: dict[str, str] = {t: s.bucket_key for t, s in ASSET_SPECS.items()}

SHEET_KEY: dict[str, str] = {t: s.sheet_field for t, s in ASSET_SPECS.items()}

GLOBAL_LIBRARY_ASSET_TYPES: frozenset[str] = frozenset(t for t, s in ASSET_SPECS.items() if s.in_global_library)

#: Shared derivative-table field for ``supports_derivatives`` types.
DERIVATIVES_FIELD = "derivatives"


@dataclass(frozen=True)
class ProjectAssetNameMatch:
    """Existing entry in the shared project asset namespace."""

    asset_type: str
    bucket_key: str
    name: str

    def is_entry(self, asset_type: str, name: str) -> bool:
        """Whether matches are *name* itself in *asset_type* (same equality key), rather than another case-only asset."""
        return self.asset_type == asset_type and asset_name_comparison_key(self.name) == asset_name_comparison_key(name)


class ProjectAssetNameConflictError(ValueError):
    """Project asset names conflict with another asset."""

    def __init__(self, name: str, existing: ProjectAssetNameMatch, requested_asset_type: str | None = None):
        self.name = name
        self.existing = existing
        self.requested_asset_type = requested_asset_type
        requested = (
            f"新增/改名后的{ASSET_SPECS[requested_asset_type].label_zh}"
            if requested_asset_type in ASSET_SPECS
            else "待写入资产"
        )
        super().__init__(
            f"{requested}名称 {name!r} 与项目内已有{ASSET_SPECS[existing.asset_type].label_zh} "
            f"{existing.name!r} 冲突（按 strip + Unicode NFC、不区分大小写判定）"
        )


ILLEGAL_ASSET_NAME_CHARS: tuple[str, ...] = ("/", "\\", "\0", ":", "*", "?", '"', "<", ">", "|", "]")

WINDOWS_RESERVED_BASENAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)


def localize_asset_type(value: str, translate: Callable[..., str]) -> str:
    """Replace internal asset types such as ``"product"`` with current-language labels.

    Pass unregistered values outside ``ASSET_SPECS`` unchanged, without semantic mapping."""
    if value not in ASSET_SPECS:
        return value
    return translate(f"asset_type_{value}")


def normalize_asset_name(name: str) -> str:
    """Normalize names to Unicode NFC, defining asset equality/membership coordinates.

    NFC (composition, primary for Web/project.json registration) and NFD (decomposition, macOS filenames/
    some input methods) display identically but differ in bytes, failing ``==`` / ``in``. Combining-mark
    languages such as supported vi often use both. Normalize before verbatim comparison so users do not
    have to diagnose visibly identical names.

    Always normalize before equality/membership: use this for text and :func:`normalize_asset_bucket`
    for table keys. Do it once at read/resolve entrances, never patch individual comparison sites or
    compare raw names.

    Only unify encoding forms without changing characters/length semantics; ASCII remains identical."""
    return unicodedata.normalize("NFC", name)


def asset_name_comparison_key(name: str) -> str:
    """Project asset equality key: strip whitespace then NFC, without casefold.

    Identifies same assets/references and is the name shape in manifest identities/bases. Changing returns
    would globally change existing keys/digests. Use :func:`asset_name_namespace_key` for new-name admission."""
    return normalize_asset_name(name.strip())


def asset_name_namespace_key(name: str) -> str:
    """Shared-namespace duplicate key: equality key plus casefold, treating case-only names as duplicates.

    Asset names directly form ``characters/{name}.png``. Default macOS/Windows filesystems merge
    ``Alice`` / ``alice``; manifest path uniqueness also uses NFC + casefold. Use only for creation,
    rename, and legacy-shape dedup; preserve user-written case in names."""
    return asset_name_comparison_key(name).casefold()


def build_asset_entry(asset_type: str, description: str, source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build from ASSET_SPECS: description + empty sheet + extras from source/defaults.

    None source initializes declared extras only (empty strings/lists). Supplied source may override
    sheets too. Never repair invalid source types; pre-persistence structure checks fail loudly.
    Initialize empty derivative tables for ``supports_derivatives`` types."""
    spec = ASSET_SPECS[asset_type]
    data = source or {}
    entry: dict[str, Any] = {"description": description, spec.sheet_field: data.get(spec.sheet_field, "")}
    for field in spec.extra_string_fields:
        entry[field] = data.get(field, "")
    for field in spec.extra_list_fields:
        value = data.get(field)
        if isinstance(value, list):
            entry[field] = list(value)  # Copy lists to avoid sharing mutable objects with callers.
        elif value is None:
            entry[field] = []
        else:
            entry[field] = value  # Pass invalid types through for prewrite structural failure.
    if spec.supports_derivatives:
        entry[DERIVATIVES_FIELD] = {}
    return entry


def record_asset_aliases(entry: dict[str, Any], aliases: Iterable[str], *, asset_name: str) -> None:
    """Merge aliases by equality key; omit asset-name equivalents and remove asset names from aliases.

    Preserve existing nonstrings for pre-persistence structural errors."""
    name_key = asset_name_comparison_key(asset_name)
    raw = entry.get(ALIASES_FIELD)
    current = list(raw) if isinstance(raw, list) else []
    kept = [item for item in current if not isinstance(item, str) or asset_name_comparison_key(item) != name_key]
    seen = {asset_name_comparison_key(item) for item in kept if isinstance(item, str)}
    for alias in aliases:
        key = asset_name_comparison_key(alias)
        if not key or key == name_key or key in seen:
            continue
        seen.add(key)
        kept.append(key)
    if kept or ALIASES_FIELD in entry:
        entry[ALIASES_FIELD] = kept


def find_project_asset_name(
    project: object,
    name: str,
    *,
    exclude_asset_type: str | None = None,
    exclude_name: str | None = None,
) -> ProjectAssetNameMatch | None:
    """Find matching names across four ``ASSET_SPECS`` types via :func:`asset_name_namespace_key`.

    Matches may be the entry itself or case-only other assets; distinguish with
    :meth:`ProjectAssetNameMatch.is_entry`. Equality matches precede case-only matches so legacy pairs
    can update individually. ``exclude_*`` excludes one exact persisted entry for rename self-checks.
    Treat malformed buckets as empty; DataValidator reports their structure."""
    if not isinstance(project, dict):
        return None
    target = asset_name_namespace_key(name)
    identity = asset_name_comparison_key(name)
    case_variant: ProjectAssetNameMatch | None = None
    for spec in sorted(ASSET_SPECS.values(), key=lambda item: item.namespace_priority):
        asset_type = spec.asset_type
        bucket = project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for raw_name in bucket:
            if not isinstance(raw_name, str):
                continue
            if asset_type == exclude_asset_type and raw_name == exclude_name:
                continue
            if asset_name_namespace_key(raw_name) != target:
                continue
            match = ProjectAssetNameMatch(asset_type=asset_type, bucket_key=spec.bucket_key, name=raw_name)
            if asset_name_comparison_key(raw_name) == identity:
                return match
            if case_variant is None:
                case_variant = match
    return case_variant


def ensure_project_asset_name_available(
    project: object,
    name: str,
    *,
    requested_asset_type: str | None = None,
    exclude_asset_type: str | None = None,
    exclude_name: str | None = None,
) -> None:
    """Require *name* availability in the shared namespace, otherwise structured conflict diagnostics."""
    existing = find_project_asset_name(
        project,
        name,
        exclude_asset_type=exclude_asset_type,
        exclude_name=exclude_name,
    )
    if existing is not None:
        raise ProjectAssetNameConflictError(name, existing, requested_asset_type)


def project_asset_name_conflicts(project: object) -> list[tuple[ProjectAssetNameMatch, ProjectAssetNameMatch]]:
    """All shared-namespace duplicate pairs, in stable reproducible order.

    Whole-project prewrite guards use case-sensitive equality. Legacy case-only assets already at current
    schema are not migrated; casefold here would reject every write. New names use
    :func:`ensure_project_asset_name_available` casefold admission."""
    if not isinstance(project, dict):
        return []
    first_by_key: dict[str, ProjectAssetNameMatch] = {}
    conflicts: list[tuple[ProjectAssetNameMatch, ProjectAssetNameMatch]] = []
    specs = sorted(ASSET_SPECS.values(), key=lambda spec: spec.namespace_priority)
    for spec in specs:
        bucket = project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for raw_name in bucket:
            if not isinstance(raw_name, str):
                continue
            current = ProjectAssetNameMatch(spec.asset_type, spec.bucket_key, raw_name)
            key = asset_name_comparison_key(raw_name)
            first = first_by_key.setdefault(key, current)
            if first is not current:
                conflicts.append((first, current))
    return conflicts


def ensure_project_asset_namespace(project: object) -> None:
    """Require uniqueness across all four project asset types, reporting the first stable conflict."""
    conflicts = project_asset_name_conflicts(project)
    if conflicts:
        first, current = conflicts[0]
        raise ProjectAssetNameConflictError(current.name, first, current.asset_type)


def normalize_asset_bucket(bucket: object) -> dict[str, Any]:
    """Read buckets as dictionaries with normalized comparison keys; malformed nondicts become empty.

    Names compare text × table keys, requiring shared coordinates. Normalize once at reading to cover
    legacy keys in either form while callers handle comparison results only.

    Equivalent keys merge last-write-wins: they already denote one asset and should not coexist, so
    merging repairs duplication rather than losing data."""
    if not isinstance(bucket, dict):
        return {}
    return {normalize_asset_name(str(name)): item for name, item in bucket.items()}


def resolve_asset_key(bucket: object, name: str) -> str | None:
    """Resolve *name* by NFC to actual persisted keys; None on misses.

    Writes use NFC (:func:`validate_asset_name`), but legacy keys may use either form without migration.
    In-place updates/deletion/conflict checks must resolve keys first, not index by normalized names,
    or visually identical second entries/missed conflicts result.

    Equivalent legacy keys use last-write-wins, matching :func:`normalize_asset_bucket`. Nondict buckets count as empty."""
    if not isinstance(bucket, dict):
        return None
    target = normalize_asset_name(name)
    found: str | None = None
    for key in bucket:
        if isinstance(key, str) and normalize_asset_name(key) == target:
            found = key
    return found


def rekey_equivalent_entries[T](bucket: dict[str, T], old_name: str, new_name: str) -> T | None:
    """Rename all visually equivalent *old_name* keys to *new_name*, returning the renamed value.

    Adopt entire NFC/NFD-equivalent clusters, not just :func:`resolve_asset_key` winners, or old-name
    keys retain invalid paths and may reconnect to newly recreated assets. Match readers' last-write-wins.
    No equivalent keys means ``None`` without changes.

    Rebuild in place instead of pop/assignment, preserving user-visible JSON entry order."""
    if resolve_asset_key(bucket, old_name) is None:
        return None
    target = normalize_asset_name(old_name)
    rekeyed = {(new_name if normalize_asset_name(key) == target else key): value for key, value in bucket.items()}
    bucket.clear()
    bucket.update(rekeyed)
    return rekeyed[new_name]


def validate_asset_name(name: object) -> str:
    """Validate/normalize strip + NFC; invalid names raise ValueError, valid names return normalized.

    Registration gate for all newly persisted names, using :func:`normalize_asset_name` coordinates so
    NFD inputs cannot create visually duplicate assets.

    Names are single path components for ``characters/{name}.png``,
    ``versions/{type}/{name}_v{n}_{ts}.png``, and REST path parameters. Separators, controls, and ``..``
    cause nested/unmatchable paths; Windows also rejects ``: * ? " < > |``, trailing dots, and devices
    (CON / COM1, etc., by first dotted segment; ``CON.backup`` is reserved too). Apply constraints on
    all platforms at creation, ensuring portable projects.

    Reject ``]`` too: ``@[名称]`` terminates at first ``]``
    (:func:`lib.script.reference_video.text_parser._iter_mentions`), truncating names containing ``]``.
    Allow ``[``: ``[甲`` in ``@[[甲]`` still parses completely as ``[甲``."""
    if not isinstance(name, str):
        raise ValueError(f"资产名称必须是字符串，当前为 {type(name).__name__}")
    cleaned = normalize_asset_name(name.strip())
    if not cleaned:
        raise ValueError("资产名称不能为空或仅含空白字符")
    if (
        ".." in cleaned
        or any(c in cleaned for c in ILLEGAL_ASSET_NAME_CHARS)
        or any(ord(c) < 32 or ord(c) == 127 for c in cleaned)
    ):
        raise ValueError(
            f"资产名称 {cleaned!r} 含非法字符：不允许路径分隔符（/ \\）、"
            f'Windows 保留字符（: * ? " < > |）、引用定界符（]）、控制字符或 ..'
        )
    if cleaned.endswith("."):
        raise ValueError(f"资产名称 {cleaned!r} 不能以点结尾（Windows 文件名约束）")
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_BASENAMES:
        raise ValueError(f"资产名称 {cleaned!r} 是 Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）")
    return cleaned
