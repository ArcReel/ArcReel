from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from lib.backends.agnes_shared import AGNES_BASE_URL
from lib.backends.ark_shared import ARK_BASE_URL
from lib.backends.dashscope_shared import DASHSCOPE_BASE_URL
from lib.backends.minimax_shared import MINIMAX_BASE_URL

# 合法并发 lane 名，与 CapacityTable 的 image/video/audio 三条容量通道对齐。
_VALID_LANES = frozenset({"image", "video", "audio"})


@dataclass(frozen=True)
class ProviderMeta:
    """一个内置供应商的代码事实：显示名、凭证形态、默认地址与各条 lane 的出厂并发。

    供应商的模型清单、默认模型、模型能力与价格不在这里，经目录查询（``lib.catalog.query``）读取。
    """

    display_name: str
    description: str
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    default_base_url: str | None = None
    # 凭证「二选一」分组：非空时凭证表单按「满足任一组」校验（组内字段全填），而非默认的
    # 「required_keys ∩ secret_keys 全填」。目前仅可灵需要（api_key 单键 / access_key+secret_key
    # 双键二选一）；空列表（默认）保持原语义不变，由 router 按
    # [[全部 secret 字段]] 回退成单一必填组。声明的每个 key 须是 required_keys ∩ secret_keys 的
    # 子集，在 __post_init__ 校验，misconfig fail-fast。
    credential_groups: list[list[str]] = field(default_factory=list)
    # 按 lane（image / video / audio）声明的出厂默认并发上限；某条 lane 未列入则该 lane
    # 走全局默认。容量回退优先级：用户配置值 > 此处声明默认 > 全局默认。声明给不支持的
    # lane 无害——_lane_limits 会按 media_types 把不支持的 lane 投影为 0。键名与上限值在
    # __post_init__ 校验。
    default_concurrency: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # default_concurrency 是注册表静态声明：拼错的 lane key 会被静默忽略、该 lane 漂回
        # 全局默认（限并发失效），非正整数上限则在投影后变成 capacity<=0、把支持的 lane 误判为
        # 不支持。这两类都是 import 期就该 fail-fast 的作者笔误，而非到 worker 装载容量表时才暴露。
        for lane, limit in self.default_concurrency.items():
            if lane not in _VALID_LANES:
                raise ValueError(
                    f"{self.display_name} default_concurrency 含未知 lane {lane!r}，合法值：{sorted(_VALID_LANES)}"
                )
            # 注册表是手写静态数据，运行时 Python 不强制注解；用精确类型判定兜住作者笔误。
            # bool 是 int 子类，isinstance(True, int) 为真会把 True 当并发 1 静默放行；字符串
            # "1"、浮点 3.0 同样违反声明类型。type() is int 把这几类一并挡在 import 期。
            if type(limit) is not int or limit < 1:
                raise ValueError(f"{self.display_name} default_concurrency[{lane!r}] 必须是 >=1 的整数，得到 {limit!r}")
        if self.credential_groups:
            allowed = set(self.required_keys) & set(self.secret_keys)
            covered: set[str] = set()
            for group in self.credential_groups:
                # 空分组会被 unknown=set() 判定为合法、前端 group.every(...) 对空数组恒真，
                # 误判该分组"已满足"——同属 import 期该拦的作者笔误。
                if not group:
                    raise ValueError(f"{self.display_name} credential_groups 含空分组")
                unknown = set(group) - allowed
                if unknown:
                    raise ValueError(
                        f"{self.display_name} credential_groups 含未登记为 required∩secret 的 key {sorted(unknown)}"
                    )
                covered |= set(group)
            # 未被任何分组覆盖的 key 既不受旧版"全部必填"约束，也不受任何分组约束——
            # 声明了分组却漏登记某个 key，前端会误判该 key 为可选。
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
        description="Google AI Studio 提供 Gemini 系列模型，支持图片、视频和语音生成，适合快速原型和个人项目。",
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
        description="Google Cloud Vertex AI 企业级平台，支持 Gemini 和 Imagen 模型，提供更高配额和音频生成能力。",
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
        description="字节跳动火山方舟 AI 平台，支持 Seedance 视频生成和 Seedream 图片生成，具备音频生成和种子控制能力。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        default_base_url=ARK_BASE_URL,
    ),
    "ark-agent-plan": ProviderMeta(
        display_name="火山方舟 Agent Plan",
        description="火山方舟 Agent Plan 套餐，聚合豆包及多家主流大模型，覆盖文本、图片与视频生成。",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        default_base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        description="xAI Grok 模型，支持视频和图片生成。",
        required_keys=["api_key"],
        optional_keys=["video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
    ),
    "openai": ProviderMeta(
        display_name="OpenAI",
        description="OpenAI 官方平台，支持 GPT-5.5 / GPT-5.4 文本与 GPT Image 2 图片生成。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers"],
        secret_keys=["api_key"],
    ),
    "vidu": ProviderMeta(
        display_name="Vidu",
        description="生数科技 Vidu 视频生成平台，支持文生视频、图生视频、首尾帧、参考生视频与参考生图，仅图片与视频能力。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
    ),
    "dashscope": ProviderMeta(
        display_name="阿里百炼",
        description="阿里云百炼（Model Studio）全模态平台，支持 Qwen 文本、Qwen-Image / 万相图像与 HappyHorse / 万相视频（含参考生视频）。",
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
        description="MiniMax（海螺）多模态平台，提供文本、图片、视频与语音生成。默认连接国内站，海外可将 base_url 切换到国际站。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers", "audio_max_workers"],
        secret_keys=["api_key"],
        default_base_url=MINIMAX_BASE_URL,
    ),
    "kling": ProviderMeta(
        display_name="可灵 Kling",
        description=(
            "快手可灵 Kling 视频与图像生成平台。API Key（Bearer）适用于全部模型；"
            "Access Key + Secret Key（JWT）仅适用于 3.0 及更早模型，二者二选一，同时填写时 API Key 优先。"
        ),
        # 首个需要两个 secret 字符串的内置供应商（JWT HS256 鉴权），凭证按 registry key 名
        # 存入 provider_credential 的 access_key / secret_key 定型列（见 ADR 0037）。api_key 复用
        # 该表已有的 api_key 定型列（其余 provider 的静态 Bearer key 同列），无需新迁移。
        required_keys=["api_key", "access_key", "secret_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key", "access_key", "secret_key"],
        # api_key 单键 / access_key+secret_key 双键二选一（官方 API Key 鉴权全模型可用，AK/SK JWT
        # 仅 3.0 及更早模型）；同时填写时 backend_assembly 按 api_key 优先分派。
        credential_groups=[["api_key"], ["access_key", "secret_key"]],
        # 国内调用域名官方已由 api.klingai.com 迁移至 api-beijing.klingai.com（旧域名仍可用，
        # 未强制下线）；仅影响未显式配置 base_url 的新用户，存量显式配置不受影响。
        default_base_url="https://api-beijing.klingai.com/v1",
    ),
    "agnes": ProviderMeta(
        display_name="Agnes",
        description="Agnes 多模态平台（OpenAI 风格），使用 Bearer API Key 鉴权；当前支持图像 / 文本 / 视频生成。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        default_base_url=AGNES_BASE_URL,
        # Agnes 视频上游对并发敏感，出厂串行（默认 1）避免主动制造 503 Service busy；
        # 用户可经 video_max_workers 覆盖。其余 lane 未声明，走全局默认。
        default_concurrency={"video": 1},
    ),
}
