"""zh 消息的标点，与前端 frontend/src/i18n/locale-wording.test.ts 保持同一口径。"""

import re

import pytest

from lib.i18n import MESSAGES

# 对应前端的 \p{Script=Han}：基本区、扩展 A 区、兼容区与扩展 B 区以后
HAN = r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]"

ZH_RULES = [
    ("中文后用全角冒号", re.compile(rf"{HAN}\s?:")),
    ("省略号用「…」", re.compile(r"\.\.\.")),
    # 只查成对的引号：列举非法字符时单独出现的 " 是字符本身
    ("引号用「」", re.compile(r'"[^"]*"')),
    # 端点含占位符或百分号、或后接单位的才算范围；两端都是裸数字的是用户照样输入的语法
    (
        "数值范围用「～」",
        re.compile(
            rf"\{{\w+\}}\s?[-–]\s?(\d|\{{)|\d\s?[-–]\s?\{{|\d%\s?[-–]\s?\d|\d\s?[-–]\s?\d+(%|\s?({HAN}|[A-Za-z]))"
        ),
    ),
]


@pytest.mark.parametrize(("rule", "pattern"), ZH_RULES, ids=[r for r, _ in ZH_RULES])
def test_zh_messages_follow_punctuation_rule(rule, pattern):
    offenders = {key: text for key, text in MESSAGES["zh"].items() if pattern.search(text)}
    assert offenders == {}, rule
