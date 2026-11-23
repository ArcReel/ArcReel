from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from lib.backends.agnes_shared import AGNES_BASE_URL
from lib.backends.ark_shared import ARK_BASE_URL
from lib.backends.dashscope_shared import DASHSCOPE_BASE_URL
from lib.backends.minimax_shared import MINIMAX_BASE_URL

_VALID_LANES = frozenset({"image", "video", "audio"})


@dataclass(frozen=True)
class ProviderMeta:
    """一个内置供应商的代码事实：显示名、凭证形态、默认地址与各条 lane 的出厂并发。

    供应商的模型清单、默认模型、模型能力与价格不在这里，经目录查询（``lib.catalog.query``）读取。
    """

    display_name: str
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    default_base_url: str | None = None
    credential_groups: list[list[str]] = field(default_factory=list)
    default_concurrency: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for lane, limit in self.default_concurrency.items():
            if lane not in _VALID_LANES:
                raise ValueError(
                    f"{self.display_name} default_concurrency 含未知 lane {lane!r}，合法值：{sorted(_VALID_LANES)}"
                )
            if type(limit) is not int or limit < 1:
                raise ValueError(f"{self.display_name} default_concurrency[{lane!r}] 必须是 >=1 的整数，得到 {limit!r}")
        if self.credential_groups:
            allowed = set(self.required_keys) & set(self.secret_keys)
            covered: set[str] = set()
            for group in self.credential_groups:
                if not group:
                    raise ValueError(f"{self.display_name} credential_groups 含空分组")
                unknown = set(group) - allowed
                if unknown:
                    raise ValueError(
                        f"{self.display_name} credential_groups 含未登记为 required∩secret 的 key {sorted(unknown)}"
                    )
                covered |= set(group)
            if covered != allowed:
                raise ValueError(f"{self.display_name} credential_groups 未覆盖 {sorted(allowed - covered)}")

    def fully_covered_credential_groups(self, values: Mapping[str, str | None]) -> list[list[str]]:
        """返回被 ``values`` 完整覆盖的凭证组（组内所有 key 均非空）。

        驱动凭证创建/更新端点的切组判定：未声明 credential_groups 的 provider
        （绝大多数）该列表恒为空，调用方据此保持"不做切组处理"的原语义不变。
        """
        return [group for group in self.credential_groups if all(values.get(k) for k in group)]


PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    "gemini-aistudio": ProviderMeta(
        display_name="AI Studio",
        required_keys=["api_key"],
        optional_keys=[
            "base_url",
            "image_rpm",
            "video_rpm",
            "request_gap",
            "image_max_workers",
            "video_max_workers",
            "audio_max_workers",
        ],
        secret_keys=["api_key"],
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Vertex AI",
        required_keys=["credentials_path"],
        optional_keys=[
            "gcs_bucket",
            "image_rpm",
            "video_rpm",
            "request_gap",
            "image_max_workers",
            "video_max_workers",
            "audio_max_workers",
        ],
        secret_keys=[],
    ),
    "ark": ProviderMeta(
        display_name="火山方舟",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        default_base_url=ARK_BASE_URL,
    ),
    "ark-agent-plan": ProviderMeta(
        display_name="火山方舟 Agent Plan",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers", "audio_max_workers"],
        secret_keys=["api_key"],
        default_base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers", "audio_max_workers"],
        secret_keys=["api_key"],
    ),
    "openai": ProviderMeta(
        display_name="OpenAI",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
    ),
    "vidu": ProviderMeta(
        display_name="Vidu",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
    ),
    "dashscope": ProviderMeta(
        display_name="阿里百炼",
        required_keys=["api_key"],
        # wan3_base_url：万相 3.0 走独立 maas 域名，且域名里含地域与 workspace，
        # 无法由通用 base_url 派生，故单列一键。仅 wan3.0-video 的请求消费它（见
        # lib/backends/video_backends/dashscope.py），留空则该模型回落通用 base_url。
        optional_keys=["base_url", "wan3_base_url", "image_max_workers", "video_max_workers", "audio_max_workers"],
        secret_keys=["api_key"],
        default_base_url=DASHSCOPE_BASE_URL,
    ),
    "minimax": ProviderMeta(
        display_name="MiniMax",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers", "audio_max_workers"],
        secret_keys=["api_key"],
        default_base_url=MINIMAX_BASE_URL,
    ),
    "kling": ProviderMeta(
        display_name="可灵 Kling",
        required_keys=["api_key", "access_key", "secret_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key", "access_key", "secret_key"],
        credential_groups=[["api_key"], ["access_key", "secret_key"]],
        default_base_url="https://api-beijing.klingai.com/v1",
    ),
    "agnes": ProviderMeta(
        display_name="Agnes",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        default_base_url=AGNES_BASE_URL,
        # Agnes 视频上游对并发敏感，出厂串行（默认 1）避免主动制造 503 Service busy；
        # 用户可经 video_max_workers 覆盖。其余 lane 未声明，走全局默认。
        default_concurrency={"video": 1},
    ),
}
