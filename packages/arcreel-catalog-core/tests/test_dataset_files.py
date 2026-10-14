"""清单文件与数据文件：哈希与数据集版本对上才读入。"""

from __future__ import annotations

import json

import pytest
from catalog_factories import DATASET_VERSION, catalog

from arcreel_catalog_core import CatalogFormatError, check_catalog_files, load_catalog, make_manifest


def _files(data: dict | None = None) -> tuple[bytes, bytes]:
    content = json.dumps(data or catalog(), ensure_ascii=False).encode("utf-8")
    return json.dumps(make_manifest(content)).encode("utf-8"), content


def test_matching_pair_loads():
    manifest, content = _files()

    assert check_catalog_files(manifest, content) == []
    assert load_catalog(manifest, content).dataset_version == DATASET_VERSION


def test_data_file_not_matching_manifest_hash_is_rejected():
    manifest, content = _files()
    tampered = content.replace(b"12.0", b"1.2")

    with pytest.raises(CatalogFormatError) as excinfo:
        load_catalog(manifest, tampered)

    assert [issue.code for issue in excinfo.value.issues] == ["sha256_mismatch"]


def test_manifest_version_differing_from_data_file_is_rejected():
    manifest, content = _files()
    edited = json.loads(manifest)
    edited["dataset_version"] = "2026-10-15T08:00:00Z"

    with pytest.raises(CatalogFormatError) as excinfo:
        load_catalog(json.dumps(edited).encode(), content)

    assert [issue.code for issue in excinfo.value.issues] == ["dataset_version_mismatch"]


def test_data_file_that_is_not_json_is_rejected():
    manifest, _ = _files()

    with pytest.raises(CatalogFormatError) as excinfo:
        load_catalog(manifest, b"{not json")

    assert [issue.code for issue in excinfo.value.issues] == ["invalid_json"]
