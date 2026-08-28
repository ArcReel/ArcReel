from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY, infer_endpoint
from lib.video_backends.base import VideoCapabilityError, VideoGenerationRequest
from lib.video_frame_slots import gate_video_request
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, request_json

PNG_START = b"\x89PNG\r\n\x1a\nSTART"
PNG_END = b"\x89PNG\r\n\x1a\nEND"
PNG_REF1 = b"\x89PNG\r\n\x1a\nREF1"
PNG_REF2 = b"\x89PNG\r\n\x1a\nREF2"
WAV = b"RIFF\x00\x00\x00\x00WAVEA"
START_URI = "data:image/png;base64,iVBORw0KGgpTVEFSVA=="
END_URI = "data:image/png;base64,iVBORw0KGgpFTkQ="
REF1_URI = "data:image/png;base64,iVBORw0KGgpSRUYx"
REF2_URI = "data:image/png;base64,iVBORw0KGgpSRUYy"
AUDIO_URI = "data:audio/x-wav;base64,UklGRgAAAABXQVZFQQ=="


def _hailuo_body(*, model: str = "MiniMax-Hailuo-2.3", resolution: str = "768P", image: str | None = None) -> dict:
    body = {"model": model, "prompt": "a cat", "duration": 6, "resolution": resolution}
    if image:
        body["first_frame_image"] = image
    return body


def _h3_body(*content: dict, ratio: str = "16:9") -> dict:
    return {
        "model": "MiniMax-H3",
        "content": [{"type": "text", "text": "a cat"}, *content],
        "resolution": "768P",
        "duration": 6,
        "ratio": ratio,
    }


def _newapi_body(*, image: str | None = None) -> dict:
    body = {
        "model": "kling-v1",
        "prompt": "A cat running",
        "width": 720,
        "height": 1280,
        "duration": 5,
        "n": 1,
        "seed": 7,
    }
    if image:
        body["image"] = image
    return body


def _v2_body(**extra: object) -> dict:
    return {
        "model": "seedance-1.0",
        "prompt": "a cat",
        "duration": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "seed": 42,
        **extra,
    }


CASES = [
    pytest.param(
        "minimax-hailuo-v1",
        "MiniMax-Hailuo-2.3",
        {},
        {"task_id": "t-1"},
        [{"status": "Processing"}, {"status": "Success", "file_id": "file-9"}],
        _hailuo_body(),
        "https://cdn.test/mm/final.mp4",
        None,
        id="minimax/hailuo-t2v",
    ),
    pytest.param(
        "minimax-hailuo-v1",
        "MiniMax-Hailuo-2.3",
        {"start_image": True, "resolution": "1080p"},
        {"task_id": "t-1"},
        [{"status": "Success", "file_id": "file-9"}],
        _hailuo_body(resolution="1080P", image=START_URI),
        "https://cdn.test/mm/final.mp4",
        None,
        id="minimax/hailuo-i2v",
    ),
    pytest.param(
        # Fast 与 2.3 请求形状一致、能力不同（仅图生视频），故独立成键；这里钉住形状一致。
        "minimax-hailuo-v1-fast",
        "MiniMax-Hailuo-2.3-Fast",
        {"start_image": True},
        {"task_id": "t-1"},
        [{"status": "Success", "file_id": "file-9"}],
        _hailuo_body(model="MiniMax-Hailuo-2.3-Fast", image=START_URI),
        "https://cdn.test/mm/final.mp4",
        None,
        id="minimax/hailuo-fast-i2v",
    ),
    pytest.param(
        "minimax-s2v-01",
        "S2V-01",
        {"reference_images": 1},
        {"task_id": "t-1"},
        [{"status": "Success", "file_id": "file-9"}],
        {
            "model": "S2V-01",
            "prompt": "a cat",
            "subject_reference": [{"type": "character", "image": [REF1_URI]}],
        },
        "https://cdn.test/mm/final.mp4",
        None,
        id="minimax/s2v",
    ),
    pytest.param(
        "minimax-hailuo-v1",
        "MiniMax-Hailuo-2.3",
        {},
        {"task_id": "t-1"},
        [{"status": "Fail", "base_resp": {"status_code": 2013, "status_msg": "invalid params"}}],
        _hailuo_body(),
        None,
        "invalid params",
        id="minimax/hailuo-fail",
    ),
    pytest.param(
        "minimax-hailuo-v1",
        "MiniMax-Hailuo-2.3",
        {},
        {"task_id": "t-1"},
        [{"base_resp": {"status_code": 1004, "status_msg": "auth failed"}}],
        _hailuo_body(),
        None,
        "auth failed",
        id="minimax/hailuo-base-resp-error-poll",
    ),
    pytest.param(
        "minimax-h3",
        "MiniMax-H3",
        {},
        {"task_id": "t-1"},
        [
            {"task": {"status": "running"}},
            {"task": {"status": "succeeded", "content": {"url": "https://cdn.test/mm/h3.mp4"}}},
        ],
        _h3_body(),
        "https://cdn.test/mm/h3.mp4",
        None,
        id="minimax/h3-t2v",
    ),
    pytest.param(
        "minimax-h3",
        "MiniMax-H3",
        {"start_image": True, "end_image": True, "aspect_ratio": "adaptive"},
        {"task_id": "t-1"},
        [{"task": {"status": "succeeded", "content": {"url": "https://cdn.test/mm/h3.mp4"}}}],
        _h3_body(
            {"type": "image_url", "image_url": {"url": START_URI}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": END_URI}, "role": "last_frame"},
            ratio="adaptive",
        ),
        "https://cdn.test/mm/h3.mp4",
        None,
        id="minimax/h3-i2v-last",
    ),
    pytest.param(
        "minimax-h3",
        "MiniMax-H3",
        {"reference_images": 2, "reference_audio_files": True},
        {"task_id": "t-1"},
        [{"task": {"status": "succeeded", "content": {"url": "https://cdn.test/mm/h3.mp4"}}}],
        _h3_body(
            {"type": "image_url", "image_url": {"url": REF1_URI}, "role": "reference_image"},
            {"type": "image_url", "image_url": {"url": REF2_URI}, "role": "reference_image"},
            {"type": "audio_url", "audio_url": {"url": AUDIO_URI}, "role": "reference_audio"},
        ),
        "https://cdn.test/mm/h3.mp4",
        None,
        id="minimax/h3-r2v-audio",
    ),
    pytest.param(
        # 与内置 `_base_resp_error` 同口径：只有顶层 base_resp 的非零 status_code 判失败，
        # 轮询中的 task.status_msg 不得被当成业务失败。
        "minimax-h3",
        "MiniMax-H3",
        {},
        {"task_id": "t-1"},
        [
            {"task": {"status": "running", "status_msg": "rendering"}, "base_resp": {"status_code": 0}},
            {
                "task": {
                    "status": "succeeded",
                    "status_msg": "success",
                    "content": {"url": "https://cdn.test/mm/h3.mp4"},
                },
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        ],
        _h3_body(),
        "https://cdn.test/mm/h3.mp4",
        None,
        id="minimax/h3-progress-status-msg-is-not-failure",
    ),
    pytest.param(
        "minimax-h3",
        "MiniMax-H3",
        {},
        {"task_id": "t-1"},
        [{"base_resp": {"status_code": 1004, "status_msg": "auth failed"}}],
        _h3_body(),
        None,
        "auth failed",
        id="minimax/h3-base-resp-error-poll",
    ),
    pytest.param(
        "minimax-h3",
        "MiniMax-H3",
        {},
        {"task_id": "t-1"},
        [{"task": {"status": "failed", "error": "quota exhausted"}}],
        _h3_body(),
        None,
        "quota exhausted",
        id="minimax/h3-failed",
    ),
    pytest.param(
        "newapi-video",
        "kling-v1",
        {},
        {"task_id": "task-42"},
        [
            {"status": "in_progress"},
            {"status": "completed", "url": "https://cdn.test/na/out.mp4", "metadata": {"duration": 5, "seed": 0}},
        ],
        _newapi_body(),
        "https://cdn.test/na/out.mp4",
        None,
        id="newapi/t2v",
    ),
    pytest.param(
        "newapi-video",
        "kling-v1",
        {"start_image": True},
        {"task_id": "task-42"},
        [{"status": "completed", "url": "https://cdn.test/na/out.mp4", "metadata": {"duration": 5, "seed": 0}}],
        _newapi_body(image=START_URI),
        "https://cdn.test/na/out.mp4",
        None,
        id="newapi/i2v",
    ),
    pytest.param(
        "newapi-video",
        "kling-v1",
        {},
        {"task_id": "task-42"},
        [
            {
                "code": "success",
                "data": {
                    "status": "SUCCESS",
                    "result_url": "https://cdn.test/na/wrapped.mp4",
                    "metadata": {"duration": 8, "seed": 4242},
                },
            }
        ],
        _newapi_body(),
        "https://cdn.test/na/wrapped.mp4",
        None,
        id="newapi/wrapped",
    ),
    pytest.param(
        "newapi-video",
        "kling-v1",
        {},
        {"task_id": "task-42"},
        [{"status": "failed", "error": {"message": "upstream down"}}],
        _newapi_body(),
        None,
        "upstream down",
        id="newapi/failed",
    ),
    pytest.param(
        "newapi-video",
        "kling-v1",
        {},
        {"task_id": "task-42"},
        [{"status": "expired"}],
        _newapi_body(),
        None,
        "provider reported failure",
        id="newapi/expired",
    ),
    pytest.param(
        "v2-video-generations",
        "seedance-1.0",
        {},
        {"id": "gen-1"},
        [{"status": "generating"}, {"status": "completed", "video": {"url": "https://cdn.test/v2/v.mp4"}}],
        _v2_body(),
        "https://cdn.test/v2/v.mp4",
        None,
        id="v2/t2v",
    ),
    pytest.param(
        "v2-video-generations",
        "seedance-1.0",
        {"start_image": True, "end_image": True},
        {"id": "gen-1"},
        [{"status": "completed", "video": {"url": "https://cdn.test/v2/v.mp4"}}],
        _v2_body(image_url=START_URI, last_image_url=END_URI),
        "https://cdn.test/v2/v.mp4",
        None,
        id="v2/i2v-last",
    ),
    pytest.param(
        "v2-video-generations",
        "seedance-1.0",
        {"reference_images": 2},
        {"id": "gen-1"},
        [{"status": "completed", "video": {"url": "https://cdn.test/v2/v.mp4"}}],
        _v2_body(image_urls=[REF1_URI, REF2_URI]),
        "https://cdn.test/v2/v.mp4",
        None,
        id="v2/r2v",
    ),
    pytest.param(
        "v2-video-generations",
        "seedance-1.0",
        {},
        {"id": 123},
        [{"status": "completed", "url": "https://cdn.test/v2/n.mp4"}],
        _v2_body(),
        "https://cdn.test/v2/n.mp4",
        None,
        id="v2/int-id",
    ),
    pytest.param(
        "v2-video-generations",
        "seedance-1.0",
        {},
        {"generation_id": "vg_1"},
        [{"status": "error", "error": {"message": "boom"}}],
        _v2_body(),
        None,
        "boom",
        id="v2/failed",
    ),
]


@pytest.mark.parametrize(
    ("endpoint", "model", "overrides", "submit_response", "poll_responses", "expected_body", "video_url", "error"),
    CASES,
)
async def test_builtin_declarative_runtime_matches_python_backend_fixtures(
    tmp_path: Path,
    endpoint: str,
    model: str,
    overrides: dict,
    submit_response: dict,
    poll_responses: list[dict],
    expected_body: dict,
    video_url: str | None,
    error: str | None,
):
    assets = {
        "start.png": PNG_START,
        "end.png": PNG_END,
        "ref1.png": PNG_REF1,
        "ref2.png": PNG_REF2,
        "a.wav": WAV,
    }
    for name, content in assets.items():
        (tmp_path / name).write_bytes(content)
    base_url = (
        "https://x/v1"
        if endpoint == "newapi-video"
        else ("https://api.aimlapi.com/v1" if endpoint == "v2-video-generations" else "https://api.minimaxi.com/v1")
    )
    request_values = {
        "prompt": "A cat running" if endpoint == "newapi-video" else "a cat",
        "output_path": tmp_path / "out.mp4",
        "duration_seconds": 5 if endpoint in {"newapi-video", "v2-video-generations"} else 6,
        "aspect_ratio": "9:16" if endpoint == "newapi-video" else overrides.get("aspect_ratio", "16:9"),
        "resolution": overrides.get(
            "resolution", "720p" if endpoint in {"newapi-video", "v2-video-generations"} else "768p"
        ),
        "seed": 7 if endpoint == "newapi-video" else (42 if endpoint == "v2-video-generations" else None),
        "generate_audio": True,
        "start_image": tmp_path / "start.png" if overrides.get("start_image") else None,
        "end_image": tmp_path / "end.png" if overrides.get("end_image") else None,
        "reference_images": [tmp_path / "ref1.png", tmp_path / "ref2.png"][: overrides.get("reference_images", 0)]
        or None,
        "reference_audio_files": [tmp_path / "a.wav"] if overrides.get("reference_audio_files") else None,
    }
    definition = ENDPOINT_REGISTRY[endpoint].definition
    assert definition is not None
    backend = DeclarativeVideoBackend(
        api_key="K", base_url=base_url, model=model, definition=definition, provider=endpoint
    )

    with capture_http() as router, bounded_poll_clock():
        submit = router.post(url__regex=r"^https://(?:x|api\.aimlapi\.com|api\.minimaxi\.com)/.+").mock(
            return_value=httpx.Response(200, json=submit_response)
        )
        poll = router.get(
            url__regex=r"^https://(?:x|api\.aimlapi\.com|api\.minimaxi\.com)/.+(?:query|generations).*$"
        ).mock(side_effect=[httpx.Response(200, json=value) for value in poll_responses])
        if endpoint.startswith("minimax-") and endpoint != "minimax-h3" and video_url:
            router.get(url__regex=r"^https://api\.minimaxi\.com/v1/files/retrieve").mock(
                return_value=httpx.Response(200, json={"file": {"download_url": video_url}})
            )
        router.get(url__regex=r"^https://cdn\.test/").mock(return_value=httpx.Response(200, content=b"mp4"))

        if error:
            with pytest.raises(RuntimeError, match=error):
                await backend.generate(VideoGenerationRequest(**request_values))
        else:
            result = await backend.generate(VideoGenerationRequest(**request_values))
            assert result.video_uri == video_url
            assert result.video_path.read_bytes() == b"mp4"

    assert request_json(submit.calls.last.request) == expected_body
    assert submit.calls.last.request.headers["Authorization"] == "Bearer K"
    assert poll.calls.last.request.headers["Authorization"] == "Bearer K"


@pytest.mark.parametrize("base_url", ["https://x", "https://x/", "https://x/v1"])
async def test_newapi_reaches_v1_path_whether_or_not_the_stored_base_url_carries_it(tmp_path: Path, base_url: str):
    """存量 newapi 自定义供应商的 base_url 可带可不带 ``/v1``，收编后调用路径不得漂移。"""
    definition = ENDPOINT_REGISTRY["newapi-video"].definition
    assert definition is not None
    backend = DeclarativeVideoBackend(
        api_key="K", base_url=base_url, model="kling-v1", definition=definition, provider="newapi"
    )
    with capture_http() as router, bounded_poll_clock():
        submit = router.post("https://x/v1/video/generations").mock(
            return_value=httpx.Response(200, json={"task_id": "task-42"})
        )
        router.get("https://x/v1/video/generations/task-42").mock(
            return_value=httpx.Response(200, json={"status": "completed", "url": "https://cdn.test/na/out.mp4"})
        )
        router.get("https://cdn.test/na/out.mp4").mock(return_value=httpx.Response(200, content=b"mp4"))

        await backend.generate(
            VideoGenerationRequest(prompt="cat", output_path=tmp_path / "out.mp4", aspect_ratio="9:16")
        )

    assert submit.called


async def test_newapi_same_origin_download_sends_auth_instead_of_repeating_bare_401(tmp_path: Path):
    definition = ENDPOINT_REGISTRY["newapi-video"].definition
    assert definition is not None
    backend = DeclarativeVideoBackend(
        api_key="K", base_url="https://x/v1", model="kling-v1", definition=definition, provider="newapi"
    )
    with capture_http() as router, bounded_poll_clock():
        router.post("https://x/v1/video/generations").mock(
            return_value=httpx.Response(200, json={"task_id": "task-42"})
        )
        router.get("https://x/v1/video/generations/task-42").mock(
            return_value=httpx.Response(200, json={"status": "completed", "url": "https://x/v1/files/out.mp4"})
        )
        download = router.get("https://x/v1/files/out.mp4").mock(return_value=httpx.Response(200, content=b"mp4"))

        await backend.generate(
            VideoGenerationRequest(prompt="cat", output_path=tmp_path / "out.mp4", aspect_ratio="9:16")
        )

    assert download.calls.last.request.headers["Authorization"] == "Bearer K"


@pytest.mark.parametrize(
    ("endpoint", "base_url", "poll_url", "response"),
    [
        (
            "newapi-video",
            "https://x/v1",
            "https://x/v1/video/generations/job-old",
            {"status": "completed", "url": "https://cdn.test/resumed.mp4"},
        ),
        (
            "v2-video-generations",
            "https://api.aimlapi.com/v1",
            "https://api.aimlapi.com/v2/video/generations?generation_id=job-old",
            {"status": "completed", "url": "https://cdn.test/resumed.mp4"},
        ),
    ],
)
async def test_existing_newapi_and_v2_endpoint_keys_resume_in_flight_jobs(
    tmp_path: Path, endpoint: str, base_url: str, poll_url: str, response: dict
):
    definition = ENDPOINT_REGISTRY[endpoint].definition
    assert definition is not None
    backend = DeclarativeVideoBackend(
        api_key="K", base_url=base_url, model="model", definition=definition, provider=endpoint
    )
    with capture_http() as router, bounded_poll_clock():
        poll = router.get(poll_url).mock(return_value=httpx.Response(200, json=response))
        router.get("https://cdn.test/resumed.mp4").mock(return_value=httpx.Response(200, content=b"mp4"))

        result = await backend.resume_video("job-old", _request(tmp_path))

    assert result.video_path.read_bytes() == b"mp4"
    assert poll.called


def _request(tmp_path: Path) -> VideoGenerationRequest:
    return VideoGenerationRequest(prompt="frozen", output_path=tmp_path / "resumed.mp4")


@pytest.mark.parametrize(
    ("model", "endpoint"),
    [
        ("MiniMax-Hailuo-2.3", "minimax-hailuo-v1"),
        ("MiniMax-Hailuo-2.3-Fast", "minimax-hailuo-v1-fast"),
        ("S2V-01", "minimax-s2v-01"),
        ("MiniMax-H3", "minimax-h3"),
    ],
)
def test_minimax_models_route_to_their_own_endpoint(model: str, endpoint: str):
    """一份定义一种请求形状加一组能力：2.3-Fast 与 2.3 形状相同但仅图生视频，故各占一键。

    发现路由与 `backend_assembly.specs._minimax_video_endpoint` 的派发口径必须一致，否则会出现
    「发现落 Fast 键、派发却回落 2.3 键」的裂缝。
    """
    assert infer_endpoint(model, "openai") == endpoint


def test_hailuo_fast_text_only_request_is_refused_before_paying():
    caps = builtin_video_capabilities_for_model("minimax", "MiniMax-Hailuo-2.3-Fast")

    with pytest.raises(VideoCapabilityError) as caught:
        gate_video_request(caps=caps, provider="minimax", model="MiniMax-Hailuo-2.3-Fast")

    assert caught.value.code == "video_capability_missing_t2v"
    assert gate_video_request(caps=caps, provider="minimax", model="MiniMax-Hailuo-2.3-Fast", has_image=True) is None
