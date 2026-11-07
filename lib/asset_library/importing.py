"""Import existing provider-library images: query assets, retrieve actual content, validate saveable images.

Accept available (``Active``) image assets only. Download exclusively from GetAsset-issued signed
URLs, never requester-supplied addresses. Require HTTPS/public destinations, revalidating every
redirect; bound response bytes and decode PNG/JPEG/WebP. Signed URLs are download credentials
during validity: never log, expose in exceptions/diagnostics, or persist them.

Callers perform formal writes/linking after retrieval: save new versions with manual finished-
upload semantics, then :func:`lib.asset_library.links.record_imported_link` links that new version.
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

#: Imported-image byte limit matches manual uploads; provider-library image limits are lower.
IMPORT_IMAGE_MAX_BYTES = 30 * 1024 * 1024

#: Importable formats/save extensions share the manual-upload format source.
_FORMAT_SUFFIXES = {name: suffixes[0] for name, suffixes in IMAGE_FORMAT_SUFFIXES.items()}

_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_REDIRECTS = 5
_DNS_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ProviderImage:
    """Retrieved provider image: asset-query result, raw bytes, and extension from actual format."""

    asset: ProviderAssetDetail
    content: bytes
    suffix: str

    def __repr__(self) -> str:
        return f"ProviderImage(asset={self.asset!r}, bytes={len(self.content)}, suffix={self.suffix!r})"


class _DestinationRejected(Exception):
    """Download destination is not a public HTTPS address."""


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


async def _check_destination(url: httpx.URL, resolver: Resolver) -> None:
    """Public-cloud-issued URLs admit only HTTPS/public destinations; reject loopback/private/link-local/metadata addresses.

    Do not decide on DNS failure/timeout; let transports connect normally, matching artifact
    downloads: proxy deployments may not resolve destination hosts locally.
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
        except OSError:  # TimeoutError is also OSError
            return
    if not all(_is_public(address) for address in addresses):
        raise _DestinationRejected


def _download_failed(reason: str, **diagnostic: object) -> BadGatewayError:
    return BadGatewayError("provider_asset_import_download_failed").with_diagnostic({"reason": reason, **diagnostic})


async def download_provider_image_bytes(url: str, *, resolver: Resolver | None = None) -> bytes:
    """Fetch content from provider-issued URLs.

    Fail with URL-free domain errors and ``from None`` to sever exception chains; some httpx
    errors contain full URLs, leaking signed parameters into logs through chained exceptions.

    Raises:
        BadGatewayError: URL unavailable, destination rejected, network/provider status failure, or empty content.
        UnprocessableError: Content exceeds :data:`IMPORT_IMAGE_MAX_BYTES`.
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
    """Choose extensions by actual decoding; reject unsupported formats, undecodable content, or excessive pixels.

    Raises:
        UnprocessableError: Not a usable PNG, JPEG, or WebP image.
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
    """Query an account-scoped asset and retrieve its image content.

    Raises:
        UnprocessableError: Not an image, not yet available, or unimportable image content.
        BadGatewayError: Control-plane failure or content retrieval failure.
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
