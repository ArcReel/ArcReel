"""Base64 data URI encoding for outbound requests.

Provider image/video APIs commonly accept inline `data:<mime>;base64,<内容>` materials, avoiding a file
service layer. Encoding is identical across providers; only extension-to-MIME mappings differ.
Providers own/pass their tables; this module owns no provider-specific rules.

Unlike `lib/infra/image_utils.py` / `lib/speech/audio_utils.py`, which guard inbound uploads through
size compression, duration/format checks, this module only encodes persisted materials into outbound
request-body fields.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path

_DEFAULT_IMAGE_MIME = "image/png"


def _image_mime_from_bytes(content: bytes) -> str | None:
    """Detect common image MIME types from headers; unknown formats use extension-based compatibility fallback."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        brand = content[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"hevx"}:
            return "image/heic"
        if brand in {b"mif1", b"msf1"}:
            return "image/heif"
    return None


def file_to_data_uri(path: Path, mime: str) -> str:
    """Local file → base64 data URI; propagate unreadable-file OSError for callers to interpret."""
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def image_to_data_uri(image_path: Path, mime_types: Mapping[str, str]) -> str:
    """Local image → base64 data URI, preferring MIME detection from actual bytes.

    Uploads/generation/compression may leave filenames out of sync with encoding. Extension-declared
    MIME would cause providers to reject such Data URLs as format forgery. When headers are unrecognized,
    retain provider `mime_types` extension lookup and PNG fallback for unregistered extensions.
    """
    content = image_path.read_bytes()
    mime = _image_mime_from_bytes(content) or mime_types.get(image_path.suffix.lower(), _DEFAULT_IMAGE_MIME)
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
