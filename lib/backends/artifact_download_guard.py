"""Unified provider-artifact downloads: outbound destination checks and bounded response-body reads.

Treat artifact URLs from provider responses as untrusted input. Two constraints:

- Destination: resolve hosts before every request, including every redirect hop. Refuse non-``http`` /
  ``https`` protocols and link-local/cloud-metadata destinations, including IPv4-mapped IPv6. Beyond
  link-local ranges, metadata addresses include ``100.100.100.200`` and ``192.0.0.192`` individually.
  Allow loopback/private addresses for legitimate self-hosted ComfyUI / Ollama services.
- Size: media-specific total byte caps abort excess responses. Write same-directory ``.part`` files and
  rename atomically, leaving no fragments on abort. Read error bodies only partially for status diagnostics.

Shared outside backend subpackages by video/image/audio backends and custom-provider runtimes.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from lib.backends.transport_layer import TransportLayer, layer_async_client
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.retry import NonRetryableError

#: Resolver: ``(host, port)`` → resolved address strings.
Resolver = Callable[[str, int], Awaitable[Iterable[str]]]

#: Media-specific total byte caps, far above normal single artifacts to reject anomalously large responses only.
VIDEO_ARTIFACT_MAX_BYTES = 2 * 1024 * 1024 * 1024
IMAGE_ARTIFACT_MAX_BYTES = 256 * 1024 * 1024
AUDIO_ARTIFACT_MAX_BYTES = 256 * 1024 * 1024
ARTIFACT_MAX_BYTES_BY_MEDIA_TYPE: dict[str, int] = {
    "video": VIDEO_ARTIFACT_MAX_BYTES,
    "image": IMAGE_ARTIFACT_MAX_BYTES,
    "audio": AUDIO_ARTIFACT_MAX_BYTES,
}

#: Separate host-resolution timeout, independent of httpx timeouts.
DNS_RESOLVE_TIMEOUT_SECONDS = 10.0

#: Error-body cap, sufficient for status diagnostic summaries.
ERROR_BODY_MAX_BYTES = 64 * 1024

_ALLOWED_SCHEMES = frozenset({"http", "https"})
#: Link-local ranges plus individual metadata addresses outside them; other addresses in those subnets remain allowed.
_REJECTED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("192.0.0.192/32"),
)

#: Disk-write batch threshold bounds resident memory and amortizes thread scheduling to once per 8 MiB.
_WRITE_BUFFER_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024

#: Original transfer headers: retained decoded/truncated bodies would otherwise be decoded twice or mismatch lengths.
_TRANSFER_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class ArtifactDestinationRejectedError(NonRetryableError):
    """Artifact request protocol/address is outside permitted destinations."""


class ArtifactTooLargeError(NonRetryableError):
    """Artifact response exceeds its media type's total byte cap."""


async def resolve_host(host: str, port: int) -> list[str]:
    """Resolve hosts with event-loop asynchronous ``getaddrinfo``, returning all textual addresses."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_rejected_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in network for network in _REJECTED_NETWORKS)


async def check_destination(
    url: httpx.URL,
    *,
    resolver: Resolver = resolve_host,
    resolve_timeout: float = DNS_RESOLVE_TIMEOUT_SECONDS,
) -> None:
    """Validate one request destination; raise :class:`ArtifactDestinationRejectedError` outside permitted ranges.

    Skip decisions on host resolution failure or ``resolve_timeout`` expiry, letting transport connect
    normally and fail as network errors if unreachable. Proxy deployments may not resolve hosts locally.
    """
    if url.scheme not in _ALLOWED_SCHEMES:
        raise ArtifactDestinationRejectedError(f"artifact URL scheme is not allowed: {url.scheme or '(none)'}")
    host = url.host
    try:
        addresses: Iterable[str] = [str(ipaddress.ip_address(host))]
    except ValueError:
        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            async with asyncio.timeout(resolve_timeout):
                addresses = await resolver(host, port)
        except OSError:  # TimeoutError is also OSError.
            return
    if any(_is_rejected_address(address) for address in addresses):
        raise ArtifactDestinationRejectedError(f"artifact host resolves to a disallowed address: {host}")


def artifact_http_client(
    *,
    resolver: Resolver = resolve_host,
    resolve_timeout: float = DNS_RESOLVE_TIMEOUT_SECONDS,
    transport_layer: TransportLayer | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Construct ``httpx.AsyncClient`` checking destinations before every request, including redirect hops.

    ``transport_layer`` layers above validation (see :func:`layer_async_client`), so checks see rewritten
    requests. Forward other keyword arguments unchanged to ``httpx.AsyncClient``.
    """

    async def guard(request: httpx.Request) -> None:
        await check_destination(request.url, resolver=resolver, resolve_timeout=resolve_timeout)

    return layer_async_client(httpx.AsyncClient(event_hooks={"request": [guard]}, **kwargs), transport_layer)


def _declared_length(response: httpx.Response) -> int | None:
    try:
        return int(response.headers["Content-Length"])
    except (KeyError, ValueError):
        return None


def _too_large(max_bytes: int) -> ArtifactTooLargeError:
    return ArtifactTooLargeError(f"artifact response exceeds the {max_bytes}-byte limit")


async def _capped_chunks(response: httpx.Response, max_bytes: int) -> AsyncIterator[bytes]:
    """Yield body chunks, raising :class:`ArtifactTooLargeError` when cumulative reads exceed ``max_bytes``.

    Reject excessive declared ``Content-Length`` before reading; actual bytes read determine overflow.
    """
    declared = _declared_length(response)
    if declared is not None and declared > max_bytes:
        raise _too_large(max_bytes)
    received = 0
    async for chunk in response.aiter_bytes(chunk_size=_CHUNK_BYTES):
        received += len(chunk)
        if received > max_bytes:
            raise _too_large(max_bytes)
        yield chunk


async def read_body_capped(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read streaming bodies into memory, raising :class:`ArtifactTooLargeError` above ``max_bytes``."""
    return b"".join([chunk async for chunk in _capped_chunks(response, max_bytes)])


async def stream_body_to_file(response: httpx.Response, output_path: Path, *, max_bytes: int) -> None:
    """Write streaming bodies to ``output_path``, aborting above ``max_bytes``.

    Write same-directory ``.part`` first and atomically rename on success. Every failure, including excess
    size/cancellation, deletes ``.part``, never leaving truncated output. Batch 8 MiB before thread writes,
    avoiding per-chunk thread scheduling without retaining entire artifacts in memory. Chunk writes use
    ``run_sync_transaction``: cancellation waits for the writing thread before propagating, preventing
    ``.part`` cleanup and caller artifact cleanup from racing active threads. These writes remain within
    caller wall-clock budgets (``asyncio.timeout``); never swallow cancellation. Rename through
    ``run_noninterruptible_sync``; see comments below.
    """
    chunks = _capped_chunks(response, max_bytes)
    # Fetch first chunk before creating .part, raising declared-size excess here.
    first = await anext(chunks, b"")
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    partial_path = output_path.with_name(f"{output_path.name}.part")
    try:
        with open(partial_path, "wb") as handle:  # noqa: ASYNC230 -- obtain the handle only; actual writes are offloaded to threads below.
            buffered: list[bytes] = [first]
            buffered_bytes = len(first)

            async def flush() -> None:
                nonlocal buffered, buffered_bytes
                if not buffered:
                    return
                payload = b"".join(buffered)
                buffered = []
                buffered_bytes = 0
                await run_sync_transaction(handle.write, payload)

            async for chunk in chunks:
                buffered.append(chunk)
                buffered_bytes += len(chunk)
                if buffered_bytes >= _WRITE_BUFFER_BYTES:
                    await flush()
            await flush()
        # Rename forms the artifact: one atomic rename is negligible within outer wall-clock budgets. Cancellation here
        # still completes rename and disappears, allowing later transcoding/commit to continue (docs/standards/backend.md,
        # provider postresponse accounting sections must be noninterruptible).
        await run_noninterruptible_sync(os.replace, partial_path, output_path)
    except BaseException:
        await asyncio.to_thread(
            partial_path.unlink, True
        )  # hard-delete: temp -- .partial files left by interrupted downloads
        raise


async def buffered_error_response(response: httpx.Response, *, max_bytes: int = ERROR_BODY_MAX_BYTES) -> httpx.Response:
    """Replace streaming error responses with buffered copies retaining only the first ``max_bytes`` bytes.

    Preserve status, request, and headers except transfer-related fields, keeping retry predicates and
    ``Retry-After`` usable. Copies support immediate ``response.text`` access.
    """
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_bytes(chunk_size=_CHUNK_BYTES):
        chunks.append(chunk[: max_bytes - received])
        received += len(chunk)
        if received >= max_bytes:
            break
    headers = [(name, value) for name, value in response.headers.multi_items() if name.lower() not in _TRANSFER_HEADERS]
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=b"".join(chunks),
        request=response.request,
    )
