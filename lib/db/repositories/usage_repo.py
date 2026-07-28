"""Async repository for API call usage tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update

from lib.cost_calculator import cost_calculator
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.repositories.base import BaseRepository, rowcount
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.pricing.strategies import PricingParams
from lib.providers import PROVIDER_GEMINI, CallType

# 计费时长合理上限（24 小时），语义单点定义：repo 写入层是全部 backend 落账的最后防线，
# 超出上限的计费时长视同未提供、回落请求时长，防超大数值写入 DB Integer 列溢出；
# 解析侧（grok / dashscope extractor）的 clamp 引用同一常量，保持口径一致。
MAX_BILLED_DURATION_SECONDS = 86400

# 存量裸 provider 值的报表显示兜底：身份反转前，文本 gemini 调用以 backend.name 落账为裸
# "gemini"（图像/视频侧已是 "gemini-aistudio"）。这些历史行不迁移，仅在分组报表按此表补一个
# 友好显示名；registry 只登记新格式 key（gemini-aistudio / gemini-vertex），故裸值查不到 meta。
_LEGACY_PROVIDER_DISPLAY_NAMES = {PROVIDER_GEMINI: "Gemini"}


@dataclass(frozen=True)
class SettlementInput:
    """仓储写侧的申报值对象：承载 caller 在快照时刻提交的原始计费维度。

    这些是"申报时刻"的原始值——``billed_duration_seconds`` 可能非法/超限、显式
    ``cost_amount`` 绕过自动计算——由 ``_settle`` 归一为生效定价（``PricingParams``）。
    与 ``PricingParams``（结算后的生效定价输入）刻意分成两个对象，避免原始值与生效值
    在同一结构里语义混淆。新增计费字段自此只扩本对象，不再穿仓储写侧散参。
    """

    cost_amount: float | None = None
    currency: str | None = None
    service_tier: str = "default"
    generate_audio: bool | None = None
    billed_duration_seconds: int | None = None
    usage_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    quality: str | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None


@dataclass(frozen=True)
class _SettledCall:
    """``_settle`` 输出：两条快照路径共用的生效结算值，各自 UPDATE 直接取用。"""

    duration_ms: int
    effective_duration_seconds: int | None
    effective_generate_audio: bool | None
    input_tokens: int | None
    output_tokens: int | None
    cost_amount: float
    currency: str


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

    async def _settle(
        self,
        *,
        row: ApiCall,
        finished_at: datetime,
        settlement: SettlementInput,
        auto_calc: bool,
        base_currency: str,
    ) -> _SettledCall:
        """两条快照路径共享的结算函数：输入 ApiCall 行与申报值，输出生效计费时长、
        生效有声标志、duration_ms、费用与币种（含 OpenAI 图片 token 聚合列归一）。

        - duration_ms 按 ``finished_at - started_at`` 回写；SQLite 跨 session 回读的
          ``started_at`` 为 naive datetime，相减前按 UTC 补齐 tzinfo 与 aware 的
          ``finished_at`` 对齐口径；计算异常仍兜底为 0（最终防线）。
        - 计费时长/有声标志覆盖：backend 回报值覆盖 start_call 时的请求值，非正或超出
          ``MAX_BILLED_DURATION_SECONDS`` 的计费时长视同未提供、回落请求时长。
        - 费用：显式 ``cost_amount`` 视作 provider 直报计费数据、优先于自动计算；否则在
          ``auto_calc`` 为真时按行字段 + 申报值调 ``cost_calculator``（自定义供应商价格预查
          避免 CostCalculator 内 sync-over-async）。
        - ``base_currency`` 由 caller 传入承载各自的币种兜底口径（finish_call 兜底
          ``row.currency``，resume 兜底 ``settlement.currency``），保持行为不分叉。

        防重复计费的 pending 守卫由各 public 方法的 UPDATE WHERE 子句显式承担，不在此。
        """
        try:
            started_at = row.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            settled_finished_at = finished_at if finished_at.tzinfo is not None else finished_at.replace(tzinfo=UTC)
            duration_ms = int((settled_finished_at - started_at).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        effective_generate_audio = (
            settlement.generate_audio if settlement.generate_audio is not None else row.generate_audio
        )

        billed = settlement.billed_duration_seconds
        effective_duration_seconds = (
            billed if billed is not None and 0 < billed <= MAX_BILLED_DURATION_SECONDS else row.duration_seconds
        )

        # OpenAI 图片调用：input_tokens/output_tokens 列的"总和"语义
        # = image_*_tokens + text_*_tokens（用于跨 call_type 聚合查询保持兼容）。
        # resume 路径无图片 token，has_image_tokens 恒 False → 保持申报值原样。
        input_tokens = settlement.input_tokens
        output_tokens = settlement.output_tokens
        has_image_tokens = any(
            t is not None
            for t in (
                settlement.image_input_tokens,
                settlement.image_output_tokens,
                settlement.text_input_tokens,
                settlement.text_output_tokens,
            )
        )
        if has_image_tokens:
            input_tokens = (settlement.image_input_tokens or 0) + (settlement.text_input_tokens or 0)
            output_tokens = (settlement.image_output_tokens or 0) + (settlement.text_output_tokens or 0)

        cost_amount = 0.0
        currency = base_currency
        if settlement.cost_amount is not None:
            cost_amount = settlement.cost_amount
            currency = settlement.currency or base_currency
        elif auto_calc:
            effective_provider = row.provider or PROVIDER_GEMINI

            # 自定义供应商价格预查（避免 CostCalculator 内 sync-over-async）；与费用预估链路共用
            # CustomProviderRepository.resolve_price，非自定义 / 畸形 id / 查询异常 / 缺价统一降级为无价。
            custom_price = await CustomProviderRepository(self.session).resolve_price(
                effective_provider, row.model or ""
            )

            params = PricingParams(
                call_type=row.call_type,  # pyright: ignore[reportArgumentType]
                model=row.model,
                resolution=row.resolution,
                aspect_ratio=row.aspect_ratio,
                duration_seconds=effective_duration_seconds,
                generate_audio=bool(effective_generate_audio),
                usage_tokens=settlement.usage_tokens,
                service_tier=settlement.service_tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                quality=settlement.quality,
                image_input_tokens=settlement.image_input_tokens,
                image_output_tokens=settlement.image_output_tokens,
                text_input_tokens=settlement.text_input_tokens,
                text_output_tokens=settlement.text_output_tokens,
            )
            cost_amount, currency = cost_calculator.calculate_cost(
                effective_provider,
                params,
                custom_price_input=custom_price.price_input,
                custom_price_output=custom_price.price_output,
                custom_currency=custom_price.currency,
            )

        return _SettledCall(
            duration_ms=duration_ms,
            effective_duration_seconds=effective_duration_seconds,
            effective_generate_audio=effective_generate_audio,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_amount=cost_amount,
            currency=currency,
        )

    async def finalize_pending_by_call_id(
        self,
        *,
        call_id: int,
        settlement: SettlementInput,
        status: str = "success",
    ) -> int:
        """Resume 路径专用：按 call_id 精准翻 pending → success/failed。

        Repo WHERE 子句包含 ``status='pending'`` —— 已 success 行不 touch
        （防止 generate 已 finish_call 后崩、resume 反向把 success 行覆写）；provider 端
        已扣费的事实由此守卫，绝不触发再次扣费。结算逻辑（计费时长/有声覆盖、自动 cost、
        duration_ms 回写）与 finish_call 共享 ``_settle``，唯币种兜底口径按 resume 语义
        取 ``settlement.currency``。返回受影响行数（0=幂等无操作；1=正常翻一行）。
        """
        finished_at = utc_now()

        # 无条件 fetch row：既用于 auto-calc cost 路径，也用于 duration_ms 回写
        # （即便 caller 显式传 cost_amount，duration_ms 计算仍需要 started_at）。
        # row.status='pending' 守卫继续由下面的 UPDATE WHERE 子句保证幂等性。
        select_result = await self.session.execute(select(ApiCall).where(ApiCall.id == call_id))
        row = select_result.scalar_one_or_none()
        if row is None:
            return 0

        settled = await self._settle(
            row=row,
            finished_at=finished_at,
            settlement=settlement,
            auto_calc=status == "success" and row.status == "pending",
            base_currency=settlement.currency or "USD",
        )

        result = await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id, ApiCall.status == "pending")
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=settled.duration_ms,
                duration_seconds=settled.effective_duration_seconds,
                cost_amount=settled.cost_amount,
                currency=settled.currency,
                usage_tokens=settlement.usage_tokens,
                generate_audio=settled.effective_generate_audio,
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
        settlement: SettlementInput,
        output_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        finished_at = utc_now()

        result = await self.session.execute(select(ApiCall).where(ApiCall.id == call_id))
        row = result.scalar_one_or_none()
        if not row:
            return

        settled = await self._settle(
            row=row,
            finished_at=finished_at,
            settlement=settlement,
            auto_calc=status == "success",
            base_currency=row.currency or "USD",
        )

        error_truncated = error_message[:500] if error_message else None

        await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=settled.duration_ms,
                duration_seconds=settled.effective_duration_seconds,
                generate_audio=settled.effective_generate_audio,
                cost_amount=settled.cost_amount,
                currency=settled.currency,
                usage_tokens=settlement.usage_tokens,
                input_tokens=settled.input_tokens,
                output_tokens=settled.output_tokens,
                image_input_tokens=settlement.image_input_tokens,
                image_output_tokens=settlement.image_output_tokens,
                text_input_tokens=settlement.text_input_tokens,
                text_output_tokens=settlement.text_output_tokens,
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
                if meta:
                    stat["display_name"] = meta.display_name
                else:
                    stat["display_name"] = _LEGACY_PROVIDER_DISPLAY_NAMES.get(provider_str or "", provider_str)

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
