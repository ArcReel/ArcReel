"""Async repository for API call usage tracking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update

from lib.cost_calculator import cost_calculator
from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.repositories.base import BaseRepository
from lib.providers import PROVIDER_GEMINI, CallType


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
        "started_at": dt_to_iso(row.started_at),
        "finished_at": dt_to_iso(row.finished_at),
        "duration_ms": row.duration_ms,
        "retry_count": row.retry_count,
        "cost_amount": row.cost_amount,
        "currency": row.currency,
        "provider": row.provider,
        "usage_tokens": row.usage_tokens,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "created_at": dt_to_iso(row.created_at),
    }


class UsageRepository(BaseRepository):
    async def start_call(
        self,
        *,
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
    ) -> int:
        now = utc_now()
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
            user_id=user_id,
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
        output_path: str | None = None,
        error_message: str | None = None,
        retry_count: int = 0,
        usage_tokens: int | None = None,
        service_tier: str = "default",
        generate_audio: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        finished_at = utc_now()

        result = await self.session.execute(select(ApiCall).where(ApiCall.id == call_id))
        row = result.scalar_one_or_none()
        if not row:
            return

        # 后端回写的实际 generate_audio 覆盖 start_call 时的请求值
        if generate_audio is not None:
            row.generate_audio = generate_audio

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
            cost_amount, currency = cost_calculator.calculate_cost(
                provider=effective_provider,
                call_type=row.call_type,
                model=row.model,
                resolution=row.resolution,
                duration_seconds=row.duration_seconds,
                generate_audio=bool(row.generate_audio),
                usage_tokens=usage_tokens,
                service_tier=service_tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
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
                cost_amount=cost_amount,
                currency=currency,
                usage_tokens=usage_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                output_path=output_path,
                error_message=error_truncated,
            )
        )
        await self.session.commit()

    @staticmethod
    def _build_filters(
        *,
        project_name: str | None = None,
        provider: str | None = None,
        call_type: CallType | None = None,
        status: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list:
        filters: list = []
        if project_name:
            filters.append(ApiCall.project_name == project_name)
        if provider:
            filters.append(ApiCall.provider == provider)
        if call_type:
            filters.append(ApiCall.call_type == call_type)
        if status:
            filters.append(ApiCall.status == status)
        if start_date:
            start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
            filters.append(ApiCall.started_at >= start)
        if end_date:
            end_exclusive = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)
            filters.append(ApiCall.started_at < end_exclusive)
        return filters

    async def get_stats(
        self,
        *,
        project_name: str | None = None,
        provider: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        filters = self._build_filters(
            project_name=project_name,
            provider=provider,
            start_date=start_date,
            end_date=end_date,
        )

        # Main aggregation query
        main_stmt = (
            select(
                func.coalesce(func.sum(case((ApiCall.currency == "USD", ApiCall.cost_amount), else_=0)), 0).label(
                    "total_cost_usd"
                ),
                func.count(case((ApiCall.call_type == "image", 1))).label("image_count"),
                func.count(case((ApiCall.call_type == "video", 1))).label("video_count"),
                func.count(case((ApiCall.call_type == "text", 1))).label("text_count"),
                func.count(case((ApiCall.status == "failed", 1))).label("failed_count"),
                func.count().label("total_count"),
            )
            .select_from(ApiCall)
            .where(*filters)
        )
        main_stmt = self._scope_query(main_stmt, ApiCall)
        row = (await self.session.execute(main_stmt)).one()

        # Cost by currency
        currency_stmt = (
            select(
                ApiCall.currency,
                func.coalesce(func.sum(ApiCall.cost_amount), 0).label("total"),
            )
            .select_from(ApiCall)
            .where(*filters)
            .group_by(ApiCall.currency)
        )
        currency_stmt = self._scope_query(currency_stmt, ApiCall)
        currency_rows = (await self.session.execute(currency_stmt)).all()

        cost_by_currency = {r.currency: round(r.total, 4) for r in currency_rows}

        return {
            "total_cost": round(row.total_cost_usd, 4),
            "cost_by_currency": cost_by_currency,
            "image_count": row.image_count,
            "video_count": row.video_count,
            "text_count": row.text_count,
            "failed_count": row.failed_count,
            "total_count": row.total_count,
        }

    async def get_stats_grouped_by_provider(
        self,
        *,
        project_name: str | None = None,
        provider: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        filters = self._build_filters(
            project_name=project_name,
            provider=provider,
            start_date=start_date,
            end_date=end_date,
        )

        stmt = (
            select(
                ApiCall.provider,
                ApiCall.call_type,
                func.count().label("total_calls"),
                func.count(case((ApiCall.status == "success", 1))).label("success_calls"),
                func.coalesce(func.sum(case((ApiCall.currency == "USD", ApiCall.cost_amount), else_=0)), 0).label(
                    "total_cost_usd"
                ),
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

        period_start: str | None = None
        period_end: str | None = None
        if start_date:
            period_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC).isoformat()
        if end_date:
            period_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC).isoformat()

        return {
            "stats": stats,
            "period": {"start": period_start, "end": period_end},
        }

    async def get_calls(
        self,
        *,
        project_name: str | None = None,
        call_type: CallType | None = None,
        status: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        filters = self._build_filters(
            project_name=project_name,
            call_type=call_type,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

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
        stmt = select(ApiCall.project_name).distinct().order_by(ApiCall.project_name)
        stmt = self._scope_query(stmt, ApiCall)
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
