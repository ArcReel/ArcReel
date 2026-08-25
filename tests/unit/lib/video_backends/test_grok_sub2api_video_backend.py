"""Sub2API Grok video backend contract tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.video_backends.base import VideoGenerationRequest

pytestmark = pytest.mark.unit


def test_build_request_body_preserves_configured_model_and_image_to_video_input(tmp_path: Path) -> None:
    from lib.video_backends.grok_sub2api import build_request_body

    image = tmp_path / "still.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nsource")
    request = VideoGenerationRequest(
        prompt="The cat slowly raises its head",
        output_path=tmp_path / "out.mp4",
        duration_seconds=5,
        aspect_ratio="16:9",
        resolution="720p",
        start_image=image,
    )

    body = build_request_body("grok-imagine-video-1.5", request)
    assert body["model"] == "grok-imagine-video-1.5"
    assert body["prompt"] == "The cat slowly raises its head"
    assert body["duration"] == 5
    assert body["aspect_ratio"] == "16:9"
    assert body["resolution"] == "720p"
    assert set(body) == {"model", "prompt", "duration", "aspect_ratio", "resolution", "image"}
    image_body = body["image"]
    assert isinstance(image_body, dict)
    image_url = image_body.get("url")
    assert isinstance(image_url, str)
    assert image_url.startswith("data:image/png;base64,")


def test_build_request_body_rejects_missing_input_image_before_submit(tmp_path: Path) -> None:
    from lib.video_backends.grok_sub2api import build_request_body

    request = VideoGenerationRequest(
        prompt="animate",
        output_path=tmp_path / "out.mp4",
        start_image=tmp_path / "missing.png",
    )

    with pytest.raises(ValueError, match="start_image"):
        build_request_body("grok-imagine-video-1.5", request)


def test_build_request_body_rejects_empty_prompt_before_submit(tmp_path: Path) -> None:
    from lib.video_backends.grok_sub2api import build_request_body

    request = VideoGenerationRequest(prompt="  ", output_path=tmp_path / "out.mp4")

    with pytest.raises(ValueError, match="prompt"):
        build_request_body("grok-imagine-video", request)


@pytest.mark.parametrize(
    "field",
    ["end_image", "reference_images", "reference_audio_files"],
)
def test_build_request_body_rejects_unsupported_inputs(tmp_path: Path, field: str) -> None:
    from lib.video_backends.grok_sub2api import build_request_body

    request = VideoGenerationRequest(prompt="animate", output_path=tmp_path / "out.mp4")
    if field == "end_image":
        request.end_image = Path("end.png")
    elif field == "reference_images":
        request.reference_images = [Path("reference.png")]
    else:
        request.reference_audio_files = [Path("voice.wav")]

    with pytest.raises(ValueError, match=field):
        build_request_body("grok-imagine-video-1.5", request)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://sub2api.example", "https://sub2api.example/v1"),
        ("https://sub2api.example/v1/", "https://sub2api.example/v1"),
    ],
)
def test_normalize_api_root_targets_sub2api_v1(base_url: str, expected: str) -> None:
    from lib.video_backends.grok_sub2api import normalize_api_root

    assert normalize_api_root(base_url) == expected


def _response(status_code: int, *, json: dict | None = None, content: bytes = b"") -> httpx.Response:
    request = httpx.Request("GET", "https://sub2api.example/v1/videos/generations")
    if json is not None:
        return httpx.Response(status_code, json=json, request=request)
    return httpx.Response(status_code, content=content, headers={"content-type": "video/mp4"}, request=request)


def _client(*, post: object, get: object) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(
        side_effect=post if isinstance(post, list) else None, return_value=None if isinstance(post, list) else post
    )
    client.get = AsyncMock(
        side_effect=get if isinstance(get, list) else None, return_value=None if isinstance(get, list) else get
    )
    return client


@pytest.mark.asyncio
async def test_generate_persists_request_id_before_status_and_content_fetch(tmp_path: Path) -> None:
    from lib.custom_provider.backends import CustomVideoBackend
    from lib.video_backends.grok_sub2api import GrokSub2APIVideoBackend

    events: list[str] = []
    client = _client(
        post=_response(200, json={"request_id": "video-123"}),
        get=[
            _response(200, json={"status": "done", "video": {"duration": 5}}),
            _response(200, content=b"mp4"),
        ],
    )
    client.post.side_effect = lambda *args, **kwargs: (
        events.append("post") or _response(200, json={"request_id": "video-123"})
    )

    async def _persist(*args, **kwargs) -> None:
        events.append("persist")

    persist = AsyncMock(side_effect=_persist)
    original_get = client.get

    async def _get(url: str, *args, **kwargs):
        events.append("content" if url.endswith("/content") else "status")
        return await original_get(url, *args, **kwargs)

    client.get = AsyncMock(side_effect=_get)

    with (
        patch("lib.video_backends.grok_sub2api.httpx.AsyncClient", return_value=client),
        patch("lib.video_backends.base.persist_provider_job_id", persist),
    ):
        backend = CustomVideoBackend(
            provider_id="custom-42",
            model="grok-imagine-video-1.5",
            endpoint="grok-sub2api-video",
            delegate=GrokSub2APIVideoBackend(
                api_key="sub2api-test-key",
                base_url="https://sub2api.example",
                model="grok-imagine-video-1.5",
                poll_interval_seconds=0.0,
            ),
        )
        image = tmp_path / "still.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nsource")
        result = await backend.generate(
            VideoGenerationRequest(
                prompt="animate",
                output_path=tmp_path / "out.mp4",
                duration_seconds=5,
                start_image=image,
                task_id="task-1",
            )
        )

    assert events == ["post", "persist", "status", "content"]
    assert result.task_id == "video-123"
    assert result.video_path.read_bytes() == b"mp4"
    persist.assert_awaited_once_with(
        "task-1",
        "video-123",
        provider="grok",
        endpoint="grok-sub2api-video",
        base_url="https://sub2api.example/v1",
    )
    post_call = client.post.await_args
    assert post_call is not None
    assert post_call.args[0] == "https://sub2api.example/v1/videos/generations"
    assert post_call.kwargs["headers"]["Authorization"] == "Bearer sub2api-test-key"


@pytest.mark.asyncio
async def test_generate_never_retries_an_ambiguous_create(tmp_path: Path) -> None:
    from lib.video_backends.base import AmbiguousSubmitError
    from lib.video_backends.grok_sub2api import GrokSub2APIVideoBackend

    request = httpx.Request("POST", "https://sub2api.example/v1/videos/generations")
    client = _client(post=_response(200, json={}), get=[])
    client.post.side_effect = httpx.ReadTimeout("response lost", request=request)

    with patch("lib.video_backends.grok_sub2api.httpx.AsyncClient", return_value=client):
        backend = GrokSub2APIVideoBackend(
            api_key="sub2api-test-key",
            base_url="https://sub2api.example",
        )
        with pytest.raises(AmbiguousSubmitError, match="create_ambiguous"):
            await backend.generate(VideoGenerationRequest(prompt="animate", output_path=tmp_path / "out.mp4"))

    assert client.post.await_count == 1
    assert client.get.await_count == 0


@pytest.mark.asyncio
async def test_resume_only_reads_exact_status_and_content_urls(tmp_path: Path) -> None:
    from lib.video_backends.grok_sub2api import GrokSub2APIVideoBackend

    client = _client(
        post=_response(200, json={}),
        get=[
            _response(200, json={"status": "done", "video": {"duration": 6}}),
            _response(200, content=b"resumed"),
        ],
    )
    with patch("lib.video_backends.grok_sub2api.httpx.AsyncClient", return_value=client):
        backend = GrokSub2APIVideoBackend(
            api_key="sub2api-test-key",
            base_url="https://sub2api.example",
            poll_interval_seconds=0.0,
        )
        result = await backend.resume_video(
            "video/id",
            VideoGenerationRequest(prompt="ignored", output_path=tmp_path / "resumed.mp4"),
        )

    client.post.assert_not_awaited()
    assert [call.args[0] for call in client.get.await_args_list] == [
        "https://sub2api.example/v1/videos/generations/video%2Fid",
        "https://sub2api.example/v1/videos/generations/video%2Fid/content",
    ]
    assert result.task_id == "video/id"
    assert result.duration_seconds == 6
    assert result.video_path.read_bytes() == b"resumed"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"request_id": "request", "id": "id", "task_id": "task"}, "request"),
        ({"data": {"request_id": "data-request", "id": "data-id"}}, "data-request"),
        ({"video": {"id": "video-id"}, "task_id": "task"}, "video-id"),
        ({"data": {"task_id": "data-task"}}, "data-task"),
    ],
)
def test_request_id_matches_sub2api_forwarding_precedence(payload: dict, expected: str) -> None:
    from lib.video_backends.grok_sub2api import _request_id

    assert _request_id(payload) == expected


@pytest.mark.asyncio
async def test_resume_maps_not_found_to_expired_without_retry(tmp_path: Path) -> None:
    from lib.video_backends.base import ResumeExpiredError
    from lib.video_backends.grok_sub2api import GrokSub2APIVideoBackend

    client = _client(post=_response(200, json={}), get=_response(404, json={"error": "not found"}))
    with patch("lib.video_backends.grok_sub2api.httpx.AsyncClient", return_value=client):
        backend = GrokSub2APIVideoBackend(
            api_key="sub2api-test-key",
            base_url="https://sub2api.example",
            max_wait_seconds=0.0,
        )
        with pytest.raises(ResumeExpiredError) as exc_info:
            await backend.resume_video(
                "missing-task",
                VideoGenerationRequest(prompt="ignored", output_path=tmp_path / "out.mp4"),
            )

    assert exc_info.value.job_id == "missing-task"
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_unknown_status_fails_loud_instead_of_polling_forever(tmp_path: Path) -> None:
    from lib.video_backends.grok_sub2api import GrokSub2APIVideoBackend

    client = _client(post=_response(200, json={}), get=_response(200, json={"status": "mystery"}))
    with patch("lib.video_backends.grok_sub2api.httpx.AsyncClient", return_value=client):
        backend = GrokSub2APIVideoBackend(
            api_key="sub2api-test-key",
            base_url="https://sub2api.example",
            max_wait_seconds=0.0,
        )
        with pytest.raises(ValueError, match="mystery"):
            await backend.resume_video(
                "video-123",
                VideoGenerationRequest(prompt="ignored", output_path=tmp_path / "out.mp4"),
            )


@pytest.mark.asyncio
async def test_empty_content_never_replaces_output(tmp_path: Path) -> None:
    from lib.video_backends.grok_sub2api import GrokSub2APIVideoBackend

    output = tmp_path / "out.mp4"
    output.write_bytes(b"existing")
    client = _client(
        post=_response(200, json={}),
        get=[
            _response(200, json={"status": "done"}),
            _response(200, content=b""),
        ],
    )
    with patch("lib.video_backends.grok_sub2api.httpx.AsyncClient", return_value=client):
        backend = GrokSub2APIVideoBackend(
            api_key="sub2api-test-key",
            base_url="https://sub2api.example",
        )
        with pytest.raises(ValueError, match="empty"):
            await backend.resume_video(
                "video-123",
                VideoGenerationRequest(prompt="ignored", output_path=output),
            )

    assert output.read_bytes() == b"existing"
