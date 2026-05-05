"""自定义供应商 model_id → supported_durations 启发式预设表。

数据来源：lmarena 视频模型排行榜 Top 20（2026-05 快照）+ 常见聚合命名。
匹配按 PRESETS 顺序，命中即返回；未匹配 → DEFAULT_FALLBACK。

歧义说明：同名 model_id（如 sora-2-pro）在 OpenAI 第一方与第三方聚合站点的实际允许
秒数可能不同。预设只是启发，给用户起点；用户必须在创建/编辑模型时 review 输入框值。
"""

from __future__ import annotations

import re

DEFAULT_FALLBACK: list[int] = [4, 8]

# 按特异性从高到低排列；命中一条即返回。range 全展开为离散集。
PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    # OpenAI Sora 第一方（严格 regex：可选 -pro，可选 -YYYY-MM-DD 日期后缀）
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    # 第三方聚合 Sora-Pro 变体（常见 6/10/12/16/20）
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),
    # Google Veo（含 fast / lite / preview）
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),
    # Kling 全系（v1/v2/v2.5/v2.6/v3.0/o1/turbo/pro/omni/standard）
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),
    # Runway Gen 系列
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),
    # Luma Ray / Dream Machine
    (re.compile(r"\bray-?\d", re.I), [5, 10]),
    # ByteDance Dreamina / Seedance（4-15 任意）
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),
    # 字节即梦
    (re.compile(r"jimeng", re.I), list(range(4, 16))),
    # Alibaba HappyHorse（3-15 任意）
    (re.compile(r"happyhorse", re.I), list(range(3, 16))),
    # xAI Grok Imagine（1-15 任意）
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    # Vidu Q 系列（1-16 任意）
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    # PixVerse V5/V5.5/V5.6/V6（1-15 任意）
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    # MiniMax Hailuo（固定 6）
    (re.compile(r"hailuo|minimax", re.I), [6]),
    # Wan
    (re.compile(r"wan-?\d", re.I), [4, 5]),
    # Pika
    (re.compile(r"pika", re.I), [3, 5, 10]),
]


def infer_supported_durations(model_id: str) -> list[int]:
    """根据 model_id 启发式推导 supported_durations。

    返回值始终是非空升序去重的正整数列表，且为独立 list（caller 可安全修改）。
    """
    for pattern, durations in PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(DEFAULT_FALLBACK)
