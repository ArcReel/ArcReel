"""
Async API 调用记录追踪器

Wraps UsageRepository with a module-level convenience class.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from lib.db import async_session_factory, init_db
from lib.db.repositories.usage_repo import UsageRepository


class UsageTracker:
    """Async API 调用记录追踪器，wrapping UsageRepository."""

    def __init__(self, *, session_factory=None, _skip_init_db: bool = False):
        self._session_factory = session_factory or async_session_factory
        self._skip_init_db = _skip_init_db
        self._db_initialized = _skip_init_db

    async def _ensure_db(self) -> None:
        if not self._db_initialized:
            await init_db()
            self._db_initialized = True

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
    ) -> int:
        await self._ensure_db()
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
            )

    async def finish_call(
        self,
        call_id: int,
        status: str,
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
    ) -> None:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = UsageRepository(session)
            await repo.finish_call(
                call_id,
                status=status,
                output_path=output_path,
                error_message=error_message,
                retry_count=retry_count,
            )

    async def get_stats(
        self,
        project_name: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_stats(
                project_name=project_name,
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
        await self._ensure_db()
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
        await self._ensure_db()
        async with self._session_factory() as session:
            repo = UsageRepository(session)
            return await repo.get_projects_list()
