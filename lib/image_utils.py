"""
Image utility helpers.

Used by WebUI upload endpoints to validate, compress, and normalize uploaded images.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

_COMPRESS_THRESHOLD = 2 * 1024 * 1024  # 2 MB
_MAX_LONG_EDGE = 2048
_JPEG_QUALITY = 85
# sentinel：意为「不向 PIL 传 subsampling」。PIL 的 subsampling=-1 仅在 JPEG→JPEG 时表示
# “保持源色度”，对 PNG/其它源解码后再编码不合法，故默认用本 sentinel 拦掉，保证缺省行为不变。
_SUBSAMPLING_KEEP = -1

# EXIF Orientation tag（ImageOps.exif_transpose 读取的字段）
_EXIF_ORIENTATION = 0x0112


def _open_oriented(content: bytes, *, target_modes: tuple[str, ...], fallback_mode: str) -> Image.Image:
    """解码 + EXIF 方向矫正 + 收敛到目标颜色模式。返回与源解码器解耦的图像对象。"""
    with Image.open(BytesIO(content)) as src:
        img = ImageOps.exif_transpose(src)
        if img.mode not in target_modes:
            img = img.convert(fallback_mode)
        return img.copy() if img is src else img


def _fit_long_edge(img: Image.Image, max_long_edge: int) -> Image.Image:
    """长边超限时等比缩放；极端宽高比下短边钳到至少 1 像素，避免 resize(…, 0) 报错。"""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge:
        return img
    scale = max_long_edge / long_edge
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)


def convert_image_bytes_to_png(content: bytes) -> bytes:
    """
    Convert arbitrary image bytes (jpg/png/webp/...) into PNG bytes.

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with _open_oriented(content, target_modes=("RGB", "RGBA"), fallback_mode="RGBA") as img:
            out = BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except Exception as e:
        raise ValueError("Invalid image") from e


def normalize_storyboard_upload(content: bytes, *, max_long_edge: int = _MAX_LONG_EDGE) -> bytes:
    """
    将上传的分镜图归一化为 PNG 字节：exif 矫正方向、长边超限时等比缩放。

    分镜图 canonical 路径固定为 .png（resource_paths / VersionManager / restore
    均按此扩展名工作），因此无论输入格式一律转 PNG。
    已合规的 PNG（模式/方向/尺寸均达标）原样返回，不做无谓的解码重编码。

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with Image.open(BytesIO(content)) as probe:
            if (
                probe.format == "PNG"
                and probe.mode in ("RGB", "RGBA")
                and max(probe.size) <= max_long_edge
                and probe.getexif().get(_EXIF_ORIENTATION, 1) == 1
            ):
                return content

        with _open_oriented(content, target_modes=("RGB", "RGBA"), fallback_mode="RGBA") as img:
            img = _fit_long_edge(img, max_long_edge)
            out = BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except Exception as e:
        raise ValueError("Invalid image") from e


def validate_image_bytes(content: bytes) -> None:
    """Validate that *content* is a decodable image.

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with Image.open(BytesIO(content)) as img:
            img.verify()
    except Exception as e:
        raise ValueError("Invalid image") from e


def compress_image_bytes(
    content: bytes,
    *,
    max_long_edge: int = _MAX_LONG_EDGE,
    quality: int = _JPEG_QUALITY,
    subsampling: int = _SUBSAMPLING_KEEP,
) -> bytes:
    """
    将任意图片字节压缩为 JPEG：等比缩放到长边不超过 max_long_edge，
    quality 控制 JPEG 压缩质量。

    subsampling 控制 JPEG 色度抽样（0=4:4:4 视觉无损，2=4:2:0 默认）；缺省为
    _SUBSAMPLING_KEEP，此时完全不向 PIL 传该参数，保持 PIL 默认（与历史行为一致）。

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with _open_oriented(content, target_modes=("RGB",), fallback_mode="RGB") as img:
            img = _fit_long_edge(img, max_long_edge)
            save_kwargs: dict[str, object] = {"format": "JPEG", "quality": quality, "optimize": True}
            if subsampling >= 0:
                save_kwargs["subsampling"] = subsampling
            out = BytesIO()
            img.save(out, **save_kwargs)  # pyright: ignore[reportArgumentType]
            return out.getvalue()
    except Exception as e:
        raise ValueError("Invalid image") from e


def normalize_uploaded_image(
    content: bytes,
    original_suffix: str,
    *,
    compress_threshold: int = _COMPRESS_THRESHOLD,
) -> tuple[bytes, str]:
    """Validate (and optionally compress) an uploaded image.

    If *content* exceeds *compress_threshold* bytes the image is compressed to
    JPEG and ``".jpg"`` is returned as the suffix.  Otherwise the original
    bytes are returned after validation, together with *original_suffix* (or
    ``".png"`` when empty).

    Returns:
        ``(processed_content, final_suffix)``

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    if len(content) > compress_threshold:
        return compress_image_bytes(content), ".jpg"
    validate_image_bytes(content)
    return content, original_suffix or ".png"
