import errno
import os
import socket
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

_BLOCKED = "Non-loopback network blocked"


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_blocks_non_loopback_socket_connection_from_worker_thread(method: str):
    address = ("203.0.113.10", 443)
    with socket.socket() as sock, ThreadPoolExecutor() as pool:
        sock.close()
        connection = pool.submit(getattr(sock, method), address)
        with pytest.raises(AssertionError, match=rf"{_BLOCKED}.*203\.0\.113\.10"):
            connection.result()


def test_blocks_httpx_before_external_hostname_resolution(monkeypatch: pytest.MonkeyPatch):
    import _socket

    def unexpected_dns(*args, **kwargs):
        raise RuntimeError("would reach the system resolver")

    monkeypatch.setattr(_socket, "getaddrinfo", unexpected_dns)
    with (
        httpx.Client(trust_env=False) as client,
        pytest.raises(AssertionError, match=rf"{_BLOCKED}.*provider\.invalid"),
    ):
        client.get("https://provider.invalid")


async def test_blocks_async_httpx_before_external_socket_connection(monkeypatch: pytest.MonkeyPatch):
    connect = socket.socket.connect
    with socket.socket() as closed:
        closed.close()
        monkeypatch.setattr(socket.socket, "connect", lambda _sock, address: connect(closed, address))
        async with httpx.AsyncClient(trust_env=False) as client:
            with pytest.RaisesGroup(AssertionError) as blocked:
                await client.get("https://203.0.113.10")
    assert f"{_BLOCKED} for ('203.0.113.10', 443)" in str(blocked.value.exceptions[0])


@pytest.mark.parametrize(
    ("method", "payload"),
    [("sendto", (b"test",)), ("sendmsg", ([b"test"], [], 0))],
)
def test_blocks_non_loopback_datagram(method: str, payload: tuple):
    address = ("203.0.113.10", 443)
    with socket.socket(type=socket.SOCK_DGRAM) as sock:
        sock.close()
        with pytest.raises(AssertionError, match=rf"{_BLOCKED}.*203\.0\.113\.10"):
            getattr(sock, method)(*payload, address)


@pytest.mark.parametrize(
    ("family", "address"),
    [
        (socket.AF_INET, ("127.0.0.1", 1)),
        (socket.AF_INET, ("localhost", 1)),
        (socket.AF_INET6, ("::1", 1)),
        (socket.AF_UNIX, "unused.sock"),
    ],
)
def test_allows_loopback_and_unix_socket_destinations(family: socket.AddressFamily, address):
    with socket.socket(family) as sock:
        sock.close()
        with pytest.raises(OSError, match=os.strerror(errno.EBADF)):
            sock.connect(address)
