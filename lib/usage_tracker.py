"""
Async API call record tracker

Wraps UsageRepository with a module-level convenience class.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.usage_repo import UsageRepository
from lib.providers import PROVIDER_GEMINI, CallType


class UsageTracker:
    """Async API call record tracker, wrapping UsageRepository."""

    def __init__(self, *, session_factory=None):
        self._session_factory = session_factory or safe_session_factory

    async def start_call(
        self,
        project_name: str,
        call_type: CallType,
        model: str,
        prompt: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        aspect_ratio: str | None = None,
        generate_audio: bool = True,
        provider: str = PROVIDER_GEMINI,
        user_id: str = DEFAULT_USER_ID,
        segment_id: str | None = None,
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
                segment_id=segment_id,
            )

    async def finish_call(
        self,
        call_id: int,
        status: str,
        output_path: str | None = None,
        error_message: str | None = None,
        retry_count: int = 0,
        usage_tokens: int | None = None,
        service_tier: str = "default",
        generate_audio: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        quality: str | None = None,
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
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                quality=quality,
            )

    async def get_stats(
        self,
        project_name: str | None = None,
        provider: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:

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
        project_name: str | None = None,
        provider: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:

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
        project_name: str | None = None,
        call_type: CallType | None = None,
        status: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:

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

    async def get_actual_costs_by_segment(self, project_name: str) -> dict:
        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_actual_costs_by_segment(project_name)

    async def get_projects_list(self) -> list[str]:

        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_projects_list()
