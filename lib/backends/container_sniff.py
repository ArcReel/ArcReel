"""Detect persisted-byte containers from headers only, never extensions.

Persistence names files using canonical final-video ``.mp4`` paths or connection-test ``artifact.mp4`` /
``artifact.png`` names; providers' export settings determine actual containers. Mismatches make
extension-declared MIME mislead browsers. Centralize container detection here: one magic-byte authority
serves postpersistence container checks and artifact-read MIME, avoiding duplicate tables updated on
only one side when adding containers.

Unlike :mod:`lib.backends.data_uri`, which declares outbound material MIME and supports GIF / HEIC plus
extension fallbacks, this module recognizes only final-video/storyboard-image containers. Unknown stays unknown.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Header bytes needed to reach ``RIFF....WEBP`` second magic bytes and ``ftyp`` major brands.
CONTAINER_HEAD_BYTES = 12

#: ISO BMFF major brands belonging to still images, not final videos.
#:
#: Identify these rather than whitelist video brands, an open set (``isom`` / ``iso2`` /
#: ``mp41`` / ``mp42`` / ``avc1`` / ``qt`` / ``M4V`` / ``dash`` …, still growing across exporters). Whitelists
#: would misclassify valid final videos as unknown, worse than missing a HEIC. Brand/MIME mapping reuses
#: the existing ``data_uri._image_mime_from_bytes`` rules.
_ISO_BMFF_IMAGE_BRANDS: Mapping[bytes, str] = {
    b"heic": "image/heic",
    b"heix": "image/heic",
    b"hevc": "image/heic",
    b"hevx": "image/heic",
    b"mif1": "image/heif",
    b"msf1": "image/heif",
    b"avif": "image/avif",
    b"avis": "image/avif",
}


def sniff_container(head: bytes) -> str | None:
    """Container MIME for these header bytes, or ``None`` if unknown.

    ``ftyp`` at bytes 4–8 identifies ISO BMFF only, also used by HEIC / AVIF stills. Exclude them by major
    brand or HEIC named ``.mp4`` would pass as final video. All other brands yield ``video/mp4``:
    ``.mov`` / ``.m4v`` brands vary by exporter but play like ``.mp4``. Narrow whitelists would reject
    valid final videos as unknown.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return _ISO_BMFF_IMAGE_BRANDS.get(head[8:12], "video/mp4")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return None
