import random

import pytest

from lib.source_loader.errors import SourceDecodeError
from lib.source_loader.txt import TxtExtractor, decode_txt


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
    # charset-normalizer 通常返回 gbk / gb18030 / cp936 之一
    assert enc and enc.lower() in {"gbk", "gb18030", "cp936"}


def test_decode_big5_via_charset_normalizer():
    raw = ("第一章 起點。" * 50).encode("big5")
    text, enc = decode_txt(raw)
    assert "起點" in text
    assert enc and "big5" in enc.lower()


def test_decode_random_bytes_raises():
    # 使用固定种子的伪随机字节：charset-normalizer 无法给出可信结果，
    # gb18030 errors='replace' 兜底也会产生远高于 5% 阈值的 \ufffd，触发 SourceDecodeError。
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
    # 使用 GB18030 4 字节序列（U+20000 "𠀀"），绕过 GBK/Big5 的误检，
    # 要么被 charset-normalizer 正确识别为 gb18030，要么直接走 gb18030 干净兜底。
    raw = "𠀀".encode("gb18030")
    text, enc = decode_txt(raw)
    assert text == "𠀀"
    # Either charset-normalizer correctly identified it (any variant) OR
    # the gb18030 clean path was taken. Both are acceptable; what's NOT
    # acceptable is the "-lossy" label for clean output.
    assert "lossy" not in enc


def test_extractor_writes_via_decode(tmp_path):
    src = tmp_path / "novel.txt"
    # 样本需足够长且具备真实中文分布，charset-normalizer 才能稳定识别为 gbk 系列
    src.write_bytes(("第一章 内容起点。" * 50).encode("gbk"))
    result = TxtExtractor().extract(src)
    assert "内容" in result.text
    assert result.used_encoding and result.used_encoding.lower() in {"gbk", "gb18030", "cp936"}
    assert result.chapter_count == 0
