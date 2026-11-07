"""Request-level language resolution: read ``Accept-Language`` and inject route translators and display-name catalogs.

Copy catalogs and language-specific rendering (``_`` / ``translate_or``) live in ``lib.i18n``, independent of HTTP frameworks;
this layer only maps requests to languages.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db import async_session_factory, get_async_session
from lib.db.repositories.display_names import build_display_names, load_display_names
from lib.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, _
from lib.i18n.display_names import DisplayNames

logger = logging.getLogger(__name__)


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

#: The request language itself. Use where translated names must fall back to the source's original name on missing keys (see translate_or);
#: ordinary wording still uses Translator.
Locale = Annotated[str, Depends(get_locale)]


def _builtin_display_names(locale: str) -> DisplayNames:
    """Catalog-load fallback: recognize only built-in providers and models; show generic names for custom providers.

    The catalog only determines provider/model names in copy; loading failure must not alter the response itself.
    """
    logger.warning("显示名目录加载失败，使用内置名称", exc_info=True)
    return build_display_names(locale, custom_providers={}, custom_models={})


async def load_display_names_or_builtin(session: AsyncSession, locale: str) -> DisplayNames:
    """Load the localized display-name catalog in the route session; fall back to built-in names on query failure.

    Routes needing on-demand loading in their function bodies also call here, not ``load_display_names`` directly.
    """
    try:
        return await load_display_names(session, locale)
    except Exception:
        # The session is shared by the request: roll back failed queries without affecting the route's own queries.
        await session.rollback()
        return _builtin_display_names(locale)


async def get_display_names(request: Request, session: AsyncSession = Depends(get_async_session)) -> DisplayNames:
    """Route dependency: load provider/model display names in the request language; fall back to built-in names on query failure."""
    return await load_display_names_or_builtin(session, get_locale(request))


async def request_display_names(request: Request) -> DisplayNames:
    """Application-level exception handlers load catalogs in the request language; preserve the original error and use built-in names on query/session failure."""
    locale = get_locale(request)
    try:
        async with async_session_factory() as session:
            return await load_display_names(session, locale)
    except Exception:
        return _builtin_display_names(locale)


#: Display-name catalog for failure copy: replace provider/model IDs with names in the request language.
DisplayNamesCatalog = Annotated[DisplayNames, Depends(get_display_names)]
