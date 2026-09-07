"""Smoke 测试：真实跑 probe，看每种错误 status / 错误体 / 诊断映射。

⚠️ 开发期手动工具，不是 CI 测试，**不应**加入 pytest 套件。
依赖外部服务可用性（anthropic.com / deepseek.com）是预期：上游协议或错误
格式变化时正是需要这个脚本来手动复现 + 沉淀新的 fixture。

只测安全路径（不发任何真付费请求；错 key 立即被上游拒绝）：
1. 故意错的 key 打真 anthropic → 看 AUTH_FAILED 抓不抓到
2. 不存在的 host → 看 NETWORK 抓不抓到
3. 完全不带 anthropic 子路径的 OpenAI 兼容 host → 看 OPENAI_COMPAT / 404 路径
4. 带 query 的地址 → 看入口校验抓不抓到

跑法：uv run python scripts/probe_smoke.py
"""

from __future__ import annotations

import asyncio
import dataclasses

from lib.config.anthropic_probe import run_test
from lib.httpx_shared import shutdown_http_client, startup_http_client


async def case(label: str, **kw):
    print(f"\n{'═' * 78}")
    print(f"  {label}")
    # 脱敏 api_key：一旦本地把 fake key 换成真 key 跑，原样 print 会留进
    # 终端历史/会话日志。
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
