"""自定义供应商 model_id → supported_durations 启发式预设表。

数据来源：lmarena 视频模型排行榜 Top 20（2026-05 快照）+ 常见聚合命名。
匹配按 PRESETS 顺序，命中即返回；未匹配 → DEFAULT_FALLBACK。

歧义说明：同名 model_id（如 sora-2-pro）在 OpenAI 第一方与第三方聚合站点的实际允许
秒数可能不同。预设只是启发，给用户起点；用户必须在创建/编辑模型时 review 输入框值。
"""

from __future__ import annotations

import re

from lib.video_backends.dashscope import classify_wan_model

DEFAULT_FALLBACK: list[int] = [4, 8]

# 万相 3.0（2-30 任意）。出处：lib/config/registry.py wan3.0-video 的 supported_durations。
_WAN3_DURATIONS: list[int] = list(range(2, 31))
# 万相 2.7（2-15 任意）。出处：lib/config/registry.py wan2.7-{t2v,i2v,r2v} 的 supported_durations。
_WAN27_DURATIONS: list[int] = list(range(2, 16))
# Alibaba HappyHorse（3-15 任意）。
_HAPPYHORSE_DURATIONS: list[int] = list(range(3, 16))

# wan3 / wan2.7 / happyhorse 三个家族的归属判定复用 classify_wan_model（lib.video_backends.dashscope
# 的单一判定入口），不在本模块另写正则——与 endpoints.py 路由推断、DashScopeVideoBackend 能力档
# 推断共用同一结论，避免三处宽度各自漂移。下方 PRESETS 里其余家族（sora/veo/kling 等）不受该判定
# 入口覆盖，仍按各自厂商关键字匹配；末尾的通用 wan 兜底同理（见该条目处的说明）。

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
    # xAI Grok Imagine（1-15 任意）
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    # Vidu Q 系列（1-16 任意）
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    # PixVerse V5/V5.5/V5.6/V6（1-15 任意）
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    # MiniMax H3（4-15 任意；与下面的 hailuo 条目无 token 重叠，按 MiniMax 家族就近排列）。
    # 出处：lib/config/registry.py MiniMax-H3 的 supported_durations。
    (re.compile(r"minimax-h3", re.I), list(range(4, 16))),
    # MiniMax Hailuo（固定 6）
    (re.compile(r"hailuo", re.I), [6]),
    # Wan（classify_wan_model 未归类到 wan3/wan2.7 的其余系列，含万相 2.x 中 2.7 以外的小版本）。
    # 分隔符接受连字符与下划线，不加标识符边界：本条是兜底启发式，"swan3"/"wan20" 一类含 wan
    # 子串的第三方型号名落到这条比落 DEFAULT_FALLBACK 更接近常见值，且预设本就要求用户在输入框
    # review。wan3/wan2.7/happyhorse 已在 infer_supported_durations 里由 classify_wan_model
    # 先行判定并返回，不会落到本条。
    (re.compile(r"wan[-_]?\d", re.I), [4, 5]),
    # Pika
    (re.compile(r"pika", re.I), [3, 5, 10]),
]


def infer_supported_durations(model_id: str) -> list[int]:
    """根据 model_id 启发式推导 supported_durations。

    返回值始终是非空升序去重的正整数列表，且为独立 list（caller 可安全修改）。
    """
    classification = classify_wan_model(model_id)
    if classification.family == "wan3":
        return list(_WAN3_DURATIONS)
    # wan2.7 未实现请求构造的模态（videoedit / s2v / v2v 等，见 classify_wan_model 的
    # has_known_modality 处的说明）时长上限与 t2v/i2v/r2v 不同，不套用家族档，落到下方通用预设。
    if classification.family == "wan2.7" and classification.has_known_modality:
        return list(_WAN27_DURATIONS)
    if classification.family == "happyhorse":
        return list(_HAPPYHORSE_DURATIONS)
    for pattern, durations in PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(DEFAULT_FALLBACK)
