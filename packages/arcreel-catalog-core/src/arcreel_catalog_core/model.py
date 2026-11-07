"""Types for parsed catalog data.

Catalog data contains a dataset version, catalog entries, and built-in provider listings. Entries describe facts about models themselves, identified by
"model maker/official model name", with dual-media entries further distinguished by media type; built-in provider listings contain each built-in provider's
officially listed provider models and defaults by media type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from .vocabulary import CURRENCIES, DIMENSIONS, METERS, MediaType


@dataclass(frozen=True)
class TextFacts:
    image_input: bool = False
    structured_output: bool = False
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ImageFacts:
    text_to_image: bool = False
    image_to_image: bool = False
    resolutions: tuple[str, ...] = ()
    max_reference_images: int | None = None
    output_token_bases: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DurationConstraints:
    """Conditional narrowing of supported durations: allowed durations for a specified resolution or when reference images are used."""

    by_resolution: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    with_reference_images: tuple[int, ...] = ()


@dataclass(frozen=True)
class VideoFacts:
    durations: tuple[int, ...] = ()
    resolutions: tuple[str, ...] = ()
    duration_constraints: DurationConstraints = field(default_factory=DurationConstraints)
    max_reference_images: int | None = None
    text_to_video: bool | None = None
    first_frame: bool | None = None
    last_frame: bool | None = None
    reference_audio: bool | None = None
    max_reference_audio_count: int | None = None
    max_reference_audio_total_seconds: float | None = None


@dataclass(frozen=True)
class AudioFacts:
    pass


ModelFacts = TextFacts | ImageFacts | VideoFacts | AudioFacts


@dataclass(frozen=True)
class PriceRow:
    """One condition → unit price row. Selection dimensions absent from ``when`` allow any value."""

    when: Mapping[str, object]
    price: float


@dataclass(frozen=True)
class Meter:
    """One price-table meter, with tiered unit prices over selection dimensions listed in ``dims``.

    ``defaults`` supplies missing request dimension values; ``free_allowance`` is the free allowance per request,
    in the meter's counting units; ``cjk_double`` applies only to character counts and counts each Han character as two.
    """

    meter: str
    dims: tuple[str, ...]
    rows: tuple[PriceRow, ...]
    defaults: Mapping[str, object] = field(default_factory=dict)
    free_allowance: float | None = None
    cjk_double: bool = False


@dataclass(frozen=True)
class PriceTable:
    """Official catalog entry pricing: single-currency, composed of meters."""

    currency: str
    meters: tuple[Meter, ...]

    @property
    def unrecognized_keys(self) -> tuple[str, ...]:
        """Currencies, meters, and selection dimensions unknown to the current code, deduplicated in order of appearance."""
        keys: list[str] = []
        if self.currency not in CURRENCIES:
            keys.append(self.currency)
        for meter in self.meters:
            if meter.meter not in METERS:
                keys.append(meter.meter)
            keys.extend(dim for dim in meter.dims if dim not in DIMENSIONS)
        return tuple(dict.fromkeys(keys))

    @property
    def is_priceable(self) -> bool:
        """When False, the price table depends on content unknown to the current code, and calls priced with it are recorded as unpriced."""
        return not self.unrecognized_keys


@dataclass(frozen=True)
class EntryKey:
    maker: str
    name: str
    media_type: MediaType

    @property
    def ref(self) -> str:
        """The "model maker/official model name" key used by built-in provider listings to reference entries."""
        return f"{self.maker}/{self.name}"


@dataclass(frozen=True)
class CatalogEntry:
    """One catalog entry.

    Lifecycle belongs to the entry and represents retirement at the model-maker level, shared by providers: ``release_date`` is the launch date,
    ``shutdown_date`` is the shutdown date, both at day precision and evaluated in UTC; the model is considered discontinued on shutdown day. ``successor``
    is the maker's official successor, referencing a same-media-type entry by "model maker/official model name". ``discontinued``
    means offline; catalog builds set it when shutdown is no later than build day, letting older instances without shutdown-date support recognize discontinued models.
    """

    maker: str
    name: str
    media_type: MediaType
    display_name: str
    facts: ModelFacts
    aliases: tuple[str, ...] = ()
    discontinued: bool = False
    context_length: int | None = None
    price: PriceTable | None = None
    release_date: date | None = None
    shutdown_date: date | None = None
    successor: str | None = None

    @property
    def key(self) -> EntryKey:
        return EntryKey(self.maker, self.name, self.media_type)


@dataclass(frozen=True)
class BuiltinProviderModel:
    """One officially listed provider model of a built-in provider.

    ``entry`` is "model maker/official model name"; ``media_type`` is needed only to disambiguate dual-media model entries.
    ``multiplier`` is the provider model multiplier; app versions below ``min_app_version`` leave it disabled and show the required version in settings. ``endpoint`` is its attached
    built-in endpoint, chosen from that provider's options for the entry media type (see ``endpoints.BUILTIN_PROVIDER_ENDPOINTS``);
    absence means undeclared. Within a provider, models are distinguished by API model name and the referenced entry's media type.
    """

    api_model_name: str
    entry: str
    media_type: str | None = None
    multiplier: float = 1.0
    min_app_version: str | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class BuiltinProviderListing:
    """One built-in provider listing. ``defaults`` gives the default API model name for each media type."""

    models: tuple[BuiltinProviderModel, ...]
    defaults: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogData:
    dataset_version: str
    entries: tuple[CatalogEntry, ...]
    builtin_providers: Mapping[str, BuiltinProviderListing]

    def entry_of(self, model: BuiltinProviderModel) -> CatalogEntry | None:
        """Entry referenced by a provider model; None when missing or ambiguous (dual-media models without a media type)."""
        matches = matching_entries(self.entries, model.entry, model.media_type)
        return matches[0] if len(matches) == 1 else None


def matching_entries(entries: tuple[CatalogEntry, ...], ref: str, media_type: str | None) -> list[CatalogEntry]:
    """Find entries by "model maker/official model name" and optional media type, keeping only the first for each identity."""
    maker, _, name = ref.partition("/")
    found: dict[EntryKey, CatalogEntry] = {}
    for entry in entries:
        if entry.maker == maker and entry.name == name and media_type in (None, entry.media_type):
            found.setdefault(entry.key, entry)
    return list(found.values())
