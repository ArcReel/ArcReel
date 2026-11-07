"""Unified aspect-ratio-first, resolution-second size calculations.

Generated media has one output aspect-ratio source, the project's ``aspect_ratio``, which always takes priority; resolution
(a preset tier or custom value) sets only the pixel scale, **not the aspect ratio**. This module implements that principle as
two pure functions and tier-to-short-edge tables for backends that accept pixel dimensions:

- :func:`aspect_size`: given an aspect ratio and target short edge, calculate ``(width, height)`` preserving the exact ratio and divisible by ``round_to``.
  Valid sizes are integer multiples of ``(aw·round_to, ah·round_to)``, so aspect ratio has
  zero error and both dimensions are divisible (satisfying gpt-image-2's requirement that both be divisible by 16).
- :func:`resolution_to_short_edge`: normalize resolution (``None`` / a tier label / a custom ``width*height``
  value / a plain number) into short-edge pixels, stripping a custom value's own ratio by taking ``min``.

Tier short-edge tables are shared across backends (see ``IMAGE_TIER_SHORT_EDGE`` / ``VIDEO_TIER_SHORT_EDGE``);
each backend then clamps using its own ``max_long_edge`` / ``max_total_pixels`` constraints.
"""

from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger(__name__)

# Fallback short edge (720P) when resolution is absent but dimensions are needed to control aspect ratio.
DEFAULT_SHORT_EDGE = 720

# Shared tiers → short-edge pixels. Each backend clamps to its own constraints (max_long_edge / max_total_pixels).
# Images: 512px / 1K / 2K / 4K; DashScope wan reuses this table for 1K/2K/4K.
IMAGE_TIER_SHORT_EDGE: dict[str, int] = {"512px": 512, "1K": 1024, "2K": 1440, "4K": 2160}
# Video: 480p / 720p / 1080p / 4K.
VIDEO_TIER_SHORT_EDGE: dict[str, int] = {"480p": 480, "720p": 720, "1080p": 1080, "4K": 2160}

# Default ratio: controlled source (projects are mostly fixed portrait short dramas); fall back to portrait 9:16 on parse failure for least surprise.
_DEFAULT_ASPECT: tuple[int, int] = (9, 16)

# Separators for custom "width*height" values: ASCII x, asterisk, and the full-width multiplication sign.
_WH_RE = re.compile(r"^\s*(\d+)\s*[xX×*]\s*(\d+)\s*$")
# Aspect-ratio separators: ASCII/full-width colon.
_RATIO_SEP_RE = re.compile(r"[:：]")


def parse_aspect_ratio(aspect_ratio: str) -> tuple[int, int]:
    """Parse ``"9:16"`` into reduced coprime ``(9, 16)``; invalid values fall back to ``(9, 16)`` with a warning."""
    try:
        parts = _RATIO_SEP_RE.split(aspect_ratio.strip())
        if len(parts) != 2:
            raise ValueError(aspect_ratio)
        aw, ah = int(parts[0]), int(parts[1])
        if aw <= 0 or ah <= 0:
            raise ValueError(aspect_ratio)
    except (AttributeError, ValueError, IndexError):
        logger.warning("无法解析 aspect_ratio=%r，回退默认 %d:%d", aspect_ratio, *_DEFAULT_ASPECT)
        return _DEFAULT_ASPECT
    g = math.gcd(aw, ah)
    return aw // g, ah // g


def aspect_size(
    aspect_ratio: str,
    short_edge: int,
    *,
    round_to: int = 16,
    max_long_edge: int | None = None,
    max_total_pixels: int | None = None,
    max_ratio: float | None = None,
) -> tuple[int, int]:
    """Calculate ``(width, height)`` from aspect ratio and target short edge, preserving the exact ratio and divisibility by ``round_to``.

    Valid size = ``(aw·round_to·t, ah·round_to·t)`` (``aw:ah`` is the reduced ratio and ``t`` a positive integer)
    → zero ratio error, with both dimensions divisible by ``round_to``. Choose ``t`` to bring the short edge closest to ``short_edge``,
    then clamp by ``max_long_edge`` / ``max_total_pixels`` (whichever is stricter), with a floor of ``t≥1``; even when
    the smallest valid size exceeds constraints, never use 0; leave the smallest valid size for upstream/API judgment.

    - ``max_long_edge``: long-edge pixel limit (such as gpt-image-2's 4K=3840; qwen-edit's 2048 per side).
    - ``max_total_pixels``: total pixel budget limit (such as DashScope standard 2048², 4K 4096²).
    - ``max_ratio``: most extreme supported ratio (gpt-image-2=3 means 1:3~3:1; DashScope=8 means
      1:8~8:1); exceeding it warns without raising, leaving judgment to the API.
    """
    aw, ah = parse_aspect_ratio(aspect_ratio)

    if max_ratio is not None:
        ratio = max(aw / ah, ah / aw)
        if ratio > max_ratio + 1e-9:
            logger.warning(
                "aspect_ratio=%s 比例 %.2f 超出后端支持上限 %.2f，可能被 API 拒绝或裁剪",
                aspect_ratio,
                ratio,
                max_ratio,
            )

    short_comp = min(aw, ah)
    long_comp = max(aw, ah)
    short_unit = round_to * short_comp  # Short-edge pixel increment per unit of t

    # The t bringing the short edge closest to short_edge
    t = max(1, round(short_edge / short_unit))

    # Clamp to the long-edge pixel limit. Even when the limit cannot accommodate the smallest valid size (t=1), keep t=1,
    # never 0: prefer slightly exceeding the limit for the API to judge over producing an invalid zero size.
    if max_long_edge is not None:
        max_t_long = max_long_edge // (round_to * long_comp)
        t = min(t, max(1, max_t_long))

    # Clamp total pixel budget: pixels = aw·ah·round_to²·t² ≤ max_total_pixels (also with floor t≥1)
    if max_total_pixels is not None:
        denom = aw * ah * round_to * round_to
        max_t_pixels = math.isqrt(max_total_pixels // denom) if denom > 0 else 0
        t = min(t, max(1, max_t_pixels))

    t = max(1, t)
    return aw * round_to * t, ah * round_to * t


def resolution_to_short_edge(
    resolution: str | None,
    *,
    tier_map: dict[str, int],
    default_short: int = DEFAULT_SHORT_EDGE,
) -> int:
    """Normalize resolution to short-edge pixels.

    - ``None`` / empty string → ``default_short``
    - Tier label (case-insensitive, such as ``"2K"`` / ``"720p"``) → look up ``tier_map``
    - Custom ``"width*height"`` → ``min(width, height)``, **stripping its own ratio** (aspect_ratio alone determines the ratio)
    - Plain number → use directly as the short edge
    - Unparseable → ``default_short`` + warning
    """
    if resolution is None:
        return default_short
    s = resolution.strip()
    if not s:
        return default_short

    norm = {k.lower(): v for k, v in tier_map.items()}
    if s.lower() in norm:
        return norm[s.lower()]

    m = _WH_RE.match(s)
    if m:
        return min(int(m.group(1)), int(m.group(2)))

    if s.isdigit():
        return int(s)

    logger.warning("无法解析 resolution=%r，回退默认短边 %d", resolution, default_short)
    return default_short


def short_edge_to_resolution(short_edge: int, *, tier_map: dict[str, int]) -> str:
    """Describe short-edge pixels for display: use the tier label on an exact match, otherwise the pixel value.

    :func:`resolution_to_short_edge` in reverse for display. Tiers are discrete; an arbitrary short edge need not match one
    (a workflow author's 848 is neither 720p nor 1080p), and this description explains what happens without a selected tier.
    Calling 848 the nearest tier, 720p, would suggest selecting that tier produces the same image, when the short edge actually changes from 848
    to 720. Use tier labels only for exact equality; otherwise report the actual short edge so users see the true number.
    """
    for tier, edge in tier_map.items():
        if edge == short_edge:
            return tier
    return f"{short_edge}px"
