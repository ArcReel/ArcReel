"""Share to the official market: local preflight, submission through the official service, and locally saved submission state.

Submit the endpoint’s saved definition and optional icon. Failed local preflight returns 422 without outbound requests.
Store only the latest submission token per endpoint. On page entry, query the official service once for each status;
retain prior status and mark ``stale`` if retrieval fails. With the official service disabled, creation and status listing
return 409 ``official_service_disabled``; local preflight remains available.

Write local records in a single statement only after the official response: upsert submissions by endpoint, and refresh
state only if the token is unchanged. Concurrent submissions and refreshes cannot overwrite each other, and outbound
requests hold no write lock."""

from __future__ import annotations

import asyncio
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
    #: Standard base64.
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
#: Validate database states against the official-service state set before writing.
_STATUSES: dict[str, SubmissionState] = {"open": "open", "merged": "merged", "closed": "closed"}


class MarketSubmissionResponse(BaseModel):
    endpoint_id: int
    endpoint_key: str
    endpoint_display_name: str
    type: str
    slug: str
    status: SubmissionState
    pr_url: str
    #: The official-service state could not be retrieved this time; show the last saved value.
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
        # Non-ASCII characters raise ValueError; other invalid encodings raise its subclass binascii.Error.
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
    """Replace this endpoint’s record with the current submission; concurrent submissions for one endpoint use the last writer."""
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
    """Run local market validation and suspected literal-credential checks; all diagnostics block sharing without outbound requests."""
    _, files = await _files_of(session, body)
    diagnostics = _diagnostics_payload(await asyncio.to_thread(check_submission, body.slug, files), _t)
    return CheckSubmissionResponse(diagnostics=[SubmissionDiagnosticResponse(**item) for item in diagnostics])


@router.post("", response_model=MarketSubmissionResponse)
async def create_submission(
    body: CreateSubmissionRequest,
    official: OfficialService,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> MarketSubmissionResponse:
    """Create a submission through the official service after local preflight passes; preserve official error and status codes in the local response."""
    endpoint, files = await _files_of(session, body)
    if diagnostics := await asyncio.to_thread(check_submission, body.slug, files):
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
    """List locally recorded submissions newest first; refresh each unaccepted submission once through the official service, stopping outbound requests if it is unreachable."""
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
        # A new submission changes the token during refresh, so do not write results for the old token.
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
