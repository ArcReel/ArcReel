"""Catalog data reading and validation, sharing one traversal between instance and build usage.

- Instance reads are permissive (``parse_*``): unknown fields are ignored; price tables with unknown meters, selection dimensions, or currencies are still
  loaded and flagged by ``PriceTable.is_priceable``; entries with unknown media types are skipped; only unparseable structure rejects
  the entire dataset. There is no schema version.
- Build checks are strict (``check_*``): in addition to instance-side rejection issues, they report unknown fields and values, duplicate entry identities,
  names conflicting between entries, listings referencing nonexistent entries, and similar issues; any issue prevents publication.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

from .endpoints import BUILTIN_ENDPOINTS, is_app_version, provider_endpoint_options
from .model import (
    AudioFacts,
    BuiltinProviderListing,
    BuiltinProviderModel,
    CatalogData,
    CatalogEntry,
    DurationConstraints,
    EntryKey,
    ImageFacts,
    Meter,
    ModelFacts,
    PriceRow,
    PriceTable,
    TextFacts,
    VideoFacts,
    matching_entries,
)
from .names import normalize_model_name
from .vocabulary import (
    CJK_DOUBLE_METER,
    CURRENCIES,
    DIMENSIONS,
    MEDIA_TYPES,
    METERS,
    MediaType,
    is_valid_dimension_value,
)

#: Dataset versions are fixed-length UTC timestamps, so lexical order equals chronological order.
DATASET_VERSION_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CONTEXT_TIER = "context_tier"


@dataclass(frozen=True)
class CatalogIssue:
    """One validation issue. ``path`` locates it within JSON, such as ``entries[3].price.meters[0]``; ``code`` is stable and comparable."""

    path: str
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.code}] {self.detail}"


class CatalogFormatError(ValueError):
    """The catalog data cannot be parsed and is rejected in its entirety."""

    def __init__(self, issues: Iterable[CatalogIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(str(issue) for issue in self.issues))


def parse_dataset_version(value: str) -> datetime:
    """Parse a dataset version, raising ``ValueError`` for invalid format. Use the result to compare dataset recency."""
    return datetime.strptime(value, DATASET_VERSION_FORMAT).replace(tzinfo=UTC)


_DATA_FIELDS = frozenset({"dataset_version", "entries", "builtin_providers"})
_ENTRY_FIELDS = frozenset(
    {
        "maker",
        "name",
        "media_type",
        "display_name",
        "aliases",
        "discontinued",
        "context_length",
        "facts",
        "price",
        "release_date",
        "shutdown_date",
        "successor",
    }
)
_FACT_FIELDS: Mapping[MediaType, frozenset[str]] = {
    "text": frozenset({"image_input", "structured_output", "max_output_tokens"}),
    "image": frozenset(
        {"text_to_image", "image_to_image", "resolutions", "max_reference_images", "output_token_bases"}
    ),
    "video": frozenset(
        {
            "durations",
            "resolutions",
            "duration_constraints",
            "max_reference_images",
            "text_to_video",
            "first_frame",
            "last_frame",
            "reference_audio",
            "max_reference_audio_count",
            "max_reference_audio_total_seconds",
        }
    ),
    "audio": frozenset(),
}
_CONSTRAINT_FIELDS = frozenset({"by_resolution", "with_reference_images"})
_PRICE_FIELDS = frozenset({"currency", "meters"})
_METER_FIELDS = frozenset({"meter", "dims", "rows", "defaults", "free_allowance", "cjk_double"})
_ROW_FIELDS = frozenset({"when", "price"})
_LISTING_FIELDS = frozenset({"models", "defaults"})
_PROVIDER_MODEL_FIELDS = frozenset(
    {"api_model_name", "entry", "media_type", "multiplier", "min_app_version", "endpoint"}
)


class CatalogReader:
    """Traverse raw JSON, recording issues while reading.

    ``fatal`` contains structural issues rejected by instances as well; ``strict`` contains build-only issues. On structural issues, readers
    record a fatal issue and return a same-type placeholder to continue traversal, allowing builds to report all issues together; any fatal issue invalidates
    the entire read result.
    """

    def __init__(self) -> None:
        self.fatal: list[CatalogIssue] = []
        self.strict: list[CatalogIssue] = []

    @property
    def issues(self) -> list[CatalogIssue]:
        return [*self.fatal, *self.strict]

    def reject(self, path: str, code: str, detail: str) -> None:
        self.fatal.append(CatalogIssue(path, code, detail))

    def flag(self, path: str, code: str, detail: str) -> None:
        self.strict.append(CatalogIssue(path, code, detail))

    # ---- Basic values ----

    def obj(self, value: object, path: str) -> dict[str, object]:
        if isinstance(value, dict):
            return cast(dict[str, object], value)
        self.reject(path, "not_object", "应为 JSON 对象")
        return {}

    def known_fields(self, raw: Mapping[str, object], path: str, allowed: frozenset[str]) -> None:
        for key in raw:
            if key not in allowed:
                self.flag(_at(path, key), "unknown_field", "不认识的字段")

    def text(self, raw: Mapping[str, object], key: str, path: str) -> str:
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
        self.reject(_at(path, key), "invalid_text", "应为非空字符串")
        return ""

    def optional_text(self, raw: Mapping[str, object], key: str, path: str) -> str | None:
        return None if raw.get(key) is None else self.text(raw, key, path)

    def flag_value(self, raw: Mapping[str, object], key: str, path: str) -> bool:
        value = raw.get(key)
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        self.reject(_at(path, key), "invalid_bool", "应为布尔值")
        return False

    def optional_date(self, raw: Mapping[str, object], key: str, path: str) -> date | None:
        """Day-precision date written as ``YYYY-MM-DD``."""
        value = raw.get(key)
        if value is None:
            return None
        if isinstance(value, str) and _ISO_DATE.fullmatch(value):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        self.reject(_at(path, key), "invalid_date", "应为 YYYY-MM-DD 格式的日期")
        return None

    def positive_int(self, raw: Mapping[str, object], key: str, path: str) -> int | None:
        value = raw.get(key)
        if value is None:
            return None
        if type(value) is int and value > 0:
            return value
        self.reject(_at(path, key), "invalid_positive_int", "应为正整数")
        return None

    def optional_flag(self, raw: Mapping[str, object], key: str, path: str) -> bool | None:
        return None if raw.get(key) is None else self.flag_value(raw, key, path)

    def nonnegative_int(self, raw: Mapping[str, object], key: str, path: str) -> int | None:
        value = raw.get(key)
        if value is None:
            return None
        if type(value) is int and value >= 0:
            return value
        self.reject(_at(path, key), "invalid_nonnegative_int", "应为非负整数")
        return None

    def nonnegative_seconds(self, raw: Mapping[str, object], key: str, path: str) -> float | None:
        """Finite nonnegative upper limit in seconds. Unlike prices, negative or non-finite limits cannot be compared, so instances reject them too."""
        value = raw.get(key)
        if value is None:
            return None
        seconds = _as_float(value)
        if seconds is not None and math.isfinite(seconds) and seconds >= 0:
            return seconds
        self.reject(_at(path, key), "invalid_nonnegative_number", "应为非负的有限数")
        return None

    def number(self, value: object, path: str) -> float:
        """Finite nonnegative number. Non-numbers are rejected; negative or non-finite values are reported only during builds."""
        result = _as_float(value)
        if result is None:
            self.reject(path, "invalid_number", "应为数字")
            return 0.0
        if not math.isfinite(result) or result < 0:
            self.flag(path, "negative_number", "应为非负的有限数")
        return result

    def items(self, value: object, path: str) -> list[object]:
        if value is None:
            return []
        if isinstance(value, list):
            return cast(list[object], value)
        self.reject(path, "not_array", "应为数组")
        return []

    def texts(self, value: object, path: str) -> tuple[str, ...]:
        result: list[str] = []
        for index, item in enumerate(self.items(value, path)):
            if isinstance(item, str) and item:
                if item in result:
                    self.flag(f"{path}[{index}]", "duplicate_value", "重复的取值")
                result.append(item)
            else:
                self.reject(f"{path}[{index}]", "invalid_text", "应为非空字符串")
        return tuple(result)

    def durations(self, value: object, path: str) -> tuple[int, ...]:
        result: list[int] = []
        for index, item in enumerate(self.items(value, path)):
            if type(item) is int and item > 0:
                if item in result:
                    self.flag(f"{path}[{index}]", "duplicate_value", "重复的取值")
                result.append(item)
            else:
                self.reject(f"{path}[{index}]", "invalid_positive_int", "应为正整数秒")
        return tuple(result)

    # ---- Catalog data ----

    def catalog(self, raw: object) -> CatalogData:
        data = self.obj(raw, "")
        self.known_fields(data, "", _DATA_FIELDS)
        dataset_version = self.dataset_version(data, "")
        entries = [
            entry
            for index, item in enumerate(self.items(data.get("entries"), "entries"))
            if (entry := self.entry(item, f"entries[{index}]")) is not None
        ]
        if "entries" not in data:
            self.reject("entries", "missing_field", "缺少条目列表")
        providers_raw = self.obj(data.get("builtin_providers", {}), "builtin_providers")
        providers = {
            provider_id: self.listing(listing, f"builtin_providers.{provider_id}")
            for provider_id, listing in providers_raw.items()
        }
        result = CatalogData(dataset_version, tuple(entries), providers)
        self.check_entry_identities(result.entries)
        self.check_successors(result.entries)
        self.check_listings(result)
        return result

    def dataset_version(self, raw: Mapping[str, object], path: str) -> str:
        value = self.text(raw, "dataset_version", path)
        try:
            parse_dataset_version(value)
        except ValueError:
            if value:
                self.reject(
                    _at(path, "dataset_version"),
                    "invalid_dataset_version",
                    f"应为 UTC 时间戳，格式 {DATASET_VERSION_FORMAT}",
                )
        return value

    def entry(self, raw: object, path: str) -> CatalogEntry | None:
        data = self.obj(raw, path)
        self.known_fields(data, path, _ENTRY_FIELDS)
        maker = self.text(data, "maker", path)
        if "/" in maker:
            self.reject(_at(path, "maker"), "invalid_maker", "模型厂商不能含 /")
        name = self.text(data, "name", path)
        media_type = self.text(data, "media_type", path)
        display_name = self.text(data, "display_name", path)
        aliases = self.texts(data.get("aliases"), _at(path, "aliases"))
        discontinued = self.flag_value(data, "discontinued", path)
        context_length = self.positive_int(data, "context_length", path)
        release_date = self.optional_date(data, "release_date", path)
        shutdown_date = self.optional_date(data, "shutdown_date", path)
        successor = self.optional_text(data, "successor", path)
        if media_type not in MEDIA_TYPES:
            if media_type:
                self.flag(_at(path, "media_type"), "unknown_media_type", "不认识的媒体类型")
            return None
        known_media_type = cast(MediaType, media_type)
        facts = self.facts(data.get("facts"), known_media_type, _at(path, "facts"))
        price = None
        if data.get("price") is not None:
            price = self.price_table(data["price"], known_media_type, _at(path, "price"))
            self.check_price_resolutions(price, facts, _at(path, "price"))
        return CatalogEntry(
            maker=maker,
            name=name,
            media_type=known_media_type,
            display_name=display_name,
            facts=facts,
            aliases=aliases,
            discontinued=discontinued,
            context_length=context_length,
            price=price,
            release_date=release_date,
            shutdown_date=shutdown_date,
            successor=successor,
        )

    def facts(self, raw: object, media_type: MediaType, path: str) -> ModelFacts:
        data = {} if raw is None else self.obj(raw, path)
        self.known_fields(data, path, _FACT_FIELDS[media_type])
        match media_type:
            case "text":
                return TextFacts(
                    image_input=self.flag_value(data, "image_input", path),
                    structured_output=self.flag_value(data, "structured_output", path),
                    max_output_tokens=self.positive_int(data, "max_output_tokens", path),
                )
            case "image":
                return ImageFacts(
                    text_to_image=self.flag_value(data, "text_to_image", path),
                    image_to_image=self.flag_value(data, "image_to_image", path),
                    max_reference_images=self.nonnegative_int(data, "max_reference_images", path),
                    output_token_bases=self.output_token_bases(
                        data.get("output_token_bases"), _at(path, "output_token_bases")
                    ),
                    resolutions=self.texts(data.get("resolutions"), _at(path, "resolutions")),
                )
            case "video":
                durations = self.durations(data.get("durations"), _at(path, "durations"))
                if not durations:
                    self.flag(_at(path, "durations"), "missing_durations", "视频条目必须给出可选时长")
                resolutions = self.texts(data.get("resolutions"), _at(path, "resolutions"))
                constraints = self.duration_constraints(
                    data.get("duration_constraints"), durations, resolutions, _at(path, "duration_constraints")
                )
                reference_audio = self.optional_flag(data, "reference_audio", path)
                audio_count = self.nonnegative_int(data, "max_reference_audio_count", path)
                if reference_audio is True and audio_count == 0:
                    self.flag(
                        _at(path, "max_reference_audio_count"),
                        "reference_audio_without_count",
                        "接受参考音频时参考音频数量上限不能为 0",
                    )
                return VideoFacts(
                    durations=durations,
                    resolutions=resolutions,
                    duration_constraints=constraints,
                    max_reference_images=self.nonnegative_int(data, "max_reference_images", path),
                    text_to_video=self.optional_flag(data, "text_to_video", path),
                    first_frame=self.optional_flag(data, "first_frame", path),
                    last_frame=self.optional_flag(data, "last_frame", path),
                    reference_audio=reference_audio,
                    max_reference_audio_count=audio_count,
                    max_reference_audio_total_seconds=self.nonnegative_seconds(
                        data, "max_reference_audio_total_seconds", path
                    ),
                )
            case "audio":
                return AudioFacts()

    def output_token_bases(self, raw: object, path: str) -> Mapping[str, int]:
        data = {} if raw is None else self.obj(raw, path)
        result: dict[str, int] = {}
        for quality, base in data.items():
            if not is_valid_dimension_value("quality", quality):
                self.reject(path, "invalid_text", "质量档应为非空字符串")
            elif type(base) is int and base > 0:
                result[quality] = base
            else:
                self.reject(_at(path, quality), "invalid_positive_int", "应为正整数")
        return result

    def duration_constraints(
        self, raw: object, durations: tuple[int, ...], resolutions: tuple[str, ...], path: str
    ) -> DurationConstraints:
        data = {} if raw is None else self.obj(raw, path)
        self.known_fields(data, path, _CONSTRAINT_FIELDS)
        by_resolution: dict[str, tuple[int, ...]] = {}
        resolution_map = {} if data.get("by_resolution") is None else self.obj(data["by_resolution"], path)
        for resolution, allowed in resolution_map.items():
            item_path = _at(path, f"by_resolution.{resolution}")
            if resolution not in resolutions:
                self.flag(item_path, "constraint_outside_facts", "约束引用了条目没有的分辨率")
            values = self.durations(allowed, item_path)
            self.check_constraint_durations(values, durations, item_path)
            by_resolution[resolution] = values
        with_reference_images = self.durations(data.get("with_reference_images"), _at(path, "with_reference_images"))
        self.check_constraint_durations(with_reference_images, durations, _at(path, "with_reference_images"))
        return DurationConstraints(by_resolution=by_resolution, with_reference_images=with_reference_images)

    def check_constraint_durations(self, values: tuple[int, ...], durations: tuple[int, ...], path: str) -> None:
        if any(value not in durations for value in values):
            self.flag(path, "constraint_outside_facts", "约束中的时长不在可选时长内")

    # ---- Price tables ----

    def price_table(self, raw: object, media_type: MediaType, path: str) -> PriceTable:
        data = self.obj(raw, path)
        self.known_fields(data, path, _PRICE_FIELDS)
        currency = self.text(data, "currency", path)
        if currency and currency not in CURRENCIES:
            self.flag(_at(path, "currency"), "unknown_currency", "不认识的币种")
        meters: list[Meter] = []
        for index, item in enumerate(self.items(data.get("meters"), _at(path, "meters"))):
            meter = self.meter(item, media_type, _at(path, f"meters[{index}]"))
            if meter.meter in (existing.meter for existing in meters):
                self.flag(_at(path, f"meters[{index}].meter"), "duplicate_meter", "同一计量项只能出现一次")
            meters.append(meter)
        if not meters:
            self.flag(_at(path, "meters"), "empty_price_table", "价目表至少要有一个计量项；没有价格时省略 price")
        self.check_context_tiers(meters, path)
        return PriceTable(currency=currency, meters=tuple(meters))

    def check_context_tiers(self, meters: list[Meter], path: str) -> None:
        """The first context tier starts at 0, with identical boundaries across meters: the whole request falls into one tier, whose prices apply to all meters."""
        grid: frozenset[int] | None = None
        for index, meter in enumerate(meters):
            tiers = frozenset(
                value for row in meter.rows if type(value := row.when.get(_CONTEXT_TIER)) is int and value >= 0
            )
            if not tiers:
                continue
            meter_path = _at(path, f"meters[{index}]")
            if 0 not in tiers:
                self.flag(meter_path, "context_tier_not_from_zero", "上下文档的首档要写 0")
            if grid is None:
                grid = tiers
            elif tiers != grid:
                self.flag(meter_path, "context_tier_mismatch", "各计量项的上下文档分界要相同")

    def meter(self, raw: object, media_type: MediaType, path: str) -> Meter:
        data = self.obj(raw, path)
        self.known_fields(data, path, _METER_FIELDS)
        key = self.text(data, "meter", path)
        spec = METERS.get(key)
        if spec is None:
            if key:
                self.flag(_at(path, "meter"), "unknown_meter", "不认识的计量项")
        elif media_type not in spec.media_types:
            self.flag(_at(path, "meter"), "meter_not_for_media_type", "这个计量项不适用于条目的媒体类型")
        dims = self.texts(data.get("dims"), _at(path, "dims"))
        for index, dim in enumerate(dims):
            if dim not in DIMENSIONS:
                self.flag(_at(path, f"dims[{index}]"), "unknown_dimension", "不认识的选择维度")
        rows: list[PriceRow] = []
        for index, item in enumerate(self.items(data.get("rows"), _at(path, "rows"))):
            row = self.row(item, dims, _at(path, f"rows[{index}]"))
            if any(_overlaps(row.when, earlier.when) for earlier in rows):
                self.flag(_at(path, f"rows[{index}]"), "overlapping_rows", "与前面的行可能命中同一请求")
            rows.append(row)
        if not rows:
            self.flag(_at(path, "rows"), "empty_rows", "计量项至少要有一行单价")
        defaults = {} if data.get("defaults") is None else self.obj(data["defaults"], _at(path, "defaults"))
        self.check_dimension_values(defaults, dims, _at(path, "defaults"))
        free_allowance = None
        if data.get("free_allowance") is not None:
            free_allowance = self.number(data["free_allowance"], _at(path, "free_allowance"))
        cjk_double = self.flag_value(data, "cjk_double", path)
        if cjk_double and key != CJK_DOUBLE_METER:
            self.flag(_at(path, "cjk_double"), "cjk_double_not_characters", "汉字计 2 只用于字符数计量项")
        return Meter(
            meter=key,
            dims=dims,
            rows=tuple(rows),
            defaults=defaults,
            free_allowance=free_allowance,
            cjk_double=cjk_double,
        )

    def row(self, raw: object, dims: tuple[str, ...], path: str) -> PriceRow:
        data = self.obj(raw, path)
        self.known_fields(data, path, _ROW_FIELDS)
        when = {} if data.get("when") is None else self.obj(data["when"], _at(path, "when"))
        self.check_dimension_values(when, dims, _at(path, "when"))
        return PriceRow(when=when, price=self.number(data.get("price"), _at(path, "price")))

    def check_price_resolutions(self, price: PriceTable, facts: ModelFacts, path: str) -> None:
        """Report a price-table resolution differing only in case from a declared resolution: it is a typo that can never match a request unchanged.

        Allow other tiers outside the entry declaration: submission does not uniformly reject resolutions based on entry declarations, so legacy settings or Agent arguments may
        still reach billing with these tiers, and the price table must price them. Skip this check when the entry declares no resolutions.
        """
        resolutions = facts.resolutions if isinstance(facts, ImageFacts | VideoFacts) else ()
        by_casefold = {resolution.casefold(): resolution for resolution in resolutions}
        for meter_index, meter in enumerate(price.meters):
            if "resolution" not in meter.dims:
                continue  # Undeclared dimensions already reported as dimension_not_declared
            meter_path = _at(path, f"meters[{meter_index}]")
            selections = [(_at(meter_path, f"rows[{i}].when"), row.when) for i, row in enumerate(meter.rows)]
            selections.append((_at(meter_path, "defaults"), meter.defaults))
            for selection_path, selection in selections:
                value = selection.get("resolution")
                if not isinstance(value, str) or value in resolutions:
                    continue
                declared = by_casefold.get(value.casefold())
                if declared is not None:
                    self.flag(
                        _at(selection_path, "resolution"),
                        "resolution_case_mismatch",
                        f"分辨率与条目声明的「{declared}」只差大小写",
                    )

    def check_dimension_values(self, values: Mapping[str, object], dims: tuple[str, ...], path: str) -> None:
        for dim, value in values.items():
            if dim not in dims:
                self.flag(_at(path, dim), "dimension_not_declared", "选择维度没有列在 dims 中")
            elif dim in DIMENSIONS and not is_valid_dimension_value(dim, value):
                self.flag(_at(path, dim), "invalid_dimension_value", "选择维度的取值不合法")

    # ---- Built-in provider listings ----

    def listing(self, raw: object, path: str) -> BuiltinProviderListing:
        data = self.obj(raw, path)
        self.known_fields(data, path, _LISTING_FIELDS)
        models = tuple(
            self.provider_model(item, _at(path, f"models[{index}]"))
            for index, item in enumerate(self.items(data.get("models"), _at(path, "models")))
        )
        defaults_raw = {} if data.get("defaults") is None else self.obj(data["defaults"], _at(path, "defaults"))
        defaults: dict[str, str] = {}
        for media_type in defaults_raw:
            defaults[media_type] = self.text(defaults_raw, media_type, _at(path, "defaults"))
        return BuiltinProviderListing(models=models, defaults=defaults)

    def provider_model(self, raw: object, path: str) -> BuiltinProviderModel:
        data = self.obj(raw, path)
        self.known_fields(data, path, _PROVIDER_MODEL_FIELDS)
        api_model_name = self.text(data, "api_model_name", path)
        entry = self.text(data, "entry", path)
        media_type = self.optional_text(data, "media_type", path)
        multiplier = 1.0
        if data.get("multiplier") is not None:
            multiplier = self.number(data["multiplier"], _at(path, "multiplier"))
        min_app_version = self.optional_text(data, "min_app_version", path)
        if min_app_version and not is_app_version(min_app_version):
            self.flag(_at(path, "min_app_version"), "invalid_app_version", "应为形如 0.34.0 的应用版本")
        return BuiltinProviderModel(
            api_model_name=api_model_name,
            entry=entry,
            media_type=media_type,
            multiplier=multiplier,
            min_app_version=min_app_version,
            endpoint=self.optional_text(data, "endpoint", path),
        )

    # ---- Cross-entry build checks ----

    def check_entry_identities(self, entries: tuple[CatalogEntry, ...]) -> None:
        """Entry identities are unique; within one media type, each normalized official model name or alias may belong to only one entry."""
        seen: set[EntryKey] = set()
        owners: dict[tuple[str, str], EntryKey] = {}
        for index, entry in enumerate(entries):
            path = f"entries[{index}]"
            if entry.key in seen:
                self.flag(path, "duplicate_entry", f"条目 {entry.key.ref}（{entry.media_type}）重复")
                continue
            seen.add(entry.key)
            names = [(_at(path, "name"), entry.name)]
            names += [(_at(path, f"aliases[{i}]"), alias) for i, alias in enumerate(entry.aliases)]
            for name_path, name in names:
                slot = (entry.media_type, normalize_model_name(name))
                owner = owners.setdefault(slot, entry.key)
                if owner != entry.key:
                    self.flag(name_path, "name_conflict", f"「{name}」与条目 {owner.ref} 的名字冲突")

    def check_successors(self, entries: tuple[CatalogEntry, ...]) -> None:
        """Successors reference existing entries with the same media type as the original. Check only the direct successor, not successor chains."""
        for index, entry in enumerate(entries):
            if entry.successor is None or matching_entries(entries, entry.successor, entry.media_type):
                continue
            path = f"entries[{index}].successor"
            if matching_entries(entries, entry.successor, None):
                self.flag(path, "successor_media_type_mismatch", "后继模型的媒体类型与原条目不同")
            else:
                self.flag(path, "unknown_successor", "后继模型指向的条目不存在")

    def check_listings(self, data: CatalogData) -> None:
        for provider_id, listing in data.builtin_providers.items():
            path = f"builtin_providers.{provider_id}"
            listed: dict[tuple[str, str], CatalogEntry] = {}
            unresolved: set[str] = set()
            for index, model in enumerate(listing.models):
                model_path = _at(path, f"models[{index}]")
                entry = self.listed_entry(data, model, model_path)
                if entry is None:
                    unresolved.add(model.api_model_name)
                    continue
                self.check_endpoint(provider_id, model, entry, model_path)
                slot = (model.api_model_name, entry.media_type)
                if slot in listed:
                    self.flag(model_path, "duplicate_provider_model", "同一供应商下 API 模型名与媒体类型重复")
                listed.setdefault(slot, entry)
            for media_type, api_model_name in listing.defaults.items():
                default_path = _at(path, f"defaults.{media_type}")
                if media_type not in MEDIA_TYPES:
                    self.flag(default_path, "unknown_media_type", "不认识的媒体类型")
                    continue
                default_entry = listed.get((api_model_name, media_type))
                if default_entry is None:
                    if api_model_name in unresolved:
                        continue  # Reference issues were already reported on the listing entry
                    self.flag(default_path, "unknown_default_model", "默认模型不在这个供应商这一媒体类型的清单中")
                elif default_entry.discontinued:
                    self.flag(default_path, "default_model_discontinued", "默认模型已下线")
                elif default_entry.shutdown_date is not None:
                    self.flag(default_path, "default_model_retiring", "默认模型带关停日期")

    def check_endpoint(self, provider_id: str, model: BuiltinProviderModel, entry: CatalogEntry, path: str) -> None:
        """The declared endpoint exists in the endpoint list, matches the referenced entry's media type, and is an eligible endpoint for this provider and media type."""
        path = _at(path, "endpoint")
        if model.endpoint is None:
            self.flag(path, "missing_endpoint", "发布的供应商模型必须声明调用端点")
            return
        declared = BUILTIN_ENDPOINTS.get(model.endpoint)
        if declared is None:
            self.flag(path, "unknown_endpoint", "端点清单里没有这个调用端点")
        elif declared.media_type != entry.media_type:
            self.flag(
                path, "endpoint_media_type_mismatch", f"这是 {declared.media_type} 端点，条目是 {entry.media_type}"
            )
        else:
            options = provider_endpoint_options(provider_id, entry.media_type)
            if options is None or model.endpoint not in options.endpoints:
                self.flag(path, "endpoint_not_selectable", "这个调用端点不在该供应商这一媒体类型的可选端点里")

    def listed_entry(self, data: CatalogData, model: BuiltinProviderModel, path: str) -> CatalogEntry | None:
        if model.media_type is not None and model.media_type not in MEDIA_TYPES:
            self.flag(_at(path, "media_type"), "unknown_media_type", "不认识的媒体类型")
            return None
        matches = matching_entries(data.entries, model.entry, model.media_type)
        if not matches:
            self.flag(_at(path, "entry"), "unknown_entry", "引用的条目不存在")
            return None
        if len(matches) > 1:
            self.flag(_at(path, "entry"), "ambiguous_entry", "引用两栖模型时须用 media_type 指明条目")
            return None
        return matches[0]


def _at(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _as_float(value: object) -> float | None:
    """Convert a JSON number to float, returning None for non-numbers. Integers outside float range become same-sign infinity for the non-finite-value rule to assess."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return float(value)
    except OverflowError:
        return math.inf if value > 0 else -math.inf


def _overlaps(a: Mapping[str, object], b: Mapping[str, object]) -> bool:
    """Whether two rows can match one request: all dimensions shared by their conditions have equal values."""
    return all(_same_value(a[dim], b[dim]) for dim in a.keys() & b.keys())


def _same_value(a: object, b: object) -> bool:
    return type(a) is type(b) and a == b


# ---- Public entry points ----


def parse_catalog(raw: object) -> CatalogData:
    """Read catalog data on an instance (decoded JSON). Raise ``CatalogFormatError`` for unparseable structure."""
    reader = CatalogReader()
    data = reader.catalog(raw)
    if reader.fatal:
        raise CatalogFormatError(reader.fatal)
    return data


def check_catalog(raw: object) -> list[CatalogIssue]:
    """Validate catalog data during a build (decoded JSON), returning all issues; publication requires an empty list."""
    reader = CatalogReader()
    reader.catalog(raw)
    return reader.issues


def parse_entry(raw: object) -> CatalogEntry:
    """Read a single catalog entry on an instance, such as a saved user-modified entry copy."""
    reader = CatalogReader()
    entry = reader.entry(raw, "")
    if entry is None and not reader.fatal:
        reader.reject("media_type", "unknown_media_type", "不认识的媒体类型")
    if reader.fatal or entry is None:
        raise CatalogFormatError(reader.fatal)
    return entry


def check_entry(raw: object) -> list[CatalogIssue]:
    """Validate a single catalog entry during a build. Check only the entry itself; name conflicts require the complete catalog dataset."""
    reader = CatalogReader()
    reader.entry(raw, "")
    return reader.issues
