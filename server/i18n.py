"""请求级语言解析：从 ``Accept-Language`` 取语言，注入路由的 translator 与显示名目录。

文案表与按语言成文（``_`` / ``translate_or``）在 ``lib.i18n``，与 HTTP 框架无关；
这里只做把请求映射到语言的那一层。
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

#: 请求语言本身。取译名需要「键缺失时回退到数据源里的原名」的地方（见 translate_or）用它，
#: 常规成文仍用 Translator。
Locale = Annotated[str, Depends(get_locale)]


def _builtin_display_names(locale: str) -> DisplayNames:
    """目录加载失败时的退路：只认内置供应商与模型，自定义供应商显示泛称。

    目录只决定文案里怎么称呼供应商与模型，加载失败不应改变响应本身。
    """
    logger.warning("显示名目录加载失败，使用内置名称", exc_info=True)
    return build_display_names(locale, custom_providers={}, custom_models={})


async def load_display_names_or_builtin(session: AsyncSession, locale: str) -> DisplayNames:
    """在路由的会话里按语言加载显示名目录；查询失败时退回内置名称。

    路由需要在函数体内按需加载目录时也调用这里，不直接调用 ``load_display_names``。
    """
    try:
        return await load_display_names(session, locale)
    except Exception:
        # 会话由整个请求共用：回滚失败的查询，路由自己的查询不受牵连。
        await session.rollback()
        return _builtin_display_names(locale)


async def get_display_names(request: Request, session: AsyncSession = Depends(get_async_session)) -> DisplayNames:
    """路由依赖：按请求语言加载供应商与模型的显示名目录；查询失败时退回内置名称。"""
    return await load_display_names_or_builtin(session, get_locale(request))


async def request_display_names(request: Request) -> DisplayNames:
    """app 级异常处理器按请求语言加载目录；查询或会话失败时保留原错误，退回内置名称。"""
    locale = get_locale(request)
    try:
        async with async_session_factory() as session:
            return await load_display_names(session, locale)
    except Exception:
        return _builtin_display_names(locale)


#: 失败文案渲染所需的显示名目录：供应商与模型 ID 按请求语言换成名称。
DisplayNamesCatalog = Annotated[DisplayNames, Depends(get_display_names)]
