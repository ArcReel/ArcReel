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
    raw = bytes(range(256)) * 200
    with pytest.raises(SourceDecodeError) as exc_info:
        decode_txt(raw)
    assert "utf-8" in exc_info.value.tried_encodings
    assert "gb18030" in exc_info.value.tried_encodings


def test_extractor_writes_via_decode(tmp_path):
    src = tmp_path / "novel.txt"
    src.write_bytes("内容".encode("gbk"))
    result = TxtExtractor().extract(src)
    assert "内容" in result.text
    assert result.used_encoding and result.used_encoding.lower() in {"gbk", "gb18030", "cp936"}
    assert result.chapter_count == 0
