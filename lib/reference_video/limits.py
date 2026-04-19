"""参考生视频模式的供应商能力上限（单一真相源）。

Spec §附录 B。被 `lib/script_generator.py:_resolve_max_refs()`（prompt 构建阶段）
和 `server/services/reference_video_tasks.py:_PROVIDER_LIMITS`（executor 强制阶段）
共享，防止两处硬编码漂移。

Provider id 归一化说明：`PROVIDER_REGISTRY` 把 Gemini 拆成 `gemini-aistudio` 和
`gemini-vertex` 两个条目，而 executor 侧的 `backend.name` 已归一化为 `"gemini"`。
这里的 key 与 executor 一致；调用方需要在查表前把 `gemini-*` 折叠到 `gemini`。
"""

from __future__ import annotations

PROVIDER_MAX_REFS: dict[str, int] = {
    "gemini": 3,
    "openai": 1,
    "grok": 7,
    "ark": 9,
}

DEFAULT_MAX_REFS = 9

PROVIDER_MAX_DURATION: dict[str, int] = {
    "gemini": 8,
    "openai": 12,
    "grok": 15,
    "ark": 15,
}


def normalize_provider_id(raw: str) -> str:
    """将 PROVIDER_REGISTRY 的 provider_id 归一到 executor 口径的 backend.name。"""
    lowered = (raw or "").lower()
    return "gemini" if lowered.startswith("gemini") else lowered
