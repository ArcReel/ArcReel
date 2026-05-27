"""按源文语言计『阅读单位』的轻量度量工具。

与 `_text_utils.count_chars` 的语义区别:
- `count_chars` 是字符级、语言无关的「非空白 Unicode 字符」计数器,用于工程上的偏移定位。
- `count_reading_units` 是语义级、按源文语言裁剪的「阅读单位」计数器,贴合用户「N 字一集」的心智。

设计约束:
- 本模块不依赖任何 lib/ 内部模块,以便 agent skill 脚本通过 sys.path 注入后干净引入。
- 接口稳定:加新语言时只新增分支,不破调用方。
"""

from __future__ import annotations

import re

# zh: CJK Unified Ideographs(基本区 + 扩展 A) + CJK 兼容汉字 + CJK 符号与标点 + 全角符号区
_ZH_UNIT_PATTERN = re.compile("[㐀-鿿豈-﫿　-〿＀-￯]")

# en / vi 等基于拉丁字母的语种走 unicode word-boundary
_LATIN_WORD_PATTERN = re.compile(r"\b\w+\b", re.UNICODE)


def count_reading_units(text: str, language: str | None) -> int:
    """按源文语言数『阅读单位』。

    zh: 汉字 + CJK 标点 / 全角符号
    en / vi: unicode word-boundary 词数(数字与缩写如 "don't" 各计 1)
    未知 / None / 空 language: 按 zh 路径处理(向后兼容老项目缺 source_language 的场景)
    """
    if not text:
        return 0
    code = (language or "").strip().lower()
    if code in ("en", "vi"):
        return len(_LATIN_WORD_PATTERN.findall(text))
    return len(_ZH_UNIT_PATTERN.findall(text))
