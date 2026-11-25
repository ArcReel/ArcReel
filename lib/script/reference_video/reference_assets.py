"""视频单元参考图候选展开与文件可用性水合（纯资产解析，不依赖配置解析与运行时）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lib.backends.input_limits import InputLimit
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.material.work_files import material_file_present
from lib.project.asset_types import AssetSpec, asset_name_comparison_key
from lib.references.reference_admission import SHEET_REQUIRED_ASSET_TYPES
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.reference_video.text_parser import derive_references_from_text
from lib.script.script_models import ReferenceResource, ResolvedReferenceAsset


class ReferenceAssetAvailability(Protocol):
    """资产可用性适配器；生产实现检查项目内文件并按产物清单认领，测试可用内存替身。"""

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class ReferenceAssetHydration:
    available: tuple[ResolvedReferenceAsset, ...]
    missing: tuple[ReferenceResource, ...]
    unreadable_files: tuple[str, ...]


def _asset_key(asset: ResolvedReferenceAsset) -> tuple[str, str]:
    return asset.reference.type, asset_name_comparison_key(asset.reference.name)


def hydrate_reference_assets(
    declared: Sequence[ReferenceResource],
    resolved_assets: Sequence[ResolvedReferenceAsset],
    availability: ReferenceAssetAvailability,
) -> ReferenceAssetHydration:
    """把候选按声明范围过滤并给出实际可用图片与缺图逻辑引用。"""

    declared_keys = {(ref.type, asset_name_comparison_key(ref.name)) for ref in declared}
    candidates = [asset for asset in resolved_assets if _asset_key(asset) in declared_keys]
    available: list[ResolvedReferenceAsset] = []
    unavailable: list[ResolvedReferenceAsset] = []
    try:
        for asset in candidates:
            (available if availability.is_available(asset) else unavailable).append(asset)
    except (OSError, ValueError) as exc:
        cause = exc if isinstance(exc, OSError) else exc.__cause__
        paths = [asset.path for asset in candidates]
        if not isinstance(cause, OSError) or not cause.filename or Path(cause.filename) not in paths:
            raise
        from arcreel_market_core.video_backend_contract import VideoCapabilityError
        from lib.backends.backend_runtime import read_video_image_slots

        names = Path(cause.filename).name
        try:
            read_video_image_slots(None, None, paths, "")
        except VideoCapabilityError as failure:
            names = failure.params["names"]
        return ReferenceAssetHydration(available=(), missing=(), unreadable_files=(names,))
    available_keys = {_asset_key(asset) for asset in available}
    missing = tuple(ref for ref in declared if (ref.type, asset_name_comparison_key(ref.name)) not in available_keys)
    return ReferenceAssetHydration(
        available=tuple(available),
        missing=missing,
        unreadable_files=tuple(asset.path.name for asset in unavailable if _asset_key(asset) in available_keys),
    )


class FilesystemReferenceAssets:
    """以项目目录为边界检查图片候选实际存在且为普通文件。"""

    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        try:
            path = safe_join(self._project_path, asset.path)
        except (OSError, PathTraversalError, TypeError):
            return False
        return material_file_present(path)


def _candidate_path(project_path: Path, value: object) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    try:
        return safe_join(project_path, value)
    except (OSError, PathTraversalError, TypeError):
        return None


class MemoizedReferenceAssets:
    """一批读侧计算内按图片记住可用性：成本随设计图数量增长，而不是随被引用次数增长。"""

    def __init__(self, inner: ReferenceAssetAvailability) -> None:
        self._inner = inner
        self._known: dict[tuple[Path, str, str, str], bool] = {}

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        key = (asset.path, asset.kind, asset.reference.type, asset.reference.name)
        known = self._known.get(key)
        if known is None:
            known = self._known[key] = self._inner.is_available(asset)
        return known


class ReferenceExpansionCache:
    """一次读侧批量计算内共用的引用目录与候选路径解析。"""

    def __init__(self, project: dict, project_path: Path) -> None:
        self.catalog = build_reference_catalog(project)
        self._project_path = project_path
        self._paths: dict[str, Path | None] = {}

    def candidate_path(self, value: object) -> Path | None:
        if not isinstance(value, str):
            return _candidate_path(self._project_path, value)
        if value not in self._paths:
            self._paths[value] = _candidate_path(self._project_path, value)
        return self._paths[value]


def unit_text(unit: dict) -> str:
    raw_text = unit.get("text")
    return raw_text if isinstance(raw_text, str) else ""


def unit_reference_declarations(
    project: dict, unit: dict, *, catalog: ReferenceCatalog | None = None
) -> tuple[ReferenceResource, ...]:
    """视频单元正文 → 该单元生成所用的逻辑参考图引用，按首次提及顺序。"""

    references, _missing = derive_references_from_text(unit_text(unit), project, catalog=catalog)
    return tuple(references)


def _original_image_paths(entry: dict, spec: AssetSpec) -> list[object]:
    paths: list[object] = []
    for field_name in spec.original_image_fields:
        value = entry.get(field_name)
        if isinstance(value, list):
            paths.extend(value)
        elif value:
            paths.append(value)
    return paths


def resolve_reference_assets(
    project: dict,
    project_path: Path,
    unit: dict,
    *,
    expansion: ReferenceExpansionCache | None = None,
) -> tuple[ResolvedReferenceAsset, ...]:
    """把正文派生的逻辑引用展开为图片候选，不把「路径已登记」误当成「文件存在」。"""

    if expansion is None:
        expansion = ReferenceExpansionCache(project, project_path)
    catalog = expansion.catalog
    result: list[ResolvedReferenceAsset] = []
    for reference in unit_reference_declarations(project, unit, catalog=catalog):
        catalog_entry = catalog.lookup(reference.type, reference.name)
        if catalog_entry is None:
            continue
        entry = catalog_entry.asset
        if not isinstance(entry, dict):
            continue
        spec = catalog_entry.spec
        sheet = expansion.candidate_path(entry.get(spec.sheet_field))
        if sheet is not None:
            result.append(ResolvedReferenceAsset(path=sheet, reference=reference, kind="sheet"))
            continue
        if catalog_entry.asset_type in SHEET_REQUIRED_ASSET_TYPES:
            continue
        for raw_path in _original_image_paths(entry, spec):
            original = expansion.candidate_path(raw_path)
            if original is not None:
                result.append(ResolvedReferenceAsset(path=original, reference=reference, kind="original"))
    return tuple(result)


def clamp_reference_assets(
    assets: Sequence[ResolvedReferenceAsset], max_references: int | None
) -> tuple[ResolvedReferenceAsset, ...]:
    """按历史成片记录的上限重建其产物依据；新提交始终使用完整参考集。"""

    bound = InputLimit.from_json(max_references).clamp_bound()
    if bound is None or len(assets) <= bound:
        return tuple(assets)
    return tuple(assets[:bound])
