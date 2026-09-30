"""校验消息的结构化载体（locale-neutral 的 ``key + params``）。

校验器产出的诊断会流向多个消费边界：Web 请求按 ``Accept-Language`` 渲染，Agent 工具固定用
应用的默认语言渲染，市场源 CLI 不带任何翻译目录。消息因此不能在产出点就定死成某种语言的裸
字符串——产出结构、边界渲染。本包不携带翻译目录：渲染一律由调用方传入 translator；手里没有
翻译目录的边界用 :func:`code_translator` 输出 ``key`` 与参数本身。

``params`` 里的值默认按 ``str.format`` 直出；需要跟随语言变化的词用 ``MessageRef`` 包一层，
完整的嵌套句子用 ``ValidationMessage``，渲染时都先按同一语言翻译再代入。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: ``ValidationMessage.literal`` 用的透传 key：消息体本身已是成品文本（Pydantic 报错、
#: 第三方异常文案），没有可翻译的结构，占位后原样输出。
LITERAL_KEY = "val_literal"

#: 渲染消息用的 translator：``translate(key, **params) -> str``。
type Translator = Callable[..., str]


@dataclass(frozen=True)
class MessageRef:
    """参数位上的嵌套翻译键：渲染时先把它翻成当前语言，再作为参数代入外层消息。"""

    key: str


@dataclass(frozen=True)
class MessageJoin:
    """参数位上的片段序列：字面文本与 ``MessageRef`` 混排，逐段解析后按分隔符拼接。

    供一个参数位需要承载多条子消息的场景（如把多条 Pydantic 报错压成一行摘要）。
    """

    parts: tuple[MessagePart, ...]
    separator: str = "; "


#: 片段序列里允许出现的元素：字面文本、嵌套翻译键，或再嵌一层的片段序列。
type MessagePart = str | MessageRef | MessageJoin


@dataclass(frozen=True)
class ValidationMessage:
    """一条 locale-neutral 的校验消息。"""

    key: str
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def literal(cls, text: str) -> ValidationMessage:
        """把已成文的字符串包成消息，供无可翻译结构的来源（Pydantic 报错等）复用同一通道。"""
        return cls(LITERAL_KEY, {"text": text})

    def render(self, translate: Translator) -> str:
        """按 ``translate`` 渲染成文本；嵌套在参数里的消息与翻译键用同一个 translator。"""
        resolved = {name: _resolve_param(value, translate) for name, value in self.params.items()}
        return translate(self.key, **resolved)


def code_translator(key: str, **params: Any) -> str:
    """不带翻译目录的 translator：输出 ``key`` 与参数本身，字面消息原样输出。

    形如 ``val_market_slug_invalid(slug=My Slug)``，参数按名字排序、值原样代入不加引号——嵌套消息
    渲染出的文本再次代入外层时不会层层转义。供 CLI 与异常文本这类没有语言上下文、也不该携带翻译
    目录的边界使用。
    """
    if key == LITERAL_KEY:
        return str(params.get("text", ""))
    if not params:
        return key
    rendered = ", ".join(f"{name}={value}" for name, value in sorted(params.items()))
    return f"{key}({rendered})"


def _resolve_param(value: Any, translate: Translator) -> Any:
    """把参数值里的嵌套翻译标记解析成当前语言的文本，其余值原样返回。"""
    if isinstance(value, ValidationMessage):
        return value.render(translate)
    if isinstance(value, MessageRef):
        return translate(value.key)
    if isinstance(value, MessageJoin):
        return value.separator.join(str(_resolve_param(part, translate)) for part in value.parts)
    return value
