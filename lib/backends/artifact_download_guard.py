"""供应商产物下载的统一入口：出站目的地校验与有上限的响应体读取。

产物地址来自供应商响应，按不可信输入处理。两道约束：

- 目的地：每次请求（含每一跳重定向）发出前解析目标主机，拒绝非 ``http`` / ``https`` 协议，
  以及落在链路本地与云元数据地址上的目标（含 IPv4 映射的 IPv6 写法）。云元数据地址除链路本地段内的
  以外，另含 ``100.100.100.200`` 与 ``192.0.0.192`` 两个单地址。环回与私网地址放行——
  自建 ComfyUI / Ollama 这类服务合法地位于其中。
- 体积：响应体按媒体类型分档设总字节上限，超限即中止；落盘走同目录 ``.part`` 再原子改名，
  中止时不留残片。错误响应的响应体只读前一段，供状态错误携带诊断。

位于 backend 各子包之外的共用层：视频 / 图片 / 音频 backend 与自定义供应商运行时共用本模块。
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

from lib.infra.retry import NonRetryableError

#: 解析器：``(host, port)`` → 该主机解析出的地址文本。
Resolver = Callable[[str, int], Awaitable[Iterable[str]]]

#: 各媒体类型产物的总字节上限。取值远高于各供应商单个产物的实际体积，只拦异常大的响应。
VIDEO_ARTIFACT_MAX_BYTES = 2 * 1024 * 1024 * 1024
IMAGE_ARTIFACT_MAX_BYTES = 256 * 1024 * 1024
AUDIO_ARTIFACT_MAX_BYTES = 256 * 1024 * 1024
ARTIFACT_MAX_BYTES_BY_MEDIA_TYPE: dict[str, int] = {
    "video": VIDEO_ARTIFACT_MAX_BYTES,
    "image": IMAGE_ARTIFACT_MAX_BYTES,
    "audio": AUDIO_ARTIFACT_MAX_BYTES,
}

#: 主机名解析的时限。解析不走 httpx 的超时配置，单独兜住。
DNS_RESOLVE_TIMEOUT_SECONDS = 10.0

#: 错误响应体读取上限：够状态错误带出诊断摘要即可。
ERROR_BODY_MAX_BYTES = 64 * 1024

_ALLOWED_SCHEMES = frozenset({"http", "https"})
#: 链路本地段，加上不在链路本地段内的云元数据单地址；只拦单地址，所在网段的其余地址照常放行。
_REJECTED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("192.0.0.192/32"),
)

#: 落盘攒批阈值：驻留内存的上界，同时把线程池调度摊薄到每 8 MiB 一次。
_WRITE_BUFFER_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024

#: 这些头描述的是原始传输形态；截断后的响应体已解码，保留它们会让读取端再解码一次或长度对不上。
_TRANSFER_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class ArtifactDestinationRejectedError(NonRetryableError):
    """产物请求的目标协议或地址不在允许范围内。"""


class ArtifactTooLargeError(NonRetryableError):
    """产物响应体超过该媒体类型的总字节上限。"""


async def resolve_host(host: str, port: int) -> list[str]:
    """用事件循环的异步 ``getaddrinfo`` 解析主机，返回全部地址文本。"""
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
    """校验一次请求的目标；不在允许范围内抛 :class:`ArtifactDestinationRejectedError`。

    主机名解析失败或超过 ``resolve_timeout`` 秒时不作判定，交给传输层照常连接
    （连不上即按网络错误失败）：经代理出网的部署里本机可能解析不了目标主机。
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
        except OSError:  # TimeoutError 亦是 OSError
            return
    if any(_is_rejected_address(address) for address in addresses):
        raise ArtifactDestinationRejectedError(f"artifact host resolves to a disallowed address: {host}")


def artifact_http_client(
    *,
    resolver: Resolver = resolve_host,
    resolve_timeout: float = DNS_RESOLVE_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """构造对每次请求（含每一跳重定向）先做目的地校验的 ``httpx.AsyncClient``。

    其余关键字参数原样交给 ``httpx.AsyncClient``。
    """

    async def guard(request: httpx.Request) -> None:
        await check_destination(request.url, resolver=resolver, resolve_timeout=resolve_timeout)

    return httpx.AsyncClient(event_hooks={"request": [guard]}, **kwargs)


def _declared_length(response: httpx.Response) -> int | None:
    try:
        return int(response.headers["Content-Length"])
    except (KeyError, ValueError):
        return None


def _too_large(max_bytes: int) -> ArtifactTooLargeError:
    return ArtifactTooLargeError(f"artifact response exceeds the {max_bytes}-byte limit")


async def _capped_chunks(response: httpx.Response, max_bytes: int) -> AsyncIterator[bytes]:
    """逐块产出响应体，累计超过 ``max_bytes`` 抛 :class:`ArtifactTooLargeError`。

    ``Content-Length`` 声明超限时在读取前拒绝；是否超限以实际读到的字节数为准。
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
    """把流式响应的响应体读进内存，超过 ``max_bytes`` 抛 :class:`ArtifactTooLargeError`。"""
    return b"".join([chunk async for chunk in _capped_chunks(response, max_bytes)])


async def stream_body_to_file(response: httpx.Response, output_path: Path, *, max_bytes: int) -> None:
    """把流式响应的响应体写入 ``output_path``，超过 ``max_bytes`` 即中止。

    先写同目录 ``.part``、成功后原子改名；任何失败（含超限与取消）都删掉 ``.part``，产物路径上
    不会留下截断的文件。攒够 8 MiB 再一次 ``to_thread`` 落盘：既不为每个分片调度一次线程池任务，
    也不把整段产物留在内存里。
    """
    chunks = _capped_chunks(response, max_bytes)
    # 先取首块：声明超限在此抛出，不建 .part
    first = await anext(chunks, b"")
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    partial_path = output_path.with_name(f"{output_path.name}.part")
    try:
        with open(partial_path, "wb") as handle:  # noqa: ASYNC230 -- 只在此取句柄，实际写入均由下方 to_thread 卸载
            buffered: list[bytes] = [first]
            buffered_bytes = len(first)

            async def flush() -> None:
                nonlocal buffered, buffered_bytes
                if not buffered:
                    return
                payload = b"".join(buffered)
                buffered = []
                buffered_bytes = 0
                await asyncio.to_thread(handle.write, payload)

            async for chunk in chunks:
                buffered.append(chunk)
                buffered_bytes += len(chunk)
                if buffered_bytes >= _WRITE_BUFFER_BYTES:
                    await flush()
            await flush()
        await asyncio.to_thread(os.replace, partial_path, output_path)
    except BaseException:
        await asyncio.to_thread(partial_path.unlink, True)
        raise


async def buffered_error_response(response: httpx.Response, *, max_bytes: int = ERROR_BODY_MAX_BYTES) -> httpx.Response:
    """把流式错误响应换成一个已读完的副本，响应体只保留前 ``max_bytes`` 字节。

    状态码、响应头（去掉传输形态相关的几项）与请求原样保留，重试谓词与 ``Retry-After``
    照常可用；``response.text`` 在副本上可直接读取。
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
