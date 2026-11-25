"""按模型名推断可选时长的预设表。

数据来源：lmarena 视频模型排行榜 Top 20（2026-05 快照）+ 常见聚合命名。
匹配按 PRESETS 顺序，命中即返回；一个族都匹配不上 → DEFAULT_FALLBACK（``[4, 8]``）。

预设只有两处用途（docs/adr/0104、0105）：新建模型条目时预填可选时长，以及对应不上条目的供应商模型
在运行时回落（:mod:`lib.custom_provider.model_facts`）。回落结果不写入任何字段——保存供应商模型时
不预填，否则推断值会冒充用户设过的能力覆盖。

歧义说明：同名 model_id（如 sora-2-pro）在 OpenAI 第一方与第三方聚合站点的实际允许
秒数可能不同。预设只是启发；渠道限制由用户写进能力覆盖。
"""

from __future__ import annotations

import re

from lib.backends.dashscope_model_names import classify_wan_model

DEFAULT_FALLBACK: list[int] = [4, 8]

_WAN3_DURATIONS: list[int] = list(range(2, 31))
_WAN27_DURATIONS: list[int] = list(range(2, 16))
_HAPPYHORSE_DURATIONS: list[int] = list(range(3, 16))


PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),
    (re.compile(r"\bray-?\d", re.I), [5, 10]),
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),
    (re.compile(r"jimeng", re.I), list(range(4, 16))),
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),
    (re.compile(r"vidu", re.I), list(range(1, 17))),
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),
    (re.compile(r"minimax-h3", re.I), list(range(4, 16))),
    (re.compile(r"hailuo", re.I), [6]),
    (re.compile(r"wan[-_]?\d", re.I), [4, 5]),
    (re.compile(r"pika", re.I), [3, 5, 10]),
]


def infer_supported_durations(model_id: str) -> list[int]:
    """根据 model_id 启发式推导 supported_durations。

    返回值始终是非空升序去重的正整数列表，且为独立 list（caller 可安全修改）。
    """
    classification = classify_wan_model(model_id)
    if classification.family == "wan3":
        return list(_WAN3_DURATIONS)
    if classification.family == "wan2.7" and classification.has_known_modality:
        return list(_WAN27_DURATIONS)
    if classification.family == "happyhorse":
        return list(_HAPPYHORSE_DURATIONS)
    for pattern, durations in PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(DEFAULT_FALLBACK)
