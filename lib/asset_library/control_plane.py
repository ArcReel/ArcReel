"""火山方舟 / BytePlus ModelArk 素材库控制面 OpenAPI 客户端。

控制面只接受 Access Key 签名（HMAC-SHA256，火山引擎 OpenAPI 签名 V4），两站算法相同，只有主机与
区域不同。请求一律 ``POST https://{host}/?Action=...&Version=2024-01-01``，JSON 请求体。官方文档：

- 签名：https://docs.volcengine.com/docs/6369/67269 、https://docs.byteplus.com/en/docs/byteplus-platform/reference-how-to-calculate-a-signature
- ListAssets：https://docs.volcengine.com/docs/ark/list-assets-api 、https://docs.byteplus.com/en/docs/modelark/list-assets-api

本模块只做协议：签名、发送与响应解析。Access Key 只进签名，不进日志与异常文本。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx

from lib.asset_library.sites import ASSET_API_SERVICE, ASSET_API_VERSION, control_plane_host

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


class AssetLibraryRequestError(Exception):
    """控制面请求失败。``code`` / ``message`` 是供应商原文，``kind`` 是给界面选文案用的分类。"""

    def __init__(
        self,
        kind: ErrorKind,
        *,
        code: str = "",
        message: str = "",
        status_code: int | None = None,
        request_id: str = "",
    ) -> None:
        super().__init__(f"{kind}: {code} {message}".strip())
        self.kind = kind
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id


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
        raise AssetLibraryRequestError("network", message=type(exc).__name__) from exc
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
        )
    result = data.get("Result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        raise AssetLibraryRequestError(
            "provider", code="InvalidResponse", status_code=response.status_code, request_id=request_id
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
