"""Structured validation messages (locale-neutral ``key + params``).

Validator diagnostics flow to multiple consumer boundaries: Web requests render according to ``Accept-Language``, Agent tools use
the app's default language, and the market source CLI carries no translation catalog. Messages therefore cannot be fixed at their source as raw
language-specific strings: produce structure and render at boundaries. This package has no translation catalog: callers supply translators; boundaries without
a translation catalog use :func:`code_translator` to output ``key`` and parameters directly.

``params`` values are inserted directly through ``str.format`` by default; language-dependent terms are wrapped in ``MessageRef``,
and full nested sentences use ``ValidationMessage``; both are translated into the same language before interpolation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Pass-through key for ``ValidationMessage.literal``: the body is already finished text (Pydantic errors,
#: third-party exception text), with no translatable structure, and is output unchanged through a placeholder.
LITERAL_KEY = "val_literal"

#: Translator for rendering messages: ``translate(key, **params) -> str``.
type Translator = Callable[..., str]


@dataclass(frozen=True)
class MessageRef:
    """Nested translation key in a parameter: translated into the current language before interpolation into the outer message."""

    key: str


@dataclass(frozen=True)
class MessageJoin:
    """Fragment sequence in a parameter: literal text and ``MessageRef`` mixed together, resolved per fragment and joined by the separator.

    Used when one parameter must carry multiple submessages (such as compressing several Pydantic errors into a one-line summary).
    """

    parts: tuple[MessagePart, ...]
    separator: str = "; "


#: Allowed sequence elements: literal text, nested translation keys, or another nested fragment sequence.
type MessagePart = str | MessageRef | MessageJoin


@dataclass(frozen=True)
class ValidationMessage:
    """One locale-neutral validation message."""

    key: str
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def literal(cls, text: str) -> ValidationMessage:
        """Wrap finished text as a message, allowing sources without translatable structure (such as Pydantic errors) to use the same channel."""
        return cls(LITERAL_KEY, {"text": text})

    def render(self, translate: Translator) -> str:
        """Render text through ``translate``; messages and translation keys nested in parameters use the same translator."""
        resolved = {name: _resolve_param(value, translate) for name, value in self.params.items()}
        return translate(self.key, **resolved)


def code_translator(key: str, **params: Any) -> str:
    """Translator without a translation catalog: outputs ``key`` and parameters directly, preserving literal messages.

    For example, ``val_market_slug_invalid(slug=My Slug)``: parameters are sorted by name and values inserted unchanged without quotes, so nested message
    text is not repeatedly escaped when inserted into outer messages. Used by CLI and exception text boundaries that lack language context and should not carry a translation
    catalog.
    """
    if key == LITERAL_KEY:
        return str(params.get("text", ""))
    if not params:
        return key
    rendered = ", ".join(f"{name}={value}" for name, value in sorted(params.items()))
    return f"{key}({rendered})"


def _resolve_param(value: Any, translate: Translator) -> Any:
    """Resolve nested translation markers in parameter values into current-language text; return other values unchanged."""
    if isinstance(value, ValidationMessage):
        return value.render(translate)
    if isinstance(value, MessageRef):
        return translate(value.key)
    if isinstance(value, MessageJoin):
        return value.separator.join(str(_resolve_param(part, translate)) for part in value.parts)
    return value
