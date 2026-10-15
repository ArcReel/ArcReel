"""视频调用通道按用量契约上报的计量（docs/adr/0100）。

每个内置视频调用通道提供静态方法 ``metered_usage(model, request, result=None)``：给出这次请求的计量项用量与
选择维度。选择维度取自真正发给供应商的请求字段，与请求构造走同一个变换（docs/adr/0056），没发送的维度不写，
由价目表的默认值补上。

- ``result`` 为 ``None`` 时用于提交前预估：用量全部取自请求事实；需要换算的计量项（如方舟视频 token）由通道
  的估算函数给出，算不出就不上报，预估因此记为未定价。
- 给出 ``result`` 时用于结算：输出秒数与是否有声取供应商回报的结果，供应商回报的用量（如方舟视频 token）
  覆盖估算值。

计数类计量项如实上报（输出秒数、参考图张数、输入视频秒数、调用次数），价目表没有的那一项不计费；选择维度
同理，价目表的计量项不按它分档时不参与查价。
"""

from __future__ import annotations

from collections.abc import Mapping

from arcreel_market_core.video_backend_contract import VideoGenerationRequest, VideoGenerationResult
from lib.billing.metering import Usage
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS


def video_usage(
    request: VideoGenerationRequest,
    result: VideoGenerationResult | None,
    *,
    seconds: int,
    resolution: str | None,
    audio: bool | None,
    reference_images: int | None = None,
    reference_to_video: bool | None = None,
    video_tokens: float | None = None,
    dimensions: Mapping[str, object] | None = None,
) -> Usage:
    """按通道给出的请求字段组装一次视频请求的用量。

    ``seconds`` 是发出的时长（也作时长档）；``resolution`` 是发出的分辨率档位，没发送时为 ``None``；
    ``audio`` 是按通道口径成片是否有声，通道不表达有声与否时为 ``None``；``reference_images`` 是发出的参考图
    张数，默认取请求的参考图张数；``reference_to_video`` 默认按是否发出参考图判断；``video_tokens`` 是通道估算
    或供应商回报的视频 token 数；``dimensions`` 是通道特有的其他维度。
    结算时输出秒数与是否有声取 ``result``；回报的秒数非正或超出 ``MAX_BILLED_DURATION_SECONDS`` 时取发出的时长，
    与 ``api_calls.duration_seconds`` 的落库口径一致。
    """
    reported = result.duration_seconds if result is not None else None
    output_seconds = reported if reported is not None and 0 < reported <= MAX_BILLED_DURATION_SECONDS else seconds
    if result is not None and result.generate_audio is not None:
        audio = result.generate_audio
    if reference_images is None:
        reference_images = len(request.reference_images or ())
    quantities: dict[str, float] = {
        "output_seconds": output_seconds,
        "input_images": reference_images,
        # 内置调用通道都不发送输入视频。
        "input_video_seconds": 0,
        "requests": 1,
    }
    if video_tokens is not None:
        quantities["video_tokens"] = video_tokens
    dims: dict[str, object] = {
        "duration": seconds,
        "input_video": False,
        "reference_to_video": bool(reference_images) if reference_to_video is None else reference_to_video,
        **(dimensions or {}),
    }
    if resolution is not None:
        dims["resolution"] = resolution
    if audio is not None:
        dims["audio"] = audio
    return Usage(quantities=quantities, dimensions=dims)
