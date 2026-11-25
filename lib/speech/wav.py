"""Normalize streaming sizes in downloaded RIFF/WAVE payloads."""

import re
import struct
import wave
from io import BytesIO

_CHUNK = struct.Struct("<4sI")
_SIZE = struct.Struct("<I")
_FORMAT = struct.Struct("<HHIIHH")
_FOURCC = re.compile(rb"[ -~]{4}")
_TRAILING_FOURCC = re.compile(rb"[A-Za-z0-9]{2,4} *")
_EMPTY_FILLER_IDS = {b"JUNK", b"PAD "}
_PCM_SUBFORMAT = bytes.fromhex("01000000 0000 1000 8000 00aa00389b71")


def _is_streaming_size(size: int) -> bool:
    return size == 0 or 0x7FFF0000 <= size <= 0x7FFFFFFF or 0xFFFF0000 <= size <= 0xFFFFFFFF


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


def _has_list_subchunks(content: bytes, start: int, end: int) -> bool:
    offset = start + 4
    if offset == end:
        return False
    while offset + _CHUNK.size <= end:
        name, size = _CHUNK.unpack_from(content, offset)
        if not _FOURCC.fullmatch(name):
            return False
        offset += _CHUNK.size + size + (size & 1)
    return offset == end


def _find_data_size(content: bytes, start: int, frame_size: int) -> int:
    """Infer PCM length from frame alignment and the trailing RIFF chunk chain.

    A LIST with complete subchunks supports retaining its chain. Without that
    structure, require a unique frame-aligned interpretation.
    """
    end = len(content)
    boundaries = {end: False}
    structured_boundary = None
    for offset in range((end - _CHUNK.size) & ~1, start - 1, -2):
        size = _SIZE.unpack_from(content, offset + 4)[0]
        chunk_end = offset + _CHUNK.size + size
        if chunk_end == end:
            next_chunk = end
        else:
            next_chunk = chunk_end + (size & 1)
            if size & 1 and (next_chunk > end or content[next_chunk - 1] != 0):
                continue
        has_list = boundaries.get(next_chunk)
        if has_list is None or not _TRAILING_FOURCC.fullmatch(content, offset, offset + 4):
            continue
        name = content[offset : offset + 4]
        if size == 0 and name not in _EMPTY_FILLER_IDS:
            continue
        if name == b"LIST":
            list_start = offset + _CHUNK.size
            if size < 4 or not _TRAILING_FOURCC.fullmatch(content, list_start, list_start + 4):
                continue
            has_list = has_list or _has_list_subchunks(content, list_start, chunk_end)
        boundaries[offset] = has_list
        if has_list:
            structured_boundary = offset

    candidate_boundaries = boundaries if structured_boundary is None else (structured_boundary,)
    sizes = []
    for boundary in candidate_boundaries:
        span = boundary - start
        if span % frame_size == 0:
            sizes.append(span)
        if span > 0 and span % 2 == 0 and content[boundary - 1] == 0 and (span - 1) % frame_size == 0:
            sizes.append(span - 1)
        if len(sizes) > 1:
            break
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
            is_streaming_data = (start + size > len(content) and _is_streaming_size(size)) or (
                size == 0 and start < len(content) and riff_size != actual_riff_size
            )
            if is_streaming_data:
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
        if frame_size is not None:
            normalized.seek(0)
            try:
                with wave.open(normalized, "rb"):
                    pass
            except (wave.Error, EOFError) as exc:
                raise ValueError("Invalid WAV: repaired header is unreadable") from exc
        return normalized.getvalue()
