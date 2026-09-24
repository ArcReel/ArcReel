from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

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


def _(key: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate a message key to the given locale."""
    msg_map = MESSAGES.get(locale, MESSAGES[DEFAULT_LOCALE])
    msg = msg_map.get(key, MESSAGES[DEFAULT_LOCALE].get(key, key))
    try:
        return msg.format(**kwargs)
    except Exception:
        return msg


def render_generation_input_error(key: str, params: Mapping[str, Any], translate: Callable[..., str]) -> str:
    """Render mixed generation-input gaps by cause while preserving the first machine code."""
    gaps = params.get("gaps")
    if not isinstance(gaps, list):
        return translate(key, **params)
    grouped: dict[str, list[str]] = {}
    for gap in gaps:
        if not isinstance(gap, dict):
            return translate(key, **params)
        code, name, asset_type = gap.get("code"), gap.get("name"), gap.get("asset_type")
        if code not in {
            "script_prompt_pending",
            "reference_asset_unregistered",
            "reference_asset_missing",
            "asset_original_missing",
        } or not isinstance(name, str):
            return translate(key, **params)
        text = (
            name
            if code in {"script_prompt_pending", "reference_asset_unregistered"} or not isinstance(asset_type, str)
            else f"{asset_type}: {name}"
        )
        grouped.setdefault(code, []).append(text)
    if len(grouped) < 2 or next(iter(grouped)) != key:
        return translate(key, **params)
    details = []
    for code, names in grouped.items():
        if code == "script_prompt_pending":
            details.append(translate(code, segment_id=names[0]))
        else:
            details.append(translate(code, missing_text=", ".join(names)))
    return translate("generation_input_multiple_gaps", details="; ".join(details))


def translate_or(key: str, fallback: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate ``key``, falling back to ``fallback`` when no locale defines it.

    目录类文案（模型名、供应商名）多数是各语言通用的专有名词，只有非拉丁写法的条目需要
    译名表。未登记的键返回数据源里的原名，而不是 ``_()`` 的裸键回退——后者会把
    "Gemini 3 Pro" 变成 "model_name_gemini-aistudio_gemini-3-pro"。
    """
    if key not in MESSAGES[DEFAULT_LOCALE]:
        return fallback
    return _(key, locale=locale, **kwargs)
