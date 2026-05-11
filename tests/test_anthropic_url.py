"""derive_anthropic_endpoints 单元测试。"""

import pytest

from lib.config.anthropic_url import AnthropicEndpoints, derive_anthropic_endpoints


@pytest.mark.parametrize(
    ("user_url", "expected"),
    [
        # 官方根
        (
            "https://api.anthropic.com",
            AnthropicEndpoints("https://api.anthropic.com", "https://api.anthropic.com", False),
        ),
        # /anthropic 子路径 (DeepSeek/Kimi/MiniMax/Hunyuan/MiMo)
        (
            "https://api.deepseek.com/anthropic",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # /api/anthropic (z.ai)
        (
            "https://api.z.ai/api/anthropic",
            AnthropicEndpoints("https://api.z.ai/api/anthropic", "https://api.z.ai/api", True),
        ),
        # /apps/anthropic (DashScope)
        (
            "https://dashscope.aliyuncs.com/apps/anthropic",
            AnthropicEndpoints(
                "https://dashscope.aliyuncs.com/apps/anthropic",
                "https://dashscope.aliyuncs.com",
                True,
            ),
        ),
        # /coding/anthropic (LKEAP)
        (
            "https://api.lkeap.cloud.tencent.com/coding/anthropic",
            AnthropicEndpoints(
                "https://api.lkeap.cloud.tencent.com/coding/anthropic",
                "https://api.lkeap.cloud.tencent.com",
                True,
            ),
        ),
        # /api/coding (火山方舟 Coding Plan)
        (
            "https://ark.cn-beijing.volces.com/api/coding",
            AnthropicEndpoints(
                "https://ark.cn-beijing.volces.com/api/coding",
                "https://ark.cn-beijing.volces.com",
                True,
            ),
        ),
        # 用户误带 /v1
        (
            "https://api.deepseek.com/anthropic/v1",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # 用户误带 /v1/messages
        (
            "https://api.deepseek.com/anthropic/v1/messages",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # 末尾多斜杠
        (
            "https://api.deepseek.com/anthropic/",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # 未识别子路径 → 不剥
        (
            "https://example.com/v2/proxy",
            AnthropicEndpoints("https://example.com/v2/proxy", "https://example.com/v2/proxy", False),
        ),
        # 纯根域，未带 /anthropic
        (
            "https://api.deepseek.com",
            AnthropicEndpoints("https://api.deepseek.com", "https://api.deepseek.com", False),
        ),
    ],
)
def test_derive_endpoints(user_url: str, expected: AnthropicEndpoints) -> None:
    assert derive_anthropic_endpoints(user_url) == expected


def test_empty_url_raises() -> None:
    with pytest.raises(ValueError):
        derive_anthropic_endpoints("")


def test_whitespace_stripped() -> None:
    ep = derive_anthropic_endpoints("  https://api.deepseek.com/anthropic  ")
    assert ep.messages_root == "https://api.deepseek.com/anthropic"
