"""从供应商素材库导入已有图片：查询素材、取回实际内容并校验成可保存的图片。

导入只接受可用（``Active``）的图片素材。内容只从 GetAsset 返回的签名访问地址下载，地址不来自请求方；
下载限定 HTTPS 与公网地址（每一跳重定向都重新校验），响应体有总字节上限，取回的字节须能解码为
PNG、JPEG 或 WebP。签名访问地址在有效期内等同下载凭证，不写日志、异常文本与诊断，也不持久保存。

取回内容之后的正式写入与关联由调用方完成：按手动上传成品的语义保存为新版本，再经
:func:`lib.asset_library.links.record_imported_link` 为这个新内容版本建立关联。
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image, UnidentifiedImageError

from lib.asset_library.control_plane import (
    AssetLibraryRequestError,
    ProviderAssetDetail,
    credentials_of,
    get_asset_download,
    request_failure_error,
)
from lib.backends.artifact_download_guard import (
    ArtifactTooLargeError,
    Resolver,
    read_body_capped,
    resolve_host,
)
from lib.db.models.asset_library import AssetLibraryAccount
from lib.infra.api_errors import BadGatewayError, UnprocessableError
from lib.infra.image_utils import IMAGE_FORMAT_SUFFIXES, MAX_UPLOAD_PIXELS, ImagePixelLimitError

#: 导入图片的字节上限，与手动上传图片相同；供应商素材库的图片上限低于此值。
IMPORT_IMAGE_MAX_BYTES = 30 * 1024 * 1024

#: 可以导入的图片格式与保存时使用的扩展名，与手动上传允许的格式同出一处。
_FORMAT_SUFFIXES = {name: suffixes[0] for name, suffixes in IMAGE_FORMAT_SUFFIXES.items()}

_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_REDIRECTS = 5
_DNS_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ProviderImage:
    """取回的供应商图片：素材查询结果、原始字节与按实际格式确定的扩展名。"""

    asset: ProviderAssetDetail
    content: bytes
    suffix: str

    def __repr__(self) -> str:
        return f"ProviderImage(asset={self.asset!r}, bytes={len(self.content)}, suffix={self.suffix!r})"


class _DestinationRejected(Exception):
    """下载目标不是 HTTPS 公网地址。"""


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


async def _check_destination(url: httpx.URL, resolver: Resolver) -> None:
    """素材地址由公有云供应商签发：只放行 HTTPS 与公网地址，环回、私网、链路本地与元数据地址一律拒绝。

    主机名解析失败或超时时不作判定，交给传输层照常连接，与产物下载的先例一致：经代理出网的部署里
    本机可能解析不了目标主机。
    """
    if url.scheme != "https":
        raise _DestinationRejected
    host = url.host
    try:
        addresses: Iterable[str] = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            async with asyncio.timeout(_DNS_TIMEOUT_SECONDS):
                addresses = await resolver(host, url.port or 443)
        except OSError:  # TimeoutError 亦是 OSError
            return
    if not all(_is_public(address) for address in addresses):
        raise _DestinationRejected


def _download_failed(reason: str, **diagnostic: object) -> BadGatewayError:
    return BadGatewayError("provider_asset_import_download_failed").with_diagnostic({"reason": reason, **diagnostic})


async def download_provider_image_bytes(url: str, *, resolver: Resolver | None = None) -> bytes:
    """从供应商签发的访问地址取回素材内容。

    失败一律抛不带地址的领域错误，并以 ``from None`` 断开异常链：httpx 的部分异常文本带完整 URL，
    签名参数会随异常链进入日志。

    Raises:
        BadGatewayError: 地址不可用、目标被拒、网络失败、供应商返回错误状态或空内容。
        UnprocessableError: 内容超过 :data:`IMPORT_IMAGE_MAX_BYTES`。
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise _download_failed("invalid_url") from None

    resolve = resolver or resolve_host

    async def guard(request: httpx.Request) -> None:
        await _check_destination(request.url, resolve)

    client = httpx.AsyncClient(
        timeout=_DOWNLOAD_TIMEOUT,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        event_hooks={"request": [guard]},
    )
    try:
        async with client, client.stream("GET", parsed) as response:
            if response.status_code >= 400:
                raise _download_failed("http_status", status=response.status_code)
            content = await read_body_capped(response, max_bytes=IMPORT_IMAGE_MAX_BYTES)
    except _DestinationRejected:
        raise _download_failed("destination_rejected") from None
    except ArtifactTooLargeError:
        raise UnprocessableError(
            "provider_asset_import_too_large", max_mb=IMPORT_IMAGE_MAX_BYTES // (1024 * 1024)
        ) from None
    except httpx.HTTPError as exc:
        raise _download_failed("network", error=type(exc).__name__) from None
    if not content:
        raise _download_failed("empty_body")
    return content


def image_suffix(content: bytes) -> str:
    """按实际解码结果确定扩展名；不是可导入的格式、无法解码或像素超限时抛错。

    Raises:
        UnprocessableError: 内容不是可用的 PNG、JPEG 或 WebP 图片。
    """
    try:
        with Image.open(BytesIO(content)) as img:
            suffix = _FORMAT_SUFFIXES.get(img.format or "")
            if img.size[0] * img.size[1] > MAX_UPLOAD_PIXELS:
                raise ImagePixelLimitError("too many pixels")
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise UnprocessableError("provider_asset_import_invalid_image") from None
    if suffix is None:
        raise UnprocessableError("provider_asset_import_invalid_image")
    return suffix


async def fetch_provider_image(
    account: AssetLibraryAccount,
    asset_id: str,
    *,
    resolver: Resolver | None = None,
) -> ProviderImage:
    """查询账号范围里的素材并取回它的图片内容。

    Raises:
        UnprocessableError: 素材不是图片、还不可用，或内容不是可导入的图片。
        BadGatewayError: 控制面请求失败，或取不回素材内容。
    """
    try:
        download = await get_asset_download(credentials_of(account), asset_id)
    except AssetLibraryRequestError as exc:
        raise request_failure_error(exc) from None
    asset = download.detail
    if asset.asset_type != "Image":
        raise UnprocessableError("provider_asset_import_not_image")
    if asset.status != "active":
        raise UnprocessableError("provider_asset_import_not_active")
    if download.url is None:
        raise _download_failed("no_url")
    content = await download_provider_image_bytes(download.url, resolver=resolver)
    suffix = await asyncio.to_thread(image_suffix, content)
    return ProviderImage(asset=asset, content=content, suffix=suffix)


__all__ = [
    "IMPORT_IMAGE_MAX_BYTES",
    "ProviderImage",
    "download_provider_image_bytes",
    "fetch_provider_image",
    "image_suffix",
]
