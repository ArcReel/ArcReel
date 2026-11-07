"""Character-derivative sheet identity, persistence shape, and generation admission (``docs/adr/0072``).

Derivatives live in base entries' ``DERIVATIVES_FIELD``, sharing base names/identity and holding only
relative appearance-change descriptions and sheets. Sheets edit **base sheets** under ``docs/adr/0050``:
changes are instructions, with fixed guards preserving three-view layout/other appearance. No base
sheet means no generation.

Sole source for naming, location, and inputs, shared by routes/execution/artifact planning (currency)/
history, avoiding independently constructed ``本体名/衍生名`` coordinates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.artifacts.input_gap import InputGap
from lib.artifacts.visual_artifact_provenance import VisualReference, build_asset_sheet_visual_basis
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    asset_name_comparison_key,
    asset_name_namespace_key,
    resolve_asset_key,
    validate_asset_name,
)
from lib.project.resource_paths import (
    CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    resource_relative_path,
    version_snapshot_dir,
)

#: Separator for base/derivative references and IDs; ``/`` is already forbidden in asset names, making joins unambiguous.
DERIVATIVE_ID_SEPARATOR = "/"

#: Queue/edit API type, shared by task_type and ``image_edit``
#: request ``resource_type``. Names must agree because ``TaskSpec`` uses one table for ID segment counts;
#: define only once. Persistence/history resource types are separate; see
#: ``resource_paths.CHARACTER_DERIVATIVE_RESOURCE_TYPE``。
DERIVATIVE_TASK_TYPE = "character_derivative"

#: Asset spec supplying derivative sheets. ``AssetSpec.supports_derivatives`` models this generically,
#: currently enabled only for character, fixing persisted/artifact keys to that type.
DERIVATIVE_ASSET_TYPE = "character"

#: Base-sheet edit-input role/kind in artifact basis, shared by planning/execution;
#: disagreement would make all derivative images permanently stale.
DERIVATIVE_SOURCE_ROLE = "derivative_source"
DERIVATIVE_SOURCE_KIND = "sheet"


def derivative_artifact_id(owner_name: str, derivative_name: str) -> str:
    """Join base/derivative names into artifact/history/path IDs."""
    return f"{owner_name}{DERIVATIVE_ID_SEPARATOR}{derivative_name}"


def is_derivative_artifact_id(artifact_id: str) -> bool:
    """Whether an ID addresses derivatives rather than bases."""
    return DERIVATIVE_ID_SEPARATOR in artifact_id


def split_derivative_artifact_id(artifact_id: str) -> tuple[str, str]:
    """Split IDs into (base, derivative), valid only with both nonempty."""
    owner, separator, derivative = artifact_id.partition(DERIVATIVE_ID_SEPARATOR)
    if not separator or not owner or not derivative or DERIVATIVE_ID_SEPARATOR in derivative:
        raise ValueError(f"不是合法的衍生资产图 id: {artifact_id!r}")
    return owner, derivative


def derivative_sheet_relative_path(owner_name: str, derivative_name: str) -> str:
    """Project-relative derivative sheet: ``characters/derivatives/{本体}/{衍生}.png``."""
    return resource_relative_path(
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
    )


def derivative_sheet_dir(owner_name: str) -> str:
    """Project-relative directory for all derivative sheets under a base."""
    return f"{PurePosixPath(derivative_sheet_relative_path(owner_name, '_')).parent}"


def derivative_version_dir(owner_name: str) -> str:
    """Project-relative snapshot directory for all derivatives under a base."""
    return f"{version_snapshot_dir(CHARACTER_DERIVATIVE_RESOURCE_TYPE)}/{owner_name}"


def derivative_artifact_key(owner_name: str, derivative_name: str) -> ArtifactKey:
    """Derivative manifest key: ``asset_sheet("character", "本体/衍生")``."""
    return ArtifactKey.asset_sheet(
        DERIVATIVE_ASSET_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
    )


def derivative_table(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read base derivative tables; absent/malformed means empty, with structural errors reported separately by validation."""
    if not isinstance(entry, Mapping):
        return {}
    table = entry.get(DERIVATIVES_FIELD)
    return dict(table) if isinstance(table, Mapping) else {}


def ensure_derivative_table(entry: dict[str, Any]) -> dict[str, Any]:
    """Get writable base derivative tables, filling absent/malformed tables in place for predictable write shapes.

    Unlike read-only :func:`derivative_table` copies, return the original table for mutation."""

    table = entry.get(DERIVATIVES_FIELD)
    if not isinstance(table, dict):
        table = {}
        entry[DERIVATIVES_FIELD] = table
    return table


def find_derivative_name(table: object, name: str, *, exclude: str | None = None) -> str | None:
    """Find duplicate *name* within one base, returning actual persisted names or ``None``.

    Share :func:`lib.project.asset_types.find_project_asset_name` semantics via
    :func:`asset_name_namespace_key`: case-only variants collide (files use
    ``characters/derivatives/{本体}/{衍生}.png``). Equality-key matches outrank case-only matches, letting
    legacy case-only pairs each update in place. Equality keys distinguish *name* itself. *exclude* removes
    same-equality-key entries for self-ignoring rename checks. Malformed tables count as empty."""
    if not isinstance(table, Mapping):
        return None
    excluded = asset_name_comparison_key(exclude) if exclude is not None else None
    key = resolve_asset_key(table, name)
    if key is not None and asset_name_comparison_key(key) != excluded:
        return key
    target = asset_name_namespace_key(name)
    for key in table:
        if (
            isinstance(key, str)
            and asset_name_namespace_key(key) == target
            and asset_name_comparison_key(key) != excluded
        ):
            return key
    return None


def normalize_agent_derivatives(raw: object, *, spec: AssetSpec, field_path: str) -> dict[str, dict[str, Any]]:
    """Normalize Agent-write derivative tables to persisted shape, failing loudly on invalid input.

    Agents write change descriptions only; generation writes sheets. Construct empty sheet fields,
    retaining existing paths via :func:`merge_agent_derivatives`. Apply asset-name validation; strip/NFC
    equivalent keys would silently overwrite, so reject whole tables rather than choosing one.

    *field_path* locates the table in errors, constructed from caller-specific input shapes."""

    if not isinstance(raw, Mapping):
        raise ValueError(f"{field_path}.{DERIVATIVES_FIELD} 必须是对象（衍生名 → {{description}}）")
    normalized: dict[str, dict[str, Any]] = {}
    raw_keys: dict[str, object] = {}
    for raw_name, attrs in raw.items():
        try:
            name = validate_asset_name(raw_name)
        except ValueError as exc:
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}: {exc}") from None
        if name in normalized:
            raise ValueError(
                f"{field_path}.{DERIVATIVES_FIELD} 含规范化后冲突的衍生名 {name!r}："
                f"原始键 {raw_keys[name]!r} 与 {raw_name!r} 在规范化（strip + NFC）后等价"
            )
        if not isinstance(attrs, Mapping):
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}[{name!r}] 必须是对象")
        extra = sorted(str(key) for key in attrs if key != "description")
        if extra:
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}[{name!r}] 含不可写字段: {extra}")
        description = attrs.get("description")
        if not isinstance(description, str):
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}[{name!r}].description 必须是字符串")
        normalized[name] = {"description": description, spec.sheet_field: ""}
        raw_keys[name] = raw_name
    return normalized


def merge_agent_derivatives(entry: dict[str, Any], normalized: Mapping[str, dict[str, Any]]) -> None:
    """Merge normalized derivatives by name: change existing descriptions, add new entries whole, preserve unmentioned entries.

    Whole replacement would erase unmentioned sheets. Existing NFD keys update in place through
    :func:`find_derivative_name` rather than adding NFC duplicates. Reject case-only new-name collisions with ``ValueError``."""

    table = ensure_derivative_table(entry)
    for name, derivative in normalized.items():
        key = find_derivative_name(table, name)
        if key is not None and asset_name_comparison_key(key) != asset_name_comparison_key(name):
            raise ValueError(f"衍生名 {name!r} 与已有衍生 {key!r} 冲突（按 strip + Unicode NFC、不区分大小写判定）")
        current = table.get(key) if key is not None else None
        if key is not None and isinstance(current, dict):
            current["description"] = derivative["description"]
        else:
            table[key if key is not None else name] = dict(derivative)


@dataclass(frozen=True, slots=True)
class DerivativeSheetTarget:
    """Derivative-sheet write coordinates, both actual persisted names with NFC/NFD resolved."""

    owner_key: str
    derivative_key: str

    @property
    def artifact_id(self) -> str:
        return derivative_artifact_id(self.owner_key, self.derivative_key)

    @property
    def sheet_path(self) -> str:
        return derivative_sheet_relative_path(self.owner_key, self.derivative_key)


@dataclass(frozen=True, slots=True)
class DerivativeSheetSource:
    """All inputs for one derivative generation, resolved by actual persisted names."""

    target: DerivativeSheetTarget
    description: str
    owner_sheet_path: str

    @property
    def owner_key(self) -> str:
        return self.target.owner_key

    @property
    def derivative_key(self) -> str:
        return self.target.derivative_key


def resolve_derivative_target(
    project: Mapping[str, Any],
    owner_name: str,
    derivative_name: str,
) -> DerivativeSheetTarget:
    """Resolve two names in either encoding to actual persisted coordinates.

    Missing bases raise ``KeyError``, like base-asset addressing; missing derivatives raise 404."""
    spec = ASSET_SPECS[DERIVATIVE_ASSET_TYPE]
    bucket = project.get(spec.bucket_key)
    owner_key = resolve_asset_key(bucket, owner_name)
    entry = bucket.get(owner_key) if isinstance(bucket, Mapping) and owner_key is not None else None
    if owner_key is None or not isinstance(entry, Mapping):
        raise KeyError(f"{spec.label_zh} '{owner_name}' 不存在")
    table = derivative_table(entry)
    derivative_key = resolve_asset_key(table, derivative_name)
    if derivative_key is None or not isinstance(table.get(derivative_key), Mapping):
        raise NotFoundError("asset_derivative_not_found", name=derivative_name)
    return DerivativeSheetTarget(owner_key=owner_key, derivative_key=derivative_key)


def resolve_derivative_sheet_source(
    project: Mapping[str, Any],
    owner_name: str,
    derivative_name: str,
) -> DerivativeSheetSource:
    """Resolve generation inputs, rejecting every nongeneratable shape here.

    Missing bases raise ``KeyError`` as base addressing does; missing derivatives raise 404. Missing base
    sheets or unwritten change descriptions raise 400 as user-repairable prerequisites, with separate
    guidance pointing to different actions."""
    spec = ASSET_SPECS[DERIVATIVE_ASSET_TYPE]
    target = resolve_derivative_target(project, owner_name, derivative_name)
    bucket = project[spec.bucket_key]
    entry = bucket[target.owner_key]
    derivative = derivative_table(entry)[target.derivative_key]

    description = derivative.get("description")
    if not isinstance(description, str) or not description.strip():
        raise BadRequestError("derivative_description_required", name=target.derivative_key)

    owner_sheet = entry.get(spec.sheet_field)
    if not isinstance(owner_sheet, str) or not owner_sheet:
        raise BadRequestError("derivative_owner_sheet_missing", name=target.owner_key)

    return DerivativeSheetSource(
        target=target,
        description=description.strip(),
        owner_sheet_path=owner_sheet,
    )


def build_derivative_sheet_basis(
    *,
    owner_name: str,
    derivative_name: str,
    description: str,
    aspect_ratio: str,
    source: VisualReference | None,
    absent: Sequence[InputGap] = (),
) -> ArtifactBasis:
    """Canonical derivative basis: change description + base-sheet content, excluding project style.

    Base sheets carry style themselves; derivatives merely edit them, without injecting project style in
    instructions, like image edits. Planning/execution share this constructor, so regenerated base sheets
    or edited changes mismatch canonical basis and make derivatives stale.

    ``absent`` records generation gaps (``docs/adr/0098``): unavailable bases have no ``source`` and change
    descriptions may be empty; accept both only with recorded gaps."""
    if source is None and not absent:
        raise ValueError("a derivative sheet basis without its owner sheet must record the gap")
    return build_asset_sheet_visual_basis(
        asset_type=DERIVATIVE_ASSET_TYPE,
        asset_id=derivative_artifact_id(owner_name, derivative_name),
        description=description,
        style="",
        style_description="",
        aspect_ratio=aspect_ratio,
        references=(source,) if source is not None else (),
        absent=absent,
    )
