"""语速估算单一真相源。

把「一段口播文本朗读需多少秒」收敛到一处，供 drama 成片字幕定时与说话量对场景
时长的上界校验共用，避免两处各自维护一套语速常量而漂移。

语速以「阅读单位 / 秒」表示，阅读单位的语言裁剪口径复用 ``lib.text_metrics``
（zh 计汉字 / CJK 标点，en / vi 计词）——因此单位换算天然随语言切换，不必为
中英文各写一套字符规则。语速值可调、按 ``source_language`` 覆盖、缺省回退默认；
新增语言只在 ``SPEECH_RATE_UPS_BY_LANGUAGE`` 登记，调用点不写死任何数值。
"""

from __future__ import annotations

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


def speech_rate_units_per_second(language: str | None = None) -> float:
    """返回该语言的语速（阅读单位 / 秒）。

    语言代码大小写不敏感；``None`` / 空 / 未登记语言回退 ``DEFAULT_SPEECH_RATE_UPS``。
    """
    if not language:
        return DEFAULT_SPEECH_RATE_UPS
    return SPEECH_RATE_UPS_BY_LANGUAGE.get(language.strip().lower(), DEFAULT_SPEECH_RATE_UPS)


def estimate_spoken_seconds(text: str | None, language: str | None = None) -> float:
    """估算 ``text`` 以 ``language`` 朗读所需秒数。

    口径：阅读单位数 ÷ 语速（阅读单位计法见 ``lib.text_metrics.count_reading_units``）。
    None / 空串 / 纯空白 / 纯标点（无阅读单位）一律计 0 秒——既是字幕单条定时的输入，
    也是说话量求和的单项，两处共用同一换算、不在调用点重复。
    """
    if not text:
        return 0.0
    units = count_reading_units(text, language)
    if units <= 0:
        return 0.0
    return units / speech_rate_units_per_second(language)
