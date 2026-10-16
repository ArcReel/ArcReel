"""ArcReel 模型目录数据的格式定义与校验器。

ArcReel 应用加载出厂快照与官方同步数据、目录数据仓库的 CI 共用这一份实现：实例侧从宽读入
（``parse_*``、``load_catalog``），构建侧从严校验（``check_*``）；用户修改的条目副本经 ``dump_entry``
写回。本包只依赖标准库，不依赖 ArcReel 应用。
"""

from .dataset import (
    CATALOG_FILENAME,
    MANIFEST_FILENAME,
    CatalogManifest,
    check_catalog_files,
    load_catalog,
    make_manifest,
    parse_manifest,
)
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
)
from .names import normalize_model_name
from .reading import (
    CatalogFormatError,
    CatalogIssue,
    check_catalog,
    check_entry,
    parse_catalog,
    parse_dataset_version,
    parse_entry,
)
from .vocabulary import (
    CJK_DOUBLE_METER,
    CURRENCIES,
    DIMENSION_MEDIA_TYPES,
    DIMENSIONS,
    MEDIA_TYPES,
    METERS,
    SERVICE_TIERS,
    Currency,
    MediaType,
    MeterKind,
    MeterSpec,
    is_valid_dimension_value,
)
from .writing import dump_entry

__all__ = [
    "CATALOG_FILENAME",
    "CJK_DOUBLE_METER",
    "CURRENCIES",
    "DIMENSIONS",
    "DIMENSION_MEDIA_TYPES",
    "MANIFEST_FILENAME",
    "MEDIA_TYPES",
    "METERS",
    "SERVICE_TIERS",
    "AudioFacts",
    "BuiltinProviderListing",
    "BuiltinProviderModel",
    "CatalogData",
    "CatalogEntry",
    "CatalogFormatError",
    "CatalogIssue",
    "CatalogManifest",
    "Currency",
    "DurationConstraints",
    "EntryKey",
    "ImageFacts",
    "MediaType",
    "Meter",
    "MeterKind",
    "MeterSpec",
    "ModelFacts",
    "PriceRow",
    "PriceTable",
    "TextFacts",
    "VideoFacts",
    "check_catalog",
    "check_catalog_files",
    "check_entry",
    "dump_entry",
    "is_valid_dimension_value",
    "load_catalog",
    "make_manifest",
    "normalize_model_name",
    "parse_catalog",
    "parse_dataset_version",
    "parse_entry",
    "parse_manifest",
]
