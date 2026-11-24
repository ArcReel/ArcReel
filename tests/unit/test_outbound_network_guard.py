import errno
import socket
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

_BLOCKED = "Non-loopback network blocked"
_UNIX_SOCKET_FAMILY = getattr(socket, "AF_UNIX", None)


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


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("gethostbyname", ("provider.invalid\0",)),
        ("gethostbyname_ex", ("provider.invalid\0",)),
        ("gethostbyaddr", ("203.0.113.10\0",)),
        ("getnameinfo", (("203.0.113.10\0", 443), 0)),
    ],
)
def test_blocks_external_socket_dns_before_native_argument_processing(method: str, args: tuple):
    """Embedded NULs make native calls fail before DNS even if the guard regresses."""
    with pytest.raises(AssertionError, match=_BLOCKED):
        getattr(socket, method)(*args)


def test_allows_numeric_reverse_lookup_without_dns():
    address = ("203.0.113.10", 443)
    assert socket.getnameinfo(address, socket.NI_NUMERICHOST | socket.NI_NUMERICSERV) == ("203.0.113.10", "443")


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
    [
        ("sendto", (b"test",)),
        pytest.param(
            "sendmsg",
            ([b"test"], [], 0),
            marks=pytest.mark.skipif(not hasattr(socket.socket, "sendmsg"), reason="socket.sendmsg is unavailable"),
        ),
    ],
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
        pytest.param(
            _UNIX_SOCKET_FAMILY,
            "unused.sock",
            marks=pytest.mark.skipif(_UNIX_SOCKET_FAMILY is None, reason="socket.AF_UNIX is unavailable"),
        ),
    ],
)
def test_allows_loopback_and_unix_socket_destinations(family: socket.AddressFamily, address):
    with socket.socket(family) as sock:
        sock.close()
        with pytest.raises(
            OSError,
            check=lambda error: (
                error.errno == errno.EBADF
                or getattr(error, "winerror", None) == getattr(errno, "WSAENOTSOCK", errno.EBADF)
            ),
        ):
            sock.connect(address)
