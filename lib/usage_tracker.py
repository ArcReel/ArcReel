"""
Async API 调用记录追踪器

Wraps UsageRepository with a module-level convenience class.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.usage_repo import UsageRepository
from lib.providers import PROVIDER_GEMINI


class UsageTracker:
    """Async API 调用记录追踪器，wrapping UsageRepository."""

    def __init__(self, *, session_factory=None):
        self._session_factory = session_factory or safe_session_factory

    async def start_call(
        self,
        project_name: str,
        call_type: str,
        model: str,
        prompt: Optional[str] = None,
        resolution: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        aspect_ratio: Optional[str] = None,
        generate_audio: bool = True,
        provider: str = PROVIDER_GEMINI,
        user_id: str = DEFAULT_USER_ID,
    ) -> int:

        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.start_call(
                project_name=project_name,
                call_type=call_type,
                model=model,
                prompt=prompt,
                resolution=resolution,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                generate_audio=generate_audio,
                provider=provider,
                user_id=user_id,
            )

    async def finish_call(
        self,
        call_id: int,
        status: str,
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
        usage_tokens: Optional[int] = None,
        service_tier: str = "default",
        generate_audio: Optional[bool] = None,
    ) -> None:

        async with self._session_factory() as session:
            repo = UsageRepository(session)
            await repo.finish_call(
                call_id,
                status=status,
                output_path=output_path,
                error_message=error_message,
                retry_count=retry_count,
                usage_tokens=usage_tokens,
                service_tier=service_tier,
                generate_audio=generate_audio,
            )

    async def get_stats(
        self,
        project_name: Optional[str] = None,
        provider: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:

        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_stats(
                project_name=project_name,
                provider=provider,
                start_date=start_date,
                end_date=end_date,
            )

    async def get_stats_grouped_by_provider(
        self,
        project_name: Optional[str] = None,
        provider: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:

        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_stats_grouped_by_provider(
                project_name=project_name,
                provider=provider,
                start_date=start_date,
                end_date=end_date,
            )

    async def get_calls(
        self,
        project_name: Optional[str] = None,
        call_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:

        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_calls(
                project_name=project_name,
                call_type=call_type,
                status=status,
                start_date=start_date,
                end_date=end_date,
                page=page,
                page_size=page_size,
            )

    async def get_projects_list(self) -> List[str]:

        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_projects_list()
