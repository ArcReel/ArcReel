"""Reference catalog: the sole constructor of registered project reference names.

Body syntax ``@[名称]`` (:mod:`lib.script.reference_video.text_parser`) and storyboard
``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot`` fields name registered
references: asset names or character ``本体名/衍生名`` (``docs/adr/0072``). Data/draft validation,
body-reference attribution, and reference-image projection all ask the same questions: is a name
registered, which asset type owns it, and which asset entry carries it?

All consumers must use :class:`ReferenceCatalog` built here from project data, never independently
assemble name sets from project.json. Only :func:`build_reference_catalog` defines the namespace
and equality coordinate system; scattered assembly would produce different answers for identical data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    asset_name_comparison_key,
    normalize_asset_bucket,
)

#: Attribution priority resolves one body-reference owner when the same name is registered in several asset tables.
#:
#: New projects share one namespace across all four asset types (:func:`lib.project.asset_types.ensure_project_asset_namespace`);
#: same names can only come from older projects predating that gate. Fixed priority makes their attribution reproducible,
#: never dependent on dict iteration order. This differs from ``AssetSpec.namespace_priority``:
#: that determines which entry is existing in collision reports; this determines the reference’s asset type.
REFERENCE_ATTRIBUTION_ORDER: tuple[str, ...] = ("product", "character", "scene", "prop")

#: Separator between base and derivative reference names (``docs/adr/0072``). Both names prohibit it
#: (:data:`lib.project.asset_types.ILLEGAL_ASSET_NAME_CHARS`), making ``本体名/衍生名`` splitting unambiguous.
DERIVATIVE_SEPARATOR = "/"


def split_derivative_reference(name: str) -> tuple[str, str]:
    """Split a reference name into ``(本体名, 衍生名)``; nonderivatives get an empty derivative name.

    Split syntax only; :class:`ReferenceCatalog` decides whether 张三 has the derivative in
    ``@[张三/劲装]``. Separators are illegal in both names, so split at the first; additional
    separators (``甲/乙/丙``) leave illegal characters in the derivative, guaranteeing lookup
    failure just like misspelled names.
    """
    base, separator, derivative = name.partition(DERIVATIVE_SEPARATOR)
    return (base, derivative) if separator else (name, "")


def derivative_reference(base_name: str, derivative_name: str) -> str:
    """Join base/derivative names into reference name ``本体名/衍生名``."""
    return f"{base_name}{DERIVATIVE_SEPARATOR}{derivative_name}"


def renamed_reference(name: str, old_name: str, new_name: str) -> str | None:
    """Rewrite a reference name for one asset rename, or return ``None`` when unrelated.

    This is the sole name-level cascading-rename decision: body mentions (``@[名称]``) and
    reference arrays/speaker fields carry the same names, so both rewrite paths share this
    function rather than independently deciding old-name matches.

    When *old_name* is a base name, rewrite all its derivative references too
    (``旧角色/衍生`` → ``新角色/衍生``): derivatives share base names, so old names must not linger
    as derivative forms. When *old_name* itself is ``角色/衍生`` (derivative rename), match only
    that exact form, leaving the character’s other references untouched.
    """
    key = asset_name_comparison_key(name)
    target = asset_name_comparison_key(old_name)
    if key == target:
        return new_name
    base, derivative = split_derivative_reference(key)
    if derivative and base == target and not split_derivative_reference(target)[1]:
        return derivative_reference(new_name, derivative)
    return None


@dataclass(frozen=True)
class ReferenceCatalogEntry:
    """One registered reference in the catalog."""

    #: Owning asset type (key in ``ASSET_SPECS``).
    asset_type: str
    #: Name usable in body/reference fields, in trimmed + Unicode NFC comparison coordinates.
    name: str
    #: Carrying asset-table entry name, in comparison coordinates; derivative references (``本体名/衍生名``) use the base name,
    #: diverging from :attr:`name`: speakers bind the base asset while references identify the derivative appearance.
    asset_name: str
    #: Persisted entry for this name: base references use asset-table entries; derivatives use their entry within the base.
    #: Both share the sheet field (derivatives reuse base spec ``sheet_field``), so reading by reference returns
    #: that form’s own sheet. Externally corrupted project.json may contain nondicts; readers check types as needed.
    asset: object

    @property
    def spec(self) -> AssetSpec:
        """Asset specifications for the owning type (sheet field, original-image field, subdirectory, etc.)."""
        return ASSET_SPECS[self.asset_type]


class ReferenceCatalog:
    """All currently referenceable names in one project payload, grouped by type and resolved by attribution priority.

    Construct only through :func:`build_reference_catalog`. Queries always take unnormalized names;
    normalization happens internally. Call sites neither need nor should know whether asset tables
    persist NFC or NFD.
    """

    def __init__(self, assets_by_type: Mapping[str, Mapping[str, Any]]) -> None:
        self._by_type: dict[str, dict[str, ReferenceCatalogEntry]] = {
            asset_type: self._type_entries(asset_type, bucket) for asset_type, bucket in assets_by_type.items()
        }
        self._attributed: dict[str, ReferenceCatalogEntry] = {}
        for asset_type in REFERENCE_ATTRIBUTION_ORDER:
            for name, entry in self._by_type[asset_type].items():
                self._attributed.setdefault(name, entry)

    @staticmethod
    def _type_entries(asset_type: str, bucket: Mapping[str, Any]) -> dict[str, ReferenceCatalogEntry]:
        """All referenceable names for this type: asset-entry names plus ``本体名/衍生名`` when derivatives are enabled.

        Define reference namespace = asset names ∪ character/derivative names only here
        (``docs/adr/0072``). Data/draft validation, body attribution, and reference-image projection
        all obtain names from the catalog; nowhere else needs to know derivatives are referenceable.
        """
        entries: dict[str, ReferenceCatalogEntry] = {}
        supports_derivatives = ASSET_SPECS[asset_type].supports_derivatives
        for name, asset in bucket.items():
            entries[name] = ReferenceCatalogEntry(asset_type=asset_type, name=name, asset_name=name, asset=asset)
            if not supports_derivatives or not isinstance(asset, Mapping):
                continue
            for derivative_name, derivative in _reference_bucket(asset.get(DERIVATIVES_FIELD)).items():
                reference = derivative_reference(name, derivative_name)
                entries[reference] = ReferenceCatalogEntry(
                    asset_type=asset_type,
                    name=reference,
                    asset_name=name,
                    asset=derivative,
                )
        return entries

    def resolve(self, name: str) -> ReferenceCatalogEntry | None:
        """Attribute a reference name to exactly one asset type; return ``None`` if unregistered.

        Resolve same-named entries by :data:`REFERENCE_ATTRIBUTION_ORDER`.
        """
        return self._attributed.get(asset_name_comparison_key(name))

    def lookup(self, asset_type: str, name: str) -> ReferenceCatalogEntry | None:
        """Look up a name within one type; return ``None`` if that type has not registered it.

        Unlike :meth:`resolve`, never attribute across types. Use when callers know the type
        (field names constrain it, or projections carry derived types), so cross-type same names
        cannot silently change the answer to another type.
        """
        return self._by_type[asset_type].get(asset_name_comparison_key(name))

    def reference_names(self, asset_type: str) -> frozenset[str]:
        """All names referenceable by bodies/reference fields for this type, in comparison coordinates.

        Never collapse equal names across types: whether this field’s name is registered concerns
        only this type, independent of same-named entries in other tables.
        """
        return frozenset(self._by_type[asset_type])

    def asset_names(self, asset_type: str) -> frozenset[str]:
        """Registered asset-table entry names for this type, in comparison coordinates.

        Unlike :meth:`reference_names`, this asks whether an asset exists, not whether a name is
        referenceable: speaker slots bind reference audio/voices and must identify real asset entries.
        """
        return frozenset(entry.asset_name for entry in self._by_type[asset_type].values())


def build_reference_catalog(project: object) -> ReferenceCatalog:
    """Build the catalog from project.json payload, the sole reference-namespace construction entry point.

    Normalize asset-table keys here into trimmed + NFC comparison coordinates. Persisted NFC/NFD
    forms both remain valid without migration; equality requires matching forms on both sides.
    Malformed payloads (whole payload or individual tables not dicts) count as empty tables;
    :class:`lib.project.data_validator.DataValidator` reports structure errors separately. Catalog
    construction neither duplicates diagnostics nor raises exceptions.
    """
    payload: dict[str, Any] = project if isinstance(project, dict) else {}
    return ReferenceCatalog(
        {spec.asset_type: _reference_bucket(payload.get(spec.bucket_key)) for spec in ASSET_SPECS.values()}
    )


def _reference_bucket(bucket: object) -> dict[str, Any]:
    return {asset_name_comparison_key(name): asset for name, asset in normalize_asset_bucket(bucket).items()}
