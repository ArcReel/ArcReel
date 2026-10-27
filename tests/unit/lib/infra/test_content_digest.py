"""内容摘要原语的口径测试。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

import pytest

import lib.infra.content_digest as content_digest
from lib.infra.content_digest import (
    CHUNK_BYTES,
    FILE_DIGEST_CACHE_CAPACITY,
    FILE_DIGEST_RACY_WINDOW_NS,
    HASH_ALGORITHM,
    TextModeDigest,
    canonical_json,
    canonical_json_bytes,
    canonical_json_digest,
    digest_stream,
    prefixed,
    prefixed_canonical_json_digest,
    prefixed_sha256_file,
    reset_file_digest_cache_for_tests,
    sha256_file,
    sha256_file_with_size,
    unprefixed,
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


def test_unprefixed_strips_only_a_valid_algorithm_prefix() -> None:
    hexdigest = "ab" * 32
    assert unprefixed(prefixed(hexdigest)) == hexdigest
    assert unprefixed(hexdigest) == hexdigest
    # 前缀后不是合法 hexdigest 时原样返回，交给调用方的比对判为不一致。
    assert unprefixed(f"{HASH_ALGORITHM}:xyz") == f"{HASH_ALGORITHM}:xyz"
    assert unprefixed(f"{HASH_ALGORITHM}:{hexdigest}\n") == f"{HASH_ALGORITHM}:{hexdigest}\n"


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


# --- 文件摘要缓存：键为 (st_dev, st_ino, st_size, st_mtime_ns) ---


@pytest.fixture(autouse=True)
def fresh_digest_cache() -> None:
    reset_file_digest_cache_for_tests()


@pytest.fixture
def hashed_files(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """在摘要循环上记录每次真正哈希的字节数；缓存命中不会调到这里。"""

    hashed: list[int] = []
    original = content_digest.digest_stream

    def _recording(read, *, collect_content: bool = False):
        result = original(read, collect_content=collect_content)
        hashed.append(result[1])
        return result

    monkeypatch.setattr(content_digest, "digest_stream", _recording)
    return hashed


def _aged_file(path: Path, payload: bytes, *, age_seconds: int = 3600) -> Path:
    """写文件并把 mtime 拨到足够久以前：刚写的文件不入缓存，要观察缓存行为得先让它「老」。"""

    path.write_bytes(payload)
    aged = time.time_ns() - age_seconds * 1_000_000_000
    os.utime(path, ns=(aged, aged))
    return path


def test_unchanged_stat_hashes_the_file_once(tmp_path: Path, hashed_files: list[int]) -> None:
    target = _aged_file(tmp_path / "sheet.png", b"design sheet")

    digests = {sha256_file(target) for _ in range(5)}

    assert digests == {hashlib.sha256(b"design sheet").hexdigest()}
    assert hashed_files == [len(b"design sheet")]


def test_cached_size_matches_the_file(tmp_path: Path) -> None:
    target = _aged_file(tmp_path / "sheet.png", b"x" * 100)

    assert sha256_file_with_size(target) == sha256_file_with_size(target)
    assert sha256_file_with_size(target)[1] == 100


def test_replaced_content_is_rehashed_when_the_size_changes(tmp_path: Path, hashed_files: list[int]) -> None:
    target = _aged_file(tmp_path / "sheet.png", b"first")
    assert sha256_file(target) == hashlib.sha256(b"first").hexdigest()

    target.write_bytes(b"second, longer")

    assert sha256_file(target) == hashlib.sha256(b"second, longer").hexdigest()
    assert len(hashed_files) == 2


def test_replaced_content_is_rehashed_when_only_the_mtime_changes(tmp_path: Path, hashed_files: list[int]) -> None:
    target = _aged_file(tmp_path / "sheet.png", b"aaaa")
    assert sha256_file(target) == hashlib.sha256(b"aaaa").hexdigest()

    _aged_file(target, b"bbbb", age_seconds=1800)

    assert sha256_file(target) == hashlib.sha256(b"bbbb").hexdigest()
    assert len(hashed_files) == 2


def test_touched_file_with_identical_content_keeps_its_digest(tmp_path: Path, hashed_files: list[int]) -> None:
    """复制、导入、恢复只改 mtime：重算一次，摘要不变。"""

    target = _aged_file(tmp_path / "sheet.png", b"same bytes")
    before = sha256_file(target)

    _aged_file(target, b"same bytes", age_seconds=1800)

    assert sha256_file(target) == before
    assert len(hashed_files) == 2


def test_rewrite_within_the_racy_window_is_never_served_stale(tmp_path: Path, hashed_files: list[int]) -> None:
    """同一时间戳下的连续改写可能不改任何一项 stat 标识；刚写的文件不入缓存，所以每次都读到最新内容。"""

    target = tmp_path / "sheet.png"
    for payload in (b"aaaa", b"bbbb", b"cccc"):
        target.write_bytes(payload)
        assert sha256_file(target) == hashlib.sha256(payload).hexdigest()

    assert len(hashed_files) == 3
    assert FILE_DIGEST_RACY_WINDOW_NS > 0


def test_cache_is_bounded_and_evicts_the_least_recently_used(tmp_path: Path, hashed_files: list[int]) -> None:
    aged = time.time_ns() - 3600 * 1_000_000_000
    files = []
    for index in range(FILE_DIGEST_CACHE_CAPACITY + 1):
        target = tmp_path / f"{index}.bin"
        target.write_bytes(index.to_bytes(4, "big"))
        os.utime(target, ns=(aged, aged))
        files.append(target)

    for target in files[:FILE_DIGEST_CACHE_CAPACITY]:
        sha256_file(target)
    sha256_file(files[0])  # 0 变成最近用过的：满容量后被挤出的是 1，不是 0
    sha256_file(files[-1])
    hashed_before = len(hashed_files)
    assert hashed_before == FILE_DIGEST_CACHE_CAPACITY + 1

    sha256_file(files[0])
    sha256_file(files[-1])
    assert len(hashed_files) == hashed_before  # 最近用过的两个仍命中

    sha256_file(files[1])
    assert len(hashed_files) == hashed_before + 1  # 最久未用的被淘汰，缓存没有无界增长
