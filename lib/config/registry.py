from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from lib.backends.agnes_shared import AGNES_BASE_URL
from lib.backends.ark_shared import ARK_BASE_URL
from lib.backends.dashscope_shared import DASHSCOPE_BASE_URL
from lib.backends.minimax_shared import MINIMAX_BASE_URL

# Valid concurrency lanes match CapacityTable image/video/audio.
_VALID_LANES = frozenset({"image", "video", "audio"})


@dataclass(frozen=True)
class ProviderMeta:
    """Built-in provider code facts: display names, credential shapes, default hosts, factory concurrency per lane.

    Manifests/default models/capabilities/prices live outside registries, read through ``lib.catalog.query``.
    """

    display_name: str
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    default_base_url: str | None = None
    # Alternative credential groups: nonempty groups validate any fully filled group, replacing default
    # required_keys∩secret_keys all-filled. Currently Kling only: api_key versus access_key+secret_key.
    # Empty default lists preserve old behavior, routers falling back to
    # one group of all secret fields. Every declared key must belong to required_keys∩secret_keys,
    # checked by __post_init__ for fail-fast misconfiguration.
    credential_groups: list[list[str]] = field(default_factory=list)
    # Factory lane concurrency image/video/audio; absent lanes use global defaults.
    # Priority: user settings>declared defaults>global defaults. Declaring unsupported lanes
    # is harmless because _lane_limits projects them zero by media_types. __post_init__
    # validates names/limits.
    default_concurrency: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Static default_concurrency misspelled lanes would silently use globals, losing limits;
        # nonpositive caps project capacity<=0, falsely declaring supported lanes unsupported.
        # Fail author mistakes at import, never defer until worker capacity loading.
        for lane, limit in self.default_concurrency.items():
            if lane not in _VALID_LANES:
                raise ValueError(
                    f"{self.display_name} default_concurrency 含未知 lane {lane!r}，合法值：{sorted(_VALID_LANES)}"
                )
            # Handwritten Python registry data lacks runtime annotation enforcement; exact types catch mistakes.
            # bool subclasses int, so isinstance(True,int) would admit concurrency 1; strings
            # "1"/float 3.0 likewise violate declarations. type() is int rejects all at import.
            if type(limit) is not int or limit < 1:
                raise ValueError(f"{self.display_name} default_concurrency[{lane!r}] 必须是 >=1 的整数，得到 {limit!r}")
        if self.credential_groups:
            allowed = set(self.required_keys) & set(self.secret_keys)
            covered: set[str] = set()
            for group in self.credential_groups:
                # Empty groups pass unknown=set() and frontend group.every(...) trivially,
                # falsely satisfied; reject author mistakes at import.
                if not group:
                    raise ValueError(f"{self.display_name} credential_groups 含空分组")
                unknown = set(group) - allowed
                if unknown:
                    raise ValueError(
                        f"{self.display_name} credential_groups 含未登记为 required∩secret 的 key {sorted(unknown)}"
                    )
                covered |= set(group)
            # Keys uncovered by groups escape both old all-required and group rules,
            # making omitted declarations falsely optional in frontend.
            if covered != allowed:
                raise ValueError(f"{self.display_name} credential_groups 未覆盖 {sorted(allowed - covered)}")

    def fully_covered_credential_groups(self, values: Mapping[str, str | None]) -> list[list[str]]:
        """Credential groups fully covered by nonempty ``values``.

        Drives create/update group switching; most providers declare no credential_groups,
        returning empty lists to preserve no-group-switch handling.
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
        optional_keys=["video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        default_base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
    ),
    "openai": ProviderMeta(
        display_name="OpenAI",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers"],
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
        # wan3_base_url: separate Wan 3.0 MaaS hosts include regions/workspaces,
        # impossible to derive from generic base_url. Only wan3.0-video requests consume it,
        # lib/backends/video_backends/dashscope.py; unset falls back to generic bases.
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
        # First built-in requiring two secrets for HS256 JWT; registry key names map to
        # provider_credential typed access_key/secret_key columns, ADR 0037. api_key reuses
        # existing static Bearer columns, no new migrations.
        required_keys=["api_key", "access_key", "secret_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key", "access_key", "secret_key"],
        # Choose api_key or access_key+secret_key; API Keys cover all models, AK/SK JWT
        # only 3.0 and earlier. backend_assembly prefers api_key when both present.
        credential_groups=[["api_key"], ["access_key", "secret_key"]],
        # Domestic official hosts moved api.klingai.com→api-beijing.klingai.com; old hosts remain available
        # without forced removal. Only new unconfigured-base users change; explicit existing hosts remain.
        default_base_url="https://api-beijing.klingai.com/v1",
    ),
    "agnes": ProviderMeta(
        display_name="Agnes",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        default_base_url=AGNES_BASE_URL,
        # Agnes video concurrency-sensitive upstream defaults serial 1, avoiding 503 Service busy;
        # video_max_workers overrides; other lanes undeclared, using globals.
        default_concurrency={"video": 1},
    ),
}
