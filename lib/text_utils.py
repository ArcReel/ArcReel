"""面向字符串的文本预处理工具。"""

from __future__ import annotations


def strip_json_code_fences(text: str) -> str:
    """剥离 LLM 输出最外层的 markdown 代码栅栏，返回可交给 json.loads 的纯文本。

    两端去空白后：开头若为 ```json（大小写不敏感，兼容 ```JSON / ```Json 等变体）
    去掉该 7 字前缀，否则若以 ``` 开头去掉 3 字；结尾若以 ``` 收束去掉尾 3 字；再去空白返回。
    无栅栏的裸 JSON 仅做两端 strip。
    """
    text = text.strip()
    if text[:7].lower() == "```json":
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()
