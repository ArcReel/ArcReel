"""Smoke-check real probes: error status/body/diagnostic mappings.

⚠️ Manual development tool, not CI; **do not** add it to pytest suites.
External availability (anthropic.com / deepseek.com) is expected: upstream protocol/error changes
are exactly when this script helps reproduce issues and capture new fixtures.

Safe paths only (no paid requests; wrong keys are immediately rejected):
1. Wrong key against real anthropic → verify AUTH_FAILED.
2. Nonexistent host → verify NETWORK.
3. OpenAI-compatible host without anthropic subpath → inspect OPENAI_COMPAT / 404.
4. URL with query → verify entry validation.

Run: uv run python scripts/probe_smoke.py
"""

from __future__ import annotations

import asyncio
import dataclasses

from lib.config.anthropic_probe import run_test
from lib.infra.httpx_shared import shutdown_http_client, startup_http_client


async def case(label: str, **kw):
    print(f"\n{'═' * 78}")
    print(f"  {label}")
    # Mask api_key: if someone substitutes a real key for the fake one, raw printing would persist it
    # in terminal history/session logs.
    safe_kw = {**kw, "api_key": "***REDACTED***"} if kw.get("api_key") else kw
    print(f"  args: {safe_kw}")
    print("─" * 78)
    try:
        resp = await run_test(**kw)
    except Exception as exc:
        print(f"  raised: {type(exc).__name__}: {exc}")
        return
    print(f"  overall:    {resp.overall}")
    print(f"  diagnosis:  {resp.diagnosis}")
    print(f"  suggestion: {resp.suggestion}")
    print(f"  messages_url: {resp.messages_url}")
    print("  messages_probe:")
    for k, v in dataclasses.asdict(resp.messages_probe).items():
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
        "Case 3: OpenAI 兼容端点（不带 /anthropic 子路径）",
        preset_id=None,
        base_url="https://api.deepseek.com",
        api_key="sk-fake",
        model=None,
    )
    await case(
        "Case 4: 带 query 的地址 → 入口校验拒绝",
        preset_id=None,
        base_url="https://api.deepseek.com/anthropic?api_key=sk-fake",
        api_key="sk-fake",
        model=None,
    )


if __name__ == "__main__":
    asyncio.run(main())
