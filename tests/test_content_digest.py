"""内容摘要原语的口径测试。"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from lib.content_digest import (
    HASH_ALGORITHM,
    canonical_json,
    canonical_json_bytes,
    canonical_json_digest,
    digest_stream,
    prefixed,
    prefixed_canonical_json_digest,
    prefixed_sha256_file,
    sha256_file,
    sha256_file_with_size,
)

pytestmark = pytest.mark.unit


def test_sha256_file_streams_large_payload(tmp_path: Path) -> None:
    """流式读避免大文件 OOM；结果应与标准 hashlib 一致。"""

    big = tmp_path / "big.bin"
    payload = b"abc" * (256 * 1024)
    big.write_bytes(payload)
    assert sha256_file(big) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.touch()
    assert sha256_file(empty).startswith("e3b0c442")


def test_sha256_file_with_size_reports_byte_count(tmp_path: Path) -> None:
    target = tmp_path / "payload.bin"
    payload = b"x" * 4096
    target.write_bytes(payload)
    assert sha256_file_with_size(target) == (hashlib.sha256(payload).hexdigest(), len(payload))


def test_sha256_file_rejects_non_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path)


def test_prefixed_sha256_file_carries_algorithm(tmp_path: Path) -> None:
    target = tmp_path / "media.bin"
    target.write_bytes(b"payload")
    assert prefixed_sha256_file(target) == f"{HASH_ALGORITHM}:{sha256_file(target)}"


def test_digest_stream_can_retain_content() -> None:
    chunks = iter([b"one", b"two", b""])
    hexdigest, size, content = digest_stream(lambda _size: next(chunks), collect_content=True)
    assert (hexdigest, size, content) == (hashlib.sha256(b"onetwo").hexdigest(), 6, b"onetwo")


def test_digest_stream_drops_content_by_default() -> None:
    chunks = iter([b"one", b""])
    _hexdigest, _size, content = digest_stream(lambda _size: next(chunks))
    assert content is None


def test_canonical_json_is_stable_under_key_order_and_whitespace() -> None:
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == '{"a":[1,{"c":3,"d":2}],"b":1}'


def test_canonical_json_keeps_non_ascii_literal() -> None:
    assert canonical_json({"名": "值"}) == '{"名":"值"}'
    assert canonical_json_bytes({"名": "值"}) == '{"名":"值"}'.encode()


def test_canonical_json_accepts_non_finite_floats_by_default() -> None:
    """项目与剧本快照沿用 json 默认口径：能被 ``json.loads`` 读进来的就能算出摘要。"""

    payload = json.loads('{"value": NaN}')
    assert math.isnan(payload["value"])
    assert canonical_json_digest(payload) == hashlib.sha256(b'{"value":NaN}').hexdigest()


def test_canonical_json_rejects_non_finite_floats_when_strict() -> None:
    with pytest.raises(ValueError):
        canonical_json_digest({"value": float("nan")}, allow_nan=False)


def test_prefixed_canonical_json_digest_matches_bare_digest() -> None:
    payload = {"kind": "demo", "version": 1}
    assert prefixed_canonical_json_digest(payload) == prefixed(canonical_json_digest(payload))
