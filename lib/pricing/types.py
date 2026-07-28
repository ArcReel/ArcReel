"""定价数据类型：每种计费形状一个 frozen dataclass，``kind`` 字段为判别标签。

定价数据声明在 ``PROVIDER_REGISTRY`` 每个模型的 ``ModelInfo.pricing`` 上（单一真相源），
计算策略在 ``lib.pricing.strategies`` 按 ``kind`` 派发。两者职责分层：数据声明式、逻辑可单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 按字符计费的计价单位：费率均以「每万字符」声明（PerCharacter 与自定义供应商 audio 共用此口径）。
CHARACTERS_PER_PRICING_UNIT = 10_000


@dataclass(frozen=True)
class PerToken:
    """按 token 计费（文本，或任何 input/output token 双费率形态）。

    ``rates`` 形如 ``{model: {"input": 每百万输入价, "output": 每百万输出价}}``；
    未知 model 回落到 ``default_model``，再回落到零费率。
    """

    rates: dict[str, dict[str, float]]
    default_model: str
    currency: str
    kind: Literal["per_token"] = "per_token"


@dataclass(frozen=True)
class PerImageFlat:
    """按张计费，单价与分辨率无关。``rates`` 形如 ``{model: 每张价}``。"""

    rates: dict[str, float]
    default_model: str
    currency: str
    kind: Literal["per_image_flat"] = "per_image_flat"


@dataclass(frozen=True)
class PerImageByResolution:
    """按张计费，单价随分辨率档位变化。``rates`` 形如 ``{model: {分辨率: 每张价}}``。

    分辨率键以大写形态存储（``1K`` / ``2K`` / ``4K`` / ``512PX``），查表前对入参 ``.upper()``。
    """

    rates: dict[str, dict[str, float]]
    default_model: str
    currency: str
    kind: Literal["per_image_by_resolution"] = "per_image_by_resolution"


@dataclass(frozen=True)
class PerImageOpenAIToken:
    """OpenAI 图片计费：SDK 返回 usage 时按 token 计，否则按 (quality, size) 静态表兜底。

    - ``token_rates`` 形如 ``{model: {"image_in","image_out","text_in","text_out", ...}}``（每百万）。
    - ``fallback_rates`` 形如 ``{model: {(quality, size): 每张价}}``；仅用显式 ``size``，缺失即落
      默认 ``1024x1024`` 档（计费与输出尺寸解耦，不按比例反查 size，见 docs/adr/0011）。
    """

    token_rates: dict[str, dict[str, float]]
    fallback_rates: dict[str, dict[tuple[str, str], float]]
    default_model: str
    currency: str
    kind: Literal["per_image_openai_token"] = "per_image_openai_token"


@dataclass(frozen=True)
class PerSecondMatrix:
    """视频按秒计费，单价由 ``dimensions`` 控制的维度组合查表得出。

    ``dimensions``：
    - ``resolution_audio`` — 键 ``(分辨率小写, 是否生成音频)``，缺失回落 ``("1080p", True)``。
    - ``resolution_only`` — 键 ``(分辨率, None)``，缺失回落 ``("720p", None)`` 再回落 0.0。
    - ``flat`` — 单一费率，键 ``("", None)``，与分辨率/音频无关。
    """

    rates: dict[str, dict[tuple[str, bool | None], float]]
    default_model: str
    dimensions: Literal["resolution_audio", "resolution_only", "flat"]
    currency: str
    kind: Literal["per_second_matrix"] = "per_second_matrix"


@dataclass(frozen=True)
class PerVideoBucket:
    """视频按 (分辨率, 时长秒) 离散档计费：金额=查表 flat_price，与秒数不成比例。

    适用 MiniMax 海螺等「按 (分辨率, 时长) 定价点」而非线性每秒的视频模型。

    - ``rates`` 形如 ``{model: {(分辨率小写, 时长秒): flat_price}}``，分辨率键以小写存储，
      查表前对入参 ``.lower()``。
    - 未命中 (resolution, duration) 档时回落该 model「最接近的档」：先在同分辨率档内取
      |时长差| 最小者，无同分辨率档再在全部档内取最近，并 WARN（档表与请求漂移的可观测信号）。
    """

    rates: dict[str, dict[tuple[str, int], float]]
    default_model: str
    currency: str
    kind: Literal["per_video_bucket"] = "per_video_bucket"


@dataclass(frozen=True)
class PerSecondTiered:
    """视频按「质量档 × 是否有声」定 ¥/s（再 × 时长），比 ``PerSecondMatrix`` 多一层质量档。

    可灵 Kling 视频按维度组合计费：档位 ∈ ``{std, pro, 4k}``，再叠加是否有声。与 Veo 的
    ``per_second_matrix``（仅 resolution × audio）不同，质量档（service_tier）是独立维度。

    - ``rates`` 形如 ``{model: {(档位, 是否有声): 每秒价}}``。
    - 档位派生：``resolution.lower()=="4k"`` → ``"4k"``，否则取 ``service_tier``
      （``service_tier ∈ {std, pro}``，``"default"`` → ``"std"``）。4k 档忽略音频维度
      （两个 audio 键同价）。
    - 金额 = ``rate × duration_seconds``；未命中档回落该 model 的 ``std`` 档并 WARN
      （档表与请求漂移的可观测信号）。
    """

    rates: dict[str, dict[tuple[str, bool], float]]
    default_model: str
    currency: str = "CNY"
    kind: Literal["per_second_tiered"] = "per_second_tiered"


@dataclass(frozen=True)
class PerTokenVideo:
    """视频按 token 计费（按 ``(service_tier, 是否生成音频)`` 查每百万 token 价）。

    ``rates`` 形如 ``{model: {(service_tier, generate_audio): 每百万 token 价}}``；
    缺失键回落 ``("default", True)``，再回落 16.00。
    """

    rates: dict[str, dict[tuple[str, bool], float]]
    default_model: str
    currency: str = "CNY"
    kind: Literal["per_token_video"] = "per_token_video"


@dataclass(frozen=True)
class PerCharacter:
    """按字符计费（TTS），费率单位为每万字符。

    - ``rates``：``{model_id: 每万字符价}``，价格以 ``currency`` 计
      （如 ``{"qwen3-tts-flash": 0.8}`` 表示每万字符 0.8）。
    - ``default_model``：``rates`` 未命中请求 model 时回落到此 model 的费率。
    - ``currency``：费率币种（如 ``"CNY"``）。
    """

    rates: dict[str, float]
    default_model: str
    currency: str
    kind: Literal["per_character"] = "per_character"


@dataclass(frozen=True)
class ViduDelegate:
    """委托标记：实际费率在 ``lib.vidu_shared.calculate_vidu_cost``（依赖响应 credits）。

    本类型不携带费率，仅作为 union 成员承载币种，使空列表等分支可统一读 ``currency``，
    无需对 provider 名做硬编码判断。
    """

    currency: str = "CNY"
    kind: Literal["vidu_delegate"] = "vidu_delegate"


Pricing = (
    PerToken
    | PerImageFlat
    | PerImageByResolution
    | PerImageOpenAIToken
    | PerSecondMatrix
    | PerSecondTiered
    | PerVideoBucket
    | PerTokenVideo
    | PerCharacter
    | ViduDelegate
)
