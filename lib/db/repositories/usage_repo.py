"""Async repository for API call usage tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.cost_calculator import cost_calculator
from lib.db.models.api_call import ApiCall


def _iso_millis(value: datetime) -> str:
    try:
        return value.isoformat(timespec="milliseconds")
    except TypeError:
        return value.isoformat()


def _utc_now_iso() -> str:
    return _iso_millis(datetime.now(timezone.utc))


def _row_to_dict(row: ApiCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_name": row.project_name,
        "call_type": row.call_type,
        "model": row.model,
        "prompt": row.prompt,
        "resolution": row.resolution,
        "duration_seconds": row.duration_seconds,
        "aspect_ratio": row.aspect_ratio,
        "generate_audio": row.generate_audio,
        "status": row.status,
        "error_message": row.error_message,
        "output_path": row.output_path,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "duration_ms": row.duration_ms,
        "retry_count": row.retry_count,
        "cost_usd": row.cost_usd,
        "created_at": row.created_at,
    }


class UsageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def start_call(
        self,
        *,
        project_name: str,
        call_type: str,
        model: str,
        prompt: Optional[str] = None,
        resolution: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        aspect_ratio: Optional[str] = None,
        generate_audio: bool = True,
    ) -> int:
        now = _utc_now_iso()
        prompt_truncated = prompt[:500] if prompt else None

        row = ApiCall(
            project_name=project_name,
            call_type=call_type,
            model=model,
            prompt=prompt_truncated,
            resolution=resolution,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            status="pending",
            started_at=now,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row.id

    async def finish_call(
        self,
        call_id: int,
        *,
        status: str,
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
    ) -> None:
        finished_at = _utc_now_iso()

        result = await self.session.execute(
            select(ApiCall).where(ApiCall.id == call_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return

        # Calculate duration
        try:
            start = datetime.fromisoformat(row.started_at)
            end = datetime.fromisoformat(finished_at)
            duration_ms = int((end - start).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        # Calculate cost (failed = 0)
        cost_usd = 0.0
        if status == "success":
            if row.call_type == "image":
                cost_usd = cost_calculator.calculate_image_cost(
                    row.resolution or "1K", model=row.model
                )
            elif row.call_type == "video":
                cost_usd = cost_calculator.calculate_video_cost(
                    duration_seconds=row.duration_seconds or 8,
                    resolution=row.resolution or "1080p",
                    generate_audio=bool(row.generate_audio),
                    model=row.model,
                )

        error_truncated = error_message[:500] if error_message else None

        await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=duration_ms,
                retry_count=retry_count,
                cost_usd=cost_usd,
                output_path=output_path,
                error_message=error_truncated,
            )
        )
        await self.session.commit()

    async def get_stats(
        self,
        *,
        project_name: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, Any]:
        def _base_filters():
            filters = []
            if project_name:
                filters.append(ApiCall.project_name == project_name)
            if start_date:
                start = datetime(start_date.year, start_date.month, start_date.day)
                filters.append(ApiCall.started_at >= _iso_millis(start))
            if end_date:
                end_exclusive = datetime(end_date.year, end_date.month, end_date.day) + timedelta(days=1)
                filters.append(ApiCall.started_at < _iso_millis(end_exclusive))
            return filters

        filters = _base_filters()

        # Total cost
        cost_q = select(func.coalesce(func.sum(ApiCall.cost_usd), 0)).where(*filters)
        total_cost = (await self.session.execute(cost_q)).scalar() or 0

        # Image count
        img_q = select(func.count()).select_from(ApiCall).where(*filters, ApiCall.call_type == "image")
        image_count = (await self.session.execute(img_q)).scalar() or 0

        # Video count
        vid_q = select(func.count()).select_from(ApiCall).where(*filters, ApiCall.call_type == "video")
        video_count = (await self.session.execute(vid_q)).scalar() or 0

        # Failed count
        fail_q = select(func.count()).select_from(ApiCall).where(*filters, ApiCall.status == "failed")
        failed_count = (await self.session.execute(fail_q)).scalar() or 0

        # Total count
        total_q = select(func.count()).select_from(ApiCall).where(*filters)
        total_count = (await self.session.execute(total_q)).scalar() or 0

        return {
            "total_cost": round(total_cost, 4),
            "image_count": image_count,
            "video_count": video_count,
            "failed_count": failed_count,
            "total_count": total_count,
        }

    async def get_calls(
        self,
        *,
        project_name: Optional[str] = None,
        call_type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        filters = []
        if project_name:
            filters.append(ApiCall.project_name == project_name)
        if call_type:
            filters.append(ApiCall.call_type == call_type)
        if status:
            filters.append(ApiCall.status == status)
        if start_date:
            start = datetime(start_date.year, start_date.month, start_date.day)
            filters.append(ApiCall.started_at >= _iso_millis(start))
        if end_date:
            end_exclusive = datetime(end_date.year, end_date.month, end_date.day) + timedelta(days=1)
            filters.append(ApiCall.started_at < _iso_millis(end_exclusive))

        # Total count
        count_stmt = select(func.count()).select_from(ApiCall).where(*filters)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # Paginated items
        offset = (page - 1) * page_size
        items_stmt = select(ApiCall).where(*filters).order_by(ApiCall.started_at.desc()).limit(page_size).offset(offset)
        result = await self.session.execute(items_stmt)
        items = [_row_to_dict(row) for row in result.scalars().all()]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_projects_list(self) -> list[str]:
        result = await self.session.execute(
            select(ApiCall.project_name)
            .distinct()
            .order_by(ApiCall.project_name)
        )
        return [row[0] for row in result.all()]
