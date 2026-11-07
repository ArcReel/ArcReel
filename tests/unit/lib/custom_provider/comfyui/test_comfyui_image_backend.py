"""ComfyUI image channel: capability derivation, reference-image upload, artifact allowlisting, no resume, and remote interruption.

The post-submission segment (polling, terminal-state detection, lost-state detection, trace summary) shares
``lib.custom_provider.comfyui.comfyui_execution`` with video and is pinned case by case by ``test_comfyui_video_backend.py``. This file
only covers image-specific behavior and shared guarantees that must still hold for images (allowlisting and interruption).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from arcreel_market_core.endpoint_definition import validate_definition
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.custom_provider.comfyui.comfyui_image_backend import ComfyuiImageBackend
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.endpoint_resolution import endpoint_spec_from_row
from lib.custom_provider.factory import create_custom_backend
from tests.factories import comfyui_endpoint_definition
from tests.fakes import PNG_BYTES
from tests.http_capture import capture_http, only_request, request_json

BASE_URL = "https://comfy.test"


def _definition(*, reference_slots: int = 0) -> dict[str, Any]:
    """A minimal usable image endpoint definition: the artifact node saves images, with optional reference-image slots.

    Image endpoint semantic keys exclude first/last frames and frame count/rate (see ``comfyui.bindings.IMAGE_BINDING_KEYS``), so rewrite
    the whole binding table rather than deleting video keys; deletion would leave ``output`` pointing at a video-saving node.
    """
    definition = comfyui_endpoint_definition(media_type="image")
    workflow = definition["workflow"]
    workflow["9"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["8", 0], "filename_prefix": "ArcReel"},
        "_meta": {"title": "存图"},
    }
    bindings: dict[str, Any] = {
        "prompt": [{"node": "6", "input": "text", "class_type": "CLIPTextEncode", "title": "正向"}],
        "negative_prompt": [{"node": "7", "input": "text", "class_type": "CLIPTextEncode", "title": "负向"}],
        "width": [{"node": "5", "input": "width", "class_type": "EmptyLatentImage", "step": 16}],
        "height": [{"node": "5", "input": "height", "class_type": "EmptyLatentImage", "step": 16}],
        "seed": [{"node": "3", "input": "seed", "class_type": "KSampler", "policy": "random"}],
        "output": [{"node": "9", "class_type": "SaveImage", "title": "存图"}],
    }
    if reference_slots:
        targets = []
        for slot in range(reference_slots):
            node_id = str(20 + slot)
            workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": f"draft-{slot}.png"}}
            targets.append({"node": node_id, "input": "image", "class_type": "LoadImage"})
        bindings["reference_images"] = targets
    definition["bindings"] = bindings
    assert validate_definition(definition).valid
    return definition


def _backend(definition: dict[str, Any] | None = None, *, api_key: str = "") -> ComfyuiImageBackend:
    return ComfyuiImageBackend(
        provider_id="custom-1",
        model="flux-workflow",
        base_url=BASE_URL,
        api_key=api_key,
        definition=definition if definition is not None else _definition(),
        job_label="job-7",
    )


def _request(tmp_path: Path, **overrides: Any) -> ImageGenerationRequest:
    values: dict[str, Any] = {"prompt": "一只猫走过屋顶", "output_path": tmp_path / "out.png", "aspect_ratio": "9:16"}
    values.update(overrides)
    return ImageGenerationRequest(**values)


def _history(outputs: dict[str, Any]) -> dict[str, Any]:
    return {"status": {"completed": True, "status_str": "success"}, "outputs": outputs}


def _image_output(filename: str = "ArcReel_00001_.png") -> dict[str, Any]:
    return {"images": [{"filename": filename, "subfolder": "", "type": "output"}]}


def _cancel_here(_request: httpx.Request) -> httpx.Response:
    """Make a request on this route encounter task cancellation.

    ``CancelledError`` inherits ``BaseException``, while exception-valued respx ``side_effect`` accepts only ``Exception``;
    therefore raise through its callable path.
    """
    raise asyncio.CancelledError


def _queue(*, running: tuple[str, ...] = (), pending: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "queue_running": [[index, job, {}, [], {}] for index, job in enumerate(running)],
        "queue_pending": [[index, job, {}, [], {}] for index, job in enumerate(pending)],
    }


class TestDeclaredCapabilities:
    """Reference-image slots determine whether a workflow is exclusively text-to-image or image-to-image; these are mutually exclusive (``docs/adr/0082``)."""

    def test_a_workflow_without_reference_slots_is_text_to_image_only(self):
        backend = _backend()

        assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE}
        assert asyncio.run(backend.reference_image_limit()) is None

    def test_a_workflow_with_reference_slots_is_image_to_image_only(self):
        """With slots but no supplied image, construction must remove image-loading nodes or submit an empty image; neither means text-to-image support."""
        backend = _backend(_definition(reference_slots=2))

        assert backend.capabilities == {ImageCapability.IMAGE_TO_IMAGE}
        assert asyncio.run(backend.reference_image_limit()) == 2

    @pytest.mark.parametrize(
        ("reference_slots", "expected"),
        [(0, ["text_to_image"]), (3, ["image_to_image"])],
    )
    def test_the_endpoint_projection_says_the_same_thing(self, reference_slots: int, expected: list[str]):
        """Model-row buckets read endpoint projections; the pre-request fallback gate reads the backend's own declaration.

        Separate criteria would conflict: an endpoint admitted to the i2i bucket would block itself at the gate.
        """
        definition = _definition(reference_slots=reference_slots)
        spec = endpoint_spec_from_row(cast("Any", SimpleNamespace(id=7, definition=definition)))

        assert sorted(cap.value for cap in spec.image_capabilities or frozenset()) == expected

    def test_the_factory_wraps_an_image_backend_in_pure_forwarding(self):
        """The image wrapper injects no capabilities: the whole capability_overrides section is disabled for ComfyUI."""
        from lib.custom_provider.backends import CustomImageBackend

        definition = _definition(reference_slots=1)
        spec = endpoint_spec_from_row(cast("Any", SimpleNamespace(id=7, definition=definition)))
        provider = SimpleNamespace(provider_id="custom-1", base_url=BASE_URL, api_key="")

        backend = create_custom_backend(
            provider=cast("Any", provider), model_id="flux-workflow", endpoint="ce-7", endpoint_spec=spec
        )

        assert isinstance(backend, CustomImageBackend)
        assert backend.capabilities == {ImageCapability.IMAGE_TO_IMAGE}
        assert asyncio.run(backend.reference_image_limit()) == 1

    async def test_the_reference_limit_is_the_slot_count_and_ignores_the_matched_entry(self):
        """Even when the model-row name matches an entry supporting 14 images, the effective limit is the workflow's 2 slots; orchestration trims accordingly and candidates use only bindings."""
        from lib.backends.generation_type_buckets import custom_model_buckets
        from lib.backends.image_capabilities import backend_reference_image_limit
        from lib.backends.input_limits import InputLimit

        definition = _definition(reference_slots=2)
        spec = endpoint_spec_from_row(cast("Any", SimpleNamespace(id=7, definition=definition)))
        provider = SimpleNamespace(provider_id="custom-1", base_url=BASE_URL, api_key="")
        backend = create_custom_backend(
            provider=cast("Any", provider), model_id="gemini-3-pro-image", endpoint="ce-7", endpoint_spec=spec
        )

        assert await backend_reference_image_limit(backend, provider_id="custom-1") == InputLimit.at_most(2)
        assert custom_model_buckets(endpoint="ce-7", model_id="gemini-3-pro-image", endpoint_spec=spec) == {"i2i"}


class TestGenerate:
    async def test_an_image_to_image_generation_uploads_submits_polls_and_stores_the_artifact(self, tmp_path: Path):
        """End-to-end: fill references, submit, poll, and write the artifact to output_path; return the actual submitted seed with the result."""
        definition = _definition(reference_slots=2)
        reference = tmp_path / "ref.jpg"
        reference.write_bytes(b"jpg-bytes")

        with capture_http() as router:
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "job-7-reference_images-1.jpg", "subfolder": "arcreel"})
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(
                return_value=httpx.Response(200, json={"prompt_id": "p-1", "node_errors": {}})
            )
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json={"p-1": _history({"9": _image_output()})})
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            result = await _backend(definition).generate(
                _request(tmp_path, reference_images=[ReferenceImage(path=str(reference))])
            )

        assert result.image_path.read_bytes() == PNG_BYTES
        assert (result.provider, result.model) == ("custom-1", "flux-workflow")
        assert history.call_count == 1
        # Use response subfolder / name, not request values; the server renames collisions.
        sent = request_json(only_request(submit))
        assert sent["prompt"]["20"]["inputs"]["image"] == "arcreel/job-7-reference_images-1.jpg"
        assert sent["client_id"] == "arcreel-job-7"
        assert upload.call_count == 1
        assert only_request(view).url.params["filename"] == "ArcReel_00001_.png"
        # The seed is freshly randomized at submission; without returning it, this version cannot be reproduced.
        assert result.seed == sent["prompt"]["3"]["inputs"]["seed"]
        assert result.image_uri == f"{BASE_URL}/view?filename=ArcReel_00001_.png&subfolder=&type=output"

    async def test_excess_references_rejected_before_upload_or_submit(self, tmp_path: Path):
        references = [ReferenceImage(path=str(tmp_path / f"ref{i}.png")) for i in range(3)]
        with capture_http() as router, pytest.raises(ImageCapabilityError) as exc:
            await _backend(_definition(reference_slots=1)).generate(_request(tmp_path, reference_images=references))

        assert exc.value.code == "image_reference_images_exceeded"
        assert exc.value.params["limit"] == 1
        assert exc.value.params["count"] == 3
        assert not router.calls

    async def test_a_text_to_image_generation_uploads_nothing(self, tmp_path: Path):
        with capture_http() as router:
            upload = router.post(f"{BASE_URL}/upload/image")
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            await _backend().generate(_request(tmp_path))

        assert upload.call_count == 0

    async def test_the_requested_size_is_derived_on_the_image_tier_table(self, tmp_path: Path):
        """Image and video resolution tiers use different short-side tables; construction selects by the definition's media_type."""
        with capture_http() as router:
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output()}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            await _backend().generate(_request(tmp_path, aspect_ratio="1:1", image_size="1k"))

        latent = request_json(only_request(submit))["prompt"]["5"]["inputs"]
        assert latent["width"] == latent["height"]
        # The 1k short side exceeds the literal 832×480 workflow size, proving tier selection takes effect.
        assert latent["width"] > 832

    async def test_the_credentials_ride_every_route(self, tmp_path: Path):
        definition = _definition(reference_slots=1)
        definition["auth"] = {"headers": {"Authorization": "Bearer {{api_key}}"}}
        assert validate_definition(definition).valid
        reference = tmp_path / "ref.png"
        reference.write_bytes(b"png")

        with capture_http() as router:
            upload = router.post(f"{BASE_URL}/upload/image").mock(
                return_value=httpx.Response(200, json={"name": "stored.png", "subfolder": "arcreel"})
            )
            submit = router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            history = router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output()}))
            )
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            await _backend(definition, api_key="k-1").generate(
                _request(tmp_path, reference_images=[ReferenceImage(path=str(reference))])
            )

        for route in (upload, submit, history, view):
            assert route.calls.last.request.headers["authorization"] == "Bearer k-1"


class TestArtifactWhitelist:
    """Check whether the artifact is the endpoint's expected media type by extension first, then verify the container header after writing."""

    @pytest.mark.parametrize("filename", ["out.png", "out.jpg", "out.jpeg", "out.webp", "OUT.PNG"])
    async def test_a_whitelisted_suffix_is_stored(self, tmp_path: Path, filename: str):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output(filename)}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            result = await _backend().generate(_request(tmp_path))

        assert result.image_path.read_bytes() == PNG_BYTES

    @pytest.mark.parametrize("filename", ["out.gif", "out.apng", "out.mp4"])
    async def test_an_animated_or_video_artifact_is_a_type_mismatch(self, tmp_path: Path, filename: str):
        """An image endpoint yielding animation has its output binding on a video-composition node; ingestion would retain only its first frame."""
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output(filename)}))
            )
            view = router.get(f"{BASE_URL}/view")

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_type_mismatch"
        assert caught.value.params == {"filename": filename, "media_type": "image"}
        assert view.call_count == 0

    async def test_bytes_that_are_not_an_image_container_are_refused(self, tmp_path: Path):
        """If the header is not PNG / JPEG / WEBP, delete the written file and report container mismatch instead of retaining an unreadable image."""
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output("out.png")}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=b"GIF89a-not-a-png"))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_container_mismatch"
        assert caught.value.params == {"filename": "out.png", "media_type": "image"}
        assert not (tmp_path / "out.png").exists()

    @pytest.mark.parametrize(
        "content", [b"\xff\xd8\xff\xe0-jpeg", b"RIFF\x10\x00\x00\x00WEBPVP8 "], ids=["jpeg", "webp"]
    )
    async def test_the_other_whitelisted_containers_pass_the_head_check(self, tmp_path: Path, content: bytes):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"9": _image_output("out.jpg")}))
            )
            router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=content))

            result = await _backend().generate(_request(tmp_path))

        assert result.image_path.read_bytes() == content

    async def test_nothing_produced_by_the_output_node_is_its_own_code(self, tmp_path: Path):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(
                return_value=httpx.Response(200, json=_history({"99": _image_output()}))
            )

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_output_missing"
        assert caught.value.params == {"nodes": "9"}


class TestMultipleArtifacts:
    async def test_a_batch_of_images_yields_the_first_one_and_a_warning(self, tmp_path: Path, caplog):
        """A ``batch_size > 1`` workflow produces several images; this channel takes the first.

        Log this warning only: ``ImageGenerationResult`` lacks the video channel's ``warnings`` field, and image-call
        entry points do not collect runtime notices. Endpoint-import binding inference separately shows ``batch_size_above_one`` in the UI.
        """
        outputs = {
            "9": {
                "images": [
                    {"filename": "ArcReel_00001_.png", "subfolder": "", "type": "output"},
                    {"filename": "ArcReel_00002_.png", "subfolder": "", "type": "output"},
                ]
            }
        }

        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            with caplog.at_level("WARNING"):
                await _backend().generate(_request(tmp_path))

        assert only_request(view).url.params["filename"] == "ArcReel_00001_.png"
        assert "共 2 个" in caplog.text

    async def test_a_temp_preview_beside_the_image_is_not_taken(self, tmp_path: Path):
        """Entries with ``type != "output"`` are intermediate previews that the server may clear at any time."""
        outputs = {
            "9": {
                "images": [
                    {"filename": "preview.png", "subfolder": "", "type": "temp"},
                    {"filename": "ArcReel_00001_.png", "subfolder": "", "type": "output"},
                ]
            }
        }

        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json=_history(outputs)))
            view = router.get(f"{BASE_URL}/view").mock(return_value=httpx.Response(200, content=PNG_BYTES))

            await _backend().generate(_request(tmp_path))

        assert only_request(view).url.params["filename"] == "ArcReel_00001_.png"


class TestNoResume:
    def test_the_image_backend_offers_no_resume_entry(self):
        """Image tasks do not resume: nowhere persists ``prompt_id`` (requests lack task_id), so all orphans become restart_lost.

        Worker orphan handling branches by media_type without inspecting backend methods, so this only pins absence
        of that backend method; adding it would misleadingly imply image tasks can resume.
        """
        backend = _backend()

        assert not hasattr(backend, "resume_video")
        assert not hasattr(backend, "resume")

    async def test_a_lost_job_is_a_plain_generation_failure(self, tmp_path: Path):
        """ComfyUI restart discards the queue and executions not yet in history: classify as retryable generation failure, not resume expiry."""
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(return_value=httpx.Response(200, json={}))
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue()))

            with pytest.raises(ComfyuiError) as caught:
                await _backend().generate(_request(tmp_path))

        assert caught.value.code == "comfyui_job_lost"
        assert caught.value.params == {"prompt_id": "p-1"}


class TestStoppingTheRemote:
    """No resume does not mean no interruption: cancellation/timeout already has ``prompt_id``, while remote work still occupies the user's GPU."""

    @staticmethod
    def _version(router: Any, version: str) -> None:
        router.get(f"{BASE_URL}/system_stats").mock(
            return_value=httpx.Response(200, json={"system": {"comfyui_version": version}})
        )

    async def test_a_cancelled_generation_stops_the_remote_job(self, tmp_path: Path):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(side_effect=_cancel_here)
            self._version(router, "0.26.0")
            cancel = router.post(f"{BASE_URL}/api/jobs/p-1/cancel").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert cancel.call_count == 1

    async def test_an_older_server_interrupts_a_running_entry(self, tmp_path: Path):
        with capture_http() as router:
            router.post(f"{BASE_URL}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
            router.get(f"{BASE_URL}/history/p-1").mock(side_effect=_cancel_here)
            self._version(router, "0.25.14")
            router.get(f"{BASE_URL}/queue").mock(return_value=httpx.Response(200, json=_queue(running=("p-1",))))
            interrupt = router.post(f"{BASE_URL}/interrupt").mock(return_value=httpx.Response(200, json={}))

            with pytest.raises(asyncio.CancelledError):
                await _backend().generate(_request(tmp_path))

        assert interrupt.call_count == 1
