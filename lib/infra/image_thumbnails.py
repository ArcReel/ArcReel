"""On-demand image thumbnails for public media endpoints (``?w=``).

Contract:
- Raster images only (.png / .jpg / .jpeg / .webp); tiers are in :data:`THUMBNAIL_WIDTHS`.
  Round other positive integers up to the next tier, capped at 1280; invalid values mean absent.
- If the EXIF-corrected original is no wider than the target, callers return its original bytes.
- Output is WebP, scaled proportionally to the target width.

Disk cache: ``<cache_root>/<缓存命名空间>/<源路径摘要>/w<宽度>-<源 mtime_ns>-<源 size>.webp``.
Any source-attribute change produces a new filename. Writing a new version also
removes old versions for that source path, retaining at most the current version
per width. Concurrent requests may hold stat from before replacement: recheck
source identity before publishing and preserve the current source version during
cleanup. Old requests neither publish new content under an old key nor delete
new versions. Deleted source/project cache directories remain until the cache
root is removed; this does not affect correctness.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import stat
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from lib.infra.image_utils import MAX_UPLOAD_PIXELS

logger = logging.getLogger(__name__)

#: Allowed thumbnail-width tiers in ascending order.
THUMBNAIL_WIDTHS: tuple[int, ...] = (160, 320, 640, 1280)
#: Thumbnail source extensions, lowercase.
THUMBNAIL_SOURCE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_WEBP_QUALITY = 80
_THUMBNAIL_SUFFIX = ".webp"
# EXIF orientations 5-8 require transposition, swapping corrected width and height.
_EXIF_ORIENTATION = 0x0112
_TRANSPOSED_ORIENTATIONS = frozenset({5, 6, 7, 8})


def snap_thumbnail_width(raw: str | None) -> int | None:
    """Snap raw ``?w=`` to a tier; absent, nonnumeric or nonpositive values return None as if omitted."""
    if not raw or not raw.isascii() or not raw.isdigit():
        return None
    try:
        requested = int(raw)
    except ValueError:  # An excessively long digit string exceeds the int conversion limit.
        return THUMBNAIL_WIDTHS[-1]
    if requested <= 0:
        return None
    return next((width for width in THUMBNAIL_WIDTHS if width >= requested), THUMBNAIL_WIDTHS[-1])


def is_thumbnail_source(path: Path) -> bool:
    """Whether the file extension supports ``?w=`` thumbnails."""
    return path.suffix.lower() in THUMBNAIL_SOURCE_EXTENSIONS


def _source_dir(cache_dir: Path, source_key: str) -> Path:
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:32]
    return cache_dir / digest


def _version_prefix(source_stat: os.stat_result) -> str:
    return f"{source_stat.st_mtime_ns}-{source_stat.st_size}"


def thumbnail_cache_path(cache_dir: Path, source_key: str, source_stat: os.stat_result, width: int) -> Path:
    """Cache location; ``source_key`` uniquely identifies the source within its namespace, such as a project-relative path."""
    return _source_dir(cache_dir, source_key) / f"w{width}-{_version_prefix(source_stat)}{_THUMBNAIL_SUFFIX}"


def ensure_image_thumbnail(
    source: Path,
    source_stat: os.stat_result,
    *,
    cache_dir: Path,
    source_key: str,
    width: int,
) -> tuple[Path, os.stat_result] | None:
    """Return cache path and stat for a ``width`` thumbnail, or None to serve the original.

    Decoding/encoding blocks synchronously; callers use a worker thread. Return None
    when the original is no wider, exceeds ``MAX_UPLOAD_PIXELS``, cannot be decoded,
    or the cache cannot be written.

    Use a same-directory temporary file + ``os.replace``. Concurrent requests write
    separate temporary files, so the published file is always a complete encoding.
    Match its mtime to the source so regenerating a key gives the same ETag. If the
    source is replaced during encoding, return None for the caller to serve the original.
    """
    target = thumbnail_cache_path(cache_dir, source_key, source_stat, width)
    cached_stat = _regular_file_stat(target)
    if cached_stat is not None:
        return target, cached_stat

    encoded = _encode_thumbnail(source, width)
    if encoded is None:
        return None
    version = _version_prefix(source_stat)
    if _current_version(source) != version:
        return None

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, encoded, mtime_ns=source_stat.st_mtime_ns)
    except OSError:
        # Unwritable cache, etc.: thumbnails are an optimization; serve the original rather than failing.
        logger.warning("写入图片缩略图缓存失败，回退原图: %s", target, exc_info=True)
        return None
    _remove_stale_versions(target.parent, keep={version, _current_version(source)})

    cached_stat = _regular_file_stat(target)
    if (
        cached_stat is None
    ):  # Concurrent old-version cleanup removed it after writing because the source changed in between.
        return None
    return target, cached_stat


def _current_version(source: Path) -> str | None:
    source_stat = _regular_file_stat(source)
    return None if source_stat is None else _version_prefix(source_stat)


def _regular_file_stat(path: Path) -> os.stat_result | None:
    try:
        result = path.stat()
    except OSError:
        return None
    return result if stat.S_ISREG(result.st_mode) else None


def _oriented_width(image: Image.Image) -> int:
    orientation = image.getexif().get(_EXIF_ORIENTATION)
    return image.height if orientation in _TRANSPOSED_ORIENTATIONS else image.width


def _encode_thumbnail(source: Path, width: int) -> bytes | None:
    """Decode and encode a ``width``-wide WebP; return None if resizing is unnecessary or impossible."""
    try:
        with Image.open(source) as image:
            if image.width * image.height > MAX_UPLOAD_PIXELS:
                return None
            oriented_width = _oriented_width(image)
            if oriented_width <= width:
                return None
            # JPEG can downsample by 1/2, 1/4 or 1/8 during decode; draft keeps dimensions at least as requested.
            scale = width / oriented_width
            image.draft("RGB", (max(1, round(image.width * scale)), max(1, round(image.height * scale))))
            oriented = ImageOps.exif_transpose(image)
            has_alpha = oriented.mode in ("RGBA", "LA", "PA") or "transparency" in oriented.info
            converted = oriented.convert("RGBA" if has_alpha else "RGB")
            height = max(1, round(converted.height * width / converted.width))
            thumbnail = converted.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)
            buffer = BytesIO()
            thumbnail.save(buffer, format="WEBP", quality=_WEBP_QUALITY)
            return buffer.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError):
        logger.warning("生成图片缩略图失败，回退原图: %s", source, exc_info=True)
        return None


def _atomic_write(target: Path, data: bytes, *, mtime_ns: int) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=_THUMBNAIL_SUFFIX)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.utime(tmp_path, ns=(mtime_ns, mtime_ns))
        try:
            os.replace(tmp_path, target)
        except PermissionError:
            # Windows cannot replace a target being read; reuse an existing same-key file with identical content.
            if not target.exists():
                raise
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()  # hard-delete: temp -- temporary file for atomic thumbnail publication


def _remove_stale_versions(source_dir: Path, *, keep: set[str | None]) -> None:
    """Delete cached versions with other mtime/size values than ``keep``; cleanup failure does not fail the request."""
    for entry in source_dir.glob(f"w*{_THUMBNAIL_SUFFIX}"):
        version = entry.stem.split("-", 1)[1] if "-" in entry.stem else ""
        if version not in keep:
            with contextlib.suppress(OSError):
                entry.unlink()  # hard-delete: derived -- cached thumbnail of an older version
