"""ComfyUI video channel: upload, submit, poll, artifact ingestion, resume, remote interruption, and eight failure codes."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from arcreel_market_core.endpoint_definition import validate_definition
from arcreel_market_core.video_backend_contract import (
    ProviderResponseStage,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.artifact_download_guard import (
    VIDEO_ARTIFACT_MAX_BYTES,
    ArtifactDestinationRejectedError,
    ArtifactTooLargeError,
)
from lib.backends.backend_runtime import VIDEO_POLL_MAX_CONSECUTIVE_FAILURES
from lib.backends.video_frame_slots import gate_video_request, resolve_video_capabilities
from lib.custom_provider.comfyui.comfyui_backend import ComfyuiVideoBackend
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.comfyui.request_builder import workflow_sha256
from lib.custom_provider.endpoint_resolution import endpoint_spec_from_row
from lib.custom_provider.factory import create_custom_backend
from lib.generation.task_failure import render_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from tests.factories import comfyui_endpoint_definition, make_display_names, make_translator
from tests.fakes import MP4_BYTES, bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

BASE_URL = "https://comfy.test"


def _definition(**overrides: Any) -> dict[str, Any]:
    definition = comfyui_endpoint_definition(**overrides)
    assert validate_definition(definition).valid
    return definition


def _backend(definition: dict[str, Any] | None = None, *, api_key: str = "") -> ComfyuiVideoBackend:
    return ComfyuiVideoBackend(
        provider_id="custom-1",
        model="wan-t2v",
        base_url=BASE_URL,
        api_key=api_key,
        definition=definition if definition is not None else _definition(),
    )


def _request(tmp_path: Path, **overrides: Any) -> VideoGenerationRequest:
    values: dict[str, Any] = {
        "prompt": "一只猫走过屋顶",
        "output_path": tmp_path / "out.mp4",
        "aspect_ratio": "9:16",
        "duration_seconds": 5,
        "task_id": "task-7",
    }
    values.update(overrides)
    return VideoGenerationRequest(**values)


def _history(outputs: dict[str, Any], *, completed: bool = True) -> dict[str, Any]:
    return {"status": {"completed": completed, "status_str": "success"}, "outputs": outputs}


def _video_output(filename: str = "final_00001.mp4") -> dict[str, Any]:
    return {"images": [], "gifs": [{"filename": filename, "subfolder": "video", "type": "output"}]}


def _entry(status: Any, outputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """A terminal record preserving ``status`` unchanged (``None`` is also a real-world shape)."""
    return {"status": status, "outputs": outputs if outputs is not None else {}}


def _messages(*events: Any) -> dict[str, Any]:
    return {"completed": True, "status_str": "error", "messages": list(events)}


def _cancel_here(_request: httpx.Request) -> httpx.Response:
    """Make a request on this route encounter task cancellation.

    ``CancelledError`` inherits ``BaseException``; exception-valued respx ``side_effect`` accepts only ``Exception``,
    so raise through its callable path.
    """
    raise asyncio.CancelledError


def _queue(*, running: Sequence[str] = (), pending: Sequence[str] = ()) -> dict[str, Any]:
    """``/queue`` shape: entries are ``[序号, prompt_id, prompt, 待执行节点, 额外数据]``."""
    return {
        "queue_running": [[index, job, {}, [], {}] for index, job in enumerate(running)],
        "queue_pending": [[index, job, {}, [], {}] for index, job in enumerate(pending)],
    }


def _with_image_bindings() -> dict[str, Any]:
    """Add a first frame and two reference-image slots to the minimal definition to include upload coverage."""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["10"] = {"class_type": "LoadImage", "inputs": {"image": "draft.png"}}
    definition["workflow"]["11"] = {"class_type": "LoadImage", "inputs": {"image": "ref-a.png"}}
    definition["workflow"]["12"] = {"class_type": "LoadImage", "inputs": {"image": "ref-b.png"}}
    definition["bindings"]["start_image"] = [{"node": "10", "input": "image", "class_type": "LoadImage"}]
    definition["bindings"]["reference_images"] = [
        {"node": "11", "input": "image", "class_type": "LoadImage"},
        {"node": "12", "input": "image", "class_type": "LoadImage"},
    ]
    assert validate_definition(definition).valid
    return definition


class TestDeclaredCapabilities:
    """The backend's own capability declaration and the declaration read by the pre-generation capability gate."""

    @staticmethod
    def _gate(definition: dict[str, Any], *, has_image: bool) -> VideoCapabilities:
        """Obtain capabilities as production does: factory-built wrapper, tier query, then gate.

        Wrapper tier queries intentionally avoid short-circuiting to factory-injected combined results, instead using the wrapped backend
        declaration as their base (``CustomVideoBackend.video_capabilities_for_tier``). If the backend omits a flag, the gate blocks
        the request before it reaches ``generate``.
        """
        spec = endpoint_spec_from_row(cast("Any", SimpleNamespace(id=7, definition=definition)))
        provider = SimpleNamespace(provider_id="custom-1", base_url=BASE_URL, api_key="")
        backend = create_custom_backend(
            provider=cast("Any", provider), model_id="wan-t2v", endpoint="ce-7", endpoint_spec=spec
        )
        caps = resolve_video_capabilities(backend, service_tier="default", resolution=None)
        gate_video_request(
            caps=caps,
            provider="custom-1",
            model="wan-t2v",
            prompt="一只猫走过屋顶",
            has_image=has_image,
            end_image=None,
            reference_images=None,
            reference_audio_files=None,
            reference_audio_total_seconds=None,
        )
        return caps

    def test_a_text_only_workflow_gets_past_the_capability_gate(self):
        caps = self._gate(_definition(), has_image=False)

        assert (caps.text_to_video, caps.first_frame, caps.audio_track) == (True, False, VideoAudioMode.ALWAYS_OFF)

    def test_a_workflow_with_a_start_image_gets_past_it_as_image_to_video(self):
        definition = _definition()
        definition["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]

        caps = self._gate(definition, has_image=True)

        assert (caps.text_to_video, caps.first_frame) == (False, True)


class TestGenerate:
    async def test_a_generation_uploads_submits_polls_and_stores_the_artifact(self, tmp_path: Path):
        """End-to-end: fill materials, submit, poll, and write the artifact to output_path; return provenance with the result."""
        definition = _with_image_bindings()
        start = tmp_path / "first.png"
        start.write_bytes(b"png-bytes")
        reference = tmp_path / "ref.jpg"
        reference.write_bytes(b"jpg-bytes")

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                side_effect=[
                    httpx.Response(200, json={"name": "task-7-start_image.png", "subfolder": "arcreel"}),
                    httpx.Response(200, json={"name": "task-7-reference_images-1.jpg", "subfolder": "arcreel"}),
                ]
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1", "node_errors": {}})
            )
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                side_effect=[
                    httpx.Response(200, json={}),
                    httpx.Response(200, json={"p-1": _history({"9": _video_output()})}),
                ]
            )
            # On an empty round, also confirm the execution remains queued; otherwise a restarted ComfyUI would only poll emptiness until timeout.
            queue = router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(pending=["p-1"])))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend(definition).generate(
                _request(tmp_path, start_image=start, reference_images=[reference])
            )

        assert result.video_path.read_bytes() == MP4_BYTES
        assert result.task_id == "p-1"
        assert upload.call_count == 2
        assert history.call_count == 2
        assert queue.call_count == 1
        # Use response subfolder / name, not request values; the server renames collisions.
        submitted = request_json(submit.calls.last.request)["prompt"]
        assert submitted["10"]["inputs"]["image"] == "arcreel/task-7-start_image.png"
        assert submitted["11"]["inputs"]["image"] == "arcreel/task-7-reference_images-1.jpg"
        assert request_json(submit.calls.last.request)["client_id"] == "arcreel-task-7"
        assert view.calls.last.request.url.params["filename"] == "final_00001.mp4"
        assert persisted == [
            {
                "task_id": "task-7",
                "job_id": "p-1",
                "provider": "custom-1",
                "endpoint": None,
                "base_url": BASE_URL,
            }
        ]

    async def test_the_version_metadata_gets_the_actual_seed_and_the_workflow_fingerprint(self, tmp_path: Path):
        """Both become known only after generation: randomize the seed at submission and fingerprint the actual submitted workflow."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        sent = request_json(submit.calls.last.request)["prompt"]
        assert result.seed == sent["3"]["inputs"]["seed"]
        assert result.provenance == {"workflow_sha256": workflow_sha256(sent)}

    async def test_an_unbound_duration_is_forwarded_untouched(self, tmp_path: Path):
        """Without a ``frames`` binding, ArcReel does not drive duration: submit normally and preserve the workflow's literal frame count."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path, duration_seconds=5))

        assert "frames" not in _definition()["bindings"]
        assert result.video_path.exists()
        assert request_json(submit.calls.last.request)["prompt"]["9"]["inputs"]["fps"] == 16

    async def test_excess_references_rejected_before_upload_or_submit(self, tmp_path: Path):
        references = [tmp_path / f"ref{i}.png" for i in range(4)]
        from lib.custom_provider.backends import CustomVideoBackend

        delegate = _backend(_with_image_bindings())
        backend = CustomVideoBackend(
            provider_id="custom-1",
            delegate=delegate,
            model=delegate.model,
            capability_overrides={"max_reference_images": 4},
        )
        gate_video_request(
            caps=resolve_video_capabilities(backend),
            provider=backend.name,
            model=backend.model,
            has_image=True,
            reference_images=references,
        )
        with capture_http() as router, pytest.raises(VideoCapabilityError) as exc:
            await backend.generate(_request(tmp_path, reference_images=references))

        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params["limit"] == 2
        assert exc.value.params["count"] == 4
        assert not router.calls

    async def test_a_root_level_history_entry_is_accepted(self, tmp_path: Path):
        """Recognize both shapes returned by different versions/proxies; recognizing only the wrapped shape leaves other deployments waiting forever for terminal state."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_credentials_render_once_and_ride_every_route(self, tmp_path: Path):
        definition = _with_image_bindings()
        definition["auth"] = {"headers": {"Authorization": "Bearer {{api_key}}"}}
        assert validate_definition(definition).valid
        start = tmp_path / "first.png"
        start.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            await _backend(definition, api_key="k-1").generate(_request(tmp_path, start_image=start))

        for route in (upload, submit, history, view):
            assert route.calls.last.request.headers["authorization"] == "Bearer k-1"

    async def test_query_credentials_ride_every_route_too(self, tmp_path: Path):
        """Support reverse proxies passing credentials in query parameters: add them to every route URL, not only the header table."""
        definition = _definition(auth={"query": {"token": "{{api_key}}"}})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            await _backend(definition, api_key="k-1").generate(_request(tmp_path))

        for route in (submit, history, view):
            assert route.calls.last.request.url.params["token"] == "k-1"
        # Credentials must not displace the artifact URL's own three parameters.
        assert view.calls.last.request.url.params["filename"] == "final_00001.mp4"

    @pytest.mark.parametrize("route", ["upload", "submit", "history", "view"])
    async def test_a_cross_origin_redirect_never_takes_the_credential_along(self, tmp_path: Path, route: str):
        """Credentials must not follow a reverse proxy's 302 redirect to another destination.

        httpx follow_redirects removes only ``Authorization`` across origins, while auth allows arbitrary header names. Automatic
        following would send ``X-API-Key`` into third-party access logs on a ``/view`` redirect to object storage.
        """
        definition = _with_image_bindings()
        definition["auth"] = {"headers": {"X-API-Key": "{{api_key}}"}}
        assert validate_definition(definition).valid
        start = tmp_path / "first.png"
        start.write_bytes(b"png")
        elsewhere = "https://elsewhere.test"
        redirect = httpx.Response(307, headers={"location": f"{elsewhere}/moved"})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            moved_post = router.post(f"{elsewhere}/moved")
            moved_get = router.get(f"{elsewhere}/moved")
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=redirect
                if route == "upload"
                else httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            moved_post.mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
                if route == "upload"
                else httpx.Response(200, json={"prompt_id": "p-1"})
            )
            router.post(f"{BASE_URL}/prompt").mock(
                return_value=redirect if route == "submit" else httpx.Response(200, json={"prompt_id": "p-1"})
            )
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=redirect
                if route == "history"
                else httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(
                return_value=redirect if route == "view" else httpx.Response(200, content=MP4_BYTES)
            )
            moved_get.mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
                if route == "history"
                else httpx.Response(200, content=MP4_BYTES)
            )

            await _backend(definition, api_key="secret").generate(_request(tmp_path, start_image=start))

        followed = moved_post if route in {"upload", "submit"} else moved_get
        assert followed.call_count == 1
        assert "x-api-key" not in followed.calls.last.request.headers
        # Same-origin redirects still need credentials, or reverse-proxy deployments cannot send any requests.
        assert upload.calls.last.request.headers["x-api-key"] == "secret"

    async def test_view_redirect_to_a_link_local_address_is_not_followed(self, tmp_path: Path):
        metadata = "http://169.254.169.254/latest/"
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(302, headers={"location": metadata}))
            target = router.get(metadata).mock(return_value=httpx.Response(200, content=MP4_BYTES))

            with pytest.raises(ArtifactDestinationRejectedError):
                await _backend().generate(_request(tmp_path))

        assert view.call_count == 1
        assert target.call_count == 0

    async def test_view_body_over_the_video_limit_is_aborted(self, tmp_path: Path):
        oversized = {"Content-Length": str(VIDEO_ARTIFACT_MAX_BYTES + 1)}
        request = _request(tmp_path)
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, headers=oversized, content=b""))

            with pytest.raises(ArtifactTooLargeError):
                await _backend().generate(request)

        assert view.call_count == 1
        assert not request.output_path.exists()

    async def test_a_same_origin_redirect_keeps_the_query_credential(self, tmp_path: Path):
        """``Location`` replaces the whole query string; reapply credentials or lose them on a ``/view`` → ``/view/`` normalization redirect."""
        definition = _definition(auth={"query": {"token": "{{api_key}}"}})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(
                return_value=httpx.Response(302, headers={"location": f"{BASE_URL}/files/final.mp4"})
            )
            moved = router.get(f"{BASE_URL}/files/final.mp4").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            await _backend(definition, api_key="k-1").generate(_request(tmp_path))

        assert moved.calls.last.request.url.params["token"] == "k-1"

    async def test_an_empty_api_key_leaves_the_auth_section_unrendered(self, tmp_path: Path):
        """Native ComfyUI has no auth: an empty ``Bearer `` only makes reverse proxies reject otherwise allowed requests with 401."""
        definition = _definition(auth={"headers": {"Authorization": "Bearer {{api_key}}"}})

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            await _backend(definition, api_key="").generate(_request(tmp_path))

        assert "authorization" not in submit.calls.last.request.headers


class TestFailures:
    async def test_an_upload_failure_stops_before_any_submit(self, tmp_path: Path):
        definition = _with_image_bindings()
        start = tmp_path / "first.png"
        start.write_bytes(b"png")

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/upload/image").mock(return_value=httpx.Response(507, text="disk full"))
            submit = router.post(f"{BASE_URL}/prompt")

            with pytest.raises(ComfyuiError) as caught:
                await _backend(definition).generate(_request(tmp_path, start_image=start))

        assert caught.value.code == "comfyui_upload_failed"
        assert submit.call_count == 0

    async def test_node_errors_on_a_200_fail_the_task_without_polling(self, tmp_path: Path):
        """The graph was submitted but node parameters failed validation; entering polling would only wait until timeout."""
        node_errors = {
            "3": {"class_type": "KSampler", "errors": [{"message": "value 4096 out of range"}]},
            "4": {"class_type": "CheckpointLoaderSimple", "errors": [{"message": "model not found"}]},
        }

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1", "node_errors": node_errors})
            )
            history = router.get(f"{BASE_URL}/history/p-1")

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_node_errors"
        assert caught.value.params == {"nodes": 2, "summary": "KSampler: value 4096 out of range"}
        assert history.call_count == 0
        assert persisted == []

    async def test_a_400_carries_the_same_failure_code(self, tmp_path: Path):
        """400 and 200 with node_errors have the same cause; summaries use the title users see on the canvas."""
        body = {"error": {"message": "Prompt has no outputs"}, "node_errors": {"6": {"class_type": "CLIPTextEncode"}}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(400, json=body))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_node_errors"
        assert caught.value.params == {"nodes": 1, "summary": "正向"}

    async def test_a_400_without_node_errors_falls_back_to_the_error_message(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(400, json={"error": {"message": "prompt outputs failed validation"}})
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.params == {"nodes": 0, "summary": "prompt outputs failed validation"}

    async def test_a_failed_job_id_persistence_stops_before_polling(self, tmp_path: Path):
        """Polling before persisting job_id makes an already running task unrecoverable after process restart."""

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids(fail=RuntimeError("db is down")):
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1")

            with pytest.raises(RuntimeError, match="db is down"):
                await _backend().generate(_request(tmp_path))

        assert history.call_count == 0

    async def test_an_output_node_without_artifacts_is_refused(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history({"9": {}})))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"
        assert caught.value.params == {"nodes": "9"}

    async def test_temp_artifacts_do_not_count_as_output(self, tmp_path: Path):
        """``temp`` means intermediate previews, which the server may clear anytime; accept only ``type == "output"``."""
        outputs = {"9": {"gifs": [{"filename": "preview.mp4", "subfolder": "", "type": "temp"}]}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"

    async def test_artifacts_of_an_unbound_node_are_not_taken(self, tmp_path: Path):
        """A side-branch ``PreviewImage`` also writes history artifacts; scanning the whole graph would retrieve a preview as final video."""
        outputs = {"99": _video_output("preview.mp4")}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"

    async def test_a_still_image_from_a_video_endpoint_is_refused(self, tmp_path: Path):
        outputs = {"9": {"images": [{"filename": "final_00001.png", "subfolder": "", "type": "output"}]}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_type_mismatch"
        assert caught.value.params == {"filename": "final_00001.png", "media_type": "video"}

    @pytest.mark.parametrize("filename", ["final_00001.mov", "final_00001.m4v", "FINAL.MP4"])
    async def test_the_iso_bmff_family_is_accepted(self, tmp_path: Path, filename: str):
        """All three allowed extensions belong to ISO BMFF, matching the three accepted for reference-video uploads."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output(filename)}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_a_webm_artifact_is_refused_with_the_allowed_suffixes_in_the_copy(self, tmp_path: Path):
        """Putting ``.webm`` bytes in the canonical ``.mp4`` path misrepresents both extension and sent MIME; reject as a type mismatch."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output("final_00001.webm")}))
            )
            view = router.get(f"{BASE_URL}/view")

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_type_mismatch"
        assert view.call_count == 0
        rendered = render_failure(
            encode_task_failure_message(caught.value), make_translator("zh"), make_display_names("zh")
        )
        assert ".m4v / .mov / .mp4" in rendered
        assert "h264-mp4" in rendered

    async def test_webm_bytes_under_an_mp4_name_are_refused_and_nothing_is_left_on_disk(self, tmp_path: Path):
        """An extension is only a history string: save nodes can write another container under an ``.mp4`` name."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"\x1a\x45\xdf\xa3webm"))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_container_mismatch"
        assert caught.value.params == {"filename": "final_00001.mp4", "media_type": "video"}
        assert not (tmp_path / "out.mp4").exists()
        rendered = render_failure(
            encode_task_failure_message(caught.value), make_translator("zh"), make_display_names("zh")
        )
        assert ".m4v / .mov / .mp4" in rendered

    async def test_still_image_iso_bmff_bytes_under_an_mp4_name_are_refused(self, tmp_path: Path):
        """``ftyp`` only establishes ISO BMFF membership, also used by HEIC / AVIF; admitting them leaves an unplayable final video."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(
                return_value=httpx.Response(200, content=b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heic-bytes")
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_container_mismatch"
        assert not (tmp_path / "out.mp4").exists()

    @pytest.mark.parametrize(
        ("code", "params"),
        [
            ("comfyui_upload_failed", {"detail": "disk full"}),
            ("comfyui_node_errors", {"nodes": 2, "summary": "KSampler: out of range"}),
            ("comfyui_job_lost", {"prompt_id": "p-1"}),
            ("comfyui_execution_error", {"node": "KSampler", "detail": "OutOfMemoryError"}),
            ("comfyui_interrupted", {}),
            ("comfyui_output_missing", {"nodes": "9"}),
            ("comfyui_output_type_mismatch", {"filename": "a.png", "media_type": "video"}),
            ("comfyui_output_container_mismatch", {"filename": "a.mp4", "media_type": "video"}),
            ("comfyui_image_drop_unsupported", {"node": "10"}),
        ],
    )
    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_every_failure_code_renders_in_every_locale(self, code: str, params: dict[str, Any], locale: str):
        """Persist machine codes only, rendering on read by Accept-Language; any missing language among the three exposes raw codes to users.

        Encoding also pins worker recognition of this exception: exceptions unknown to ``encode_task_failure_message``
        degrade to plain text, which read-side translation can no longer translate.

        Rendered results must contain no ``{``: ``lib.i18n`` falls back to unfilled templates on formatting errors, so mismatched
        placeholders would expose ``{name}`` directly to users.
        """
        message = encode_task_failure_message(ComfyuiError(code, **params))

        rendered = render_failure(message, make_translator(locale), make_display_names(locale))

        assert rendered
        assert code not in rendered
        assert "{" not in rendered

    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_the_mismatch_text_lists_the_allowed_extensions(self, locale: str):
        """The read side calculates allowed extensions from persisted ``media_type``; stored parameters contain only filename and media type."""
        message = encode_task_failure_message(
            ComfyuiError("comfyui_output_type_mismatch", filename="a.png", media_type="video")
        )

        rendered = render_failure(message, make_translator(locale), make_display_names(locale))

        assert rendered
        assert ".m4v / .mov / .mp4" in rendered


class TestMultipleArtifacts:
    async def test_the_first_artifact_is_taken_and_the_rest_are_reported(self, tmp_path: Path, caplog):
        outputs = {
            "9": {
                "gifs": [
                    {"filename": "final_00001.mp4", "subfolder": "video", "type": "output"},
                    {"filename": "final_00002.mp4", "subfolder": "video", "type": "output"},
                ]
            }
        }

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            with caplog.at_level("WARNING"):
                result = await _backend().generate(_request(tmp_path))

        assert only_request(view).url.params["filename"] == "final_00001.mp4"
        assert "共 2 个" in caplog.text
        # Only operators see logs; this notice must reach task results so users know they received one of several outputs.
        assert result.warnings == (
            {"key": "comfyui_multiple_outputs", "params": {"count": 2, "filename": "final_00001.mp4"}},
        )

    async def test_a_thumbnail_beside_the_video_does_not_pass_for_the_result(self, tmp_path: Path):
        """When one bound node writes a thumbnail and final video, select the final video.

        ``_output_artifacts`` follows the order of its ``images`` / ``gifs`` / ``audio`` keys, not which output
        is final video; choosing the first would misreport a successful generation as an artifact-type mismatch.
        """
        outputs = {
            "9": {
                "images": [{"filename": "preview_00001.png", "subfolder": "", "type": "output"}],
                "gifs": [{"filename": "final_00001.mp4", "subfolder": "video", "type": "output"}],
            }
        }

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert only_request(view).url.params["filename"] == "final_00001.mp4"
        assert result.warnings == (
            {"key": "comfyui_multiple_outputs", "params": {"count": 2, "filename": "final_00001.mp4"}},
        )

    async def test_a_single_artifact_reports_nothing(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.warnings == ()

    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_the_warning_renders_in_every_locale(self, locale: str):
        """Task-result warnings render in the current language like failure reasons; missing any of three languages exposes raw keys to users."""
        rendered = make_translator(locale)("comfyui_multiple_outputs", count=2, filename="final_00001.mp4")

        assert rendered
        assert "comfyui_multiple_outputs" not in rendered


class TestDiagnostics:
    async def test_the_recorded_history_keeps_only_the_status_and_the_output_node(self, tmp_path: Path):
        """Full history contains every node's outputs; one trace can inflate the diagnostic column to hundreds of KB."""
        recorded: list[tuple[ProviderResponseStage, object]] = []

        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        outputs = {"9": _video_output(), "99": {"images": [{"filename": "noise.png", "type": "temp"}]}}

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            await _backend().generate(_request(tmp_path, on_provider_response=_record))

        polled = [body for stage, body in recorded if stage == "poll"]
        assert polled == [{"status": {"completed": True, "status_str": "success"}, "outputs": {"9": _video_output()}}]
        assert [stage for stage, _ in recorded] == ["submit", "poll", "result"]


class TestTerminalStates:
    """The final event in ``status.messages`` determines whether a terminal record means success or failure."""

    async def test_a_node_exception_is_reported_with_its_node_and_summary(self, tmp_path: Path):
        event = [
            "execution_error",
            {
                "prompt_id": "p-1",
                "node_id": "3",
                "node_type": "KSampler",
                "exception_message": "CUDA out of memory",
                "exception_type": "torch.OutOfMemoryError",
                "traceback": ["line one", "line two"],
            },
        ]

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_entry(_messages(event))))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_execution_error"
        # Tracebacks have hundreds of lines and failure reasons persist whole; summarize only exception type and message.
        assert caught.value.params == {"node": "KSampler", "detail": "torch.OutOfMemoryError: CUDA out of memory"}

    async def test_an_interrupted_execution_has_its_own_code(self, tmp_path: Path):
        """Someone canceled in ComfyUI: this incomplete execution does not itself imply a faulty workflow."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_entry(_messages(["execution_interrupted", {"node_id": "9"}])))
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_interrupted"
        assert caught.value.params == {}

    async def test_only_the_last_event_decides(self, tmp_path: Path):
        """Nodes commonly finish after another node errors; any-error detection would label a completed video as failed."""
        events = (
            ["execution_error", {"node_type": "UpscaleImage", "exception_message": "skipped"}],
            ["execution_success", {"prompt_id": "p-1"}],
        )

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_entry(_messages(*events), {"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_a_null_status_without_outputs_falls_back_to_execution_error(self, tmp_path: Path):
        """Unknown state falls back to execution failure; claiming the output node produced nothing would blame bindings for an environment problem."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_entry(None)))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_execution_error"
        assert caught.value.params["node"] == "-"

    async def test_a_null_status_with_outputs_is_still_a_success(self, tmp_path: Path):
        """Some versions/proxies omit status; outputs imply completion, or those deployments could never produce a video."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_entry(None, {"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_a_finished_run_without_artifacts_stays_output_missing(self, tmp_path: Path):
        """The final event says success, but the bound node produced no file; only this case is a binding issue."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_entry(_messages(["execution_success", {}]), {"9": {}}))
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"


class TestJobLost:
    """When history stays empty, determine whether this execution still exists on this machine."""

    async def test_an_id_in_neither_queue_is_reported_lost(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json={}))
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(running=["other"])))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_job_lost"
        assert caught.value.params == {"prompt_id": "p-1"}
        # Detect loss in the first round: after confirming queue absence, recheck history; stop spinning after those two checks.
        assert history.call_count == 2

    @pytest.mark.parametrize("lane", ["running", "pending"])
    async def test_an_id_still_on_the_queue_keeps_polling(self, tmp_path: Path, lane: str):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                side_effect=[
                    httpx.Response(200, json={}),
                    httpx.Response(200, json=_history({"9": _video_output()})),
                ]
            )
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(**{lane: ["p-1"]})))
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_a_run_that_finished_between_the_two_requests_is_not_lost(self, tmp_path: Path):
        """Queue and history require separate requests; completion between them can make execution absent from both."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                side_effect=[
                    httpx.Response(200, json={}),
                    httpx.Response(200, json=_history({"9": _video_output()})),
                ]
            )
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue()))
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_a_broken_queue_route_never_fails_a_healthy_run(self, tmp_path: Path):
        """A proxy blocking only ``/queue`` leaves task URLs usable; do not let an auxiliary criterion declare a running generation lost."""
        rounds = itertools.count()

        def _history_route(_request: httpx.Request) -> httpx.Response:
            # A real long generation stays empty until its artifact is ready, so run this auxiliary check every round.
            if next(rounds) < VIDEO_POLL_MAX_CONSECUTIVE_FAILURES:
                return httpx.Response(200, json={})
            return httpx.Response(200, json=_history({"9": _video_output()}))

        with capture_http() as router, bounded_poll_clock(step=1.0), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(side_effect=_history_route)
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(503, text="bad gateway"))
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_an_unreadable_queue_never_declares_a_loss(self, tmp_path: Path):
        """HTML returned during proxy restart means unreadable queue, not empty queue."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                side_effect=[
                    httpx.Response(200, json={}),
                    httpx.Response(200, json=_history({"9": _video_output()})),
                ]
            )
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, html="<html>502</html>"))
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES

    async def test_a_json_body_without_the_two_lists_is_unreadable_too(self, tmp_path: Path):
        """A restarting proxy returning ``{"error": "restarting"}`` yields parseable JSON, not a readable queue."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                side_effect=[
                    httpx.Response(200, json={}),
                    httpx.Response(200, json={}),
                    httpx.Response(200, json=_history({"9": _video_output()})),
                ]
            )
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json={"error": "restarting"}))
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES


class TestResume:
    """``provider_job_id`` is ``prompt_id``: resume the same execution without reupload or resubmission."""

    async def test_a_resume_goes_straight_to_polling(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            upload = router.post(f"{BASE_URL}/upload/image")
            submit = router.post(f"{BASE_URL}/prompt")
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend(_with_image_bindings()).resume_video("p-1", _request(tmp_path))

        assert result.video_path.read_bytes() == MP4_BYTES
        assert result.task_id == "p-1"
        assert upload.call_count == 0
        assert submit.call_count == 0
        # Submission happened in the previous process; no new job_id needs persistence now.
        assert persisted == []

    async def test_a_resume_polls_the_host_the_job_was_submitted_to(self, tmp_path: Path):
        """Provider base_url may change after submission, while this execution lives on the original ComfyUI.

        Querying the current host asks a different machine, which reports no such prompt_id. A still-running generation
        would be marked lost while occupying the user's GPU.
        """
        submitted = "https://old-comfy.test"
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            current = router.get(f"{BASE_URL}/history/p-1")
            router.get(f"{submitted}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            view = router.get(f"{submitted}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().resume_video("p-1", _request(tmp_path, submitted_base_url=submitted))

        assert result.video_path.read_bytes() == MP4_BYTES
        assert current.call_count == 0
        assert view.call_count == 1

    async def test_a_resume_carries_no_seed_or_fingerprint(self, tmp_path: Path):
        """Both exist only in submission-time construction; this path performs no construction, so omit both rather than invent values."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))

            result = await _backend().resume_video("p-1", _request(tmp_path))

        assert result.seed is None
        assert result.provenance is None

    async def test_a_resume_reads_the_output_binding_as_it_stands_now(self, tmp_path: Path):
        """If users change bindings before resume, retrieve artifacts by the new bindings; failure means output_missing."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["output"] = [{"node": "9", "class_type": "SaveVideo"}]
        assert validate_definition(definition).valid
        moved = comfyui_endpoint_definition()
        moved["workflow"]["77"] = {"class_type": "SaveVideo", "inputs": {"video": ["8", 0]}}
        moved["bindings"]["output"] = [{"node": "77", "class_type": "SaveVideo"}]
        assert validate_definition(moved).valid

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _video_output()}))
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend(moved).resume_video("p-1", _request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"
        assert caught.value.params == {"nodes": "77"}

    async def test_a_lost_job_on_resume_becomes_resume_expired(self, tmp_path: Path):
        """Loss during resume becomes resume_expired; the worker marks failure and settles the pending call row accordingly."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json={}))
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue()))

            with pytest.raises(ResumeExpiredError) as caught:
                await _backend().resume_video("p-1", _request(tmp_path))

        assert caught.value.job_id == "p-1"


class TestStoppingTheRemote:
    """On local cancellation or timeout, do not abandon an unwanted execution on the user's GPU."""

    @staticmethod
    def _polling_cancelled(router: Any) -> None:
        router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
        router.get(f"{BASE_URL}/history/p-1").mock(side_effect=_cancel_here)

    @staticmethod
    def _version(router: Any, version: str | None) -> None:
        body = {"system": {"comfyui_version": version}} if version is not None else {"system": {}}
        router.get(f"{BASE_URL}/system_stats").mock(return_value=httpx.Response(200, json=body))

    async def test_a_new_enough_server_gets_one_cancel_call(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            self._polling_cancelled(router)
            self._version(router, "0.26.0")
            cancel = router.post(f"{BASE_URL}/api/jobs/p-1/cancel").mock(return_value=httpx.Response(200, json={}))
            queue = router.get(f"{BASE_URL}/queue")

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert cancel.call_count == 1
        # One action covers queued and running states without another queue query.
        assert queue.call_count == 0

    async def test_an_older_server_deletes_a_pending_entry(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            self._polling_cancelled(router)
            self._version(router, "0.25.14")
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(pending=["p-1"])))
            drop = router.post(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json={}))
            interrupt = router.post(f"{BASE_URL}/interrupt")

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert request_json(only_request(drop)) == {"delete": ["p-1"]}
        assert interrupt.call_count == 0

    async def test_an_older_server_interrupts_a_running_entry(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            self._polling_cancelled(router)
            self._version(router, "0.25.14")
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(running=["p-1"])))
            drop = router.post(f"{BASE_URL}/queue")
            interrupt = router.post(f"{BASE_URL}/interrupt").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert interrupt.call_count == 1
        assert drop.call_count == 0

    async def test_someone_elses_run_is_never_interrupted(self, tmp_path: Path):
        """``/interrupt`` stops the currently executing task and does not accept an ID."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            self._polling_cancelled(router)
            self._version(router, "0.25.14")
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(running=["other"])))
            drop = router.post(f"{BASE_URL}/queue")
            interrupt = router.post(f"{BASE_URL}/interrupt")

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert interrupt.call_count == 0
        assert drop.call_count == 0

    async def test_an_unreadable_version_takes_the_older_path(self, tmp_path: Path):
        """Older versions and some proxies omit this field; selecting the new path would get 404 and stop nothing."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            self._polling_cancelled(router)
            router.get(f"{BASE_URL}/system_stats").mock(return_value=httpx.Response(500, text="boom"))
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(pending=["p-1"])))
            drop = router.post(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert drop.call_count == 1

    async def test_a_failed_stop_leaves_the_local_outcome_alone(self, tmp_path: Path):
        """Interruption is best-effort: log remote rejection but still propagate the original cancellation."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            self._polling_cancelled(router)
            self._version(router, "0.26.1")
            cancel = router.post(f"{BASE_URL}/api/jobs/p-1/cancel").mock(return_value=httpx.Response(500, text="boom"))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert cancel.call_count == 1

    async def test_the_remote_is_stopped_before_a_timeout_surfaces(self, tmp_path: Path):
        """Global timeout also interrupts remote work: an unwanted half-finished execution still occupies the GPU."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json={}))
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(running=["p-1"])))
            self._version(router, "0.26.0")
            cancel = router.post(f"{BASE_URL}/api/jobs/p-1/cancel").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(TimeoutError):
                await _backend().generate(_request(tmp_path, poll_timeout_seconds=60))

        assert cancel.call_count == 1

    async def test_nothing_is_stopped_when_the_job_was_never_submitted(self, tmp_path: Path):
        """Before submission there is neither a prompt_id to stop nor a running execution."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{BASE_URL}/prompt").mock(side_effect=_cancel_here)
            stats = router.get(f"{BASE_URL}/system_stats")

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert stats.call_count == 0

    async def test_a_missing_version_field_takes_the_older_path(self, tmp_path: Path):
        """A reachable ``/system_stats`` without a version gets the same handling as an unreadable response."""
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            self._polling_cancelled(router)
            self._version(router, None)
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(pending=["p-1"])))
            drop = router.post(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert drop.call_count == 1
