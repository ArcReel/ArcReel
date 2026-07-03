"""按源文语言计『阅读单位』的轻量度量工具。

`count_reading_units` 是语义级、按源文语言裁剪的「阅读单位」计数器,贴合用户
「N 字一集」的心智(区别于字符级、语言无关的偏移定位度量)。

输入约定:
- 调用方应传入 NFC normalize 过的文本。NFD/组合重音形式的越南语等会让 `\\w` word
  boundary 把组合标记拆出 token,导致 ``Hôm``(``H + o + ̂ + m``) 这类词被计为多 token。
  调用方在文件读入边界 ``unicodedata.normalize("NFC", text)`` 即可。lib 本身不主动
  normalize 以保持纯字符串处理 + offset 与输入文本同坐标系。

设计约束:
- 本模块不依赖任何 lib/ 内部模块,以便 agent skill 脚本通过 sys.path 注入后干净引入。
- 接口稳定:加新语言时只新增分支,不破调用方。
"""

from __future__ import annotations

import re

# zh: CJK Unified Ideographs(基本区 + 扩展 A) + CJK 兼容汉字 + CJK 符号与标点 +
# 全角符号区 + SIP 平面 CJK Ext B-H(罕用 / 古籍 / 人名地名生僻字,U+20000-U+323AF)
_ZH_UNIT_PATTERN = re.compile("[㐀-鿿豈-﫿　-〿＀-￯𠀀-𲎯]")

# en / vi 等基于拉丁字母的语种走 unicode word-boundary
_LATIN_WORD_PATTERN = re.compile(r"\b\w+\b", re.UNICODE)


def _pattern_for(language: str | None) -> re.Pattern[str]:
    code = (language or "").strip().lower()
    if code in ("en", "vi"):
        return _LATIN_WORD_PATTERN
    return _ZH_UNIT_PATTERN


def count_reading_units(text: str, language: str | None) -> int:
    """按源文语言数『阅读单位』。

    zh: 汉字 + CJK 标点 / 全角符号
    en / vi: unicode word-boundary 词数（数字计作词;缩写如 "don't" 会按 word-boundary
        规则拆分为 ``don`` + ``t``。如需把缩写计为单一 token，需换用不同的正则。）
    未知 / None / 空 language: 按 zh 路径处理(向后兼容老项目缺 source_language 的场景)
    """
    if not text:
        return 0
    return len(_pattern_for(language).findall(text))


def reading_unit_noun(language: str | None) -> str:
    """该语言『阅读单位』的中文量词：词（按词计的 en / vi 等）/ 字（zh 及未知语言按字计）。

    量词名直接由 ``_pattern_for`` 的分类派生，与 ``count_reading_units`` 天然同源——
    新增按词计的语言只在 ``_pattern_for`` 加分支，本函数自动跟随，避免各调用点各写一套
    「词 / 字」映射而与实际计数口径漂移（prompt 里「N 词 / 秒」「N 字一集」等描述据此取名）。
    """
    return "词" if _pattern_for(language) is _LATIN_WORD_PATTERN else "字"


def find_reading_unit_offset(text: str, target_units: int, language: str | None) -> int:
    """返回第 ``target_units`` 个阅读单位末尾的字符偏移（含尾）。

    与 ``count_reading_units`` 共用度量口径。按原文顺序累计扫描，避免全局比例换算
    在 ASCII/数字分布不均（en/vi 或 zh 混排）时把目标单位映射到错误位置。

    - ``target_units <= 0`` 或 ``text`` 为空 → 返回 0
    - text 中阅读单位数 < target_units → 返回 ``len(text)``
    """
    if target_units <= 0 or not text:
        return 0
    count = 0
    last_end = 0
    for match in _pattern_for(language).finditer(text):
        count += 1
        last_end = match.end()
        if count >= target_units:
            return last_end
    return len(text)
