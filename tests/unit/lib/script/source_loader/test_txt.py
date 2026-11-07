import random

import pytest

from lib.script.source_loader.errors import SourceDecodeError
from lib.script.source_loader.txt import TxtExtractor, decode_txt


def test_decode_utf8_bom():
    raw = b"\xef\xbb\xbf\xe4\xbd\xa0\xe5\xa5\xbd"  # "你好"
    text, enc = decode_txt(raw)
    assert text == "你好"
    assert enc == "utf-8-sig"


def test_decode_utf16_le_bom():
    raw = "\u4f60\u597d".encode("utf-16-le")
    raw = b"\xff\xfe" + raw
    text, enc = decode_txt(raw)
    assert text == "你好"
    assert enc == "utf-16-le"


def test_decode_utf16_be_bom():
    raw = "\u4f60\u597d".encode("utf-16-be")
    raw = b"\xfe\xff" + raw
    text, enc = decode_txt(raw)
    assert text == "你好"
    assert enc == "utf-16-be"


def test_decode_pure_utf8_no_bom():
    raw = "中文小说内容".encode()
    text, enc = decode_txt(raw)
    assert text == "中文小说内容"
    assert enc == "utf-8"


def test_decode_gbk_via_charset_normalizer():
    raw = ("第一章 起点。" * 50).encode("gbk")
    text, enc = decode_txt(raw)
    assert "起点" in text
    # charset-normalizer usually returns gbk / gb18030 / cp936.
    assert enc
    assert enc.lower() in {"gbk", "gb18030", "cp936"}


def test_decode_big5_via_charset_normalizer():
    raw = ("第一章 起點。" * 50).encode("big5")
    text, enc = decode_txt(raw)
    assert "起點" in text
    assert enc
    assert "big5" in enc.lower()


def test_decode_random_bytes_raises():
    # Fixed-seed pseudorandom bytes prevent charset-normalizer from producing trustworthy results;
    # gb18030 errors='replace' fallback also yields \ufffd far above 5%, triggering SourceDecodeError.
    rng = random.Random(42)
    raw = bytes(rng.randint(0, 255) for _ in range(4000))
    with pytest.raises(SourceDecodeError) as exc_info:
        decode_txt(raw)
    assert "utf-8" in exc_info.value.tried_encodings
    assert "gb18030" in exc_info.value.tried_encodings


def test_decode_clean_gb18030_without_replacements():
    # Short, very few sinograms — charset-normalizer may not be confident
    # (chaos >= 0.5 → falls through to gb18030 fallback). Decoding should
    # still succeed cleanly (no \ufffd) and be labeled "gb18030" (not "-lossy").
    # GB18030 4-byte sequence (U+20000 "𠀀") avoids GBK/Big5 misdetection;
    # charset-normalizer either recognizes gb18030 correctly or clean gb18030 fallback applies.
    raw = "𠀀".encode("gb18030")
    text, enc = decode_txt(raw)
    assert text == "𠀀"
    # Either charset-normalizer correctly identified it (any variant) OR
    # the gb18030 clean path was taken. Both are acceptable; what's NOT
    # acceptable is the "-lossy" label for clean output.
    assert "lossy" not in enc


def test_extractor_writes_via_decode(tmp_path):
    src = tmp_path / "novel.txt"
    # Samples must be long enough with realistic Chinese distribution for stable gbk-family detection.
    src.write_bytes(("第一章 内容起点。" * 50).encode("gbk"))
    result = TxtExtractor().extract(src)
    assert "内容" in result.text
    assert result.used_encoding
    assert result.used_encoding.lower() in {"gbk", "gb18030", "cp936"}
    assert result.chapter_count == 0


def test_gb18030_fallback_clean_is_labeled_gb18030(monkeypatch):
    """Force control into gb18030 fallback with clean GB18030 bytes; expect label 'gb18030' (not 'gb18030-lossy')."""
    import lib.script.source_loader.txt as mod

    class _NoBest:
        def best(self):
            return None

    monkeypatch.setattr(mod.charset_normalizer, "from_bytes", lambda _raw: _NoBest())
    # Valid GB18030 bytes — no decode replacements needed
    raw = ("一二三四五" * 10).encode("gb18030")
    text, enc = mod.decode_txt(raw)
    assert "一二三四五" in text
    assert enc == "gb18030"


def test_gb18030_fallback_with_replacements_is_labeled_lossy(monkeypatch):
    """Force gb18030 fallback with some invalid bytes; decode yields \\ufffd → label 'gb18030-lossy'."""
    import lib.script.source_loader.txt as mod

    class _NoBest:
        def best(self):
            return None

    monkeypatch.setattr(mod.charset_normalizer, "from_bytes", lambda _raw: _NoBest())
    # Mostly valid GB18030 mixed with a handful of illegal 0x80 lead bytes
    # (0x80 is not a valid GB18030 lead byte → replacement char)
    raw = ("一二三四" * 100).encode("gb18030") + b"\x80" * 5
    text, enc = mod.decode_txt(raw)
    # Replaces present but ratio < 5%, so no raise; labeled lossy
    assert "\ufffd" in text
    assert enc == "gb18030-lossy"
