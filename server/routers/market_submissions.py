"""分享到官方市场：本地预检、经官方服务创建提交，以及本地保存的提交状态。

提交的是端点已保存的定义与可选图标；本地预检不通过即 422，不出站。每个端点只记最近一次提交的令牌，
状态列表在进入页面时向官方服务各查一次，取不回时保留上次状态并标记 ``stale``。官方服务关闭时，
创建提交与状态列表均 409 ``official_service_disabled``，本地预检不受影响。

本地记录只在官方服务返回之后以单条语句写入：创建提交按端点 upsert，状态刷新只在令牌未变时生效，
因此同一端点的并发提交与刷新不会互相覆盖，出站期间也不持有写锁。
"""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider import make_endpoint_key
from lib.db import get_async_session
from lib.db.base import utc_now
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_submission import MarketSubmission
from lib.infra.api_errors import BadGatewayError, NotFoundError, UnprocessableError
from lib.market.official_service import (
    OfficialServiceError,
    OfficialServiceGateway,
    SubmissionStatus,
    get_official_service_gateway,
)
from lib.market.submissions import (
    SUBMISSION_TYPE,
    SubmissionDiagnostic,
    check_submission,
    remote_diagnostics,
    submission_files,
)
from server.i18n import Translator

router = APIRouter(prefix="/market/submissions", tags=["Market"])

OfficialService = Annotated[OfficialServiceGateway, Depends(get_official_service_gateway)]


class SubmissionIcon(BaseModel):
    filename: Literal["icon.png", "icon.webp", "icon.svg"]
    #: 标准 base64。
    content: str


class CheckSubmissionRequest(BaseModel):
    endpoint_id: int = Field(gt=0)
    slug: str = Field(max_length=128)
    icon: SubmissionIcon | None = None


class CreateSubmissionRequest(CheckSubmissionRequest):
    github_username: str | None = Field(default=None, max_length=39)


class SubmissionDiagnosticResponse(BaseModel):
    file: str
    path: str
    code: str
    message: str


class CheckSubmissionResponse(BaseModel):
    diagnostics: list[SubmissionDiagnosticResponse]


SubmissionState = Literal["open", "merged", "closed"]
#: 库里的状态写入前已按官方服务的状态集合校验。
_STATUSES: dict[str, SubmissionState] = {"open": "open", "merged": "merged", "closed": "closed"}


class MarketSubmissionResponse(BaseModel):
    endpoint_id: int
    endpoint_key: str
    endpoint_display_name: str
    type: str
    slug: str
    status: SubmissionState
    pr_url: str
    #: 本次没能从官方服务取回状态，展示的是上次保存的值。
    stale: bool


class MarketSubmissionListResponse(BaseModel):
    submissions: list[MarketSubmissionResponse]


def _diagnostics_payload(diagnostics: list[SubmissionDiagnostic], _t: Translator) -> list[dict[str, str]]:
    return [diagnostic.to_payload(_t) for diagnostic in diagnostics]


async def _files_of(session: AsyncSession, body: CheckSubmissionRequest) -> tuple[CustomEndpoint, dict[str, bytes]]:
    endpoint = await session.get(CustomEndpoint, body.endpoint_id)
    if endpoint is None:
        raise NotFoundError("custom_endpoint_not_found")
    icon = None
    if body.icon is not None:
        try:
            icon = (body.icon.filename, base64.b64decode(body.icon.content, validate=True))
        # 非 ASCII 字符抛 ValueError，其余非法编码抛其子类 binascii.Error。
        except ValueError as exc:
            raise UnprocessableError("market_submission_icon_encoding_invalid") from exc
    return endpoint, submission_files(endpoint.definition, icon)


def _response(row: MarketSubmission, endpoint: CustomEndpoint, *, stale: bool = False) -> MarketSubmissionResponse:
    return MarketSubmissionResponse(
        endpoint_id=endpoint.id,
        endpoint_key=make_endpoint_key(endpoint.id),
        endpoint_display_name=endpoint.display_name,
        type=row.type,
        slug=row.slug,
        status=_STATUSES[row.status],
        pr_url=row.pr_url,
        stale=stale,
    )


def _fields(status: SubmissionStatus) -> dict[str, str]:
    return {
        "token": status.token,
        "type": status.type,
        "slug": status.slug,
        "status": status.status,
        "pr_url": status.pr_url,
    }


async def _record_submission(session: AsyncSession, endpoint_id: int, status: SubmissionStatus) -> None:
    """以本次提交覆盖该端点的记录；同一端点并发提交时后写者生效。"""
    values = {**_fields(status), "submitted_at": utc_now()}
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(MarketSubmission).values(custom_endpoint_id=endpoint_id, **values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[MarketSubmission.custom_endpoint_id],
            set_={name: stmt.excluded[name] for name in values},
        )
    )


@router.post("/check", response_model=CheckSubmissionResponse)
async def check(
    body: CheckSubmissionRequest, _t: Translator, session: AsyncSession = Depends(get_async_session)
) -> CheckSubmissionResponse:
    """本地市场校验与疑似字面凭证检查；诊断均阻断分享，不出站。"""
    _, files = await _files_of(session, body)
    diagnostics = _diagnostics_payload(check_submission(body.slug, files), _t)
    return CheckSubmissionResponse(diagnostics=[SubmissionDiagnosticResponse(**item) for item in diagnostics])


@router.post("", response_model=MarketSubmissionResponse)
async def create_submission(
    body: CreateSubmissionRequest,
    official: OfficialService,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> MarketSubmissionResponse:
    """本地预检通过后经官方服务创建提交；官方服务的错误码与状态码原样落到本地响应。"""
    endpoint, files = await _files_of(session, body)
    if diagnostics := check_submission(body.slug, files):
        raise UnprocessableError("market_submission_invalid").with_diagnostic(
            {"diagnostics": _diagnostics_payload(diagnostics, _t)}
        )
    client = await official.require()
    try:
        status = await client.create_submission(
            type=SUBMISSION_TYPE, slug=body.slug, files=files, github_username=body.github_username or None
        )
    except OfficialServiceError as exc:
        if exc.code == "submission_invalid":
            exc.with_diagnostic(
                {
                    "official_service": {"code": exc.code, "params": exc.remote_params},
                    "diagnostics": _diagnostics_payload(remote_diagnostics(exc.remote_params), _t),
                }
            )
        raise
    await _record_submission(session, endpoint.id, status)
    await session.commit()
    row = await session.get(MarketSubmission, endpoint.id, populate_existing=True)
    if row is None:
        raise NotFoundError("custom_endpoint_not_found")
    return _response(row, endpoint)


@router.get("", response_model=MarketSubmissionListResponse)
async def list_submissions(
    official: OfficialService, session: AsyncSession = Depends(get_async_session)
) -> MarketSubmissionListResponse:
    """本地记录的提交，按提交时间倒序；未采纳的逐个向官方服务刷新一次，官方服务不可达时不再继续出站。"""
    client = await official.require()
    newest_first = (MarketSubmission.submitted_at.desc(), MarketSubmission.custom_endpoint_id.desc())
    pending = (
        await session.execute(
            select(MarketSubmission.custom_endpoint_id, MarketSubmission.token)
            .where(MarketSubmission.status != "merged")
            .order_by(*newest_first)
        )
    ).all()
    refreshed: list[tuple[int, str, SubmissionStatus]] = []
    stale: set[int] = set()
    reachable = True
    for endpoint_id, token in pending:
        if not reachable:
            stale.add(endpoint_id)
            continue
        try:
            refreshed.append((endpoint_id, token, await client.submission_status(token)))
        except OfficialServiceError:
            stale.add(endpoint_id)
        except BadGatewayError:
            reachable = False
            stale.add(endpoint_id)
    for endpoint_id, token, status in refreshed:
        # 刷新期间该端点已有新提交时令牌已变，旧令牌的结果不再写入。
        await session.execute(
            update(MarketSubmission)
            .where(MarketSubmission.custom_endpoint_id == endpoint_id, MarketSubmission.token == token)
            .values(**_fields(status))
        )
    await session.commit()
    rows = (
        await session.execute(
            select(MarketSubmission, CustomEndpoint)
            .join(CustomEndpoint)
            .order_by(*newest_first)
            .execution_options(populate_existing=True)
        )
    ).all()
    return MarketSubmissionListResponse(
        submissions=[_response(row, endpoint, stale=row.custom_endpoint_id in stale) for row, endpoint in rows]
    )
