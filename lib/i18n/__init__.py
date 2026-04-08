from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Request

from .en import emails as en_emails
from .en import errors as en_errors
from .en import system as en_system
from .zh import emails as zh_emails
from .zh import errors as zh_errors
from .zh import system as zh_system

logger = logging.getLogger(__name__)

# Default locale
DEFAULT_LOCALE = "zh"
SUPPORTED_LOCALES = ["zh", "en"]

# Merged message dictionary
MESSAGES: dict[str, dict[str, str]] = {
    "zh": {
        **zh_errors.MESSAGES,
        **zh_system.MESSAGES,
        **zh_emails.MESSAGES,
    },
    "en": {
        **en_errors.MESSAGES,
        **en_system.MESSAGES,
        **en_emails.MESSAGES,
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


def _(key: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate a message key to the given locale."""
    msg_map = MESSAGES.get(locale, MESSAGES[DEFAULT_LOCALE])
    msg = msg_map.get(key, MESSAGES[DEFAULT_LOCALE].get(key, key))
    try:
        return msg.format(**kwargs)
    except Exception:
        return msg
