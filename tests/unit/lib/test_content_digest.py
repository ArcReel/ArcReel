"""内容摘要原语的口径测试。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import pytest

from lib.content_digest import (
    CHUNK_BYTES,
    HASH_ALGORITHM,
    TextModeDigest,
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


def test_sha256_file_streams_large_payload(tmp_path: Path) -> None:
    """流式读避免大文件 OOM；跨 CHUNK_BYTES 边界的多块读取结果应与标准 hashlib 一致。"""

    big = tmp_path / "big.bin"
    payload = b"abc" * (CHUNK_BYTES // len(b"abc") + 1)
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
    with pytest.raises(FileNotFoundError, match=re.escape(str(tmp_path))):
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
    with pytest.raises(ValueError, match=r"Out of range float values are not JSON compliant"):
        canonical_json_digest({"value": float("nan")}, allow_nan=False)


def test_prefixed_canonical_json_digest_matches_bare_digest() -> None:
    payload = {"kind": "demo", "version": 1}
    assert prefixed_canonical_json_digest(payload) == prefixed(canonical_json_digest(payload))


def _text_mode_view(content: bytes) -> bytes:
    return content.split(b"\x1a", 1)[0].replace(b"\r\n", b"\n")


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"plain\n",
        b"a\r\nb\r\n",
        b"lone\rcr\r",
        b"\x89PNG\r\n\x1a\nIHDR\r\n",
        b"\x1a",
        b"tail-cr\r",
    ],
)
def test_text_mode_digest_matches_whole_read_view(content: bytes) -> None:
    digest = TextModeDigest()
    digest.update(content)

    assert digest.hexdigest() == hashlib.sha256(_text_mode_view(content)).hexdigest()


@pytest.mark.parametrize(
    ("chunks", "content"),
    [
        ((b"a\r", b"\nb"), b"a\r\nb"),
        ((b"a\r", b"\r\nb"), b"a\r\r\nb"),
        ((b"a\r", b"x"), b"a\rx"),
        ((b"a\r", b""), b"a\r"),
        ((b"a\r", b"\x1a\r\n"), b"a\r\x1a\r\n"),
        ((b"head\x1a", b"\r\ntail\r"), b"head\x1a\r\ntail\r"),
    ],
)
def test_text_mode_digest_folds_across_chunk_boundaries(chunks: tuple[bytes, ...], content: bytes) -> None:
    """块边界上的 ``\\r`` 要等到下一块才能决定是否折叠，结果须与一次性读取一致。"""
    digest = TextModeDigest()
    for chunk in chunks:
        digest.update(chunk)

    assert digest.hexdigest() == hashlib.sha256(_text_mode_view(content)).hexdigest()
