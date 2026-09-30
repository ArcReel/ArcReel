"""校验结果与默认语言渲染。

消息载体（``ValidationMessage`` / ``MessageRef`` / ``MessageJoin``）在
``arcreel_market_core.validation_messages``，那里不携带翻译目录；本模块把它接到应用的
``lib.i18n``：:func:`default_translate` 是 Agent、CLI 与不带请求上下文的内部比对使用的默认语言
（中文）translator，Web 边界改用 ``server.i18n.get_translator`` 按请求语言渲染。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from arcreel_market_core.validation_messages import ValidationMessage
from lib.i18n import DEFAULT_LOCALE, _


def default_translate(key: str, **kwargs: Any) -> str:
    """默认语言（中文）的 translator。"""
    return _(key, locale=DEFAULT_LOCALE, **kwargs)


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
        return [message.render(translate or default_translate) for message in self.error_messages]

    def render_warnings(self, translate: Callable[..., str] | None = None) -> list[str]:
        return [message.render(translate or default_translate) for message in self.warning_messages]

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
