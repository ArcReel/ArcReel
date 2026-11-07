"""Video-channel measurements reported under the usage contract (docs/adr/0100).

Each built-in video channel provides static ``metered_usage(model, request, result=None)``: usage measurements and
selection dimensions. Dimensions come from actual provider request fields, using the same transformations as request construction
(docs/adr/0056). Omit unsent dimensions, letting price-table defaults supply them.

- ``result`` = ``None`` means presubmission estimation: use request facts only. Converted measurements, such as Ark video tokens,
  come from channel estimators; omit when uncomputable, leaving estimates unpriced.
- With ``result``, settle using provider-reported output seconds/audio; provider-reported usage such as Ark video tokens
  overrides estimates.

Report count measurements faithfully (output seconds, reference images, input-video seconds, API calls); absent price-table items are not billed.
Likewise, dimensions do not affect price lookup when the relevant price-table measurement is not tiered by them.
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
    """Assemble one video request's usage from channel-provided request fields.

    ``seconds`` is sent duration (also the duration tier); ``resolution`` is sent resolution tier, ``None`` when unsent;
    ``audio`` describes output audio under channel semantics, ``None`` if channels cannot express it; ``reference_images`` counts sent
    reference images, defaulting to request reference count; ``reference_to_video`` defaults to whether references were sent; ``video_tokens``
    is channel-estimated or provider-reported video tokens; ``dimensions`` carries other channel-specific dimensions.
    Settlement takes output seconds/audio from ``result``; nonpositive or above-``MAX_BILLED_DURATION_SECONDS`` reported durations fall back to sent duration,
    matching persisted ``api_calls.duration_seconds`` semantics.
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
        # Built-in channels never send input videos.
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
