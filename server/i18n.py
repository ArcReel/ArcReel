"""请求级语言解析：从 ``Accept-Language`` 取语言，注入路由的 translator 与显示名目录。

文案表与按语言成文（``_`` / ``translate_or``）在 ``lib.i18n``，与 HTTP 框架无关；
这里只做把请求映射到语言的那一层。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db import async_session_factory, get_async_session
from lib.db.repositories.display_names import load_display_names
from lib.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, _
from lib.i18n.display_names import DisplayNames


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


async def get_display_names(request: Request, session: AsyncSession = Depends(get_async_session)) -> DisplayNames:
    """Dependency to load the provider and model display-name catalog in the request locale."""
    return await load_display_names(session, get_locale(request))


async def request_display_names(request: Request) -> DisplayNames:
    """拿不到依赖注入的地方（app 级异常处理器）按请求语言加载显示名目录。"""
    async with async_session_factory() as session:
        return await load_display_names(session, get_locale(request))


#: 失败文案渲染所需的显示名目录：供应商与模型 ID 按请求语言换成名称。
DisplayNamesCatalog = Annotated[DisplayNames, Depends(get_display_names)]
