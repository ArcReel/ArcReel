"""ComfyUI 专用的 httpx 客户端构造：在产物下载闸之上按目标 host 决定是否绕系统代理。

ComfyUI 常是用户自建、经内网 / Tailscale 访问的服务（如 ``http://192.168.5.149:8188``）。httpx 默认
``trust_env=True`` 会读 macOS 系统代理，把发往内网 / Tailscale 地址的请求也劫持到代理上 → 代理答不出
内网主机、回 502（本次 ``comfyui_upload_failed`` 的根因）。故 ComfyUI 这一侧的客户端把 ComfyUI 的
``base_url`` 作为 ``bypass_proxy_for`` 传入统一机制：base_url 判私有 / 内网时对该 host 直连、绕系统
代理（等价于原先对该 host 的 ``trust_env=False``），万一 ComfyUI 挂在公网则仍走系统代理。

本封装只在 :func:`~lib.backends.artifact_download_guard.artifact_http_client` 之上转交参数，出站目的地
校验（``check_destination`` 安全闸）照旧由后者挂上——绕代理只影响是否读系统代理，不动那道闸。
"""

from __future__ import annotations

from typing import Any

import httpx

from lib.backends.artifact_download_guard import artifact_http_client


def comfyui_http_client(**kwargs: Any) -> httpx.AsyncClient:
    """构造 ComfyUI 调用点用的 ``httpx.AsyncClient``。

    调用点把 ComfyUI 的 ``base_url`` 作为 ``bypass_proxy_for`` 传入：内网 base_url 直连绕代理、公网
    base_url 走系统代理。其余关键字参数（``timeout`` / ``follow_redirects`` 等）原样透传给
    :func:`~lib.backends.artifact_download_guard.artifact_http_client`。
    """
    return artifact_http_client(**kwargs)
