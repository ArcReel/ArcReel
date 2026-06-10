"""统一「比例优先、清晰度其次」的尺寸计算。

媒体生成的输出比例只有一个来源——项目的 ``aspect_ratio``，永远优先；分辨率
（预设档位或自定义值）只决定清晰度规模，**不决定比例**。本模块把这条原则收口成
两个纯函数 + 一组档位短边表，供各「用像素尺寸」的后端复用：

- :func:`aspect_size` —— 给定比例 + 短边目标，算出精确遵循比例、且被 ``round_to``
  整除的 ``(宽, 高)``。合法尺寸取 ``(aw·round_to, ah·round_to)`` 的整数倍，故比例
  零偏差、长宽天然整除（gpt-image-2 要求宽高均被 16 整除即由此满足）。
- :func:`resolution_to_short_edge` —— 把分辨率（``None`` / 档位词 / ``宽*高``
  自定义值 / 纯数字）规范化成「短边像素」，自定义值剥离其自带比例（取 ``min``）。

档位短边表跨后端统一（见 ``IMAGE_TIER_SHORT_EDGE`` / ``VIDEO_TIER_SHORT_EDGE``），
各后端再按自身像素约束用 ``max_long_edge`` / ``max_total_pixels`` 夹取。
"""

from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger(__name__)

# 缺分辨率但必需尺寸来控制比例时的兜底短边（720P）。
DEFAULT_SHORT_EDGE = 720

# 跨后端统一的档位 → 短边像素。各后端按自身约束（max_long_edge / max_total_pixels）夹取。
# 图片：512px / 1K / 2K / 4K；DashScope wan 的 1K/2K/4K 复用本表。
IMAGE_TIER_SHORT_EDGE: dict[str, int] = {"512px": 512, "1K": 1024, "2K": 1440, "4K": 2160}
# 视频：480p / 720p / 1080p / 4K。
VIDEO_TIER_SHORT_EDGE: dict[str, int] = {"480p": 480, "720p": 720, "1080p": 1080, "4K": 2160}

# 默认比例：来源受控（项目固定竖屏短剧居多），无法解析时回退竖屏 9:16，最小惊讶。
_DEFAULT_ASPECT: tuple[int, int] = (9, 16)

# 自定义 "宽*高" 值的分隔符：英文 x、星号、全角叉号。
_WH_RE = re.compile(r"^\s*(\d+)\s*[xX×*]\s*(\d+)\s*$")
# 比例分隔符：英文/全角冒号。
_RATIO_SEP_RE = re.compile(r"[:：]")


def parse_aspect_ratio(aspect_ratio: str) -> tuple[int, int]:
    """把 ``"9:16"`` 解析成约简互质的 ``(9, 16)``；非法值回退 ``(9, 16)`` 并 warn。"""
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
    """按比例 + 短边目标算出精确遵循比例、且被 ``round_to`` 整除的 ``(宽, 高)``。

    合法尺寸 = ``(aw·round_to·t, ah·round_to·t)``（``aw:ah`` 为约简比例，``t`` 正整数）
    → 比例零偏差、长宽均被 ``round_to`` 整除。``t`` 取使短边最接近 ``short_edge`` 的值，
    再受 ``max_long_edge`` / ``max_total_pixels`` 夹取（取更严者），下限 ``t≥1``——即便
    最小合法尺寸超出约束也不取 0，留最小合法尺寸由上游/API 判定。

    - ``max_long_edge``：长边像素上限（如 gpt-image-2 的 4K=3840；qwen-edit 单边 2048）。
    - ``max_total_pixels``：总像素预算上限（如 DashScope 标准 2048²、4K 4096²）。
    - ``max_ratio``：后端支持的最极端比例（gpt-image-2=3 即 1:3~3:1，DashScope=8 即
      1:8~8:1）；超出仅 warn 不抛错，留 API 判定。
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
    short_unit = round_to * short_comp  # 短边每单位 t 的像素增量

    # 短边最接近 short_edge 的 t
    t = max(1, round(short_edge / short_unit))

    # 长边像素上限夹取。上限连最小合法尺寸（t=1）都容不下时，仍取 t=1（最小合法尺寸），
    # 不取 0——宁可略超上限交 API 判定，也不产出非法的 0 尺寸。
    if max_long_edge is not None:
        max_t_long = max_long_edge // (round_to * long_comp)
        t = min(t, max(1, max_t_long))

    # 总像素预算夹取：总像素 = aw·ah·round_to²·t² ≤ max_total_pixels（同样 t≥1 floor）
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
    """把分辨率规范化成「短边像素」。

    - ``None`` / 空串 → ``default_short``
    - 档位词（大小写不敏感，如 ``"2K"`` / ``"720p"``）→ 查 ``tier_map``
    - 自定义 ``"宽*高"`` → 取 ``min(宽, 高)``，**剥离其自带比例**（比例只由 aspect_ratio 决定）
    - 纯数字 → 直接当短边
    - 无法解析 → ``default_short`` + warn
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
