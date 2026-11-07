"""ArkVideoBackend unit tests with a mocked Ark SDK."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.video_backends.ark import (
    SEEDANCE_BACKENDS,
    ArkVideoBackend,
    Seedance1VideoBackend,
    Seedance2VideoBackend,
    Seedance25VideoBackend,
)
from lib.backends.video_frame_slots import FIRST_FRAME_ADAPTIVE_RATIO, resolve_first_frame_aspect_ratio
from tests.fakes import (
    blocking_file_read_gate,
    bounded_poll_clock,
    captured_ark_clients,
    captured_provider_job_ids,
)
from tests.http_capture import capture_http, only_request

# Ark-compatible gateways may still offer Seedance 1.5 Pro; keep backend request-construction coverage for it.
_SEEDANCE_1_5_PRO = "doubao-seedance-1-5-pro-251215"


@pytest.fixture
def mock_ark_client():
    client = MagicMock()
    client.content_generation = MagicMock()
    client.content_generation.tasks = MagicMock()
    return client


@pytest.fixture
def ark_backend(mock_ark_client):
    with patch("lib.backends.video_backends.ark.create_ark_client", return_value=mock_ark_client):
        b = Seedance1VideoBackend(
            api_key="test-ark-key",
            model=_SEEDANCE_1_5_PRO,
        )
    b._client = mock_ark_client
    return b


def _mock_httpx_stream(data: bytes = b"fake-mp4-data"):
    """Outbound final-video download stream: respx intercepts transport and returns given bytes for any download URL.

    Keep start/stop usage (callers finish with try/finally), replacing only the interception layer. Assertions cover
    real serialized streaming GET requests, rather than arguments recorded by a client substitute.
    """
    router = respx.mock(assert_all_called=False)
    router.start()
    router.get(url__regex=r".*").mock(return_value=httpx.Response(200, content=data))
    return router


class TestArkProperties:
    def test_name(self, ark_backend):
        assert ark_backend.name == "ark"


class TestArkGenerate:
    async def test_text_to_video(self, ark_backend, tmp_path):
        """Text-to-video: no start_image."""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-20250101-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 58944
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 246840
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="a flower field",
                output_path=output,
                duration_seconds=5,
            )
            result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "ark"
        assert result.model == "doubao-seedance-1-5-pro-251215"
        assert result.seed == 58944
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101-test"

    async def test_image_to_video(self, ark_backend, tmp_path):
        """Image-to-video: start_image requires role=first_frame."""
        output = tmp_path / "out.mp4"
        frame = tmp_path / "scene_E1S01.png"
        frame.write_bytes(b"fake-png")

        create_result = MagicMock()
        create_result.id = "cgt-i2v-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video2.mp4"
        get_result.seed = 12345
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 200000
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="girl opens eyes",
                output_path=output,
                start_image=frame,
                generate_audio=True,
            )
            result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert result.provider == "ark"
        create_call = ark_backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        content_arg = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
        assert len(content_arg) == 2
        assert content_arg[1]["type"] == "image_url"
        assert content_arg[1]["image_url"]["url"].startswith("data:image/")
        assert content_arg[1]["role"] == "first_frame"

    async def test_start_image_encoding_keeps_event_loop_running(self, ark_backend, tmp_path, monkeypatch):
        """Offload first-frame base64 encoding, which reads the whole image, to a thread; otherwise the read blocks the event loop."""
        output = tmp_path / "out.mp4"
        frame = tmp_path / "scene_E1S01.png"
        frame.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        create_result = MagicMock()
        create_result.id = "cgt-i2v-gate"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video-gate.mp4"
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 1
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="girl opens eyes",
                output_path=output,
                start_image=frame,
            )
            with blocking_file_read_gate(monkeypatch, frame) as gate:
                task = asyncio.create_task(ark_backend.generate(request))
                await gate.wait_until_read_started()
                gate.release()
                result = await task
                gate.assert_read_was_offloaded()
        finally:
            patcher.stop()

        assert result.provider == "ark"
        content_arg = ark_backend._client.content_generation.tasks.create.call_args.kwargs["content"]
        assert content_arg[1]["image_url"]["url"].startswith("data:image/png;base64,")

    async def test_first_last_frame_role_fields(self, ark_backend, tmp_path):
        """First/last frames: start_image/end_image require role=first_frame / role=last_frame respectively;
        image_url has no position, since role expresses placement."""
        output = tmp_path / "out.mp4"
        first = tmp_path / "first.png"
        first.write_bytes(b"fake-first")
        last = tmp_path / "last.png"
        last.write_bytes(b"fake-last")

        create_result = MagicMock()
        create_result.id = "cgt-fl-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="morph",
                output_path=output,
                start_image=first,
                end_image=last,
            )
            await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_kwargs = ark_backend._client.content_generation.tasks.create.call_args.kwargs
        content_arg = create_kwargs["content"]
        image_items = [c for c in content_arg if c["type"] == "image_url"]
        assert len(image_items) == 2
        assert image_items[0]["role"] == "first_frame"
        assert image_items[1]["role"] == "last_frame"
        # Once role expresses placement, do not also put position into image_url.
        assert "position" not in image_items[1]["image_url"]

    async def test_reference_images_role(self, ark_backend, tmp_path):
        """Reference images: every reference_images item requires role=reference_image (the Ark multi-image trigger)."""
        output = tmp_path / "out.mp4"
        ref1 = tmp_path / "ref1.jpg"
        ref1.write_bytes(b"fake-ref-1")
        ref2 = tmp_path / "ref2.jpg"
        ref2.write_bytes(b"fake-ref-2")

        create_result = MagicMock()
        create_result.id = "cgt-refs-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="[图1] 与 [图2] 对话",
                output_path=output,
                reference_images=[ref1, ref2],
            )
            await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_kwargs = ark_backend._client.content_generation.tasks.create.call_args.kwargs
        content_arg = create_kwargs["content"]
        image_items = [c for c in content_arg if c["type"] == "image_url"]
        assert len(image_items) == 2
        assert all(item["role"] == "reference_image" for item in image_items)

    async def test_missing_end_image_fails_loud(self, ark_backend, tmp_path):
        """Abort submission if the last-frame file is missing: silently skipping it would charge normally and produce a video without that last frame."""
        ark_backend._client.content_generation.tasks.create = MagicMock()

        request = VideoGenerationRequest(
            prompt="morph",
            output_path=tmp_path / "out.mp4",
            end_image=tmp_path / "gone.png",
        )
        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend.generate(request)
        assert exc.value.code == "video_end_image_unreadable"
        ark_backend._client.content_generation.tasks.create.assert_not_called()

    async def test_missing_reference_image_fails_loud(self, ark_backend, tmp_path):
        """Abort submission for missing reference images, as for last frames: even one missing image can yield a video that diverges from intent."""
        present = tmp_path / "ref1.jpg"
        present.write_bytes(b"fake-ref-1")
        ark_backend._client.content_generation.tasks.create = MagicMock()

        request = VideoGenerationRequest(
            prompt="[图1] 与 [图2] 对话",
            output_path=tmp_path / "out.mp4",
            reference_images=[present, tmp_path / "gone.jpg"],
        )
        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend.generate(request)
        assert exc.value.code == "video_reference_images_unreadable"
        ark_backend._client.content_generation.tasks.create.assert_not_called()

    async def test_failed_task_raises(self, ark_backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-fail"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "failed"
        get_result.error = "content violation"
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(RuntimeError, match="Ark 视频生成失败"):
            await ark_backend.generate(request)

    async def test_with_seed_and_flex(self, ark_backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-flex"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 42
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 100000
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output,
                seed=42,
                service_tier="flex",
            )
            await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_call = ark_backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        assert call_kwargs.kwargs.get("seed") == 42 or call_kwargs[1].get("seed") == 42
        assert call_kwargs.kwargs.get("service_tier") == "flex" or call_kwargs[1].get("service_tier") == "flex"

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError, match="Ark API Key"):
            Seedance1VideoBackend(api_key=None, model=_SEEDANCE_1_5_PRO)


class TestArkRetryBehavior:
    """Test separation of task-creation and polling retries."""

    async def test_poll_transient_error_retries_without_recreating_task(self, ark_backend, tmp_path):
        """Transient polling errors retry polling instead of creating another task."""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-retry-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_success = MagicMock()
        get_success.status = "succeeded"
        get_success.content = MagicMock()
        get_success.content.video_url = "https://cdn.example.com/video.mp4"
        get_success.seed = None
        get_success.usage = None

        # First poll raises ConnectionError; second succeeds.
        ark_backend._client.content_generation.tasks.get = MagicMock(
            side_effect=[ConnectionError("connection reset"), get_success]
        )

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with bounded_poll_clock():
                result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert result.task_id == "cgt-retry-test"
        # Critical assertion: create the task only once.
        assert ark_backend._client.content_generation.tasks.create.call_count == 1
        # Poll twice (one failure + one success).
        assert ark_backend._client.content_generation.tasks.get.call_count == 2

    async def test_create_retries_on_transient_error(self, ark_backend, tmp_path):
        """Transient task-creation errors are retried by @with_retry_async."""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-create-retry"
        # First creation raises ConnectionError; second succeeds.
        ark_backend._client.content_generation.tasks.create = MagicMock(
            side_effect=[ConnectionError("connection reset"), create_result]
        )

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with (
                bounded_poll_clock(),
            ):
                result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert result.task_id == "cgt-create-retry"
        # Create twice (one failure + one success).
        assert ark_backend._client.content_generation.tasks.create.call_count == 2

    async def test_poll_non_retryable_error_propagates(self, ark_backend, tmp_path):
        """Nonretryable polling errors raise immediately."""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-no-retry"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        ark_backend._client.content_generation.tasks.get = MagicMock(side_effect=ValueError("invalid response"))

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(ValueError, match="invalid response"), bounded_poll_clock():
            await ark_backend.generate(request)

        # Create once and raise after only one polling attempt.
        assert ark_backend._client.content_generation.tasks.create.call_count == 1
        assert ark_backend._client.content_generation.tasks.get.call_count == 1


def _effective(model: str):
    """Effective capabilities evaluate both a name-prefilled Seedance endpoint and its model entry under custom providers."""
    from lib.custom_provider.capabilities import two_sided_video_capabilities
    from lib.custom_provider.endpoints import infer_endpoint

    return two_sided_video_capabilities(endpoint=infer_endpoint(model, "openai", "video"), model_id=model)


class TestArkModelCapabilities:
    """Ark channel declarations and effective capabilities after matching entries by name (two-sided evaluation, docs/adr/0104).

    Channel declarations follow the endpoint, independent of model name; model differences in first/last frames, reference images, and reference audio come from entries. Unmatched models
    (unlisted 1.0 series, future upstream additions, unknown suffixes) use conservative fallback: first frame follows the channel, last frame/reference audio stay off, and only the channel limits reference-image count.
    """

    @pytest.mark.parametrize("backend", list(SEEDANCE_BACKENDS.values()))
    @pytest.mark.parametrize("model", ["doubao-seedance-1-5-pro-251215", "doubao-seedance-9-9-ultra-future"])
    def test_channel_declares_every_input_path_regardless_of_model(self, backend: type[ArkVideoBackend], model: str):
        caps = backend.video_capabilities_for_model(model)
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images is None
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count is None

    @pytest.mark.parametrize("model", ["doubao-seedance-1-5-pro-251215", "doubao-seedance-1.5-pro"])
    def test_seedance_1_5_pro_entry_opens_last_frame_and_rejects_references(self, model: str):
        """The 1.5 Pro entry enables first/last frames and rejects reference images (explicit 0); exclude it from reference-to-video candidates and reject reference images at execution."""
        caps = _effective(model)
        assert caps.last_frame is True
        assert caps.max_reference_images == 0
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    @pytest.mark.parametrize(
        "model",
        [
            "doubao-seedance-1-0-pro-fast-251015",
            "doubao-seedance-1-0-lite-t2v-250428",
            "doubao-seedance-9-9-ultra-future",
            "doubao-seedance-1-5-pro-future",
            "doubao-seedance-2-0-future",
        ],
    )
    def test_model_without_entry_falls_back_conservatively(self, model: str):
        """No matching entry: first frame follows the channel, last frame/reference audio stay off, and reference-image count follows only the channel (unlimited on this channel, unlisted model)."""
        caps = _effective(model)
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images is None
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0

    @pytest.mark.parametrize(
        "model",
        [
            "doubao-seedance-2-0-260128",
            "doubao-seedance-2-0-fast-260128",
            "doubao-seedance-2-0-mini-260615",
            "doubao-seedance-2.0",
            "doubao-seedance-2.0-fast",
            "doubao-seedance-2.0-mini",
        ],
    )
    def test_seedance_2_known_variants_support_last_frame(self, model: str):
        caps = _effective(model)
        assert caps.last_frame is True
        assert caps.max_reference_images == 9
        assert caps.max_reference_audio_count == 3

    @pytest.mark.parametrize("model", ["doubao-seedance-2-5-260628", "doubao-seedance-2.5"])
    def test_seedance_2_5_declares_own_material_limits(self, model: str):
        """2.5 has wider material limits than 2.0 (30 reference images / 10 audio clips totaling 30 seconds), from its own entry."""
        caps = _effective(model)
        assert caps.last_frame is True
        assert caps.max_reference_images == 30
        assert caps.reference_audio_mode == ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 10
        assert caps.max_reference_audio_total_seconds == 30.0

    @pytest.mark.parametrize("model", ["doubao-seedance-2-5-260628", "ep-20261103-opaque"])
    def test_seedance_2_5_endpoint_requires_adaptive_first_frame_ratio(self, model: str):
        """With a first frame, endpoint 2.5 lets upstream adapt the ratio to that frame: a fixed ratio conflicts with its dimensions. This request structure follows the endpoint, not model name."""
        assert Seedance25VideoBackend.video_capabilities_for_model(model).first_frame_ratio_adaptive_only is True

    @pytest.mark.parametrize("aspect_ratio", ["16:9", "9:16"])
    def test_seedance_2_5_first_frame_request_ratio_resolves_to_adaptive(self, aspect_ratio: str):
        """The shared capability resolver determines actual sent values: with a first frame, override any user ratio to adaptive.

        Testing the caps flag alone pins the declaration, not the external promise that first-frame tasks always use adaptive ratio.
        That promise requires both the declaration and resolve_first_frame_aspect_ratio, so test them together here.
        """
        caps = Seedance25VideoBackend.video_capabilities_for_model("doubao-seedance-2-5-260628")
        resolved = resolve_first_frame_aspect_ratio(caps=caps, aspect_ratio=aspect_ratio, has_first_frame=True)
        assert resolved == FIRST_FRAME_ADAPTIVE_RATIO
        # Without a first frame, text-only / reference-image-only requests still send the user ratio unchanged.
        assert (
            resolve_first_frame_aspect_ratio(caps=caps, aspect_ratio=aspect_ratio, has_first_frame=False)
            == aspect_ratio
        )

    @pytest.mark.parametrize("backend", [Seedance1VideoBackend, Seedance2VideoBackend])
    def test_other_endpoints_keep_passthrough_first_frame_ratio(self, backend: type[ArkVideoBackend]):
        """Endpoints 1.x and 2.0 do not declare adaptive, even with 2.5 in the model name: ratio semantics follow only the endpoint."""
        assert (
            backend.video_capabilities_for_model("doubao-seedance-2-5-260628").first_frame_ratio_adaptive_only is False
        )


class TestArkPollBudget:
    async def test_uses_request_poll_timeout(self, ark_backend, tmp_path):
        request = VideoGenerationRequest(
            prompt="p",
            output_path=tmp_path / "out.mp4",
            duration_seconds=30,
            service_tier="flex",
            poll_timeout_seconds=7200,
        )
        with patch("lib.backends.video_backends.ark.poll_with_retry", new_callable=AsyncMock) as poll:
            poll.side_effect = RuntimeError("stop")
            with pytest.raises(RuntimeError):
                await ark_backend._poll_until_done("task-1", request)
        assert poll.call_args.kwargs["max_wait"] == 7200
        assert "poll_interval" not in poll.call_args.kwargs


async def _created_params(backend_class: type[ArkVideoBackend], model: str, tmp_path, **request_fields) -> dict:
    """Task-creation arguments emitted by ``backend_class`` for one request (stop at task creation)."""
    with patch("lib.backends.video_backends.ark.create_ark_client", return_value=MagicMock()):
        backend = backend_class(api_key="test", model=model)
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.content_generation.tasks.create = fake_create
    with pytest.raises(RuntimeError):
        await backend._create_task(VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4", **request_fields))
    return captured


class TestArkServiceTierParam:
    """Send service_tier only on Seedance 1.x endpoints: 2.0 rejects it with 400 in reference-to-video, and 2.5 follows the same-generation policy of omission."""

    @pytest.mark.parametrize(
        ("backend", "sends_tier"),
        [(Seedance1VideoBackend, True), (Seedance2VideoBackend, False), (Seedance25VideoBackend, False)],
    )
    async def test_service_tier_follows_the_endpoint(self, tmp_path, backend: type[ArkVideoBackend], sends_tier: bool):
        """Opaque model IDs also follow the endpoint; the request retains the original model ID."""
        params = await _created_params(backend, "ep-20261103-opaque", tmp_path, service_tier="flex")

        assert params["model"] == "ep-20261103-opaque"
        assert (params.get("service_tier") == "flex") is sends_tier
        assert ("service_tier" in params) is sends_tier

    @pytest.mark.parametrize("backend", list(SEEDANCE_BACKENDS.values()))
    async def test_model_name_does_not_change_request_or_metering(self, tmp_path, backend: type[ArkVideoBackend]):
        """Changing model name on one endpoint (including names suggesting another generation) leaves requests and metering unchanged: only the endpoint determines generation."""
        fields = {"service_tier": "flex", "resolution": "720p", "aspect_ratio": "16:9", "duration_seconds": 5}
        names = ("doubao-seedance-1-5-pro-251215", "doubao-seedance-2-0-260128", "dreamina-seedance-2-5-x", "ep-opaque")
        requests = [await _created_params(backend, name, tmp_path, **fields) for name in names]
        for params in requests:
            params.pop("model")
        assert all(params == requests[0] for params in requests)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4", **fields)
        usages = [backend.metered_usage(name, request) for name in names]
        assert all(usage == usages[0] for usage in usages)

    async def test_seedance_1_5_sends_service_tier(self, ark_backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-seedance15"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/v.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with bounded_poll_clock():
                await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_kwargs = ark_backend._client.content_generation.tasks.create.call_args.kwargs
        assert create_kwargs.get("service_tier") == "default"


class TestArkVideoBackendBaseUrl:
    def test_custom_base_url_passed_through(self):
        with captured_ark_clients("lib.backends.video_backends.ark") as created:
            Seedance1VideoBackend(
                api_key="k", model=_SEEDANCE_1_5_PRO, base_url="https://ark.cn-beijing.volces.com/api/plan/v3"
            )
        assert created == [
            {"api_key": "k", "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3", "transport_layer": None}
        ]

    def test_default_base_url_is_none(self):
        with captured_ark_clients("lib.backends.video_backends.ark") as created:
            Seedance1VideoBackend(api_key="k", model=_SEEDANCE_1_5_PRO)
        assert created == [{"api_key": "k", "base_url": None, "transport_layer": None}]


_RELAY_A = "https://relay-a.example.com/api/v3"
_RELAY_B = "https://relay-b.example.com/api/v3"
_ARK_VIDEO_URL = "https://cdn.example.com/relay.mp4"


def _ark_task_json(task_id: str) -> dict:
    return {
        "id": task_id,
        "model": _SEEDANCE_1_5_PRO,
        "status": "succeeded",
        "content": {"video_url": _ARK_VIDEO_URL},
        "usage": {"completion_tokens": 10, "total_tokens": 10},
        "seed": 7,
        "created_at": 1,
        "updated_at": 2,
    }


class TestArkSubmittedBaseUrlReplay:
    """Ark protocol under a custom provider: persist the actual request host at submission and poll it on resume (real SDK, respx interception)."""

    async def test_generate_persists_request_base_url(self, tmp_path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post(f"{_RELAY_A}/contents/generations/tasks").mock(
                return_value=httpx.Response(200, json={"id": "cgt-a"})
            )
            router.get(f"{_RELAY_A}/contents/generations/tasks/cgt-a").mock(
                return_value=httpx.Response(200, json=_ark_task_json("cgt-a"))
            )
            router.get(_ARK_VIDEO_URL).mock(return_value=httpx.Response(200, content=b"mp4"))

            await Seedance1VideoBackend(api_key="k", model=_SEEDANCE_1_5_PRO, base_url=f"{_RELAY_A}/").generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", task_id="db-task-a")
            )

        assert [(r["job_id"], r["base_url"]) for r in persisted] == [("cgt-a", _RELAY_A)]

    async def test_resume_polls_submitted_base_url_after_config_change(self, tmp_path):
        # Submit to host A, then configure B before resume: only A can query the task; use current credentials.
        with capture_http() as router, bounded_poll_clock():
            submitted = router.get(f"{_RELAY_A}/contents/generations/tasks/cgt-a").mock(
                return_value=httpx.Response(200, json=_ark_task_json("cgt-a"))
            )
            current = router.get(url__regex=r"^https://relay-b\.example\.com/").mock(
                return_value=httpx.Response(404, json={"error": {"code": "ResourceNotFound", "message": "not found"}})
            )
            router.get(_ARK_VIDEO_URL).mock(return_value=httpx.Response(200, content=b"resumed"))

            result = await Seedance1VideoBackend(api_key="k", model=_SEEDANCE_1_5_PRO, base_url=_RELAY_B).resume_video(
                "cgt-a",
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", submitted_base_url=_RELAY_A),
            )

            assert only_request(submitted).headers["Authorization"] == "Bearer k"
            assert current.call_count == 0

        assert result.task_id == "cgt-a"
        assert (tmp_path / "o.mp4").read_bytes() == b"resumed"


class TestIsArkNotFound:
    """Match task_not_found / tasknotfound exactly, removing broad "not found" fallback;
    retain "expired" substring recognition (_poll_until_done converts status=expired to RuntimeError)."""

    def test_excludes_business_not_found(self):
        from lib.backends.video_backends.ark import _is_ark_not_found

        exc = RuntimeError("reference image not found in storage")
        assert _is_ark_not_found(exc) is False

    def test_recognizes_task_not_found(self):
        from lib.backends.video_backends.ark import _is_ark_not_found

        assert _is_ark_not_found(RuntimeError("task_not_found: invalid id")) is True

    def test_recognizes_expired_status(self):
        from lib.backends.video_backends.ark import _is_ark_not_found

        assert _is_ark_not_found(RuntimeError("Ark 任务失败 ... status=expired")) is True

    def test_recognizes_404(self):
        from lib.backends.video_backends.ark import _is_ark_not_found

        exc = RuntimeError("any")
        exc.status_code = 404
        assert _is_ark_not_found(exc) is True


class TestArkReferenceAudio:
    """Seedance 2.0 reference audio: content-array entries and ordering contract."""

    @staticmethod
    def _seedance_2_backend():
        with patch("lib.backends.video_backends.ark.create_ark_client", return_value=MagicMock()):
            return Seedance2VideoBackend(api_key="test", model="doubao-seedance-2-0-260128")

    def test_request_log_view_collapses_media_without_base64(self):
        """Keep material out of logs: images/audio in content are base64 data URIs; passing them unchanged to log formatting
        only truncates to a prefix rather than removing them, exposing material content (and possibly mp3 ID3 metadata) in info logs.
        Keep prompt, which is user text needed to diagnose generation quality."""
        from lib.backends.video_backends.ark import _safe_create_params_for_log

        view = _safe_create_params_for_log(
            {
                "model": "doubao-seedance-2-0-260128",
                "content": [
                    {"type": "text", "text": "两人对话"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    {"type": "audio_url", "audio_url": {"url": "data:audio/mp3;base64,BBBB"}},
                ],
            }
        )

        assert "base64" not in json.dumps(view, ensure_ascii=False)
        assert view["content"] == "<1 audio_url, 1 image_url, 1 text>"
        assert view["prompt"] == "两人对话"

    def test_seedance_2_declares_audio_capability(self):
        caps = _effective("doubao-seedance-2-0-260128")
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3

    @pytest.mark.parametrize("model", ["doubao-seedance-2.0-fast", "doubao-seedance-2-0-mini-260615"])
    def test_seedance_2_variants_declare_audio_capability(self, model):
        assert _effective(model).reference_audio_mode is ReferenceAudioMode.DIRECT

    def test_unverified_seedance_2_variant_does_not_inherit_audio(self):
        """Unmatched 2.0 variants disable reference audio: do not enable it when the entry does not list it."""
        caps = _effective("doubao-seedance-2-0-future")
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0

    def test_seedance_1_5_declares_no_audio_capability(self):
        assert _effective("doubao-seedance-1-5-pro-251215").reference_audio_mode is ReferenceAudioMode.NONE

    async def test_reference_audio_sent_as_audio_url_entries(self, tmp_path):
        """Send one type=audio_url + role=reference_audio entry per audio clip, in request-field order."""
        ark_backend = self._seedance_2_backend()
        first_audio = tmp_path / "a.mp3"
        first_audio.write_bytes(b"id3-first")
        second_audio = tmp_path / "b.wav"
        second_audio.write_bytes(b"riff-second")
        ref = tmp_path / "r.png"
        ref.write_bytes(b"fake-ref")

        ark_backend._client.content_generation.tasks.create = MagicMock(side_effect=RuntimeError("stop"))

        request = VideoGenerationRequest(
            prompt="两人对话",
            output_path=tmp_path / "out.mp4",
            reference_images=[ref],
            reference_audio_files=[first_audio, second_audio],
        )
        with pytest.raises(RuntimeError):
            await ark_backend._create_task(request)

        content = ark_backend._client.content_generation.tasks.create.call_args.kwargs["content"]
        audio_items = [c for c in content if c["type"] == "audio_url"]
        assert len(audio_items) == 2
        assert [c["role"] for c in audio_items] == ["reference_audio", "reference_audio"]
        # Order determines the prompt marker 音频N: the first clip must be the first one in the request.
        assert audio_items[0]["audio_url"]["url"].startswith("data:audio/mp3;base64,")
        assert audio_items[1]["audio_url"]["url"].startswith("data:audio/wav;base64,")

    async def test_reference_audio_encoding_keeps_event_loop_running(self, tmp_path, monkeypatch):
        """Offload reference-audio base64 encoding, which reads the whole clip, to a thread; otherwise the read blocks the event loop."""
        ark_backend = self._seedance_2_backend()
        audio = tmp_path / "voice.mp3"
        audio.write_bytes(b"ID3" + b"\x00" * 100)

        create_result = MagicMock()
        create_result.id = "cgt-audio-gate"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        request = VideoGenerationRequest(
            prompt="两人对话",
            output_path=tmp_path / "out.mp4",
            reference_audio_files=[audio],
        )
        with blocking_file_read_gate(monkeypatch, audio) as gate:
            task = asyncio.create_task(ark_backend._create_task(request))
            await gate.wait_until_read_started()
            gate.release()
            task_id = await task
            gate.assert_read_was_offloaded()

        assert task_id == "cgt-audio-gate"
        content = ark_backend._client.content_generation.tasks.create.call_args.kwargs["content"]
        audio_items = [c for c in content if c["type"] == "audio_url"]
        assert audio_items[0]["audio_url"]["url"].startswith("data:audio/mp3;base64,")

    async def test_no_audio_entries_when_request_has_none(self, tmp_path):
        ark_backend = self._seedance_2_backend()
        ref = tmp_path / "r.png"
        ref.write_bytes(b"fake-ref")
        ark_backend._client.content_generation.tasks.create = MagicMock(side_effect=RuntimeError("stop"))

        with pytest.raises(RuntimeError):
            await ark_backend._create_task(
                VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4", reference_images=[ref])
            )

        content = ark_backend._client.content_generation.tasks.create.call_args.kwargs["content"]
        assert not [c for c in content if c["type"] == "audio_url"]

    async def test_unsupported_audio_format_raises(self, tmp_path):
        """Raise on unsupported formats rather than skip: skipping shifts all later audio numbers and assigns the wrong character voices."""
        ark_backend = self._seedance_2_backend()
        bad = tmp_path / "a.ogg"
        bad.write_bytes(b"ogg")

        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend._create_task(
                VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4", reference_audio_files=[bad])
            )

        assert exc.value.code == "video_reference_audio_format_unsupported"

    async def test_missing_audio_file_raises(self, tmp_path):
        ark_backend = self._seedance_2_backend()

        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend._create_task(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_audio_files=[tmp_path / "missing.mp3"],
                )
            )

        assert exc.value.code == "video_reference_audio_unreadable"


class TestArkProviderAssets:
    """Provider material references: replace Seedance 2.x reference-image slots in place with ``asset://``; rejected input images yield structured errors that can identify them."""

    _PRIVACY_CODE = "InputImageSensitiveContentDetected.PrivacyInformation"

    @staticmethod
    def _backend(
        backend: type[ArkVideoBackend] = Seedance2VideoBackend, model: str = "doubao-seedance-2-0-260128"
    ) -> ArkVideoBackend:
        client = MagicMock()
        with patch("lib.backends.video_backends.ark.create_ark_client", return_value=client):
            return backend(api_key="TEST-ark-key", model=model)

    @staticmethod
    def _rejection(code: str) -> Exception:
        from volcenginesdkarkruntime._exceptions import ArkBadRequestError

        response = httpx.Response(400, request=httpx.Request("POST", "https://ark.test/api/v3/contents"))
        return ArkBadRequestError(
            "rejected",
            response=response,
            body={"code": code, "message": "input image may contain a real person"},
            request_id="req-1",
        )

    def test_only_seedance_2_reference_slots_accept_asset_references(self):
        """Determine by endpoint: 2.0 / 2.5 declare reference-image slots; 1.x does not, regardless of model name."""
        from lib.backends.provider_assets import REFERENCE_IMAGE_ROLE

        assert self._backend().provider_asset_roles == frozenset({REFERENCE_IMAGE_ROLE})
        assert self._backend(Seedance25VideoBackend, "ep-opaque").provider_asset_roles == frozenset(
            {REFERENCE_IMAGE_ROLE}
        )
        assert self._backend(Seedance1VideoBackend, "doubao-seedance-2-0-260128").provider_asset_roles == frozenset()

    async def test_asset_reference_replaces_its_image_in_place(self, tmp_path):
        """A material reference occupies its original image position without reading local bytes; embed other images normally, preserving item count and order."""
        backend = self._backend()
        scene = tmp_path / "scene.png"
        scene.write_bytes(b"scene-bytes")
        create_result = MagicMock()
        create_result.id = "cgt-asset"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)
        backend._client.content_generation.tasks.get = MagicMock(side_effect=RuntimeError("stop after create"))

        request = VideoGenerationRequest(
            prompt="@图片1 的角色走进 @图片2",
            output_path=tmp_path / "out.mp4",
            reference_images=[tmp_path / "missing-character.png", scene],
            reference_image_asset_uris=["asset://asset-alice-1", None],
        )
        with pytest.raises(RuntimeError, match="stop after create"):
            await backend.generate(request)

        content = backend._client.content_generation.tasks.create.call_args.kwargs["content"]
        images = [item for item in content if item["type"] == "image_url"]
        assert [item["role"] for item in images] == ["reference_image", "reference_image"]
        assert images[0]["image_url"]["url"] == "asset://asset-alice-1"
        assert images[1]["image_url"]["url"].startswith("data:image/")
        assert content[0] == {"type": "text", "text": "@图片1 的角色走进 @图片2"}

    async def test_model_without_asset_support_refuses_asset_references(self, tmp_path):
        backend = self._backend(Seedance1VideoBackend, _SEEDANCE_1_5_PRO)
        backend._client.content_generation.tasks.create = MagicMock()
        request = VideoGenerationRequest(
            prompt="p",
            output_path=tmp_path / "out.mp4",
            reference_images=[tmp_path / "a.png"],
            reference_image_asset_uris=["asset://asset-alice-1"],
        )
        with pytest.raises(ValueError, match="provider asset"):
            await backend.generate(request)
        backend._client.content_generation.tasks.create.assert_not_called()

    @pytest.mark.parametrize(
        ("refs", "asset_uris", "expected_index", "sent_as_asset"),
        [
            (1, None, 0, False),
            (1, ["asset://asset-alice-1"], 0, True),
            (2, None, None, False),
        ],
        ids=["single-image", "single-asset", "ambiguous"],
    )
    async def test_input_image_rejection_names_the_image_only_when_certain(
        self, tmp_path, refs, asset_uris, expected_index, sent_as_asset
    ):
        """Ark gives no rejected-image position: identify an image only when exactly one exists; do not guess among several."""
        from lib.backends.provider_assets import ProviderInputImageRejectedError

        backend = self._backend()
        paths = []
        for i in range(refs):
            path = tmp_path / f"ref{i}.png"
            path.write_bytes(b"ref")
            paths.append(path)
        backend._client.content_generation.tasks.create = MagicMock(side_effect=self._rejection(self._PRIVACY_CODE))
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", reference_images=paths, reference_image_asset_uris=asset_uris
        )

        with pytest.raises(ProviderInputImageRejectedError) as excinfo:
            await backend.generate(request)

        assert excinfo.value.provider_code == self._PRIVACY_CODE
        assert excinfo.value.reference_index == expected_index
        assert excinfo.value.sent_as_asset is sent_as_asset
        assert excinfo.value.params["provider_reason"] == "input image may contain a real person"

    async def test_input_image_rejection_reported_by_the_task_is_structured(self, tmp_path):
        from lib.backends.provider_assets import ProviderInputImageRejectedError

        backend = self._backend()
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"ref")
        create_result = MagicMock()
        create_result.id = "cgt-rejected"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)
        task = MagicMock()
        task.status = "failed"
        task.error = MagicMock(code=self._PRIVACY_CODE, message="flagged")
        backend._client.content_generation.tasks.get = MagicMock(return_value=task)

        with pytest.raises(ProviderInputImageRejectedError) as excinfo:
            await backend.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", reference_images=[ref])
            )
        assert excinfo.value.reference_index == 0

    async def test_other_bad_requests_propagate_unchanged(self, tmp_path):
        from volcenginesdkarkruntime._exceptions import ArkBadRequestError

        backend = self._backend()
        backend._client.content_generation.tasks.create = MagicMock(side_effect=self._rejection("InvalidParameter"))
        with pytest.raises(ArkBadRequestError):
            await backend.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4"))
