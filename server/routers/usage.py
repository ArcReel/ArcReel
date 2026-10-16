"""
API 调用统计路由

提供调用记录查询和统计摘要接口。
"""

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.backends.providers import CallStatus, CallType
from lib.billing.usage_summary import UsageFilterOptions, UsageWindowTooWideError, build_summary
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.db import async_session_factory
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.db.repositories.usage_repo import (
    UsageCursor,
    UsageCursorError,
    UsageFilters,
    UsageRepository,
    UsageSessionCursor,
    as_utc,
)
from lib.i18n import _, translate_or
from lib.infra.api_errors import NotFoundError, UnprocessableError
from lib.project.project_manager import get_project_manager
from server.i18n import Locale, Translator
from server.services.project.episode_item_refs import with_episode_item_refs

router = APIRouter()

# 使用记录的两个视图：「生成调用」不含 Agent 记录，「Agent 会话」只含 Agent 记录。
UsageView = Literal["generation", "agent"]
_VIEW_DESCRIPTION = "视图：generation（生成调用，默认，不含 Agent 记录）或 agent（Agent 会话的记录）"


def _view_filter(view: UsageView) -> bool:
    """视图 → ``UsageFilters.agent``。"""
    return view == "agent"


_CALL_STATUS_DESCRIPTION = f"状态 ({'/'.join(CallStatus)})"


# ---------------------------------------------------------------------------
# 使用记录读接口；响应形状由 Pydantic 模型声明。
# ---------------------------------------------------------------------------


class EpisodeItemRef(BaseModel):
    """条目所属集的标题与播出位置、集内 ID；界面据此显示「标题 · S01」，不显示集 ID。"""

    episode_title: str
    episode_position: int
    item_id: str


class UsageRecord(BaseModel):
    """一次供应商调用在列表里的投影。``user_id`` 不出现。"""

    id: int
    project_name: str
    # 项目标题；项目已删除、读不到或未设标题时为 None，界面回退到项目名。
    project_title: str | None = None
    purpose: str | None = None
    task_id: str | None = None
    task_type: str | None = None
    media_type: str
    provider: str
    model: str
    status: str
    error_code: str | None = None
    error_params: Any = None
    error_message: str | None = None
    segment_id: str | None = None
    segment_ref: EpisodeItemRef | None = None
    output_path: str | None = None
    started_at: str
    finished_at: str | None = None
    duration_ms: int | None = None
    # None 表示未定价；0 是定价为零。
    cost_amount: float | None
    # 未定价的原因码（``lib.billing.metering.UnpricedReason``）；已定价的记录为 None。
    unpriced_reason: str | None = None
    currency: str
    # input_tokens 是总输入，含缓存读写；未缓存输入 = input_tokens - 缓存读 - 两档缓存写。
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    usage_tokens: int | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None
    resolution: str | None = None
    duration_seconds: int | None = None
    aspect_ratio: str | None = None
    session_id: str | None = None


class UsageRecordDetail(UsageRecord):
    """详情比列表多三项重载荷，列表里不返回。"""

    prompt: str | None = None
    inputs: Any = None
    last_provider_response: Any = None
    #: 调用的供应商仍在供应商设置里（内置供应商，或没有删除的自定义供应商），详情据此链接到供应商模型的设置。
    #: Agent 凭证、已删除的自定义供应商与已不再内置的旧值为 ``False``。
    provider_in_settings: bool = False


class UsageRecordPage(BaseModel):
    """keyset 分页的一页；``next_cursor`` 为空表示已到末页。"""

    items: list[UsageRecord]
    next_cursor: str | None = None
    total: int


# 时间参数可表示的 UTC 区间：留出一天余量，任何时区偏移、半开右端的减一微秒与本地日折算
# 都不会越过 datetime 的边界。
_EARLIEST_INSTANT = datetime(1, 1, 2, tzinfo=UTC)
_LATEST_INSTANT = datetime(9999, 12, 30, tzinfo=UTC)


def _utc_instant(value: datetime | None) -> datetime | None:
    """时间参数换算到 UTC；落在可表示区间之外的时刻 422，而不是在换算或折日时溢出成 500。"""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not _EARLIEST_INSTANT <= aware <= _LATEST_INSTANT:
        raise UnprocessableError("usage_time_out_of_range")
    return as_utc(value)


def _multi(value: str | None) -> tuple[str, ...]:
    """逗号分隔的多选参数；空串与纯空白项丢弃，整体为空表示该维度不筛。"""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@router.get("/usage/records", response_model=UsageRecordPage)
async def list_usage_records(
    _t: Translator,
    project_name: str | None = Query(None, description="项目名称；端点试跑记录用空串"),
    provider: str | None = Query(None, description="供应商 id，逗号分隔多选"),
    model: str | None = Query(None, description="模型，逗号分隔多选"),
    media_type: str | None = Query(None, description="媒体类型 (image/video/text/audio)，逗号分隔多选"),
    status: str | None = Query(None, description=f"{_CALL_STATUS_DESCRIPTION}，逗号分隔多选"),
    segment_id: str | None = Query(None, description="分镜 id，逗号分隔多选"),
    since: datetime | None = Query(None, description="起始时刻（含），ISO 8601，无时区按 UTC"),
    until: datetime | None = Query(None, description="结束时刻（不含），ISO 8601，无时区按 UTC"),
    limit: int = Query(20, ge=1, le=200, description="每页记录数"),
    cursor: str | None = Query(None, description="上一页返回的不透明游标"),
    view: UsageView = Query("generation", description=_VIEW_DESCRIPTION),
) -> UsageRecordPage:
    try:
        decoded = UsageCursor.decode(cursor) if cursor else None
    except UsageCursorError as exc:
        raise UnprocessableError("usage_cursor_invalid") from exc

    async with async_session_factory() as session:
        page = await UsageRepository(session).list_records(
            filters=UsageFilters(
                project_name=project_name,
                providers=_multi(provider),
                models=_multi(model),
                media_types=_multi(media_type),
                since=_utc_instant(since),
                until=_utc_instant(until),
                agent=_view_filter(view),
            ),
            statuses=_multi(status),
            segment_ids=_multi(segment_id),
            limit=limit,
            cursor=decoded,
        )
    page["items"] = await asyncio.to_thread(_present_records, page["items"], _t)
    return UsageRecordPage.model_validate(page)


def _with_segment_refs(items: list[dict[str, Any]], translate: Callable[..., str] = _) -> list[dict[str, Any]]:
    return with_episode_item_refs(
        items, id_field="segment_id", ref_field="segment_ref", projects=get_project_manager(), translate=translate
    )


def _project_titles(project_names: Iterable[str]) -> dict[str, str]:
    """项目名 → 标题，只含读得到且标题非空的项目。

    记录按项目名（目录名）落账，界面显示标题。已删除项目的记录挂在墓碑名上，读不到项目，
    与读取失败、未设标题的项目一样不进映射，由界面回退显示项目名。
    """
    projects = get_project_manager()
    titles: dict[str, str] = {}
    for name in set(project_names):
        if not name:
            continue
        try:
            title = projects.load_project(name).get("title")
        except (OSError, ValueError):
            continue
        if isinstance(title, str) and title.strip():
            titles[name] = title.strip()
    return titles


def _present_records(items: list[dict[str, Any]], translate: Callable[..., str] = _) -> list[dict[str, Any]]:
    titles = _project_titles(item["project_name"] for item in items)
    return [
        {**item, "project_title": titles.get(item["project_name"])} for item in _with_segment_refs(items, translate)
    ]


@router.get("/usage/records/{record_id}", response_model=UsageRecordDetail)
async def get_usage_record(record_id: int, _t: Translator) -> UsageRecordDetail:
    async with async_session_factory() as session:
        record = await UsageRepository(session).get_record(record_id)
        if record is None:
            raise NotFoundError("usage_record_not_found")
        in_settings = await _provider_in_settings(session, record.get("provider") or "")
    [record] = await asyncio.to_thread(_present_records, [record], _t)
    return UsageRecordDetail.model_validate({**record, "provider_in_settings": in_settings})


async def _provider_in_settings(session: AsyncSession, provider_id: str) -> bool:
    """供应商设置里还有这个供应商：内置供应商恒在；自定义供应商要求没有删除。"""
    if provider_id in PROVIDER_REGISTRY:
        return True
    if not is_custom_provider(provider_id):
        return False
    try:
        db_id = parse_provider_id(provider_id)
    except ValueError:
        return False
    return await CustomProviderRepository(session).get_provider(db_id) is not None


# --- Agent 会话视图 ---------------------------------------------------------------------
# 记账粒度不变（每轮每个模型一条记录），这里把 Agent 记录按 session_id 聚合成每个会话一行。


class UsageAgentSession(BaseModel):
    """一个 Agent 会话在会话列表里的一行；合计只含落在筛选内的记录。"""

    session_id: str
    # 会话标题取自会话元数据；元数据已删除时为 None（``session_deleted`` 同时为真），空串表示尚未命名。
    title: str | None
    session_deleted: bool
    project_name: str
    project_title: str | None = None
    # 会话里用到的模型，按首次出现的先后。
    models: list[str]
    calls: int
    # input_tokens 是总输入，含缓存读写。
    input_tokens: int
    output_tokens: int
    # 已定价记录的费用，按币种分别合计；未定价（NULL）的记录不计入，数量见 unpriced_calls。
    cost: dict[str, float]
    unpriced_calls: int
    first_started_at: str
    last_started_at: str


class UsageAgentSessionPage(BaseModel):
    """keyset 分页的一页，按最近一轮的时间倒序；``next_cursor`` 为空表示已到末页。"""

    items: list[UsageAgentSession]
    next_cursor: str | None = None
    total: int


class UsageAgentSessionRecord(BaseModel):
    """会话明细里的一条记录：一轮里某个模型的用量与费用。"""

    id: int
    started_at: str
    status: str
    # 这一轮的用户消息，超长时截断。
    prompt: str | None = None
    provider: str
    model: str
    # input_tokens 是总输入，含缓存读写；未缓存输入 = input_tokens - 缓存读 - 两档缓存写。
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    # None 表示未定价，原因见 unpriced_reason；0 是定价为零。
    cost_amount: float | None
    unpriced_reason: str | None = None
    currency: str


class UsageAgentSessionDetail(BaseModel):
    session_id: str
    items: list[UsageAgentSessionRecord]


@router.get("/usage/sessions", response_model=UsageAgentSessionPage)
async def list_usage_sessions(
    project_name: str | None = Query(None, description="项目名称"),
    provider: str | None = Query(None, description="供应商 id，逗号分隔多选"),
    model: str | None = Query(None, description="模型，逗号分隔多选"),
    since: datetime | None = Query(None, description="起始时刻（含），ISO 8601，无时区按 UTC"),
    until: datetime | None = Query(None, description="结束时刻（不含），ISO 8601，无时区按 UTC"),
    limit: int = Query(20, ge=1, le=200, description="每页会话数"),
    cursor: str | None = Query(None, description="上一页返回的不透明游标"),
) -> UsageAgentSessionPage:
    """Agent 会话列表：按 ``session_id`` 聚合，每个会话一行，按最近一轮的时间倒序。

    筛选与 ``/usage/records`` 同口径（时间范围、项目、供应商、模型）；已经以 ``anthropic`` 落库的
    历史记录同样按会话聚合。
    """
    try:
        decoded = UsageSessionCursor.decode(cursor) if cursor else None
    except UsageCursorError as exc:
        raise UnprocessableError("usage_cursor_invalid") from exc

    async with async_session_factory() as session:
        page = await UsageRepository(session).list_agent_sessions(
            filters=UsageFilters(
                project_name=project_name,
                providers=_multi(provider),
                models=_multi(model),
                since=_utc_instant(since),
                until=_utc_instant(until),
            ),
            limit=limit,
            cursor=decoded,
        )
    titles = await asyncio.to_thread(_project_titles, (item["project_name"] for item in page["items"]))
    page["items"] = [{**item, "project_title": titles.get(item["project_name"])} for item in page["items"]]
    return UsageAgentSessionPage.model_validate(page)


@router.get("/usage/sessions/{session_id}", response_model=UsageAgentSessionDetail)
async def get_usage_session(session_id: str) -> UsageAgentSessionDetail:
    """一个 Agent 会话的明细：按时间每条记录一行，不套列表的筛选。"""
    async with async_session_factory() as session:
        items = await UsageRepository(session).list_agent_session_records(session_id)
    if not items:
        raise NotFoundError("usage_session_not_found")
    return UsageAgentSessionDetail(session_id=session_id, items=[UsageAgentSessionRecord(**item) for item in items])


# --- 汇总读接口（GET /usage/summary）---------------------------------------------------
# 设置页总览与顶栏入口共用这一次请求：KPI、日桶趋势、三维构成、需要关注与筛选候选值。
# 聚合规则在 lib/billing/usage_summary.py，这里只负责取参、取行与形状声明。


class UsageStats(BaseModel):
    """一组调用的计数与分币种参考费用。"""

    calls: int
    success: int
    failed: int
    cancelled: int
    success_rate: float | None
    # 只合计已定价的调用；未定价的调用不计入金额，数量见 unpriced_calls。
    cost: dict[str, float]
    unpriced_calls: int


class UsageRange(BaseModel):
    """趋势覆盖的本地日区间，两端均含，与 daily 首尾桶一致。"""

    since: str
    until: str


class UsageDailyBucket(BaseModel):
    date: str
    success: int
    failed: int
    cancelled: int
    cost_by_media_type: dict[str, float]


class UsageProjectRow(UsageStats):
    project_name: str


class UsageProviderRow(UsageStats):
    # Agent 记录合并成一项：``agent`` 为真，``provider`` 为空。
    provider: str
    agent: bool = False


class UsageModelRow(UsageStats):
    provider: str
    model: str
    agent: bool = False


class UsageBreakdownOther(UsageStats):
    """构成表列表外的余量：被合并的分组数与它们的合计。"""

    groups: int


class UsageProjectBreakdown(BaseModel):
    rows: list[UsageProjectRow]
    other: UsageBreakdownOther | None


class UsageProviderBreakdown(BaseModel):
    rows: list[UsageProviderRow]
    other: UsageBreakdownOther | None


class UsageModelBreakdown(BaseModel):
    rows: list[UsageModelRow]
    other: UsageBreakdownOther | None


class UsageBreakdown(BaseModel):
    project: UsageProjectBreakdown
    provider: UsageProviderBreakdown
    model: UsageModelBreakdown


class UsageFailureRateAttention(BaseModel):
    type: Literal["failure_rate"]
    provider: str
    model: str | None
    success: int
    failed: int
    failure_rate: float
    overall_failure_rate: float


class UsageConsecutiveFailuresAttention(BaseModel):
    type: Literal["consecutive_failures"]
    project_name: str
    media_type: str
    segment_id: str
    segment_ref: EpisodeItemRef | None = None
    count: int
    first_failed_at: str
    last_failed_at: str
    last_error_code: str | None


class UsageProviderOption(BaseModel):
    provider: str
    label: str


class UsageModelOption(BaseModel):
    provider: str
    model: str


class UsageFilterOptionsResponse(BaseModel):
    projects: list[str]
    # 候选项目里读得到标题的那部分：项目名 → 标题。构成表与需要关注里的项目名同样在候选范围内。
    project_titles: dict[str, str] = Field(default_factory=dict)
    providers: list[UsageProviderOption]
    models: list[UsageModelOption]


class UsageSummaryResponse(BaseModel):
    range: UsageRange | None
    primary_currency: str | None
    kpi: UsageStats
    daily: list[UsageDailyBucket]
    breakdown: UsageBreakdown
    attention: list[
        Annotated[UsageFailureRateAttention | UsageConsecutiveFailuresAttention, Field(discriminator="type")]
    ]
    filter_options: UsageFilterOptionsResponse


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    locale: Locale,
    tz: str = Query("UTC", description="IANA 时区名，按此切天"),
    project_name: str | None = Query(None, description="项目名称（空串筛选端点试跑）"),
    provider: str | None = Query(None, description="按供应商筛选"),
    model: str | None = Query(None, description="按模型筛选"),
    media_type: CallType | None = Query(None, description="媒体类型 (image/video/text/audio)"),
    since: datetime | None = Query(None, description="起始时刻（含），ISO 8601，无时区按 UTC"),
    until: datetime | None = Query(None, description="结束时刻（不含），ISO 8601，无时区按 UTC"),
    view: UsageView = Query("generation", description="决定筛选候选值列哪个视图的项目、供应商与模型；合计不分视图"),
) -> dict[str, object]:
    """一次返回总览所需的全部聚合；不传 since / until 即全部时间。

    合计、趋势与构成包含 Agent 记录（构成里 Agent 会话单独占一项），「需要关注」只统计生成调用。

    不收 ``status``：pending 不进聚合，其余三个终态一起构成 KPI 的口径，按状态再切会让
    调用次数与成功率互相矛盾。记录表的状态筛选带上来时在此被忽略。

    since / until 展开的日桶超过 ``MAX_DAILY_BUCKETS`` 返回 422：桶数由这两个时刻直接决定，
    不设上界会让一对离谱的时刻在服务端同步铺出几百万个桶。
    """
    # 解析不了的时区名直接 422，不静默回落 UTC——切天口径错了整张趋势图都会错位。
    try:
        zone = await asyncio.to_thread(ZoneInfo, tz)
    except (KeyError, ValueError, OSError) as exc:
        raise UnprocessableError("usage_timezone_invalid") from exc
    since_utc = _utc_instant(since)
    until_utc = _utc_instant(until)
    filters = UsageFilters(
        project_name=project_name,
        providers=(provider,) if provider else (),
        models=(model,) if model else (),
        media_types=(media_type,) if media_type else (),
        since=since_utc,
        until=until_utc,
    )

    async with async_session_factory() as session:
        repo = UsageRepository(session)
        rows = await repo.fetch_summary_rows(filters=filters)
        options = await repo.fetch_usage_filter_options(agent=_view_filter(view), locale=locale)

    # 仓储按默认语言给出目录里的显示名；有译名表的内置供应商按请求语言改写，自定义供应商
    # 用户自填的名字原样保留，与 /providers 目录同一张表。
    localized = UsageFilterOptions(
        projects=options.projects,
        providers=[
            (provider_id, translate_or(f"provider_name_{provider_id}", label, locale))
            for provider_id, label in options.providers
        ],
        models=options.models,
    )
    try:
        summary = build_summary(rows, tz=zone, since=since_utc, until=until_utc, filter_options=localized)
    except UsageWindowTooWideError as exc:
        raise UnprocessableError("usage_range_too_wide") from exc
    summary["attention"] = await asyncio.to_thread(_with_segment_refs, cast(list[dict[str, Any]], summary["attention"]))
    filter_options = cast(dict[str, Any], summary["filter_options"])
    filter_options["project_titles"] = await asyncio.to_thread(_project_titles, filter_options["projects"])
    return summary
