from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request

from .en import assets as en_assets
from .en import emails as en_emails
from .en import errors as en_errors
from .en import events as en_events
from .en import providers as en_providers
from .en import system as en_system
from .en import templates as en_templates
from .en import validation as en_validation
from .vi import assets as vi_assets
from .vi import emails as vi_emails
from .vi import errors as vi_errors
from .vi import events as vi_events
from .vi import providers as vi_providers
from .vi import system as vi_system
from .vi import templates as vi_templates
from .vi import validation as vi_validation
from .zh import assets as zh_assets
from .zh import emails as zh_emails
from .zh import errors as zh_errors
from .zh import events as zh_events
from .zh import providers as zh_providers
from .zh import system as zh_system
from .zh import templates as zh_templates
from .zh import validation as zh_validation

logger = logging.getLogger(__name__)

# Default locale
DEFAULT_LOCALE = "zh"
SUPPORTED_LOCALES = ["zh", "en", "vi"]

# Mapping from locale code to human-readable language name
LOCALE_LANGUAGE_MAP: dict[str, str] = {
    "zh": "中文",
    "en": "English",
    "vi": "Tiếng Việt",
}

# Merged message dictionary
MESSAGES: dict[str, dict[str, str]] = {
    "zh": {
        **zh_errors.MESSAGES,
        **zh_events.MESSAGES,
        **zh_system.MESSAGES,
        **zh_emails.MESSAGES,
        **zh_providers.MESSAGES,
        **zh_templates.MESSAGES,
        **zh_assets.MESSAGES,
        **zh_validation.MESSAGES,
    },
    "en": {
        **en_errors.MESSAGES,
        **en_events.MESSAGES,
        **en_system.MESSAGES,
        **en_emails.MESSAGES,
        **en_providers.MESSAGES,
        **en_templates.MESSAGES,
        **en_assets.MESSAGES,
        **en_validation.MESSAGES,
    },
    "vi": {
        **vi_errors.MESSAGES,
        **vi_events.MESSAGES,
        **vi_system.MESSAGES,
        **vi_emails.MESSAGES,
        **vi_providers.MESSAGES,
        **vi_templates.MESSAGES,
        **vi_assets.MESSAGES,
        **vi_validation.MESSAGES,
    },
}


def get_locale(request: Request) -> str:
    """Get locale from Accept-Language header."""
    accept_lang = request.headers.get("accept-language", "")
    if not accept_lang:
        return DEFAULT_LOCALE

    # Simple parser for Accept-Language header
    # e.g., "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"
    for lang_range in accept_lang.split(","):
        lang = lang_range.split(";")[0].split("-")[0].strip().lower()
        if lang in SUPPORTED_LOCALES:
            return lang

    return DEFAULT_LOCALE


def get_translator(request: Request) -> Callable[..., str]:
    """Dependency to get a translator function for the current request."""
    locale = get_locale(request)

    def translate(key: str, **kwargs: Any) -> str:
        return _(key, locale=locale, **kwargs)

    return translate


Translator = Annotated[Callable[..., str], Depends(get_translator)]

#: 请求语言本身。取译名需要「键缺失时回退到数据源里的原名」的地方（见 translate_or）用它，
#: 常规成文仍用 Translator。
Locale = Annotated[str, Depends(get_locale)]


def _(key: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate a message key to the given locale."""
    msg_map = MESSAGES.get(locale, MESSAGES[DEFAULT_LOCALE])
    msg = msg_map.get(key, MESSAGES[DEFAULT_LOCALE].get(key, key))
    try:
        return msg.format(**kwargs)
    except Exception:
        return msg


def translate_or(key: str, fallback: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate ``key``, falling back to ``fallback`` when no locale defines it.

    目录类文案（模型名、供应商名）多数是各语言通用的专有名词，只有非拉丁写法的条目需要
    译名表。未登记的键返回数据源里的原名，而不是 ``_()`` 的裸键回退——后者会把
    "Gemini 3 Pro" 变成 "model_name_gemini-aistudio_gemini-3-pro"。
    """
    if key not in MESSAGES[DEFAULT_LOCALE]:
        return fallback
    return _(key, locale=locale, **kwargs)
