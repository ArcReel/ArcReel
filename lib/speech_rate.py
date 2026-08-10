"""语速估算单一真相源。

把「一段口播文本朗读需多少秒」收敛到一处，供 drama 成片字幕定时与说话量对场景
时长的上界校验共用，避免两处各自维护一套语速常量而漂移。

语速以「阅读单位 / 秒」表示，阅读单位的语言裁剪口径复用 ``lib.text_metrics``
（zh 计汉字 / CJK 标点，en / vi 计词）——因此单位换算天然随语言切换，不必为
中英文各写一套字符规则。语速值可调、按 ``source_language`` 覆盖、缺省回退默认；
新增语言只在 ``SPEECH_RATE_UPS_BY_LANGUAGE`` 登记，调用点不写死任何数值。

取值优先级：项目级覆盖（``project.json`` 顶层 ``speech_rate_units_per_second``，经
``project_speech_rate_override`` 解析）> 语言默认 > 全局默认。覆盖只在这里叠加，
消费方仍只经本模块两个函数取值，不各自读 project.json 字段。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from lib.text_metrics import count_reading_units

#: 默认语速（阅读单位 / 秒）。中文可懂配音常见约 4–6 字 / 秒，取 5 为中位。
#: 未登记 / 缺失语言回退此值（与 ``count_reading_units`` 未知语言按 zh 计字的口径对齐）。
DEFAULT_SPEECH_RATE_UPS: float = 5.0

#: 按语言代码覆盖语速（阅读单位 / 秒），键用项目 ``source_language``（zh / en / vi）。
#: en / vi 的阅读单位是「词」，正常口语约 2–3 词 / 秒，取 2.5；与 zh 的「字 / 秒」不可
#: 直接通约，故必须分语言登记而非全局一个数值。值为可调估算，按实际配音节奏微调。
SPEECH_RATE_UPS_BY_LANGUAGE: dict[str, float] = {
    "zh": 5.0,
    "en": 2.5,
    "vi": 2.5,
}


#: 项目级语速覆盖在 ``project.json`` 的顶层字段名（阅读单位 / 秒）。
#: 与 TTS 供应商配音倍率 ``narration_speed`` 是两码事：后者是倍率、前者是绝对速度。
SPEECH_RATE_FIELD: str = "speech_rate_units_per_second"

#: 项目级语速覆盖的硬区间（开下界 / 闭上界，阅读单位 / 秒）。宽松区间只拦下 ≤0 与
#: 明显误输入（如把 TTS 倍率或毫秒数填进来），区间内不做任何倾向性提示。
MIN_SPEECH_RATE_UPS: float = 0.0
MAX_SPEECH_RATE_UPS: float = 20.0

#: 溢出探针：判定语速可用性时假想的单段口播阅读单位数，取值远超任何真实文本。
#: 语速越小时长越大，只要探针长度除以它仍是有限数，真实文本的估算就不会溢出。
_OVERFLOW_PROBE_UNITS: float = 1e6


def is_valid_speech_rate(value: float) -> bool:
    """该数值是否落在项目级语速覆盖的硬区间内（``0 < value <= 20``，且能算出有限时长）。

    前端输入校验、请求模型校验与持久化后的读时守卫共用这一把尺，避免三处各写一套边界。
    入参允许是 project.json 直接解析出的值，故两类输入病理在这里收掉，调用方不必各自处理：
    JSON 整数字面量没有位宽上限，``float()`` 对超出双精度表示范围的整数抛 ``OverflowError``；
    JSON 布尔解析出的 ``bool`` 是 ``int`` 子类，``float(True)`` 得 ``1.0`` 会落进合法区间。
    两者一律判为「不可用」。

    区间下界之外还要求探针长度除以该语速仍为有限数：极小的正语速（如 ``1e-308``）虽然
    大于 0，却让 ``estimate_spoken_seconds`` 得出 ``inf``，成片字幕的微秒换算会直接抛
    ``OverflowError``。校验发生在写入时、文本尚不存在，故以 ``_OVERFLOW_PROBE_UNITS``
    代替真实长度取上界，下游拿到的估算值因此恒为有限数。该约束的有效下限在 1e-302
    量级，正常取值不受影响。
    """
    if isinstance(value, bool):
        return False
    try:
        rate = float(value)
    except OverflowError:
        return False
    if not (math.isfinite(rate) and MIN_SPEECH_RATE_UPS < rate <= MAX_SPEECH_RATE_UPS):
        return False
    return math.isfinite(_OVERFLOW_PROBE_UNITS / rate)


def project_speech_rate_override(project: Mapping[str, Any] | None) -> float | None:
    """从 project.json 解析项目级语速覆盖，未填 / 脏值 / 越界一律返回 ``None``。

    返回 ``None`` 即「无覆盖」，调用方把它原样交给下面两个函数即回退语言默认——未填该
    字段的项目一律按语言默认估算。写入侧（创建 / PATCH 请求模型）已
    按同一把尺拒绝越界值，这里的守卫是对手改 project.json 与历史脏数据的读时兜底：估算
    语速不值得让一次已付费的生成崩在脏字段上。
    """
    if not isinstance(project, Mapping):
        return None
    raw = project.get(SPEECH_RATE_FIELD)
    # bool 是 int 的子类，True 会被当成 1.0；显式排除，避免脏数据把语速压到 1 单位 / 秒。
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    # 先过区间再转 float：区间内的值必然可安全转换，超大整数在 is_valid_speech_rate 内按越界收掉。
    return float(raw) if is_valid_speech_rate(raw) else None


def speech_rate_units_per_second(language: str | None = None, override: float | None = None) -> float:
    """返回生效语速（阅读单位 / 秒）：``override`` 优先，否则按语言取默认。

    ``override`` 是项目级覆盖（由 ``project_speech_rate_override`` 解析），越界 / 非有限数
    按无覆盖处理。语言代码大小写不敏感；``None`` / 空 / 未登记语言回退 ``DEFAULT_SPEECH_RATE_UPS``。
    """
    if override is not None and is_valid_speech_rate(override):
        return float(override)
    if not language:
        return DEFAULT_SPEECH_RATE_UPS
    return SPEECH_RATE_UPS_BY_LANGUAGE.get(language.strip().lower(), DEFAULT_SPEECH_RATE_UPS)


def estimate_spoken_seconds(text: str | None, language: str | None = None, override: float | None = None) -> float:
    """估算 ``text`` 以 ``language`` 朗读所需秒数。

    口径：阅读单位数 ÷ 语速（阅读单位计法见 ``lib.text_metrics.count_reading_units``；
    语速取值优先级见 ``speech_rate_units_per_second``）。None / 空串 / 纯空白 / 纯标点
    （无阅读单位）一律计 0 秒——既是字幕单条定时的输入，也是说话量求和的单项，两处共用
    同一换算、不在调用点重复。
    """
    if not text:
        return 0.0
    units = count_reading_units(text, language)
    if units <= 0:
        return 0.0
    return units / speech_rate_units_per_second(language, override)
