"""Anthropic base_url 派生：把用户填的 URL 拆为 messages_root + discovery_root。

各国内代理网关把 Claude 兼容协议挂在不同的子路径下：
- /anthropic              DeepSeek、Kimi、MiniMax、腾讯 Hunyuan、小米 MiMo
- /api/anthropic          GLM (z.ai)
- /apps/anthropic         阿里百炼 (DashScope)
- /plan/anthropic         腾讯 LKEAP Token Plan
- /coding/anthropic       腾讯 LKEAP Coding Plan
- /api/coding             火山方舟 Coding Plan

而模型发现 /v1/models 总是在「子路径之前的根」下。
本模块负责一次性派生这两个 root，下游 SDK / 模型发现各取所需。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 已知的 Claude 兼容子路径。每个 pattern 是 (regex, keep_prefix_group)。
# keep_prefix_group=True 表示 discovery_root 需要保留正则匹配段之前 + 捕获的前缀段。
#
# 规则（按优先级，精确模式在前）：
#   /api/anthropic  (z.ai) → discovery_root 保留 /api 前缀
#   其他已知子路径          → discovery_root 剥掉整个子路径段
#
# 每条 entry: (compiled_re, n_keep_chars_before_strip)
#   n_keep_chars_before_strip = 正则 match.start() 之后还需要保留多少字符到 discovery_root
_SUFFIX_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    # /api/anthropic — discovery_root 保留 "/api"（4 字符）
    (re.compile(r"/api/anthropic/?$"), 4),
    # 其余已知子路径 — 整体剥掉（保留 0 字符）
    (re.compile(r"/(?:apps/anthropic|plan/anthropic|coding/anthropic|api/coding|anthropic)/?$"), 0),
]
# 用户误带的版本路径，先剥
_TRAILING_VERSION = re.compile(r"/v\d+\w*(?:/messages)?/?$")


@dataclass(frozen=True)
class AnthropicEndpoints:
    """从用户填的 base_url 派生出的两个端点根。"""

    messages_root: str
    """Claude SDK 拼 /v1/messages 用 (含 anthropic 子路径)。"""

    discovery_root: str
    """模型发现拼 /v1/models 用 (剥掉 anthropic 子路径)。"""

    has_explicit_suffix: bool
    """用户输入是否已经显式带了已知 anthropic 子路径。"""


def derive_anthropic_endpoints(user_url: str) -> AnthropicEndpoints:
    """派生 Anthropic 兼容端点。

    Steps:
        1) 去首尾空白、剥末尾斜杠
        2) 剥末尾的 /v\\d+ 或 /v\\d+/messages（用户误带）
        3) 用 _KNOWN_ANTHROPIC_SUFFIX 匹配子路径：
           匹配 → messages_root = 原值, discovery_root = 剥掉子路径
           不匹配 → messages_root == discovery_root == 原值
    """
    if not user_url or not user_url.strip():
        raise ValueError("user_url is empty")
    cleaned = user_url.strip().rstrip("/")
    cleaned = _TRAILING_VERSION.sub("", cleaned).rstrip("/")
    for pattern, keep_extra in _SUFFIX_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            messages_root = cleaned[: match.end()].rstrip("/")
            discovery_root = (cleaned[: match.start()] + cleaned[match.start() : match.start() + keep_extra]).rstrip(
                "/"
            )
            return AnthropicEndpoints(messages_root, discovery_root, has_explicit_suffix=True)
    return AnthropicEndpoints(cleaned, cleaned, has_explicit_suffix=False)
