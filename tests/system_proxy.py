"""屏蔽 macOS 的系统代理发现，让测试会话里的请求直连。

macOS 上 ``urllib.request.getproxies()`` 在环境里没有 ``*_proxy`` 变量时会经 ``_scproxy``
调 SystemConfiguration 读系统代理。httpx 建客户端时按 ``getproxies()`` 挂代理 mount，于是
指向 ``127.0.0.1`` 的回环请求也被交给代理：代理连不上本机端口时返回 502，用例拿到的是
``HTTPStatusError`` 而不是它要断言的 ``ConnectError`` ——「连接被拒绝」这类断言因此在开着
系统代理的机器上必然失败。

把两个 macOS 专属入口换成「无系统代理」的常量实现即可：环境变量给的代理仍按原路生效
（``getproxies()`` 先读环境），因此显式设置 ``HTTP_PROXY`` 的用例不受影响。

两处加载点：

- 根 conftest 的 ``pytest_sessionstart``：普通 pytest 会话。
- ``tests/mutmut_plugin.py``：mutmut fork 出的子进程，除本模块外还需私有 basetemp。

该模块在非 macOS 上是空操作（``getproxies_macosx_sysconf`` 只在 macOS 存在）。
"""

from __future__ import annotations

import urllib.request


def _no_system_proxies() -> dict[str, str]:
    return {}


def _never_bypass_by_system_settings(host: str) -> bool:
    return False


def disable_macos_system_proxy() -> None:
    """让 macOS 的系统代理对当前进程不可见；其它平台与已设的环境变量不受影响。"""
    if hasattr(urllib.request, "getproxies_macosx_sysconf"):
        urllib.request.getproxies_macosx_sysconf = _no_system_proxies
        urllib.request.proxy_bypass_macosx_sysconf = _never_bypass_by_system_settings
