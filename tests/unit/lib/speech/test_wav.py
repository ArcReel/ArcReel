import struct
import wave
from io import BytesIO

import pytest

from lib.speech.wav import normalize_wav_header

_PCM_FMT = struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
_SAMPLES = b"\x01\x02\x03\x04" * 120


def _chunk(name: bytes, body: bytes, *, size: int | None = None) -> bytes:
    return struct.pack("<4sI", name, len(body) if size is None else size) + body + b"\0" * (len(body) % 2)


def _riff(*chunks: bytes, size: int | None = None) -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return struct.pack("<4sI", b"RIFF", len(body) if size is None else size) + body


@pytest.mark.parametrize(
    ("riff_size", "data_size"),
    [
        (0x7FFFFFBF, 0x7FFFFF9B),
        (0x7FFFFFFF, 0x7FFFFFFF),
        (0xFFFFFFFF, 0xFFFFFFFF),
        (0xFFFFFFFF, None),
        (None, 0xFFFFFFFF),
    ],
)
def test_normalize_streaming_sizes_preserves_chunks_and_pcm(riff_size: int | None, data_size: int | None):
    prefix = (_chunk(b"JUNK", b"abc"), _chunk(b"fmt ", _PCM_FMT), _chunk(b"fact", struct.pack("<I", 240)))
    suffix = (_chunk(b"LIST", b"INFO" + _chunk(b"INAM", b"clip\0")), _chunk(b"zzzz", b"extra"))
    streamed = _riff(*prefix, _chunk(b"data", _SAMPLES, size=data_size), *suffix, size=riff_size)
    expected = _riff(*prefix, _chunk(b"data", _SAMPLES), *suffix)

    normalized = normalize_wav_header(streamed)

    assert normalized == expected
    with wave.open(BytesIO(normalized), "rb") as wav:
        assert wav.getnframes() == 240
        assert wav.getnframes() / wav.getframerate() == 0.01
        assert wav.readframes(240) == _SAMPLES


def test_normalize_preserves_padding_after_odd_pcm_size():
    pcm24 = struct.pack("<HHIIHH", 1, 1, 24000, 72000, 3, 24)
    prefix = _chunk(b"fmt ", pcm24)
    samples = b"\x11\x22\x33" * 3
    tail = _chunk(b"JUNK", b"abc")
    streamed = _riff(prefix, _chunk(b"data", samples, size=0xFFFFFFFF), tail, size=0xFFFFFFFF)

    assert normalize_wav_header(streamed) == _riff(prefix, _chunk(b"data", samples), tail)


@pytest.mark.parametrize(
    "content",
    [
        _riff(_chunk(b"fmt ", _PCM_FMT), _chunk(b"data", _SAMPLES), _chunk(b"JUNK", b"abc")),
        _riff(
            _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)),
            struct.pack("<4sI", b"data", 1) + b"\x80",
        ),
        _riff(
            _chunk(b"fmt ", struct.pack("<HHIIHHH", 3, 1, 24000, 96000, 4, 32, 0)),
            _chunk(b"fact", struct.pack("<I", 1)),
            _chunk(b"data", struct.pack("<f", 0.5)),
        ),
        b"",
        b"ID3\x03\x00\x00",
        b"fLaC\x00",
        b"OggS\x00",
        b"OpusHead",
        b"RIFF\x04\x00\x00\x00WEBP",
    ],
    ids=["pcm", "unpadded-pcm", "float", "empty", "mp3", "flac", "ogg", "opus", "other-riff"],
)
def test_normalize_passes_through_valid_wav_and_other_formats(content: bytes):
    assert normalize_wav_header(content) == content


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"RIFF\xff\xff\xff\xffWAV", "truncated RIFF header"),
        (_riff(_chunk(b"data", _SAMPLES)), "expected one data chunk after fmt"),
        (_riff(_chunk(b"fmt ", _PCM_FMT)), "missing fmt or data"),
        (_riff(_chunk(b"fmt ", _PCM_FMT[:8]), _chunk(b"data", _SAMPLES)), "truncated fmt chunk"),
        (_riff(_chunk(b"fmt ", _PCM_FMT), _chunk(b"data", _SAMPLES), size=1000), "RIFF size does not match"),
        (_riff(_chunk(b"fmt ", _PCM_FMT), _chunk(b"data", _SAMPLES, size=600)), "truncated b'data' chunk"),
        (_riff(_chunk(b"fmt ", _PCM_FMT), _chunk(b"data", b"x")), "incomplete PCM frame"),
        (_riff(_chunk(b"fmt ", _PCM_FMT), _chunk(b"data", _SAMPLES), b"JUN"), "truncated chunk header"),
        (_riff(_chunk(b"fmt ", _PCM_FMT), _chunk(b"data", _SAMPLES), b"\0" * 8), "invalid chunk ID"),
        (
            _riff(
                _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)),
                _chunk(b"data", b"\x80", size=0xFFFFFFFF),
            ),
            "cannot determine PCM data length",
        ),
    ],
)
def test_normalize_rejects_corrupt_or_ambiguous_wav(content: bytes, reason: str):
    with pytest.raises(ValueError, match=f"Invalid WAV: {reason}"):
        normalize_wav_header(content)
