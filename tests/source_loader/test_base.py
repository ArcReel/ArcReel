from pathlib import Path

from lib.source_loader.base import ExtractedText, FormatExtractor, NormalizeResult  # noqa: F401


def test_extracted_text_defaults():
    e = ExtractedText(text="hello")
    assert e.text == "hello"
    assert e.used_encoding is None
    assert e.chapter_count == 0


def test_normalize_result_required_and_defaults():
    r = NormalizeResult(
        normalized_path=Path("/p/source/a.txt"),
        raw_path=None,
        used_encoding="utf-8",
        chapter_count=0,
        original_filename="a.txt",
    )
    assert r.normalized_path.name == "a.txt"
    assert r.raw_path is None
    assert r.used_encoding == "utf-8"
    assert r.chapter_count == 0
    assert r.original_filename == "a.txt"


def test_format_extractor_protocol_runtime_check():
    class _Stub:
        def extract(self, path: Path) -> ExtractedText:
            return ExtractedText(text="x")

    stub = _Stub()
    # Protocol 仅做静态检查；运行期通过 hasattr 验证 duck-typing
    assert hasattr(stub, "extract")
    assert isinstance(stub.extract(Path(".")), ExtractedText)
