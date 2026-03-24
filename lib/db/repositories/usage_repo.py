"""Async repository for API call usage tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.cost_calculator import cost_calculator
from lib.db.base import _utc_now
from lib.db.models.api_call import ApiCall
from lib.db.repositories.base import BaseRepository
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_SEEDANCE


def _dt_to_iso(val: Optional[datetime]) -> Optional[str]:
    """Convert datetime to ISO string for JSON serialization."""
    return val.isoformat() if val else None


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
        "started_at": _dt_to_iso(row.started_at),
        "finished_at": _dt_to_iso(row.finished_at),
        "duration_ms": row.duration_ms,
        "retry_count": row.retry_count,
        "cost_amount": row.cost_amount,
        "currency": row.currency,
        "provider": row.provider,
        "usage_tokens": row.usage_tokens,
        "created_at": _dt_to_iso(row.created_at),
    }


class UsageRepository(BaseRepository):

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
        provider: str = PROVIDER_GEMINI,
    ) -> int:
        now = _utc_now()
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
            provider=provider,
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
        usage_tokens: Optional[int] = None,
        service_tier: str = "default",
    ) -> None:
        finished_at = _utc_now()

        result = await self.session.execute(
            select(ApiCall).where(ApiCall.id == call_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return

        # Calculate duration
        try:
            duration_ms = int((finished_at - row.started_at).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        # Calculate cost (failed = 0)
        cost_amount = 0.0
        currency = row.currency or "USD"
        effective_provider = row.provider or PROVIDER_GEMINI

        if status == "success":
            if effective_provider == PROVIDER_SEEDANCE and row.call_type == "video":
                cost_amount, currency = cost_calculator.calculate_seedance_video_cost(
                    usage_tokens=usage_tokens or 0,
                    service_tier=service_tier,
                    generate_audio=bool(row.generate_audio),
                    model=row.model,
                )
            elif effective_provider == PROVIDER_GROK and row.call_type == "video":
                cost_amount = cost_calculator.calculate_grok_video_cost(
                    duration_seconds=row.duration_seconds or 8,
                    model=row.model,
                )
                currency = "USD"
            elif row.call_type == "image":
                cost_amount = cost_calculator.calculate_image_cost(
                    row.resolution or "1K", model=row.model
                )
                currency = "USD"
            elif row.call_type == "video":
                cost_amount = cost_calculator.calculate_video_cost(
                    duration_seconds=row.duration_seconds or 8,
                    resolution=row.resolution or "1080p",
                    generate_audio=bool(row.generate_audio),
                    model=row.model,
                )
                currency = "USD"

        error_truncated = error_message[:500] if error_message else None

        await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=duration_ms,
                retry_count=retry_count,
                cost_amount=cost_amount,
                currency=currency,
                usage_tokens=usage_tokens,
                output_path=output_path,
                error_message=error_truncated,
            )
        )
        await self.session.commit()

    async def get_stats(
        self,
        *,
        project_name: Optional[str] = None,
        provider: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, Any]:
        def _base_filters():
            filters = []
            if project_name:
                filters.append(ApiCall.project_name == project_name)
            if provider:
                filters.append(ApiCall.provider == provider)
            if start_date:
                start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
                filters.append(ApiCall.started_at >= start)
            if end_date:
                end_exclusive = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
                filters.append(ApiCall.started_at < end_exclusive)
            return filters

        filters = _base_filters()

        # Main aggregation query
        main_stmt = select(
            func.coalesce(func.sum(
                case((ApiCall.currency == "USD", ApiCall.cost_amount), else_=0)
            ), 0).label("total_cost_usd"),
            func.count(case((ApiCall.call_type == "image", 1))).label("image_count"),
            func.count(case((ApiCall.call_type == "video", 1))).label("video_count"),
            func.count(case((ApiCall.status == "failed", 1))).label("failed_count"),
            func.count().label("total_count"),
        ).select_from(ApiCall).where(*filters)
        main_stmt = self._scope_query(main_stmt, ApiCall)
        row = (await self.session.execute(main_stmt)).one()

        # Cost by currency
        currency_stmt = select(
            ApiCall.currency,
            func.coalesce(func.sum(ApiCall.cost_amount), 0).label("total"),
        ).select_from(ApiCall).where(*filters).group_by(ApiCall.currency)
        currency_stmt = self._scope_query(currency_stmt, ApiCall)
        currency_rows = (await self.session.execute(currency_stmt)).all()

        cost_by_currency = {r.currency: round(r.total, 4) for r in currency_rows}

        return {
            "total_cost": round(row.total_cost_usd, 4),
            "cost_by_currency": cost_by_currency,
            "image_count": row.image_count,
            "video_count": row.video_count,
            "failed_count": row.failed_count,
            "total_count": row.total_count,
        }

    async def get_stats_grouped_by_provider(
        self,
        *,
        project_name: Optional[str] = None,
        provider: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, Any]:
        filters = []
        if project_name:
            filters.append(ApiCall.project_name == project_name)
        if provider:
            filters.append(ApiCall.provider == provider)
        if start_date:
            start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
            filters.append(ApiCall.started_at >= start)
        if end_date:
            end_exclusive = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
            filters.append(ApiCall.started_at < end_exclusive)

        stmt = (
            select(
                ApiCall.provider,
                ApiCall.call_type,
                func.count().label("total_calls"),
                func.count(case((ApiCall.status == "success", 1))).label("success_calls"),
                func.coalesce(func.sum(
                    case((ApiCall.currency == "USD", ApiCall.cost_amount), else_=0)
                ), 0).label("total_cost_usd"),
                func.coalesce(func.sum(ApiCall.duration_ms), 0).label("total_duration_ms"),
            )
            .select_from(ApiCall)
            .where(*filters)
            .group_by(ApiCall.provider, ApiCall.call_type)
            .order_by(ApiCall.provider, ApiCall.call_type)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()

        stats = [
            {
                "provider": row.provider,
                "call_type": row.call_type,
                "total_calls": row.total_calls,
                "success_calls": row.success_calls,
                "total_cost_usd": round(row.total_cost_usd, 4),
                "total_duration_seconds": round(row.total_duration_ms / 1000, 1) if row.total_duration_ms else 0,
            }
            for row in rows
        ]

        period_start: Optional[str] = None
        period_end: Optional[str] = None
        if start_date:
            period_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).isoformat()
        if end_date:
            period_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc).isoformat()

        return {
            "stats": stats,
            "period": {"start": period_start, "end": period_end},
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
            start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
            filters.append(ApiCall.started_at >= start)
        if end_date:
            end_exclusive = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
            filters.append(ApiCall.started_at < end_exclusive)

        # Total count
        count_stmt = select(func.count()).select_from(ApiCall).where(*filters)
        count_stmt = self._scope_query(count_stmt, ApiCall)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # Paginated items
        offset = (page - 1) * page_size
        items_stmt = select(ApiCall).where(*filters).order_by(ApiCall.started_at.desc()).limit(page_size).offset(offset)
        items_stmt = self._scope_query(items_stmt, ApiCall)
        result = await self.session.execute(items_stmt)
        items = [_row_to_dict(row) for row in result.scalars().all()]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_projects_list(self) -> list[str]:
        stmt = (
            select(ApiCall.project_name)
            .distinct()
            .order_by(ApiCall.project_name)
        )
        stmt = self._scope_query(stmt, ApiCall)
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
