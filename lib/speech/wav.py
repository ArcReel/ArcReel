"""Normalize streaming sizes in downloaded RIFF/WAVE payloads."""

import re
import struct
import wave
from io import BytesIO

_CHUNK = struct.Struct("<4sI")
_SIZE = struct.Struct("<I")
_FORMAT = struct.Struct("<HHIIHH")
_FOURCC = re.compile(rb"[ -~]{4}")
_PCM_SUBFORMAT = bytes.fromhex("01000000 0000 1000 8000 00aa00389b71")


def _is_streaming_size(size: int) -> bool:
    return 0x7FFF0000 <= size <= 0x7FFFFFFF or size == 0xFFFFFFFF


def _format_frame_size(content: bytes, start: int, end: int) -> int | None:
    if end - start < _FORMAT.size:
        raise ValueError("Invalid WAV: truncated fmt chunk")
    encoding, channels, rate, byte_rate, frame_size, bits = _FORMAT.unpack_from(content, start)
    if not (encoding and channels and rate and byte_rate and frame_size):
        raise ValueError("Invalid WAV: invalid fmt fields")
    if encoding == 0xFFFE:
        if end - start < 40 or not 22 <= struct.unpack_from("<H", content, start + 16)[0] <= end - start - 18:
            raise ValueError("Invalid WAV: truncated extensible fmt chunk")
        if content[start + 24 : start + 40] != _PCM_SUBFORMAT:
            return None
    elif encoding != 1:
        return None
    if not bits or frame_size != channels * ((bits + 7) // 8) or byte_rate != rate * frame_size:
        raise ValueError("Invalid WAV: invalid PCM frame size or byte rate")
    return frame_size


def _find_data_size(content: bytes, start: int, frame_size: int) -> int:
    """Infer PCM length from the longest complete suffix of aligned RIFF chunks."""
    end = len(content)
    suffix = [end]
    boundaries = {end}
    for offset in range((end - _CHUNK.size) & ~1, start - 1, -2):
        size = _SIZE.unpack_from(content, offset + 4)[0]
        next_chunk = offset + _CHUNK.size + size + (size & 1)
        if next_chunk not in boundaries or not _FOURCC.fullmatch(content, offset, offset + 4):
            continue
        if size & 1 and content[next_chunk - 1] != 0:
            continue
        while suffix[-1] != next_chunk:
            boundaries.remove(suffix.pop())
        suffix.append(offset)
        boundaries.add(offset)

    suffix_start = suffix[-1]
    span = suffix_start - start
    sizes = []
    if span % frame_size == 0:
        sizes.append(span)
    if span > 0 and span % 2 == 0 and content[suffix_start - 1] == 0 and (span - 1) % frame_size == 0:
        sizes.append(span - 1)
    if len(sizes) != 1:
        raise ValueError("Invalid WAV: cannot determine PCM data length")
    return sizes[0]


def normalize_wav_header(content: bytes) -> bytes:
    """Return RIFF/WAVE bytes with streaming size fields filled in.

    A complete chunk layout is required. Ordinary truncated chunks and ambiguous
    PCM boundaries raise ValueError. Other containers pass through unchanged.
    """
    if not content.startswith(b"RIFF"):
        return content
    if len(content) < 12:
        raise ValueError("Invalid WAV: truncated RIFF header")
    if content[8:12] != b"WAVE":
        return content

    riff_size = _SIZE.unpack_from(content, 4)[0]
    actual_riff_size = len(content) - 8
    if riff_size != actual_riff_size and not _is_streaming_size(riff_size):
        raise ValueError("Invalid WAV: RIFF size does not match the payload")

    offset = 12
    seen_format = False
    frame_size = None
    data_size_fix: tuple[int, int] | None = None
    seen_data = False
    while offset < len(content):
        if offset + _CHUNK.size > len(content):
            raise ValueError("Invalid WAV: truncated chunk header")
        name, size = _CHUNK.unpack_from(content, offset)
        if not _FOURCC.fullmatch(name):
            raise ValueError("Invalid WAV: invalid chunk ID")
        start = offset + _CHUNK.size
        if name == b"data":
            if not seen_format or seen_data:
                raise ValueError("Invalid WAV: expected one data chunk after fmt")
            seen_data = True
            if start + size > len(content) and _is_streaming_size(size):
                if frame_size is None:
                    raise ValueError("Invalid WAV: streaming data requires PCM")
                size = _find_data_size(content, start, frame_size)
                data_size_fix = (offset + 4, size)
            if frame_size is not None and size % frame_size:
                raise ValueError("Invalid WAV: incomplete PCM frame")

        end = start + size
        if end > len(content):
            raise ValueError(f"Invalid WAV: truncated {name!r} chunk")
        if name == b"fmt ":
            if seen_format:
                raise ValueError("Invalid WAV: duplicate fmt chunk")
            seen_format = True
            frame_size = _format_frame_size(content, start, end)
        offset = end + (size & 1) if end < len(content) else end

    if not seen_data:
        raise ValueError("Invalid WAV: missing fmt or data chunk")
    if riff_size == actual_riff_size and data_size_fix is None:
        return content

    with BytesIO(content) as normalized:
        normalized.seek(4)
        normalized.write(_SIZE.pack(actual_riff_size))
        if data_size_fix is not None:
            position, size = data_size_fix
            normalized.seek(position)
            normalized.write(_SIZE.pack(size))
        normalized.seek(0)
        try:
            with wave.open(normalized, "rb"):
                return normalized.getvalue()
        except (wave.Error, EOFError) as exc:
            raise ValueError("Invalid WAV: repaired header is unreadable") from exc
