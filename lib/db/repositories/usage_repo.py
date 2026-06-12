"""Async repository for API call usage tracking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update

from lib.cost_calculator import cost_calculator
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.repositories.base import BaseRepository, rowcount
from lib.providers import PROVIDER_GEMINI, CallType

# 计费时长合理上限（24 小时），语义单点定义：repo 写入层是全部 backend 落账的最后防线，
# 超出上限的计费时长视同未提供、回落请求时长，防超大数值写入 DB Integer 列溢出；
# 解析侧（grok / dashscope extractor）的 clamp 引用同一常量，保持口径一致。
MAX_BILLED_DURATION_SECONDS = 86400


def _classify_asset_output_path(output_path: str | None) -> str:
    """从 api_call.output_path 推断资产类型（characters/scenes/props/products/other）。

    v0→v1 迁移前的历史任务会写入 ``clues/...`` 路径，这里归并到 props，
    与迁移默认的 clue→prop 映射一致，避免旧账单被静默归入 other 而丢失。
    """
    if not output_path:
        return "other"
    # 兼容绝对路径与相对路径
    normalized = output_path.replace("\\", "/").lower()
    for asset_type in ("characters", "scenes", "props", "products"):
        if f"/{asset_type}/" in normalized or normalized.startswith(f"{asset_type}/"):
            return asset_type
    if "/clues/" in normalized or normalized.startswith("clues/"):
        return "props"
    return "other"


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
        "segment_id": row.segment_id,
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
        "image_input_tokens": row.image_input_tokens,
        "image_output_tokens": row.image_output_tokens,
        "text_input_tokens": row.text_input_tokens,
        "text_output_tokens": row.text_output_tokens,
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
        segment_id: str | None = None,
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
            segment_id=segment_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row.id

    async def finalize_pending_by_call_id(
        self,
        *,
        call_id: int,
        cost_amount: float | None = None,
        currency: str | None = None,
        status: str = "success",
        service_tier: str = "default",
        usage_tokens: int | None = None,
        generate_audio: bool | None = None,
        billed_duration_seconds: int | None = None,
    ) -> int:
        """Resume 路径专用：按 call_id 精准翻 pending → success/failed。

        Repo WHERE 子句包含 ``status='pending'`` —— 已 success 行不 touch
        （防止 generate 已 finish_call 后崩、resume 反向把 success 行覆写）。
        cost_amount/currency 行为对齐 finish_call：
        - cost_amount=None + status='success' → 按 ApiCall 行字段调 cost_calculator
          算实际 cost（与 generate 路径等价记账，避免视频已生成但 cost=0 永久漏记）
        - cost_amount=None + status='failed' → 走 0.0/USD（失败不计费）
        - 显式传 cost_amount → 直接用
        service_tier 由 caller 从原 generate 上下文透传（ApiCall 模型无此列，
        与 finish_call 同 caller-passed 模式），非 default 档位才能按真实档计费。
        usage_tokens 同样由 caller 从 resume_video 返回的 VideoGenerationResult.usage_tokens
        透传：Ark video 按 usage_tokens 计费，未传则 cost 永远为 0；其它 provider 不依赖该字段。
        generate_audio 由 caller 从 backend 返回值透传：provider 在 submit 后可能降级/关闭音频，
        与 finish_call 的 ``generate_audio is not None`` 覆盖语义对齐，避免按请求值误计费。
        billed_duration_seconds 同样由 caller 从 backend 返回值透传：provider 回报的实际计费
        时长覆盖请求时长（与 finish_call 同覆盖语义，非正值视同未提供），保证同一笔调用
        无论经 generate 还是 resume 完成，账本时长与自动 cost 口径一致。
        duration_ms 按 (finished_at - started_at) 回写，让 get_stats_grouped_by_provider
        的时长汇总不会因 resume 完成的调用 duration_ms=NULL 而系统性压低。
        provider 端已扣费的事实通过 status='pending' WHERE 保护——绝不触发再次扣费。
        返回受影响行数（0=幂等无操作；1=正常翻一行）。
        """
        finished_at = utc_now()

        # 无条件 fetch row：既用于 auto-calc cost 路径，也用于 duration_ms 回写
        # （即便 caller 显式传 cost_amount，duration_ms 计算仍需要 started_at）。
        # row.status='pending' 守卫继续由下面的 UPDATE WHERE 子句保证幂等性。
        select_result = await self.session.execute(select(ApiCall).where(ApiCall.id == call_id))
        row = select_result.scalar_one_or_none()
        if row is None:
            return 0

        duration_ms = 0
        try:
            duration_ms = int((finished_at - row.started_at).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        # backend 回写的实际 generate_audio 覆盖 start_call 时的请求值
        # （与 finish_call 同语义；用于 cost_calculator 输入及 UPDATE 回写）
        effective_generate_audio = generate_audio if generate_audio is not None else row.generate_audio

        # provider 回报的实际计费时长覆盖请求时长（与 finish_call 同覆盖语义，非正或超出
        # 合理上限视同未提供）。走局部变量 + UPDATE 列回写而非 ORM 属性赋值，让覆盖继续受
        # WHERE status='pending' 幂等守卫保护，不被 autoflush 绕过。
        effective_duration_seconds = (
            billed_duration_seconds
            if billed_duration_seconds is not None and 0 < billed_duration_seconds <= MAX_BILLED_DURATION_SECONDS
            else row.duration_seconds
        )

        final_cost_amount = 0.0
        final_currency = currency or "USD"

        if cost_amount is not None:
            final_cost_amount = cost_amount
            final_currency = currency or "USD"
        elif status == "success" and row.status == "pending":
            effective_provider = row.provider or PROVIDER_GEMINI
            custom_price_input: float | None = None
            custom_price_output: float | None = None
            custom_currency: str | None = None
            if is_custom_provider(effective_provider):
                from lib.db.repositories.custom_provider_repo import CustomProviderRepository

                repo = CustomProviderRepository(self.session)
                price_model = await repo.get_model_by_ids(parse_provider_id(effective_provider), row.model or "")
                if price_model:
                    custom_price_input = price_model.price_input
                    custom_price_output = price_model.price_output
                    custom_currency = price_model.currency

            final_cost_amount, final_currency = cost_calculator.calculate_cost(
                provider=effective_provider,
                call_type=row.call_type,  # type: ignore[arg-type]
                model=row.model,
                resolution=row.resolution,
                aspect_ratio=row.aspect_ratio,
                duration_seconds=effective_duration_seconds,
                generate_audio=bool(effective_generate_audio),
                service_tier=service_tier,
                usage_tokens=usage_tokens,
                custom_price_input=custom_price_input,
                custom_price_output=custom_price_output,
                custom_currency=custom_currency,
            )

        result = await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id, ApiCall.status == "pending")
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=duration_ms,
                duration_seconds=effective_duration_seconds,
                cost_amount=final_cost_amount,
                currency=final_currency,
                usage_tokens=usage_tokens,
                generate_audio=effective_generate_audio,
            )
        )
        affected = rowcount(result)
        if affected > 0:
            await self.session.commit()
        return affected

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
        quality: str | None = None,
        image_input_tokens: int | None = None,
        image_output_tokens: int | None = None,
        text_input_tokens: int | None = None,
        text_output_tokens: int | None = None,
        cost_amount: float | None = None,
        currency: str | None = None,
        billed_duration_seconds: int | None = None,
    ) -> None:
        finished_at = utc_now()

        result = await self.session.execute(select(ApiCall).where(ApiCall.id == call_id))
        row = result.scalar_one_or_none()
        if not row:
            return

        # provider 回报的实际计费时长覆盖 start_call 时的请求时长（如 DashScope usage.duration
        # 含输入参考视频时长）；非正或超出合理上限的值视同未提供，回落请求时长，不记 0 秒账。
        # 显式 cost_amount 仍优先于按时长的自动计算，但实际计费时长照常回写账本。
        # 走局部变量 + UPDATE 列回写而非 ORM 属性赋值，避免 autoflush 对同一行额外多发一条 UPDATE。
        effective_duration_seconds = (
            billed_duration_seconds
            if billed_duration_seconds is not None and 0 < billed_duration_seconds <= MAX_BILLED_DURATION_SECONDS
            else row.duration_seconds
        )

        # 后端回写的实际 generate_audio 覆盖 start_call 时的请求值
        effective_generate_audio = generate_audio if generate_audio is not None else row.generate_audio

        # Calculate duration
        try:
            duration_ms = int((finished_at - row.started_at).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        # Calculate cost. Explicit cost input is treated as provider-reported billing data.
        final_cost_amount = 0.0
        final_currency = row.currency or "USD"
        effective_provider = row.provider or PROVIDER_GEMINI

        # Pre-query custom provider pricing (avoids sync-over-async in CostCalculator)
        custom_price_input: float | None = None
        custom_price_output: float | None = None
        custom_currency: str | None = None
        if status == "success" and is_custom_provider(effective_provider):
            from lib.db.repositories.custom_provider_repo import CustomProviderRepository

            repo = CustomProviderRepository(self.session)
            price_model = await repo.get_model_by_ids(parse_provider_id(effective_provider), row.model or "")
            if price_model:
                custom_price_input = price_model.price_input
                custom_price_output = price_model.price_output
                custom_currency = price_model.currency

        # OpenAI 图片调用：input_tokens/output_tokens 列的"总和"语义
        # = image_*_tokens + text_*_tokens（用于跨 call_type 聚合查询保持兼容）
        has_image_tokens = any(
            t is not None for t in (image_input_tokens, image_output_tokens, text_input_tokens, text_output_tokens)
        )
        if has_image_tokens:
            input_tokens = (image_input_tokens or 0) + (text_input_tokens or 0)
            output_tokens = (image_output_tokens or 0) + (text_output_tokens or 0)

        if cost_amount is not None:
            final_cost_amount = cost_amount
            final_currency = currency or row.currency or "USD"
        elif status == "success":
            final_cost_amount, final_currency = cost_calculator.calculate_cost(
                provider=effective_provider,
                call_type=row.call_type,  # type: ignore[arg-type]
                model=row.model,
                resolution=row.resolution,
                aspect_ratio=row.aspect_ratio,
                duration_seconds=effective_duration_seconds,
                generate_audio=bool(effective_generate_audio),
                usage_tokens=usage_tokens,
                service_tier=service_tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                quality=quality,
                image_input_tokens=image_input_tokens,
                image_output_tokens=image_output_tokens,
                text_input_tokens=text_input_tokens,
                text_output_tokens=text_output_tokens,
                custom_price_input=custom_price_input,
                custom_price_output=custom_price_output,
                custom_currency=custom_currency,
            )

        error_truncated = error_message[:500] if error_message else None

        await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=duration_ms,
                duration_seconds=effective_duration_seconds,
                generate_audio=effective_generate_audio,
                retry_count=retry_count,
                cost_amount=final_cost_amount,
                currency=final_currency,
                usage_tokens=usage_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                image_input_tokens=image_input_tokens,
                image_output_tokens=image_output_tokens,
                text_input_tokens=text_input_tokens,
                text_output_tokens=text_output_tokens,
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
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (ApiCall.status == "success") & (ApiCall.currency == "USD") & (ApiCall.cost_amount > 0),
                                ApiCall.cost_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("total_cost_usd"),
                func.count(case((ApiCall.call_type == "image", 1))).label("image_count"),
                func.count(case((ApiCall.call_type == "video", 1))).label("video_count"),
                func.count(case((ApiCall.call_type == "text", 1))).label("text_count"),
                func.count(case((ApiCall.call_type == "audio", 1))).label("audio_count"),
                func.count(case((ApiCall.status == "failed", 1))).label("failed_count"),
                func.count().label("total_count"),
            )
            .select_from(ApiCall)
            .where(*filters)
        )
        main_stmt = self._scope_query(main_stmt, ApiCall)
        row = (await self.session.execute(main_stmt)).one()

        # Cost by currency mirrors project cost estimates: only successful billed calls count.
        currency_stmt = (
            select(
                ApiCall.currency,
                func.coalesce(func.sum(ApiCall.cost_amount), 0).label("total"),
            )
            .select_from(ApiCall)
            .where(
                *filters,
                ApiCall.status == "success",
                ApiCall.cost_amount > 0,
                ApiCall.currency.isnot(None),
            )
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
            "audio_count": row.audio_count,
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
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (ApiCall.status == "success") & (ApiCall.currency == "USD") & (ApiCall.cost_amount > 0),
                                ApiCall.cost_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("total_cost_usd"),
                func.coalesce(func.sum(ApiCall.duration_ms), 0).label("total_duration_ms"),
            )
            .select_from(ApiCall)
            .where(*filters)
            .group_by(ApiCall.provider, ApiCall.call_type)
            .order_by(ApiCall.provider, ApiCall.call_type)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()

        currency_stmt = (
            select(
                ApiCall.provider,
                ApiCall.call_type,
                ApiCall.currency,
                func.coalesce(func.sum(ApiCall.cost_amount), 0).label("total"),
            )
            .select_from(ApiCall)
            .where(
                *filters,
                ApiCall.status == "success",
                ApiCall.cost_amount > 0,
                ApiCall.currency.isnot(None),
            )
            .group_by(ApiCall.provider, ApiCall.call_type, ApiCall.currency)
        )
        currency_stmt = self._scope_query(currency_stmt, ApiCall)
        currency_rows = (await self.session.execute(currency_stmt)).all()
        cost_by_group: dict[tuple[str | None, str | None], dict[str, float]] = {}
        for provider_value, call_type_value, currency, total in currency_rows:
            cost_by_group.setdefault((provider_value, call_type_value), {})[currency] = round(total, 4)

        stats = [
            {
                "provider": row.provider,
                "call_type": row.call_type,
                "total_calls": row.total_calls,
                "success_calls": row.success_calls,
                "total_cost_usd": round(row.total_cost_usd, 4),
                "cost_by_currency": cost_by_group.get((row.provider, row.call_type), {}),
                "total_duration_seconds": round(row.total_duration_ms / 1000, 1) if row.total_duration_ms else 0,
            }
            for row in rows
        ]

        # Enrich each stat entry with display_name (batch query for custom providers)
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.db.models.custom_provider import CustomProvider

        custom_ids = set()
        for stat in stats:
            p = stat["provider"]
            if p and is_custom_provider(p):
                try:
                    custom_ids.add(parse_provider_id(p))
                except ValueError:
                    pass  # 防御畸形 provider 字符串（如 "custom-abc"）

        custom_names: dict[int, str] = {}
        if custom_ids:
            cp_stmt = select(CustomProvider).where(CustomProvider.id.in_(custom_ids))
            cp_rows = (await self.session.execute(cp_stmt)).scalars()
            custom_names = {cp.id: cp.display_name for cp in cp_rows}

        for stat in stats:
            provider_str = stat["provider"]
            if provider_str and is_custom_provider(provider_str):
                try:
                    db_id = parse_provider_id(provider_str)
                    stat["display_name"] = custom_names.get(db_id, provider_str)
                except ValueError:
                    stat["display_name"] = provider_str
            else:
                meta = PROVIDER_REGISTRY.get(provider_str or "")
                stat["display_name"] = meta.display_name if meta else provider_str

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

    async def get_actual_costs_by_segment(
        self,
        project_name: str,
    ) -> dict[str, dict[str, dict[str, float]]]:
        """按 segment_id + call_type + currency 汇总实际费用。

        Returns:
            {segment_id: {call_type: {currency: total_amount}}}
            segment_id 为 None 的记录归入 "__project__" 键。
        """
        stmt = (
            select(
                ApiCall.segment_id,
                ApiCall.call_type,
                ApiCall.currency,
                func.sum(ApiCall.cost_amount).label("total"),
            )
            .where(
                ApiCall.project_name == project_name,
                ApiCall.status == "success",
                ApiCall.cost_amount > 0,
            )
            .group_by(ApiCall.segment_id, ApiCall.call_type, ApiCall.currency)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()

        result: dict[str, dict[str, dict[str, float]]] = {}
        for seg_id, call_type, currency, total in rows:
            key = seg_id if seg_id is not None else "__project__"
            result.setdefault(key, {}).setdefault(call_type, {})[currency] = round(total, 6)
        return result

    async def get_project_image_costs_by_asset_type(
        self,
        project_name: str,
    ) -> dict[str, dict[str, float]]:
        """project-level（segment_id is null）的 image 成本按 output_path 前缀分拆。

        Returns:
            {asset_type: {currency: total_amount}}，asset_type ∈ {characters, scenes, props, products, other}。
        """
        stmt = (
            select(
                ApiCall.output_path,
                ApiCall.currency,
                func.sum(ApiCall.cost_amount).label("total"),
            )
            .where(
                ApiCall.project_name == project_name,
                ApiCall.status == "success",
                ApiCall.cost_amount > 0,
                ApiCall.call_type == "image",
                ApiCall.segment_id.is_(None),
            )
            .group_by(ApiCall.output_path, ApiCall.currency)
        )
        stmt = self._scope_query(stmt, ApiCall)
        rows = (await self.session.execute(stmt)).all()

        result: dict[str, dict[str, float]] = {}
        for output_path, currency, total in rows:
            asset_type = _classify_asset_output_path(output_path)
            bucket = result.setdefault(asset_type, {})
            bucket[currency] = round(bucket.get(currency, 0) + total, 6)
        return result

    async def get_projects_list(self) -> list[str]:
        stmt = select(ApiCall.project_name).distinct().order_by(ApiCall.project_name)
        stmt = self._scope_query(stmt, ApiCall)
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
