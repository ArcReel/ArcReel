"""API usage-statistics routes.

Provide call-record queries and statistical summaries."""

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

# Usage views: generation calls exclude Agent records; Agent sessions include only Agent records.
UsageView = Literal["generation", "agent"]
_VIEW_DESCRIPTION = "视图：generation（生成调用，默认，不含 Agent 记录）或 agent（Agent 会话的记录）"


def _view_filter(view: UsageView) -> bool:
    """View → ``UsageFilters.agent``."""
    return view == "agent"


_CALL_STATUS_DESCRIPTION = f"状态 ({'/'.join(CallStatus)})"


# ---------------------------------------------------------------------------
# Usage-record read API; Pydantic models declare response shapes.
# ---------------------------------------------------------------------------


class EpisodeItemRef(BaseModel):
    """Owning episode title/broadcast position and current item position; the UI displays “Title · 5” rather than item IDs."""

    episode_title: str
    episode_position: int
    item_id: str
    # Current one-based shot/unit position in the episode array; None when absent from the script.
    item_position: int | None = None
    item_excerpt: str = ""
    item_thumbnail: str | None = None


class UsageRecord(BaseModel):
    """List projection of one provider call, omitting ``user_id``."""

    id: int
    project_name: str
    # Project title, None if deleted/unreadable/untitled; UI falls back to project name.
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
    # None means unpriced; 0 means priced at zero.
    cost_amount: float | None
    # Unpriced reason (``lib.billing.metering.UnpricedReason``); None for priced records.
    unpriced_reason: str | None = None
    currency: str
    # input_tokens includes cache reads/writes; uncached input = input_tokens minus cache reads and both cache-write tiers.
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
    """Details add three heavy payloads omitted from lists."""

    prompt: str | None = None
    inputs: Any = None
    last_provider_response: Any = None
    #: Provider still exists in settings, built-in or undeleted custom, enabling detail links to its model settings.
    #: ``False`` for Agent credentials, deleted custom providers and obsolete former built-ins.
    provider_in_settings: bool = False


class UsageRecordPage(BaseModel):
    """One keyset page; empty ``next_cursor`` means the last page."""

    items: list[UsageRecord]
    next_cursor: str | None = None
    total: int


# Representable UTC interval leaves one day for timezone offsets, subtracting a microsecond from half-open ends and local-day conversion
# without crossing datetime boundaries.
_EARLIEST_INSTANT = datetime(1, 1, 2, tzinfo=UTC)
_LATEST_INSTANT = datetime(9999, 12, 30, tzinfo=UTC)


def _utc_instant(value: datetime | None) -> datetime | None:
    """Convert time parameters to UTC; reject unrepresentable times with 422 rather than overflow to 500 during conversion/day bucketing."""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not _EARLIEST_INSTANT <= aware <= _LATEST_INSTANT:
        raise UnprocessableError("usage_time_out_of_range")
    return as_utc(value)


def _multi(value: str | None) -> tuple[str, ...]:
    """Comma-separated multiselection; discard empty/whitespace items, with an empty result disabling this filter dimension."""
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@router.get("/usage/records", response_model=UsageRecordPage)
async def list_usage_records(
    _t: Translator,
    project_name: str | None = Query(None, description="Project name; use an empty string for endpoint trial records"),
    provider: str | None = Query(None, description="Provider IDs, comma-separated for multiple selections"),
    model: str | None = Query(None, description="Models, comma-separated for multiple selections"),
    media_type: str | None = Query(
        None, description="Media types (image/video/text/audio), comma-separated for multiple selections"
    ),
    status: str | None = Query(None, description=f"{_CALL_STATUS_DESCRIPTION}，逗号分隔多选"),
    segment_id: str | None = Query(None, description="Storyboard IDs, comma-separated for multiple selections"),
    since: datetime | None = Query(None, description="Inclusive start time, ISO 8601; timezone-naive values use UTC"),
    until: datetime | None = Query(None, description="Exclusive end time, ISO 8601; timezone-naive values use UTC"),
    limit: int = Query(20, ge=1, le=200, description="Records per page"),
    cursor: str | None = Query(None, description="Opaque cursor returned by the previous page"),
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
    """Project name → title for readable projects with nonempty titles only.

    Records store project directory names, while UI shows titles. Deleted projects use tombstone names with no readable
    project, so omit them like unreadable/untitled projects and let UI fall back to names."""
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
    """Whether provider settings still contain this provider: built-ins always exist; custom providers must not be deleted."""
    if provider_id in PROVIDER_REGISTRY:
        return True
    if not is_custom_provider(provider_id):
        return False
    try:
        db_id = parse_provider_id(provider_id)
    except ValueError:
        return False
    return await CustomProviderRepository(session).get_provider(db_id) is not None


# --- Agent session view -------------------------------------------------------
# Preserve one record per model/turn; aggregate Agent records by session_id into one row per session.


class UsageAgentSession(BaseModel):
    """One Agent-session list row, totaling only records matching filters."""

    session_id: str
    # Titles come from session metadata; deleted metadata yields None with ``session_deleted`` true; empty means unnamed.
    title: str | None
    session_deleted: bool
    project_name: str
    project_title: str | None = None
    # Session models in first-occurrence order.
    models: list[str]
    calls: int
    # input_tokens is total input, including cache reads/writes.
    input_tokens: int
    output_tokens: int
    # Total priced costs separately by currency; exclude unpriced NULL amounts and count them in unpriced_calls.
    cost: dict[str, float]
    unpriced_calls: int
    first_started_at: str
    last_started_at: str


class UsageAgentSessionPage(BaseModel):
    """One keyset page ordered by latest turn descending; empty ``next_cursor`` means the last page."""

    items: list[UsageAgentSession]
    next_cursor: str | None = None
    total: int


class UsageAgentSessionRecord(BaseModel):
    """One session-detail record: one model’s usage and cost in a turn."""

    id: int
    started_at: str
    status: str
    # This turn’s user message, truncated when too long.
    prompt: str | None = None
    provider: str
    model: str
    # input_tokens includes cache reads/writes; uncached input = input_tokens minus cache reads and both cache-write tiers.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    # None means unpriced, explained by unpriced_reason; 0 means priced at zero.
    cost_amount: float | None
    unpriced_reason: str | None = None
    currency: str


class UsageAgentSessionDetail(BaseModel):
    session_id: str
    items: list[UsageAgentSessionRecord]


@router.get("/usage/sessions", response_model=UsageAgentSessionPage)
async def list_usage_sessions(
    project_name: str | None = Query(None, description="Project name"),
    provider: str | None = Query(None, description="Provider IDs, comma-separated for multiple selections"),
    model: str | None = Query(None, description="Models, comma-separated for multiple selections"),
    since: datetime | None = Query(None, description="Inclusive start time, ISO 8601; timezone-naive values use UTC"),
    until: datetime | None = Query(None, description="Exclusive end time, ISO 8601; timezone-naive values use UTC"),
    limit: int = Query(20, ge=1, le=200, description="Sessions per page"),
    cursor: str | None = Query(None, description="Opaque cursor returned by the previous page"),
) -> UsageAgentSessionPage:
    """Agent sessions aggregated by ``session_id``, one row per session ordered by latest turn descending.

    Share time/project/provider/model filters with ``/usage/records``. Historical records stored as ``anthropic``
    also aggregate by session."""
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
    """Agent-session details, one chronological row per record, without list filters."""
    async with async_session_factory() as session:
        items = await UsageRepository(session).list_agent_session_records(session_id)
    if not items:
        raise NotFoundError("usage_session_not_found")
    return UsageAgentSessionDetail(session_id=session_id, items=[UsageAgentSessionRecord(**item) for item in items])


# --- Summary read API (GET /usage/summary) -------------------------------------
# Settings overview/top-bar entry share this request: KPIs, daily trends, three-dimensional composition, attention and filter candidates.
# lib/billing/usage_summary.py owns aggregation; only resolve parameters/rows and declare shapes here.


class UsageStats(BaseModel):
    """Call counts and reference costs separated by currency."""

    calls: int
    success: int
    failed: int
    cancelled: int
    success_rate: float | None
    # Total only priced calls; exclude unpriced amounts and count them in unpriced_calls.
    cost: dict[str, float]
    unpriced_calls: int


class UsageRange(BaseModel):
    """Inclusive local-day trend interval, matching first/last daily buckets."""

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
    # Agent records combine into one item: ``agent`` true, empty ``provider``.
    provider: str
    agent: bool = False


class UsageModelRow(UsageStats):
    provider: str
    model: str
    agent: bool = False


class UsageBreakdownOther(UsageStats):
    """Remainder outside the composition list: merged group count and totals."""

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
    # Candidate projects with readable titles, name → title; composition/attention project names are also within candidate scope.
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
    tz: str = Query("UTC", description="IANA timezone name used for day boundaries"),
    project_name: str | None = Query(None, description="Project name (an empty string filters endpoint trials)"),
    provider: str | None = Query(None, description="Filter by provider"),
    model: str | None = Query(None, description="Filter by model"),
    media_type: CallType | None = Query(None, description="Media type (image/video/text/audio)"),
    since: datetime | None = Query(None, description="Inclusive start time, ISO 8601; timezone-naive values use UTC"),
    until: datetime | None = Query(None, description="Exclusive end time, ISO 8601; timezone-naive values use UTC"),
    view: UsageView = Query(
        "generation",
        description="Select which view supplies project, provider and model filter candidates; totals do not distinguish views",
    ),
) -> dict[str, object]:
    """Return all overview aggregates at once; omitted since / until means all time.

    Totals, trends and composition include Agent records, with sessions as a separate composition item; attention-needed
    counts include generation calls only.

    Do not accept ``status``: pending is excluded and all three terminal states jointly define KPIs. Further status slicing
    would contradict call counts and success rates, so ignore record-table status filters here.

    Return 422 when since / until expands beyond ``MAX_DAILY_BUCKETS``. Those timestamps directly determine bucket count;
    without a limit, extreme dates could synchronously allocate millions of daily buckets."""
    # Unresolvable timezone names return 422, never silently UTC; wrong day boundaries misalign the entire trend.
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

    # Repository catalog names use the default language; translate mapped built-in provider names to request language while retaining
    # custom user names unchanged, sharing the /providers catalog table.
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
