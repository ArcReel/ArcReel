"""校验消息的结构化载体（locale-neutral 的 ``key + params``）。

校验器与归档修复产出的 errors / warnings 会同时流向两个消费边界：Web 请求（按
``Accept-Language`` 渲染）与 Agent 工具（固定中文渲染）。两者共用同一份 key 表，消息因此
不能在产出点就定死成某种语言的裸字符串——产出结构、边界渲染。形态与参考生视频取档 warning
的 ``{"key", "params"}`` 同构（见 ``lib.reference_video.duration_slots.DurationSlot.warning``）。

``params`` 里的值默认按 ``str.format`` 直出；需要跟随语言变化的词（资产类别、生成模式、骨架
名词）用 ``MessageRef`` 包一层，完整的嵌套句子用 ``ValidationMessage``，渲染时都先按同一语言
翻译再代入。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: ``ValidationMessage.literal`` 用的透传 key：消息体本身已是成品文本（Pydantic 报错、
#: 第三方异常文案），没有可翻译的结构，占位后原样输出。
LITERAL_KEY = "val_literal"


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

    def render(self, translate: Callable[..., str] | None = None) -> str:
        """按 ``translate`` 渲染成文本；缺省用默认语言（中文）渲染，供 Agent 与 CLI 边界消费。"""
        tr = translate or _default_translate()
        resolved = {name: _resolve_param(value, tr) for name, value in self.params.items()}
        return tr(self.key, **resolved)


@dataclass
class ValidationResult:
    """验证结果。

    ``error_messages`` / ``warning_messages`` 是结构化真相；``errors`` / ``warnings`` 是它们
    按默认语言渲染出的只读视图，供 Agent、CLI 与不带请求上下文的内部比对使用。Web 边界改用
    ``render_errors`` / ``render_warnings`` 传入请求语言的 translator。
    """

    valid: bool
    error_messages: list[ValidationMessage] = field(default_factory=list)
    warning_messages: list[ValidationMessage] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return self.render_errors()

    @property
    def warnings(self) -> list[str]:
        return self.render_warnings()

    def render_errors(self, translate: Callable[..., str] | None = None) -> list[str]:
        return [message.render(translate) for message in self.error_messages]

    def render_warnings(self, translate: Callable[..., str] | None = None) -> list[str]:
        return [message.render(translate) for message in self.warning_messages]

    def __str__(self) -> str:
        errors = self.errors
        warnings = self.warnings
        if self.valid:
            msg = "验证通过"
            if warnings:
                msg += f"\n警告 ({len(warnings)}):\n" + "\n".join(f"  - {warning}" for warning in warnings)
            return msg

        msg = f"验证失败 ({len(errors)} 个错误)"
        msg += "\n错误:\n" + "\n".join(f"  - {error}" for error in errors)
        if warnings:
            msg += f"\n警告 ({len(warnings)}):\n" + "\n".join(f"  - {warning}" for warning in warnings)
        return msg


def _resolve_param(value: Any, translate: Callable[..., str]) -> Any:
    """把参数值里的嵌套翻译标记解析成当前语言的文本，其余值原样返回。"""
    if isinstance(value, ValidationMessage):
        return value.render(translate)
    if isinstance(value, MessageRef):
        return translate(value.key)
    if isinstance(value, MessageJoin):
        return value.separator.join(str(_resolve_param(part, translate)) for part in value.parts)
    return value


def _default_translate() -> Callable[..., str]:
    """默认语言的 translator。惰性 import：``lib.i18n`` 依赖 fastapi，不让它进本模块导入期。"""
    from lib.i18n import DEFAULT_LOCALE, _

    def translate(key: str, **kwargs: Any) -> str:
        return _(key, locale=DEFAULT_LOCALE, **kwargs)

    return translate
