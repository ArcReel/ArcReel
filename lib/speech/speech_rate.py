"""语速估算单一真相源。

把「一段口播文本朗读需多少秒」收敛到一处，供时长建议、分集体量折算与说话量对场景
时长的上界校验共用，避免各处各自维护一套语速常量而漂移。字幕定时不读语速。

语速以「阅读单位 / 秒」表示，阅读单位的语言裁剪口径复用 ``lib.infra.text_metrics``
（zh 计汉字 / CJK 标点，en / vi / ru 计词）——因此单位换算天然随语言切换，不必为
中英文各写一套字符规则。语速值可调、按 ``source_language`` 覆盖、缺省回退默认；
新增语言只在 ``SPEECH_RATE_UPS_BY_LANGUAGE`` 登记，调用点不写死任何数值。

取值优先级：项目级覆盖（``project.json`` 顶层 ``speech_rate_units_per_second``，经
``project_speech_rate_override`` 解析）> 语言默认 > 全局默认。覆盖只在这里叠加，
消费方仍只经本模块两个函数取值，不各自读 project.json 字段。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lib.infra.text_metrics import count_reading_units

DEFAULT_SPEECH_RATE_UPS: float = 5.0

SPEECH_RATE_UPS_BY_LANGUAGE: dict[str, float] = {
    "zh": 5.0,
    "en": 2.5,
    "vi": 2.5,
    "ru": 2.5,
}


SPEECH_RATE_FIELD: str = "speech_rate_units_per_second"

MIN_SPEECH_RATE_UPS: float = 0.001
MAX_SPEECH_RATE_UPS: float = 20.0


def is_valid_speech_rate(value: float) -> bool:
    """该数值是否落在项目级语速覆盖的硬区间内（``0.001 <= value <= 20``）。

    前端输入校验、请求模型校验与持久化后的读时守卫共用这一把尺，避免三处各写一套边界。
    入参允许是 project.json 直接解析出的值，故两类输入病理在这里收掉，调用方不必各自处理：
    JSON 整数字面量没有位宽上限，``float()`` 对超出双精度表示范围的整数抛 ``OverflowError``；
    JSON 布尔解析出的 ``bool`` 是 ``int`` 子类，``float(True)`` 得 ``1.0`` 会落进合法区间。
    两者一律判为「不可用」。区间本身同时排除 ``inf`` 与 ``nan``（两者与边界的比较均为假），
    区间内的值则保证下游时长换算不溢出，依据见 ``MIN_SPEECH_RATE_UPS``。
    """
    if isinstance(value, bool):
        return False
    try:
        rate = float(value)
    except OverflowError:
        return False
    return MIN_SPEECH_RATE_UPS <= rate <= MAX_SPEECH_RATE_UPS


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
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
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

    口径：阅读单位数 ÷ 语速（阅读单位计法见 ``lib.infra.text_metrics.count_reading_units``；
    语速取值优先级见 ``speech_rate_units_per_second``）。None / 空串 / 纯空白 / 纯标点
    （无阅读单位）一律计 0 秒——它是说话量求和的单项，调用点共用同一换算、不各自重复。
    """
    if not text:
        return 0.0
    units = count_reading_units(text, language)
    if units <= 0:
        return 0.0
    return units / speech_rate_units_per_second(language, override)
