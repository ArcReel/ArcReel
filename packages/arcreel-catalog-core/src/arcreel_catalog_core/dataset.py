"""清单文件与数据文件。

目录数据仓库发布两个文件：清单文件 ``manifest.json`` 给出数据集版本与数据文件的 SHA-256，
数据文件 ``catalog.json`` 是目录数据本身。实例先取清单文件，数据集版本比本地新时再取数据文件，
校验哈希与版本一致后整份读入。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .model import CatalogData
from .reading import CatalogFormatError, CatalogIssue, CatalogReader

MANIFEST_FILENAME = "manifest.json"
CATALOG_FILENAME = "catalog.json"

_MANIFEST_FIELDS = frozenset({"dataset_version", "sha256"})
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class CatalogManifest:
    dataset_version: str
    sha256: str


def _read_manifest(reader: CatalogReader, raw: object) -> CatalogManifest:
    data = reader.obj(raw, MANIFEST_FILENAME)
    reader.known_fields(data, MANIFEST_FILENAME, _MANIFEST_FIELDS)
    dataset_version = reader.dataset_version(data, MANIFEST_FILENAME)
    sha256 = reader.text(data, "sha256", MANIFEST_FILENAME)
    if sha256 and not _SHA256.fullmatch(sha256):
        reader.reject(f"{MANIFEST_FILENAME}.sha256", "invalid_sha256", "应为 64 位小写十六进制")
    return CatalogManifest(dataset_version=dataset_version, sha256=sha256)


def parse_manifest(content: bytes) -> CatalogManifest:
    """实例侧读入清单文件。无法解析时抛 ``CatalogFormatError``。"""
    reader = CatalogReader()
    manifest = _read_manifest(reader, _decode(reader, content, MANIFEST_FILENAME))
    if reader.fatal:
        raise CatalogFormatError(reader.fatal)
    return manifest


def _decode(reader: CatalogReader, content: bytes, filename: str) -> object:
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        reader.reject(filename, "invalid_json", f"不是合法的 JSON：{exc}")
        return None


def _read_files(reader: CatalogReader, manifest_content: bytes, catalog_content: bytes) -> CatalogData | None:
    manifest_raw = _decode(reader, manifest_content, MANIFEST_FILENAME)
    catalog_raw = _decode(reader, catalog_content, CATALOG_FILENAME)
    if reader.fatal:
        return None
    manifest = _read_manifest(reader, manifest_raw)
    if manifest.sha256 and hashlib.sha256(catalog_content).hexdigest() != manifest.sha256:
        reader.reject(f"{MANIFEST_FILENAME}.sha256", "sha256_mismatch", "与数据文件的 SHA-256 不一致")
    data = reader.catalog(catalog_raw)
    if manifest.dataset_version and manifest.dataset_version != data.dataset_version:
        reader.reject(
            f"{MANIFEST_FILENAME}.dataset_version", "dataset_version_mismatch", "与数据文件的数据集版本不一致"
        )
    return data


def load_catalog(manifest_content: bytes, catalog_content: bytes) -> CatalogData:
    """实例侧读入一对清单文件与数据文件的原始字节。

    哈希或数据集版本对不上、任一文件无法解析时抛 ``CatalogFormatError``，这一份数据整体拒收。
    """
    reader = CatalogReader()
    data = _read_files(reader, manifest_content, catalog_content)
    if reader.fatal or data is None:
        raise CatalogFormatError(reader.fatal)
    return data


def check_catalog_files(manifest_content: bytes, catalog_content: bytes) -> list[CatalogIssue]:
    """构建侧校验一对清单文件与数据文件，返回全部问题；空列表才可以发布。"""
    reader = CatalogReader()
    _read_files(reader, manifest_content, catalog_content)
    return reader.issues


def make_manifest(catalog_content: bytes) -> dict[str, str]:
    """按数据文件生成清单文件的内容。数据文件无法解析时抛 ``CatalogFormatError``。"""
    reader = CatalogReader()
    data = reader.catalog(_decode(reader, catalog_content, CATALOG_FILENAME))
    if reader.fatal:
        raise CatalogFormatError(reader.fatal)
    return {"dataset_version": data.dataset_version, "sha256": hashlib.sha256(catalog_content).hexdigest()}
