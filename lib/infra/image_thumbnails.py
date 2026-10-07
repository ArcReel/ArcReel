"""公开媒体端点的按需图片缩略图（``?w=``）。

契约：
- 只对栅格图（.png / .jpg / .jpeg / .webp）生效；宽度档位见 :data:`THUMBNAIL_WIDTHS`，
  其余正整数向上吸附到最近档位（封顶 1280），非法值视同未传。
- 原图（按 EXIF 方向矫正后）不宽于目标宽度时不生成，调用方直接返回原图字节。
- 产物为 WebP，等比缩放到目标宽度。

磁盘缓存：``<cache_root>/<缓存命名空间>/<源路径摘要>/w<宽度>-<源 mtime_ns>-<源 size>.webp``。
源文件任一属性变化都会落到新的文件名；同一源路径写入新版本时顺手删掉该路径下的旧版本，
所以每个源路径最多保留各档位的当前版本。并发请求可能拿着源文件被替换前的 stat：发布前
复核源文件版本，清理时保留源文件此刻的版本，旧请求既不按旧版本键发布新内容，也不删掉新版本。
源文件或项目被删除后，对应缓存目录留在原处（缓存根整目录删除即可回收，不影响正确性）。
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

#: 允许的缩略图宽度档位（升序）
THUMBNAIL_WIDTHS: tuple[int, ...] = (160, 320, 640, 1280)
#: 可生成缩略图的源文件扩展名（小写）
THUMBNAIL_SOURCE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_WEBP_QUALITY = 80
_THUMBNAIL_SUFFIX = ".webp"
# EXIF Orientation 取 5–8 时图像需要转置，矫正后宽高互换
_EXIF_ORIENTATION = 0x0112
_TRANSPOSED_ORIENTATIONS = frozenset({5, 6, 7, 8})


def snap_thumbnail_width(raw: str | None) -> int | None:
    """把 ``?w=`` 的原始值吸附到档位；缺省、非数字或非正数返回 None（按未传处理）。"""
    if not raw or not raw.isascii() or not raw.isdigit():
        return None
    try:
        requested = int(raw)
    except ValueError:  # 超长数字串超过 int 转换位数上限
        return THUMBNAIL_WIDTHS[-1]
    if requested <= 0:
        return None
    return next((width for width in THUMBNAIL_WIDTHS if width >= requested), THUMBNAIL_WIDTHS[-1])


def is_thumbnail_source(path: Path) -> bool:
    """该文件是否可以按 ``?w=`` 生成缩略图（只看扩展名）。"""
    return path.suffix.lower() in THUMBNAIL_SOURCE_EXTENSIONS


def _source_dir(cache_dir: Path, source_key: str) -> Path:
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:32]
    return cache_dir / digest


def _version_prefix(source_stat: os.stat_result) -> str:
    return f"{source_stat.st_mtime_ns}-{source_stat.st_size}"


def thumbnail_cache_path(cache_dir: Path, source_key: str, source_stat: os.stat_result, width: int) -> Path:
    """缓存文件位置；``source_key`` 是源文件在缓存命名空间内的唯一标识（如项目内相对路径）。"""
    return _source_dir(cache_dir, source_key) / f"w{width}-{_version_prefix(source_stat)}{_THUMBNAIL_SUFFIX}"


def ensure_image_thumbnail(
    source: Path,
    source_stat: os.stat_result,
    *,
    cache_dir: Path,
    source_key: str,
    width: int,
) -> tuple[Path, os.stat_result] | None:
    """返回 ``width`` 宽缩略图的缓存路径与 stat；应直接返回原图时返回 None。

    同步阻塞（解码与编码），调用方放进工作线程。返回 None 的情形：原图不宽于目标宽度、
    像素总数超出 ``MAX_UPLOAD_PIXELS``、源文件无法解码，或缓存写不进去。

    缓存写入走同目录临时文件 + ``os.replace``：并发请求同一缓存键时各写各的临时文件，
    最终文件始终是某一次完整的编码结果。缓存文件的 mtime 对齐源文件 mtime，
    使重新生成的同一缓存键得到相同的 ETag。编码期间源文件被替换时返回 None，
    由调用方按原图处理。
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
        # 缓存目录不可写等情形：缩略图只是优化，回退原图而不让请求失败
        logger.warning("写入图片缩略图缓存失败，回退原图: %s", target, exc_info=True)
        return None
    _remove_stale_versions(target.parent, keep={version, _current_version(source)})

    cached_stat = _regular_file_stat(target)
    if cached_stat is None:  # 写入后被并发的旧版本清理删掉（源文件刚好在此期间变化）
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
    """解码源图并编码为 ``width`` 宽的 WebP；不需要或无法缩放时返回 None。"""
    try:
        with Image.open(source) as image:
            if image.width * image.height > MAX_UPLOAD_PIXELS:
                return None
            oriented_width = _oriented_width(image)
            if oriented_width <= width:
                return None
            # JPEG 可在解码阶段按 1/2、1/4、1/8 降采样，draft 保证结果不小于请求尺寸
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
            # Windows：目标正被另一请求读取时不能替换；已有的同键文件内容相同，沿用即可
            if not target.exists():
                raise
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def _remove_stale_versions(source_dir: Path, *, keep: set[str | None]) -> None:
    """删掉同一源路径下 ``keep`` 以外版本（mtime / size 不同）的缓存；清理失败不影响本次请求。"""
    for entry in source_dir.glob(f"w*{_THUMBNAIL_SUFFIX}"):
        version = entry.stem.split("-", 1)[1] if "-" in entry.stem else ""
        if version not in keep:
            with contextlib.suppress(OSError):
                entry.unlink()
