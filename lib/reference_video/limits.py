"""参考生视频模式的供应商能力上限（单一真相源）。

Spec §附录 B。目前只保留 **provider 粒度** 的 `PROVIDER_MAX_REFS`（参考图数量上限）——
该口径目前仍是 provider 级别不区分 model。被 `ConfigResolver._resolve_video_capabilities_from_project`
（返回 caps.max_reference_images）和 `lib/script_generator._resolve_max_refs` 共用。

Duration（单次视频时长）上限的单一真相源是 **model 粒度的 `model.supported_durations`**，
由 `ConfigResolver.video_capabilities_for_project(project)` 以 `caps["max_duration"]` 暴露，
不在本模块维护（见 issue #377：删除了老的 `PROVIDER_MAX_DURATION` provider 级常量以消除双源漂移）。

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
    "vidu": 7,
}

DEFAULT_MAX_REFS = 9


def normalize_provider_id(raw: str) -> str:
    """将 PROVIDER_REGISTRY 的 provider_id 归一到 executor 口径的 backend.name。"""
    lowered = (raw or "").lower()
    return "gemini" if lowered.startswith("gemini") else lowered
