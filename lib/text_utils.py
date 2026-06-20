"""面向字符串的文本预处理工具。"""

from __future__ import annotations

import re

# 开栏：``` 后可跟空白、可选的语言标注 json（大小写不敏感）、可选空白与换行。
# 兼容 ```json / ```JSON / ``` json / ```  JSON 等带空格变体。
_OPENING_FENCE = re.compile(r"^```[ \t]*(?:json)?[ \t]*\n?", re.IGNORECASE)
# 闭栏：结尾的 ``` 及其前导换行。
_CLOSING_FENCE = re.compile(r"\n?```$")


def strip_json_code_fences(text: str) -> str:
    """剥离 LLM 输出最外层的 markdown 代码栅栏，返回可交给 json.loads 的纯文本。

    两端去空白后：剥离开头的 ``` 栅栏（可带空白与可选的 json 语言标注，大小写不敏感，
    兼容 ```JSON / ```Json / ``` json / ```  JSON 等变体），再剥离结尾的 ``` 闭栏；最后去空白返回。
    无栅栏的裸 JSON 仅做两端 strip。
    """
    text = text.strip()
    text = _OPENING_FENCE.sub("", text)
    text = _CLOSING_FENCE.sub("", text)
    return text.strip()
