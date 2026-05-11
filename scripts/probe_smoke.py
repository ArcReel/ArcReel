"""Smoke 测试：真实跑 SDK probe，看每种错误 stderr / status / 诊断映射。

只测安全路径：
1. 故意错的 key 打真 anthropic → 看 AUTH_FAILED 抓不抓到
2. 不存在的 host → 看 NETWORK 抓不抓到
3. 完全不带 anthropic 后缀的 OpenAI 兼容 host → 看自愈路径

不发任何真付费请求；错 key 在 anthropic 端会被立即拒绝。
"""

from __future__ import annotations

import asyncio
import dataclasses

from lib.config.anthropic_probe import run_test
from lib.httpx_shared import shutdown_http_client, startup_http_client


async def case(label: str, **kw):
    print(f"\n{'═' * 78}")
    print(f"  {label}")
    print(f"  args: {kw}")
    print("─" * 78)
    try:
        resp = await run_test(**kw)
    except Exception as exc:
        print(f"  raised: {type(exc).__name__}: {exc}")
        return
    print(f"  overall:    {resp.overall}")
    print(f"  diagnosis:  {resp.diagnosis}")
    print(f"  suggestion: {resp.suggestion}")
    print(f"  derived_messages_root:  {resp.derived_messages_root}")
    print(f"  derived_discovery_root: {resp.derived_discovery_root}")
    print("  messages_probe:")
    for k, v in dataclasses.asdict(resp.messages_probe).items():
        if k == "error" and v:
            print(f"    {k}: {v[:160]!r}{'...' if len(v) > 160 else ''}")
        else:
            print(f"    {k}: {v}")
    if resp.discovery_probe:
        print("  discovery_probe:")
        for k, v in dataclasses.asdict(resp.discovery_probe).items():
            if k == "error" and v:
                print(f"    {k}: {v[:160]!r}{'...' if len(v) > 160 else ''}")
            else:
                print(f"    {k}: {v}")


async def main():
    await startup_http_client()
    try:
        await _run_cases()
    finally:
        await shutdown_http_client()


async def _run_cases():
    await case(
        "Case 1: 真 anthropic + 故意错的 key",
        preset_id="anthropic-official",
        base_url=None,
        api_key="sk-ant-fake-key-for-smoke-test-not-real",
        model=None,
    )
    await case(
        "Case 2: 不存在的 host",
        preset_id=None,
        base_url="https://nonexistent.host.invalid",
        api_key="sk-ant-fake",
        model=None,
    )
    await case(
        "Case 3: OpenAI 兼容端点（不带 /anthropic 后缀）→ 触发自愈路径",
        preset_id=None,
        base_url="https://api.deepseek.com",
        api_key="sk-fake",
        model=None,
    )


if __name__ == "__main__":
    asyncio.run(main())
