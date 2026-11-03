"""火山方舟 / BytePlus ModelArk 素材库控制面 OpenAPI 客户端。

控制面只接受 Access Key 签名（HMAC-SHA256，火山引擎 OpenAPI 签名 V4），两站算法相同，只有主机与
区域不同。请求一律 ``POST https://{host}/?Action=...&Version=2024-01-01``，JSON 请求体。官方文档：

- 签名：https://docs.volcengine.com/docs/6369/67269 、https://docs.byteplus.com/en/docs/byteplus-platform/reference-how-to-calculate-a-signature
- ListAssets：https://docs.volcengine.com/docs/ark/list-assets-api 、https://docs.byteplus.com/en/docs/modelark/list-assets-api
- CreateAsset / GetAsset：https://docs.volcengine.com/docs/ark/create-asset-api 、https://docs.volcengine.com/docs/ark/get-asset-api ；
  BytePlus 对应 https://docs.byteplus.com/en/docs/modelark/create-asset-api 、https://docs.byteplus.com/en/docs/modelark/get-asset-api
- CreateAssetGroup / ListAssetGroups：https://docs.volcengine.com/docs/ark/create-asset-group-api 、
  https://docs.volcengine.com/docs/ark/list-asset-groups-api

两站的 Action、Version 与字段名相同（BytePlus 一侧以官方仓库 ``byteplus-sa/ark-mcp`` 的调用为对照）。
CreateAsset 只接受供应商可下载的 URL，异步处理且不承诺入库时长；返回的素材 ID 处于 ``Processing``，``GetAsset`` 返回 ``Active`` 后才可用。

本模块只做协议：签名、发送与响应解析。Access Key 只进签名，不进日志与异常文本。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

import httpx

from lib.asset_library.sites import ASSET_API_SERVICE, ASSET_API_VERSION, control_plane_host
from lib.infra.api_errors import BadGatewayError
from lib.infra.logging_utils import redact_diagnostic_text

if TYPE_CHECKING:
    from lib.db.models.asset_library import AssetLibraryAccount

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# 素材库只有 AIGC 类素材组能经 CreateAsset 写入；真人认证（LivenessFace）素材组在供应商控制台管理。
_GROUP_TYPE = "AIGC"

AssetStatus = Literal["active", "processing", "failed", "unknown"]
_STATUS_MAP: dict[str, AssetStatus] = {"Active": "active", "Processing": "processing", "Failed": "failed"}

ErrorKind = Literal["auth", "denied", "throttled", "network", "provider"]
_AUTH_CODES = frozenset(
    {
        "InvalidAccessKey",
        "SignatureDoesNotMatch",
        "InvalidSecretKey",
        "InvalidAuthorization",
        "InvalidCredential",
        "InvalidSecretToken",
    }
)
_DENIED_CODES = frozenset({"AccessDenied", "Forbidden", "UnauthorizedOperation", "NoPermission"})
_THROTTLE_CODES = frozenset({"FlowLimitExceeded", "RequestLimitExceeded", "TooManyRequests", "Throttling"})


@dataclass(frozen=True)
class ControlPlaneCredentials:
    site: str
    region: str
    project_name: str
    access_key_id: str
    secret_access_key: str


def credentials_of(account: AssetLibraryAccount) -> ControlPlaneCredentials:
    """素材库账号的控制面凭证与资源范围。"""
    return ControlPlaneCredentials(
        site=account.site,
        region=account.region,
        project_name=account.project_name,
        access_key_id=account.access_key_id,
        secret_access_key=account.secret_access_key,
    )


class AssetLibraryRequestError(Exception):
    """控制面请求失败。``code`` / ``message`` 是供应商原文，``kind`` 是给界面选文案用的分类。

    ``outcome_unknown`` 表示请求可能已被供应商执行：连接建立后超时、断开或供应商 5xx。对写操作，
    这类失败不能当作「没有创建」重试。
    """

    def __init__(
        self,
        kind: ErrorKind,
        *,
        code: str = "",
        message: str = "",
        status_code: int | None = None,
        request_id: str = "",
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(f"{kind}: {code} {message}".strip())
        self.kind = kind
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class ProviderAssetItem:
    id: str
    name: str
    asset_type: str
    status: AssetStatus
    group_id: str
    error_code: str | None
    created_at: str | None


@dataclass(frozen=True)
class ProviderAssetPage:
    items: list[ProviderAssetItem]
    total: int
    page_number: int
    page_size: int


def _canonical_query(query: dict[str, str]) -> str:
    return "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
        for k, v in sorted((str(k), str(v)) for k, v in query.items())
    )


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_request(
    *,
    method: str,
    host: str,
    query: dict[str, str],
    headers: dict[str, str],
    body: bytes,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    service: str,
    now: datetime,
) -> dict[str, str]:
    """按签名 V4 计算请求头，返回需要附加的 ``X-Date`` 与 ``Authorization``。

    ``headers`` 是参与签名的其余请求头（不含 Host 与 X-Date），名字按小写比较。
    """
    x_date = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    signed = {k.lower(): v.strip() for k, v in headers.items()}
    signed["host"] = host
    signed["x-date"] = x_date
    signed_names = sorted(signed)
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in signed_names)
    signed_headers = ";".join(signed_names)
    canonical_request = "\n".join(
        [
            method,
            "/",
            _canonical_query(query),
            canonical_headers,
            signed_headers,
            hashlib.sha256(body).hexdigest(),
        ]
    )
    scope = f"{x_date[:8]}/{region}/{service}/request"
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
    )
    key = _hmac(secret_access_key.encode("utf-8"), x_date[:8])
    for part in (region, service, "request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "X-Date": x_date,
        "Authorization": (
            f"HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


_REQUEST_FAILURE_KEYS: dict[str, str] = {
    "auth": "asset_library_request_auth_failed",
    "denied": "asset_library_request_denied",
    "throttled": "asset_library_request_throttled",
    "network": "asset_library_request_unreachable",
    "provider": "asset_library_request_failed",
}


def request_failure_error(exc: AssetLibraryRequestError) -> BadGatewayError:
    """把控制面请求失败转成面向界面的错误：按分类选文案，供应商原文脱敏后放进诊断。"""
    return BadGatewayError(_REQUEST_FAILURE_KEYS[exc.kind]).with_diagnostic(
        {
            "code": exc.code,
            "status": exc.status_code,
            "request_id": exc.request_id,
            "message": redact_diagnostic_text(exc.message),
        }
    )


def _error_kind(code: str, status_code: int | None) -> ErrorKind:
    if code in _AUTH_CODES or status_code == 401:
        return "auth"
    if code in _DENIED_CODES or status_code == 403:
        return "denied"
    if code in _THROTTLE_CODES or status_code == 429:
        return "throttled"
    return "provider"


async def call_action(
    credentials: ControlPlaneCredentials,
    action: str,
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """调用一个控制面 Action，返回 ``Result``；HTTP 失败或响应带 ``ResponseMetadata.Error`` 时抛错。"""
    host = control_plane_host(credentials.site, credentials.region)
    if host is None:
        raise AssetLibraryRequestError("provider", code="UnsupportedRegion")
    query = {"Action": action, "Version": ASSET_API_VERSION}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    content_headers = {
        "Content-Type": "application/json",
        "X-Content-Sha256": hashlib.sha256(body).hexdigest(),
    }
    auth_headers = sign_request(
        method="POST",
        host=host,
        query=query,
        headers=content_headers,
        body=body,
        access_key_id=credentials.access_key_id.strip(),
        secret_access_key=credentials.secret_access_key.strip(),
        region=credentials.region,
        service=ASSET_API_SERVICE,
        now=datetime.now(UTC),
    )
    url = f"https://{host}/"
    owned = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    try:
        response = await http.post(url, params=query, content=body, headers={**content_headers, **auth_headers})
    except httpx.HTTPError as exc:
        # 连接没有建立时请求一定没有到达供应商；其余网络失败（读超时、连接中断）结果未知。
        unknown = not isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
        raise AssetLibraryRequestError("network", message=type(exc).__name__, outcome_unknown=unknown) from exc
    finally:
        if owned:
            await http.aclose()
    try:
        data = response.json()
    except ValueError:
        data = None
    metadata = data.get("ResponseMetadata") if isinstance(data, dict) else None
    error = metadata.get("Error") if isinstance(metadata, dict) else None
    request_id = str(metadata.get("RequestId") or "") if isinstance(metadata, dict) else ""
    if isinstance(error, dict) or response.status_code >= 400:
        code = str(error.get("Code") or "") if isinstance(error, dict) else ""
        message = str(error.get("Message") or "") if isinstance(error, dict) else ""
        raise AssetLibraryRequestError(
            _error_kind(code, response.status_code),
            code=code,
            message=message,
            status_code=response.status_code,
            request_id=request_id,
            outcome_unknown=response.status_code >= 500,
        )
    result = data.get("Result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        raise AssetLibraryRequestError(
            "provider",
            code="InvalidResponse",
            status_code=response.status_code,
            request_id=request_id,
            outcome_unknown=True,
        )
    return result


def _parse_item(raw: Any) -> ProviderAssetItem | None:
    if not isinstance(raw, dict) or not raw.get("Id"):
        return None
    error = raw.get("Error")
    error_code = str(error.get("Code")) if isinstance(error, dict) and error.get("Code") else None
    return ProviderAssetItem(
        id=str(raw["Id"]),
        name=str(raw.get("Name") or ""),
        asset_type=str(raw.get("AssetType") or ""),
        status=_STATUS_MAP.get(str(raw.get("Status") or ""), "unknown"),
        group_id=str(raw.get("GroupId") or ""),
        error_code=error_code,
        created_at=str(raw["CreateTime"]) if raw.get("CreateTime") else None,
    )


async def list_assets(
    credentials: ControlPlaneCredentials,
    *,
    page_number: int,
    page_size: int,
    client: httpx.AsyncClient | None = None,
) -> ProviderAssetPage:
    """按页列出账号项目范围内 AIGC 素材组的素材，按创建时间倒序。

    官方接口不按素材类型过滤，返回的条目可能包含视频与音频，由调用方按 ``asset_type`` 区分。
    条目里的签名下载地址不取出，避免临时签名经 API 下发或进入日志。
    """
    result = await call_action(
        credentials,
        "ListAssets",
        {
            "Filter": {"GroupType": _GROUP_TYPE},
            "PageNumber": page_number,
            "PageSize": page_size,
            "SortBy": "CreateTime",
            "SortOrder": "Desc",
            "ProjectName": credentials.project_name,
        },
        client=client,
    )
    raw_items = result.get("Items")
    items = [item for raw in raw_items if (item := _parse_item(raw))] if isinstance(raw_items, list) else []
    total = result.get("TotalCount")
    return ProviderAssetPage(
        items=items,
        total=total if isinstance(total, int) else len(items),
        page_number=page_number,
        page_size=page_size,
    )


@dataclass(frozen=True)
class ProviderAssetDetail:
    """GetAsset 的结果。供应商返回的签名访问地址不取出。"""

    id: str
    name: str
    asset_type: str
    status: AssetStatus
    group_id: str
    error_code: str | None
    error_message: str | None


async def get_asset(
    credentials: ControlPlaneCredentials, asset_id: str, *, client: httpx.AsyncClient | None = None
) -> ProviderAssetDetail:
    """查询一个素材的处理状态。处理失败的素材 HTTP 仍为 200，失败原因在 ``Error`` 里。"""
    result = await call_action(
        credentials, "GetAsset", {"Id": asset_id, "ProjectName": credentials.project_name}, client=client
    )
    return _parse_detail(result, asset_id)


@dataclass(frozen=True)
class ProviderAssetDownload:
    """GetAsset 的结果连同供应商返回的签名访问地址（官方文档写明有效期 12 小时）。

    ``url`` 只用于立即取回素材内容，不回传前端、不写日志与异常文本，也不持久保存。
    """

    detail: ProviderAssetDetail
    url: str | None

    def __repr__(self) -> str:
        return f"ProviderAssetDownload(detail={self.detail!r}, url={'<redacted>' if self.url else None})"


async def get_asset_download(
    credentials: ControlPlaneCredentials, asset_id: str, *, client: httpx.AsyncClient | None = None
) -> ProviderAssetDownload:
    """查询一个素材，并取出供应商返回的签名访问地址，供导入时取回实际内容。"""
    result = await call_action(
        credentials, "GetAsset", {"Id": asset_id, "ProjectName": credentials.project_name}, client=client
    )
    url = result.get("URL")
    return ProviderAssetDownload(
        detail=_parse_detail(result, asset_id), url=url if isinstance(url, str) and url.strip() else None
    )


def _parse_detail(result: dict[str, Any], asset_id: str) -> ProviderAssetDetail:
    error = result.get("Error")
    error_code = str(error.get("Code")) if isinstance(error, dict) and error.get("Code") else None
    error_message = str(error.get("Message")) if isinstance(error, dict) and error.get("Message") else None
    return ProviderAssetDetail(
        id=str(result.get("Id") or asset_id),
        name=str(result.get("Name") or ""),
        asset_type=str(result.get("AssetType") or ""),
        status=_STATUS_MAP.get(str(result.get("Status") or ""), "unknown"),
        group_id=str(result.get("GroupId") or ""),
        error_code=error_code,
        error_message=error_message,
    )


async def create_asset(
    credentials: ControlPlaneCredentials,
    *,
    group_id: str,
    url: str,
    name: str,
    asset_type: Literal["Image", "Video", "Audio"] = "Image",
    client: httpx.AsyncClient | None = None,
) -> str:
    """提交一个素材入库，返回素材 ID。返回时素材还在处理中，可用与否以 :func:`get_asset` 为准。"""
    result = await call_action(
        credentials,
        "CreateAsset",
        {
            "GroupId": group_id,
            "URL": url,
            "AssetType": asset_type,
            "Name": name,
            "ProjectName": credentials.project_name,
        },
        client=client,
    )
    asset_id = result.get("Id")
    if not asset_id:
        raise AssetLibraryRequestError("provider", code="InvalidResponse", outcome_unknown=True)
    return str(asset_id)


async def find_assets_by_name(
    credentials: ControlPlaneCredentials,
    *,
    group_id: str,
    name: str,
    client: httpx.AsyncClient | None = None,
) -> list[ProviderAssetItem]:
    """在素材组里按名称找素材。供应商按名称模糊匹配，这里只保留名称完全相同的条目。"""
    result = await call_action(
        credentials,
        "ListAssets",
        {
            "Filter": {"GroupType": _GROUP_TYPE, "GroupIds": [group_id], "Name": name},
            "PageNumber": 1,
            "PageSize": 100,
            "ProjectName": credentials.project_name,
        },
        client=client,
    )
    raw_items = result.get("Items")
    items = [item for raw in raw_items if (item := _parse_item(raw))] if isinstance(raw_items, list) else []
    return [item for item in items if item.name == name]


async def ensure_asset_group(
    credentials: ControlPlaneCredentials,
    name: str,
    *,
    description: str = "",
    client: httpx.AsyncClient | None = None,
) -> str:
    """账号项目范围里名为 ``name`` 的 AIGC 素材组 ID；没有时创建。

    首次创建素材组前需要在供应商控制台签署授权，未签署时供应商返回的错误原样抛出。
    """
    result = await call_action(
        credentials,
        "ListAssetGroups",
        {
            "Filter": {"GroupType": _GROUP_TYPE, "Name": name},
            "PageNumber": 1,
            "PageSize": 100,
            "ProjectName": credentials.project_name,
        },
        client=client,
    )
    raw_items = result.get("Items")
    for raw in raw_items if isinstance(raw_items, list) else []:
        if isinstance(raw, dict) and raw.get("Name") == name and raw.get("Id"):
            return str(raw["Id"])
    created = await call_action(
        credentials,
        "CreateAssetGroup",
        {"Name": name, "Description": description, "GroupType": _GROUP_TYPE, "ProjectName": credentials.project_name},
        client=client,
    )
    group_id = created.get("Id")
    if not group_id:
        raise AssetLibraryRequestError("provider", code="InvalidResponse", outcome_unknown=True)
    return str(group_id)
