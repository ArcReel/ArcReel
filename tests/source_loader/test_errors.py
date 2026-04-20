from lib.source_loader.errors import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    SourceDecodeError,
    SourceLoaderError,
    UnsupportedFormatError,
)


def test_unsupported_format_error_carries_ext():
    err = UnsupportedFormatError(ext=".doc")
    assert isinstance(err, SourceLoaderError)
    assert err.ext == ".doc"
    assert ".doc" in str(err)


def test_source_decode_error_carries_filename_and_tried():
    err = SourceDecodeError(filename="novel.txt", tried_encodings=["utf-8", "gbk"])
    assert err.filename == "novel.txt"
    assert err.tried_encodings == ["utf-8", "gbk"]
    assert "novel.txt" in str(err)
    assert "utf-8" in str(err)


def test_corrupt_file_error_carries_reason():
    err = CorruptFileError(filename="x.pdf", reason="scanned")
    assert err.filename == "x.pdf"
    assert err.reason == "scanned"


def test_file_size_exceeded_error_carries_sizes():
    err = FileSizeExceededError(filename="big.pdf", size_bytes=60_000_000, limit_bytes=50_000_000)
    assert err.size_bytes == 60_000_000
    assert err.limit_bytes == 50_000_000


def test_conflict_error_carries_existing_and_suggested():
    err = ConflictError(existing="novel.txt", suggested_name="novel_1")
    assert err.existing == "novel.txt"
    assert err.suggested_name == "novel_1"
