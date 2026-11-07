"""
Image utility helpers.

Used by WebUI upload endpoints to validate, compress, and normalize uploaded images.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

_COMPRESS_THRESHOLD = 2 * 1024 * 1024  # 2 MB
_MAX_LONG_EDGE = 2048
# Decoded pixel limit (8192 x 8192). Byte limits cannot protect this: low-entropy PNG can fit 100M+ pixels
# within 30MB. PIL raises DecompressionBombError only above twice its MAX_IMAGE_PIXELS;
# between once and twice that value (about 89M-178M pixels) it merely warns, yet one RGBA buffer uses hundreds of MB.
# Grid composites reach 4K square (about 17M pixels); allow several times that before explicitly rejecting.
MAX_UPLOAD_PIXELS = 8192 * 8192
_JPEG_QUALITY = 85
# Sentinel means omit subsampling from PIL. subsampling=-1 means retain source chroma only for JPEG-to-JPEG;
# it is invalid when re-encoding decoded PNG/other sources. This default sentinel preserves omission behavior.
_SUBSAMPLING_KEEP = -1

# EXIF Orientation tag read by ImageOps.exif_transpose.
_EXIF_ORIENTATION = 0x0112


#: Image formats accepted for upload/import and original-format storage. Keys are PIL format names;
#: values are extensions, with the first used when choosing an extension from content.
IMAGE_FORMAT_SUFFIXES: dict[str, tuple[str, ...]] = {
    "PNG": (".png",),
    "JPEG": (".jpg", ".jpeg"),
    "WEBP": (".webp",),
}
#: All extensions for these formats, shared by upload validation, serving and path recognition.
IMAGE_FILE_EXTENSIONS: tuple[str, ...] = tuple(suffix for group in IMAGE_FORMAT_SUFFIXES.values() for suffix in group)


class ImagePixelLimitError(ValueError):
    """Decoded pixel count exceeds ``MAX_UPLOAD_PIXELS``.

    Subclass ValueError so callers without special handling return 400, as for other
    invalid images.
    """


def _ensure_pixel_budget(size: tuple[int, int]) -> None:
    """Reject oversized images before allocating the decoded pixel buffer."""
    if size[0] * size[1] > MAX_UPLOAD_PIXELS:
        raise ImagePixelLimitError(f"image has {size[0]}x{size[1]} pixels, over the {MAX_UPLOAD_PIXELS} limit")


def _open_oriented(content: bytes, *, target_modes: tuple[str, ...], fallback_mode: str) -> Image.Image:
    """Decode, correct EXIF orientation and convert to the target color mode; return an image independent of its decoder."""
    with Image.open(BytesIO(content)) as src:
        img = ImageOps.exif_transpose(src)
        if img.mode not in target_modes:
            img = img.convert(fallback_mode)
        return img.copy() if img is src else img


def _fit_long_edge(img: Image.Image, max_long_edge: int) -> Image.Image:
    """Scale proportionally when the long edge exceeds the limit; clamp the short edge to at least 1 pixel for extreme ratios to avoid resize(..., 0)."""
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


def normalize_storyboard_upload(content: bytes, *, max_long_edge: int | None = _MAX_LONG_EDGE) -> bytes:
    """Normalize uploaded storyboard images to PNG bytes, correcting EXIF orientation and limiting the long edge proportionally.

    Canonical storyboard paths use .png (resource_paths / VersionManager / restore),
    so all input formats convert to PNG. Return compliant PNGs unchanged when mode,
    orientation and size already match, avoiding unnecessary decode/re-encode.

    ``max_long_edge=None`` disables resizing, retaining only format/orientation
    normalization. Use it for resolution-dependent uploads such as grid composites,
    avoiding silent downsampling and distorted cells when splitting 4K composites.

    Raises:
        ImagePixelLimitError: if the decoded pixel count exceeds MAX_UPLOAD_PIXELS.
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with Image.open(BytesIO(content)) as probe:
            # Dimensions come from the header; no pixel buffer has been allocated yet.
            _ensure_pixel_budget(probe.size)
            if (
                probe.format == "PNG"
                and probe.mode in ("RGB", "RGBA")
                and (max_long_edge is None or max(probe.size) <= max_long_edge)
                and probe.getexif().get(_EXIF_ORIENTATION, 1) == 1
            ):
                return content

        with _open_oriented(content, target_modes=("RGB", "RGBA"), fallback_mode="RGBA") as img:
            if max_long_edge is not None:
                img = _fit_long_edge(img, max_long_edge)
            out = BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except ImagePixelLimitError:
        # The image parses but is oversized; preserve the error type for actionable caller guidance.
        raise
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
    """Compress arbitrary image bytes to JPEG, scaling proportionally to max_long_edge;
    quality controls JPEG compression quality.

    subsampling controls JPEG chroma sampling (0=4:4:4 visually lossless, 2=4:2:0 default).
    The default _SUBSAMPLING_KEEP omits this parameter entirely from PIL, retaining
    PIL defaults and historical behavior.

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with _open_oriented(content, target_modes=("RGB",), fallback_mode="RGB") as img:
            img = _fit_long_edge(img, max_long_edge)
            extra: dict[str, int] = {"subsampling": subsampling} if subsampling >= 0 else {}
            out = BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True, **extra)
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
