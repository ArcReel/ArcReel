"""按目标地址决定出站 HTTP 是否绕开系统代理：内网 / 私有目标直连，公网目标走系统代理。

httpx 默认 ``trust_env=True`` 会读 macOS / 环境里的系统代理（如 Clash 的 ``127.0.0.1:7890``），把发往
内网 / Tailscale 地址的请求也一并劫持到代理上 → 代理答不出内网主机、回 502（本次 ComfyUI 与自建
8001 网关连通性失败的根因）。但走公网的云厂商（OpenAI / Google / Kling / DashScope 等）又确实需要
系统代理才能出网，不能一刀切关掉。

统一机制：构造 client 时按目标 host 判定——目标落在私有 / 内网网段就为该 host 挂一个 ``None`` 的
mount（走 DIRECT 直连），公网目标不动（仍按 ``trust_env`` 读系统代理）。判定一律用 :mod:`ipaddress`
严格判网段，不用 ``startswith('192.')`` 这类脆弱字符串前缀。

与 :mod:`lib.backends.artifact_download_guard` 的出站目的地安全闸正交：mounts 只决定「走不走代理」，
不改 ``event_hooks`` 的 request guard，两者各管各的。
"""

from __future__ import annotations

import ipaddress

import httpx

#: 私有 / 内网 IPv4 网段：命中即直连、绕系统代理。
#:
#: - ``10.0.0.0/8`` / ``172.16.0.0/12`` / ``192.168.0.0/16``：RFC 1918 私有地址。
#: - ``127.0.0.0/8``：本机环回。
#: - ``100.64.0.0/10``：RFC 6598 CGNAT，Tailscale 给节点分配的 ``100.x`` 地址落在此段，必须算内网。
_PRIVATE_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
)

#: 私有 / 内网 IPv6 网段：
#:
#: - ``::1/128``：本机环回。
#: - ``fc00::/7``：唯一本地地址（ULA），IPv6 的私网等价物。
#: - ``fe80::/10``：链路本地地址。
_PRIVATE_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
)

#: 判私有的主机名后缀：
#:
#: - ``localhost`` / ``*.localhost``：本机。
#: - ``.local``：mDNS / Bonjour 本地域。
#: - ``.ts.net``：Tailscale MagicDNS 名字，实际解析到 ``100.64/10``，与 IP 判定一致。
_PRIVATE_HOST_SUFFIXES: tuple[str, ...] = (".localhost", ".local", ".ts.net")


def _ipv4_is_private(ip: ipaddress.IPv4Address) -> bool:
    return any(ip in network for network in _PRIVATE_IPV4_NETWORKS)


def is_private_host(host: str) -> bool:
    """``host`` 是否属私有 / 内网 → 该目标应绕开系统代理直连。

    先按 IP 判（去掉可能的 IPv6 ``[]`` 包裹）：IPv4 命中私网 / 环回 / CGNAT 段、IPv6 命中环回 /
    ULA / 链路本地段即私有；IPv4-mapped 的 IPv6（``::ffff:a.b.c.d``）取出内嵌 IPv4 再按 IPv4 判。
    不是 IP（是主机名）时按后缀 / 字面判：``localhost`` 与几个私有后缀算私有，其余域名（含
    ``api.openai.com`` 这类公网域名）一律当公网——判不准时 fail-safe 偏「走代理」，不误把公网
    请求甩开代理。
    """
    if not host:
        return False
    candidate = host.strip().strip("[]").lower()
    if not candidate:
        return False
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        # 主机名：按字面与后缀判。
        if candidate == "localhost":
            return True
        return any(candidate.endswith(suffix) for suffix in _PRIVATE_HOST_SUFFIXES)
    if isinstance(ip, ipaddress.IPv4Address):
        return _ipv4_is_private(ip)
    # IPv6：先还原 IPv4-mapped，再分别按 v4 / v6 网段判。
    if ip.ipv4_mapped is not None:
        return _ipv4_is_private(ip.ipv4_mapped)
    return any(ip in network for network in _PRIVATE_IPV6_NETWORKS)


def host_from_base_url(base_url: str | None) -> str | None:
    """从 base_url / 完整 URL 取出归一化的 host；取不出返回 ``None``（判不准 → 当公网）。

    用 ``httpx.URL`` 解析（host 已小写、去端口、去 IPv6 的 ``[]``）；``base_url`` 为空、缺 scheme
    导致解析不出 host、或是非法 URL，一律返回 ``None``。
    """
    if not base_url:
        return None
    try:
        host = httpx.URL(base_url).host
    except httpx.InvalidURL:
        return None
    return host or None


def proxy_bypass_mounts(base_url: str | None) -> dict[str, None]:
    """按目标 base_url 给出 httpx ``mounts``：内网目标 → ``{"all://<host>": None}``，否则 ``{}``。

    mount 的 key 用不带端口的 ``all://<host>``：对该 host 的所有端口、http / https 都生效，契合
    「同一台内网机上跑 8188 / 8001 多个端口」的场景。``None`` transport 让 httpx 对该 host 走
    DIRECT，不读系统代理；其它 host 不在 mounts 里，仍按 client 的 ``trust_env`` 读系统代理。

    返回空 dict 表示「这个目标不需要绕代理」，调用方据此可完全不碰 client 的默认代理行为。
    """
    host = host_from_base_url(base_url)
    if host is not None and is_private_host(host):
        # IPv6 字面量在 ``all://<host>`` 里须加 ``[]`` 包裹，否则 httpx 会把冒号后一段当端口解析。
        key_host = f"[{host}]" if ":" in host else host
        return {f"all://{key_host}": None}
    return {}


def openai_proxy_http_client(base_url: str | None) -> httpx.Client | None:
    """给 OpenAI SDK 用的同步 ``httpx.Client``：base_url 内网时注入绕代理 client，公网返回 ``None``。

    OpenAI SDK 自建的 httpx client 同样默认读系统代理，发往内网兼容端点（如 ``192.168.5.149:8001``）
    会被劫持到代理上回 502。base_url 判私有时返回一个为该 host 挂 ``None`` mount 的同步 client
    （``trust_env`` 保持默认 ``True``，故该 client 对公网 host 仍走代理）；base_url 公网 / 判不准时
    返回 ``None``，调用方不传 ``http_client``，维持 SDK 默认行为、零改动。
    """
    mounts = proxy_bypass_mounts(base_url)
    if not mounts:
        return None
    return httpx.Client(mounts=mounts)


def openai_async_proxy_http_client(base_url: str | None) -> httpx.AsyncClient | None:
    """给 AsyncOpenAI 用的异步 ``httpx.AsyncClient``：base_url 内网时注入绕代理 client，公网返回 ``None``。

    与同步版 :func:`openai_proxy_http_client` 完全对称，仅类型不同：``AsyncOpenAI`` 的 ``http_client``
    须是 ``httpx.AsyncClient``，同步 ``httpx.Client`` 不能直接用。判定共享同一个 :func:`proxy_bypass_mounts`
    单一来源——base_url 判私有时返回一个为该 host 挂 ``None`` mount 的异步 client（``trust_env`` 保持默认
    ``True``，故该 client 对公网 host 仍走代理）；base_url 公网 / 判不准时返回 ``None``，调用方不传
    ``http_client``，维持 SDK 默认行为、零改动。
    """
    mounts = proxy_bypass_mounts(base_url)
    if not mounts:
        return None
    return httpx.AsyncClient(mounts=mounts)


def genai_client_args(base_url: str | None) -> dict[str, object] | None:
    """给 google-genai ``HttpOptions.client_args`` 用的 kwargs：base_url 内网时注入 mounts，否则 ``None``。

    google-genai 2.25.0 的 ``HttpOptions.client_args`` 原样透传给其内部同步 ``httpx`` client
    （进程内实测：注入 ``mounts`` 后对内网 host 的 transport 不走系统代理）。base_url 公网 /
    判不准时返回 ``None``，调用方不加 ``client_args``，维持 SDK 默认行为、零改动。
    """
    mounts = proxy_bypass_mounts(base_url)
    if not mounts:
        return None
    return {"mounts": mounts}
