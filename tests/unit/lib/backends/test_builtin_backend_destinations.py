"""内置 HTTP 式 backend 的提交与轮询请求经产物下载同一道出站目的地校验。

这些 backend 的 base URL 可由管理员配置（内置供应商的 ``base_url`` 设置、自定义供应商挂载的内置
端点），故提交与轮询的目的地与供应商回传的产物地址一样按不可信处置：落在链路本地或云元数据地址上
的请求在发出前被拦下，API Key 不会随请求出站。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest

from lib.backends.artifact_download_guard import ArtifactDestinationRejectedError
from lib.backends.audio_backends.base import AudioSynthesisRequest
from lib.backends.audio_backends.dashscope import DashScopeAudioBackend
from lib.backends.image_backends.agnes import AgnesImageBackend
from lib.backends.image_backends.base import ImageGenerationRequest
from lib.backends.image_backends.dashscope import DashScopeImageBackend
from lib.backends.image_backends.kling import KlingImageBackend
from lib.backends.image_backends.minimax import MiniMaxImageBackend
from lib.backends.image_backends.vidu import ViduImageBackend
from lib.backends.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.agnes import AgnesVideoBackend
from lib.backends.video_backends.dashscope import DashScopeVideoBackend
from lib.backends.video_backends.vidu import ViduVideoBackend
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http

METADATA_BASE_URL = "http://169.254.169.254"

Call = Callable[[Path], Awaitable[object]]


def _image(tmp_path: Path) -> ImageGenerationRequest:
    return ImageGenerationRequest(prompt="一只猫走过屋顶", output_path=tmp_path / "out.png")


def _video(tmp_path: Path) -> VideoGenerationRequest:
    return VideoGenerationRequest(prompt="一只猫走过屋顶", output_path=tmp_path / "out.mp4", duration_seconds=5)


_CALLS: dict[str, Call] = {
    "dashscope-audio": lambda tmp_path: DashScopeAudioBackend(api_key="k", base_url=METADATA_BASE_URL).synthesize(
        AudioSynthesisRequest(text="你好", output_path=tmp_path / "out.wav", voice="Cherry")
    ),
    "agnes-image": lambda tmp_path: AgnesImageBackend(api_key="k", base_url=METADATA_BASE_URL).generate(
        _image(tmp_path)
    ),
    "dashscope-image": lambda tmp_path: DashScopeImageBackend(api_key="k", base_url=METADATA_BASE_URL).generate(
        _image(tmp_path)
    ),
    "kling-image": lambda tmp_path: KlingImageBackend(
        auth_mode="bearer", api_key="k", base_url=f"{METADATA_BASE_URL}/v1"
    ).generate(_image(tmp_path)),
    "minimax-image": lambda tmp_path: MiniMaxImageBackend(api_key="k", base_url=METADATA_BASE_URL).generate(
        _image(tmp_path)
    ),
    "vidu-image": lambda tmp_path: ViduImageBackend(api_key="k", base_url=f"{METADATA_BASE_URL}/ent/v2").generate(
        _image(tmp_path)
    ),
    "agnes-video": lambda tmp_path: AgnesVideoBackend(api_key="k", base_url=METADATA_BASE_URL).generate(
        _video(tmp_path)
    ),
    "agnes-video-resume": lambda tmp_path: AgnesVideoBackend(api_key="k", base_url=METADATA_BASE_URL).resume_video(
        "job-1", _video(tmp_path)
    ),
    "dashscope-video": lambda tmp_path: DashScopeVideoBackend(api_key="k", base_url=METADATA_BASE_URL).generate(
        _video(tmp_path)
    ),
    "dashscope-video-resume": lambda tmp_path: DashScopeVideoBackend(
        api_key="k", base_url=METADATA_BASE_URL
    ).resume_video("job-1", _video(tmp_path)),
    "vidu-video": lambda tmp_path: ViduVideoBackend(api_key="k", base_url=f"{METADATA_BASE_URL}/ent/v2").generate(
        _video(tmp_path)
    ),
}


@pytest.mark.parametrize("call", _CALLS.values(), ids=_CALLS.keys())
async def test_a_metadata_base_url_is_refused_before_any_request_leaves(call: Call, tmp_path: Path):
    with capture_http() as router, bounded_poll_clock():
        sent = router.route().mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(ArtifactDestinationRejectedError):
            await call(tmp_path)

    assert sent.call_count == 0
