"""Generation reference admission: block every unregistered reference name and character/scene/prop without an asset sheet.

Single and batch admission for reference-video, storyboard-image, grid-image, and image-to-video
routes share these decisions (``docs/adr/0073``). Video generation is the most expensive generation
action, requiring one admission conclusion. Separate entry-point decisions would let batch previews
admit targets rejected by single submissions, or vice versa, showing opposite answers for the
same data in two panels.

Two blocking axes:

- **Unregistered references**: body ``@[名称]`` or storyboard ``characters_in_*`` / ``scenes`` /
  ``props`` / ``products_in_shot`` names absent from the reference catalog. Leftovers after asset
  deletion and never-registered names take the same path; neither is silently dropped.
- **Missing sheets**: registered characters/scenes/props without asset sheets. Original images only
  feed sheet generation, not storyboard/video requests. Products are the exception: their original
  images anchor fidelity acceptance (``docs/adr/0034``).

Editing/save paths do not consume this module: authors need not create assets before writing,
and those paths still emit warnings only (``docs/adr/0064``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from lib.project.asset_types import ASSET_SPECS
from lib.references.reference_catalog import ReferenceCatalog

#: A name absent from the catalog. ``reference_asset_missing`` means an existing asset lacks an available image;
#: absence of the asset itself needs a different repair (register it or edit text, versus generate a sheet), hence a separate code.
UNREGISTERED_REFERENCE_CODE = "reference_asset_unregistered"

#: Registered but lacking a sheet; reuse the reference-material-missing failure-code family.
SHEET_MISSING_CODE = "reference_asset_missing"

#: Asset types requiring sheets for admission. Products are excluded: without sheets, originals go straight downstream as references,
#: anchoring fidelity acceptance (``docs/adr/0034``).
SHEET_REQUIRED_ASSET_TYPES: frozenset[str] = frozenset({"character", "scene", "prop"})


def _missing_text(entries: Iterable[tuple[str, str]]) -> str:
    return ", ".join(f"{asset_type}: {name}" for asset_type, name in entries)


@dataclass(frozen=True)
class ReferenceAdmission:
    """Reference admission outcome for one generation request.

    Keep both axes separate rather than reducing them to one boolean: failure codes and repair
    actions differ. Callers must distinguish unregistered names from assets lacking sheets,
    rather than showing users one ambiguous combined message.
    """

    #: Reference names absent from the catalog, in first-occurrence order.
    unregistered: tuple[str, ...] = ()
    #: Registered references lacking sheets, ``(资产类型, 名称)``, in first-occurrence order.
    without_sheet: tuple[tuple[str, str], ...] = ()

    @property
    def admitted(self) -> bool:
        """Admit only when both axes have no gaps."""
        return not self.unregistered and not self.without_sheet

    def unregistered_text(self) -> str:
        """Display unregistered names individually: these exact characters are what users must fix."""
        return ", ".join(self.unregistered)

    def without_sheet_text(self) -> str:
        """Display missing-sheet references with type prefixes, matching ``reference_asset_missing`` shape."""
        return _missing_text(self.without_sheet)


def _entry_has_sheet(asset: object, sheet_field: str) -> bool:
    """Whether the asset entry has a registered sheet path.

    Check registration only, never the filesystem. Each route judges file readability itself
    (reference-to-video through ``ReferenceAssetAvailability``, storyboard routes through assembly
    existence checks); duplicate admission I/O would produce two conclusions for the same data.
    Malformed nondict entries count as missing sheets; ``lib.project.data_validator.DataValidator``
    reports their structural errors separately.
    """
    if not isinstance(asset, dict):
        return False
    sheet = asset.get(sheet_field)
    return isinstance(sheet, str) and bool(sheet)


def admit_references(
    catalog: ReferenceCatalog,
    *,
    references: Iterable[tuple[str, str]],
    unregistered: Iterable[str] = (),
) -> ReferenceAdmission:
    """Admit a set of already-attributed references against the catalog.

    ``references`` contains ``(资产类型, 引用名)`` derived by callers from their reference carriers:
    body text through ``derive_references_from_text``, storyboards through :func:`admit_storyboard_item`.
    ``unregistered`` contains names already judged unregistered during body derivation; attribution
    was settled then and is not repeated here.

    Deduplicate both axes while preserving order: users fix names one by one, and shifting error
    order would make successive submissions appear to have changed content.
    """

    unregistered_names: list[str] = []
    seen_unregistered: set[str] = set()
    for name in unregistered:
        if name in seen_unregistered:
            continue
        seen_unregistered.add(name)
        unregistered_names.append(name)

    without_sheet: list[tuple[str, str]] = []
    seen_refs: set[tuple[str, str]] = set()
    for asset_type, name in references:
        entry = catalog.lookup(asset_type, name)
        if entry is None:
            if name not in seen_unregistered:
                seen_unregistered.add(name)
                unregistered_names.append(name)
            continue
        if asset_type not in SHEET_REQUIRED_ASSET_TYPES:
            continue
        key = (entry.asset_type, entry.name)
        if key in seen_refs:
            continue
        seen_refs.add(key)
        if not _entry_has_sheet(entry.asset, entry.spec.sheet_field):
            without_sheet.append(key)

    return ReferenceAdmission(unregistered=tuple(unregistered_names), without_sheet=tuple(without_sheet))


def _item_reference_names(item: Mapping[str, object]) -> list[tuple[str, str]]:
    """Storyboard reference fields → ``(资产类型, 名称)``, in ``ASSET_SPECS`` declaration order.

    Entries come from on-disk script JSON. Skip nonlist fields/nonstring elements without raising:
    ``lib.project.data_validator`` owns structural validation; admission must not turn dirty scripts into 500 errors.
    """
    names: list[tuple[str, str]] = []
    for spec in ASSET_SPECS.values():
        for field in spec.reference_list_fields:
            raw = item.get(field)
            if not isinstance(raw, (list, tuple)):
                continue
            names.extend((spec.asset_type, name) for name in raw if isinstance(name, str) and name)
    return names


def admit_storyboard_item(catalog: ReferenceCatalog, item: object) -> ReferenceAdmission:
    """Admit one storyboard entry’s references, shared by storyboard images, grids, and image-to-video.

    Field names constrain types (``scenes`` means scenes), so use :meth:`ReferenceCatalog.lookup`
    instead of cross-type attribution. Equal names across types must not replace “unregistered scene”
    with “a same-named character exists, so registered”.
    """
    if not isinstance(item, Mapping):
        return ReferenceAdmission()
    return admit_references(catalog, references=_item_reference_names(item))


def admit_storyboard_items(catalog: ReferenceCatalog, items: Iterable[object]) -> ReferenceAdmission:
    """Combined batch storyboard admission, merging/deduplicating gaps in first-occurrence order.

    Grid generation groups storyboard entries into one request. Reporting gaps one entry at a time
    requires repeated submissions; one combined list lets users repair everything at once.
    """
    unregistered: list[str] = []
    without_sheet: list[tuple[str, str]] = []
    for item in items:
        admission = admit_storyboard_item(catalog, item)
        unregistered.extend(name for name in admission.unregistered if name not in unregistered)
        without_sheet.extend(entry for entry in admission.without_sheet if entry not in without_sheet)
    return ReferenceAdmission(unregistered=tuple(unregistered), without_sheet=tuple(without_sheet))
