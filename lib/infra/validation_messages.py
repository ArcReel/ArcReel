"""Validation results and default-language rendering.

Message carriers (``ValidationMessage`` / ``MessageRef`` / ``MessageJoin``) live in
``arcreel_market_core.validation_messages``, without translation catalogs. This module
connects them to the application's ``lib.i18n``: :func:`default_translate` is the
default-language (Chinese) translator for Agents, the CLI and internal comparisons
without request context. Web boundaries instead use ``server.i18n.get_translator``
to render in the request language.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from arcreel_market_core.validation_messages import ValidationMessage
from lib.i18n import DEFAULT_LOCALE, _


def default_translate(key: str, **kwargs: Any) -> str:
    """Translator for the default language (Chinese)."""
    return _(key, locale=DEFAULT_LOCALE, **kwargs)


@dataclass
class ValidationResult:
    """Validation result.

    ``error_messages`` / ``warning_messages`` are the structured source of truth;
    ``errors`` / ``warnings`` are read-only views rendered in the default language for
    Agents, the CLI and internal comparisons without request context. Web boundaries
    instead pass a request-language translator to ``render_errors`` / ``render_warnings``.
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
