"""API 格式 MiniMax H3 样本的参考音频与画幅契约；HTTP 全部由 respx 捕获。"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from arcreel_market_core.endpoint_definition import AssetData, validate_definition
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.video_frame_slots import gate_video_request
from lib.custom_provider.comfyui.comfyui_backend import ComfyuiVideoBackend
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.comfyui.inference import BindingState, infer_bindings
from lib.custom_provider.comfyui.request_builder import MediaInputs, build_workflow
from lib.custom_provider.endpoint_test import EndpointTestAssets, EndpointTestParameters, preview_comfyui_request
from lib.custom_provider.endpoints import comfyui_endpoint_spec
from tests.factories import comfyui_endpoint_definition
from tests.fakes import MP4_BYTES
from tests.http_capture import capture_http, only_request, request_json

BASE = "https://comfy.test"


@pytest.fixture
def definition():
    document = comfyui_endpoint_definition()
    document["workflow"] = json.loads((Path(__file__).parent / "data/minimax_h3_ref2va.json").read_text())
    document["bindings"] = {
        "prompt": [{"node": "138", "input": "value", "class_type": "PrimitiveStringMultiline"}],
        "output": [{"node": "92", "class_type": "SaveVideo"}],
        "reference_audio": [
            {"node": node, "input": "audio", "class_type": "LoadAudio"} for node in ("501", "502", "503")
        ],
        "aspect_ratio": [
            {
                "node": "115",
                "input": "aspect_ratio",
                "class_type": "ResolutionSelector",
                "values": {"16:9": "16:9 (Widescreen)", "9:16": "9:16 (Portrait)"},
            }
        ],
    }
    return document


def backend(definition):
    return ComfyuiVideoBackend(provider_id="local", model="ref2va", base_url=BASE, api_key="", definition=definition)


def test_saved_bindings_reimport_with_mapping_and_audio_order(definition):
    assert validate_definition(definition).valid
    saved = json.loads(json.dumps(definition))
    saved["workflow"]["215"] = saved["workflow"].pop("115")
    for name in ("width", "height"):
        saved["workflow"]["136"]["inputs"][name][0] = "215"
    inferred = infer_bindings(saved)
    aspect = inferred.keys["aspect_ratio"].selected_targets[0]
    assert aspect["node"] == "215"
    assert aspect["values"] == {"16:9": "16:9 (Widescreen)", "9:16": "9:16 (Portrait)"}
    assert [target["node"] for target in inferred.keys["reference_audio"].selected_targets] == ["501", "502", "503"]
    del saved["workflow"]["501"]
    assert not validate_definition(saved).valid
    assert infer_bindings(saved).keys["reference_audio"].state is BindingState.NEEDS_CONFIRMATION


def test_audio_capacity_and_pixel_resolution_are_derived_independently(definition, tmp_path):
    spec = comfyui_endpoint_spec("ce-7", definition)
    caps = backend(definition).video_capabilities
    assert spec.reference_audio_capable
    assert spec.size_fixed
    assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
    assert caps.max_reference_audio_count == 3
    assert caps.max_reference_audio_total_seconds is None
    definition["workflow"]["130"]["inputs"].pop("audio")
    assert backend(definition).video_capabilities.audio_track is VideoAudioMode.ALWAYS_OFF
    with pytest.raises(VideoCapabilityError, match="video_reference_audio_exceeded"):
        gate_video_request(
            caps=caps, provider="local", model="ref2va", has_image=False, reference_audio_files=[tmp_path / "a.wav"] * 4
        )


@pytest.mark.parametrize(
    "fault",
    [
        "unsupported",
        "overflow",
        "missing_mapping",
        "required_audio",
        "unbound_audio",
        "reference_video",
    ],
)
async def test_invalid_requests_fail_before_any_upload(definition, tmp_path, fault):
    count = 1
    expected = ComfyuiError
    if fault == "unsupported":
        definition["bindings"]["reference_audio"] = []
        expected = VideoCapabilityError
    elif fault == "overflow":
        count = 4
        expected = VideoCapabilityError
    elif fault == "missing_mapping":
        del definition["bindings"]["aspect_ratio"][0]["values"]["9:16"]
    elif fault == "required_audio":
        definition["workflow"]["136"]["class_type"] = "UnknownAudioConsumer"
    elif fault == "unbound_audio":
        definition["bindings"]["reference_audio"].pop()
    elif fault == "reference_video":
        definition["workflow"]["136"]["inputs"]["ref_videos.ref_video_0"] = ["502", 0]
    request = VideoGenerationRequest(
        prompt="<Audio 1>",
        output_path=tmp_path / "out.mp4",
        aspect_ratio="9:16",
        reference_audio_files=[tmp_path / f"{i}.wav" for i in range(count)],
    )
    with capture_http() as router, pytest.raises(expected):
        await backend(definition).generate(request)
    assert not router.calls


@pytest.mark.parametrize("count", [0, 1, 3])
async def test_uploaded_audio_bytes_and_workflow_inputs_keep_request_order(definition, tmp_path, count):
    paths = [tmp_path / f"voice-{i}.wav" for i in range(count)]
    for i, path in enumerate(paths):
        await asyncio.to_thread(path.write_bytes, f"audio-payload-{i}".encode())
    with capture_http() as router:
        upload = router.post(f"{BASE}/upload/image").mock(
            side_effect=[
                httpx.Response(200, json={"name": f"renamed-{i}.wav", "subfolder": "audio"}) for i in range(count)
            ]
        )
        submit = router.post(f"{BASE}/prompt").mock(return_value=httpx.Response(200, json={"prompt_id": "p-1"}))
        router.get(f"{BASE}/history/p-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "p-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"92": {"images": [{"filename": "final.mp4", "subfolder": "", "type": "output"}]}},
                    }
                },
            )
        )
        router.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=MP4_BYTES))
        result = await backend(definition).generate(
            VideoGenerationRequest(
                prompt="<Audio 1> then <Audio 2>",
                output_path=tmp_path / "out.mp4",
                aspect_ratio="9:16",
                reference_audio_files=paths,
                task_id="audio-test",
            )
        )
    assert await asyncio.to_thread(result.video_path.read_bytes) == MP4_BYTES
    sent = request_json(only_request(submit))["prompt"]
    inputs = sent["136"]["inputs"]
    ports = sorted(key for key in inputs if key.startswith("ref_audios."))
    assert [sent[inputs[key][0]]["inputs"]["audio"] for key in ports] == [
        f"audio/renamed-{i}.wav" for i in range(count)
    ]
    for i, call in enumerate(upload.calls):
        assert f"audio-payload-{i}".encode() in call.request.content
        assert f'filename="audio-test-reference_audio-{i + 1}.wav"'.encode() in call.request.content
    assert len(upload.calls) == count
    assert sent["115"]["inputs"]["aspect_ratio"] == "9:16 (Portrait)"
    assert sent["138"]["inputs"]["value"] == "<Audio 1> then <Audio 2>"


def test_preview_consumes_all_audio_and_never_truncates(definition):
    assets = EndpointTestAssets(
        by_source={"reference_audio_files": [AssetData(content=b"voice", mime_type="audio/wav")]}
    )
    params = EndpointTestParameters(model="ref2va", prompt="<Audio 1>")
    preview = preview_comfyui_request(definition, params, assets=assets)
    assert isinstance(preview.submit.body, dict)
    graph = preview.submit.body["prompt"]
    assert graph["501"]["inputs"]["audio"] == "<reference_audio_files, 5 bytes>"
    assert "ref_audios.ref_audio_1" not in graph["136"]["inputs"]
    too_many = EndpointTestAssets(
        by_source={"reference_audio_files": [AssetData(content=b"voice", mime_type="audio/wav")] * 4}
    )
    with pytest.raises(VideoCapabilityError, match="video_reference_audio_exceeded"):
        preview_comfyui_request(definition, params, assets=too_many)


@pytest.mark.parametrize("values", [{}, {"bad": "portrait"}, {"9:16": " "}, {"9:16": 2}])
def test_invalid_aspect_mapping_is_rejected_on_save(definition, values):
    definition["bindings"]["aspect_ratio"][0]["values"] = values
    assert not validate_definition(definition).valid


def test_enum_only_does_not_write_pixels_and_invalid_binding_fails(definition):
    before = deepcopy(definition)
    built = build_workflow(definition, prompt="p", aspect_ratio="16:9", resolution="1080p")
    assert built.width is None
    assert built.height is None
    assert built.workflow["115"]["inputs"]["megapixels"] == 0.4
    assert definition == before
    definition["bindings"]["aspect_ratio"][0]["input"] = "gone"
    assert not validate_definition(definition).valid


def test_reference_audio_binding_to_a_missing_node_is_rejected_on_save(definition):
    del definition["workflow"]["501"]
    assert not validate_definition(definition).valid


@pytest.mark.parametrize("indirect", [False, True])
def test_background_soundtrack_is_not_inferred_as_reference_audio(indirect):
    definition = comfyui_endpoint_definition()
    definition["workflow"]["99"] = {"class_type": "LoadAudio", "inputs": {"audio": "background.wav"}}
    definition["workflow"]["9"]["inputs"]["audio"] = ["99", 0]
    if indirect:
        definition["workflow"]["98"] = {"class_type": "PrimitiveString", "inputs": {"value": "background.wav"}}
        definition["workflow"]["99"]["inputs"]["audio"] = ["98", 0]
    inferred = infer_bindings(definition)
    assert inferred.keys["reference_audio"].state is BindingState.NOT_FOUND
    built = build_workflow(definition, prompt="p", aspect_ratio="9:16")
    assert built.workflow["99"]["inputs"]["audio"] == (["98", 0] if indirect else "background.wav")


def test_inferred_aspect_is_only_offered_so_definitions_without_it_stay_savable(definition):
    del definition["bindings"]["aspect_ratio"]
    result = infer_bindings(definition)
    aspect = result.keys["aspect_ratio"]
    assert aspect.state is BindingState.NOT_FOUND
    assert not aspect.selected_targets
    assert [(candidate.node, candidate.input) for candidate in aspect.candidates] == [("115", "aspect_ratio")]
    assert result.savable


def test_preview_without_uploaded_audio_keeps_bound_slots_with_placeholders(definition):
    params = EndpointTestParameters(model="ref2va", prompt="<Audio 1>")
    preview = preview_comfyui_request(definition, params)
    assert isinstance(preview.submit.body, dict)
    graph = preview.submit.body["prompt"]
    assert [graph[node]["inputs"]["audio"] for node in ("501", "502", "503")] == [
        "<reference_audio_files not uploaded>"
    ] * 3


def test_indirect_audio_inputs_keep_order_and_reject_unbound_stale_audio(definition):
    for i, loader in enumerate(("503", "501", "502")):
        node = str(600 + i)
        definition["workflow"][node] = {
            "class_type": "TrimAudioDuration",
            "inputs": {"audio": [loader, 0], "duration": 2},
        }
        definition["workflow"]["136"]["inputs"][f"ref_audios.ref_audio_{i}"] = [node, 0]
    built = build_workflow(
        definition,
        prompt="p",
        aspect_ratio="9:16",
        media=MediaInputs(reference_audio_files=["a.wav", "b.wav", "c.wav"]),
    )
    assert [built.workflow["136"]["inputs"][f"ref_audios.ref_audio_{i}"] for i in range(3)] == [
        ["601", 0],
        ["602", 0],
        ["600", 0],
    ]
    assert [built.workflow[node]["inputs"]["audio"] for node in ("501", "502", "503")] == ["a.wav", "b.wav", "c.wav"]
    definition["bindings"]["reference_audio"].pop()
    with pytest.raises(ComfyuiError, match="comfyui_reference_audio_invalid"):
        build_workflow(
            definition, prompt="p", aspect_ratio="9:16", media=MediaInputs(reference_audio_files=["a.wav", "b.wav"])
        )
