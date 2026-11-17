"""本机回环 HTTP 替身：在 127.0.0.1 上按脚本回放 HTTP/1.1 响应，供需要真实 socket 的调用通道用例使用。

respx 在 transport 层拦截，拿不到「分块编码中途断开」「一个对象跨两个传输块」这类线路形态。这里起一个真实的 TCP
服务端，被测代码用自己的 httpx 客户端连过来，分块边界、截断与连接关闭都发生在线路上。

用例需要标 ``local_port``：禁止绑定本地端口的环境由根 conftest 跳过。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class ScriptedReply:
    """一次响应的脚本。

    ``chunks`` 逐个作为 HTTP 分块写出；``truncate`` 为真时最后一个分块按完整长度声明、只写出前一半就断开连接，
    不发结束块。
    """

    chunks: Sequence[bytes] = ()
    status: int = 200
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)
    truncate: bool = False
    malformed_chunk_header: bytes | None = None


@dataclass
class ReceivedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class LoopbackServer:
    """已启动的替身：``url(path)`` 拼出请求地址，``requests`` 按到达顺序记下全部请求。"""

    port: int
    replies: list[ScriptedReply]
    requests: list[ReceivedRequest] = field(default_factory=list)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def redirect(self) -> RedirectToLoopback:
        """调用通道的 transport 注入层：把请求改投到这个替身，路径与查询串不变。"""
        return RedirectToLoopback(self.port)


class RedirectToLoopback:
    """叠在调用通道 httpx 客户端上的 transport 层：请求改投到 ``127.0.0.1:port``，并记下改写前的 URL。

    被测代码照常拼出生产地址，出站目的地校验作用于改写后的回环地址。
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.seen: list[str] = []

    def __call__(self, inner: httpx.AsyncBaseTransport, /) -> httpx.AsyncBaseTransport:
        return _Redirecting(inner, self)


class _Redirecting(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, layer: RedirectToLoopback) -> None:
        self._inner = inner
        self._layer = layer

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._layer.seen.append(str(request.url))
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self._layer.port)
        request.headers["Host"] = f"127.0.0.1:{self._layer.port}"
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def _read_request(reader: asyncio.StreamReader) -> ReceivedRequest:
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    body = await reader.readexactly(int(headers.get("content-length", "0")))
    return ReceivedRequest(method=method, path=path, headers=headers, body=body)


def _status_line(status: int) -> bytes:
    return f"HTTP/1.1 {status} {'OK' if status < 400 else 'Error'}\r\n".encode()


@asynccontextmanager
async def loopback_server(replies: Sequence[ScriptedReply]) -> AsyncGenerator[LoopbackServer]:
    """在 127.0.0.1 的随机端口上按顺序回放 ``replies``；请求数超出脚本时回 500。"""
    state = LoopbackServer(port=0, replies=list(replies))

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            state.requests.append(await _read_request(reader))
            index = len(state.requests) - 1
            reply = state.replies[index] if index < len(state.replies) else ScriptedReply(status=500)
            head = _status_line(reply.status)
            head += (
                f"Content-Type: {reply.content_type}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n".encode()
            )
            for name, value in reply.headers.items():
                head += f"{name}: {value}\r\n".encode()
            writer.write(head + b"\r\n")
            if reply.malformed_chunk_header is not None:
                writer.write(reply.malformed_chunk_header + b"\r\n")
                await writer.drain()
                return
            for position, chunk in enumerate(reply.chunks):
                if reply.truncate and position == len(reply.chunks) - 1:
                    writer.write(f"{len(chunk):x}\r\n".encode() + chunk[: len(chunk) // 2])
                else:
                    writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                await writer.drain()
            if not reply.truncate:
                writer.write(b"0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()
