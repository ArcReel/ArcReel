"""The shipped local endpoint must drive real renderer controls, not inert UI knobs."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from arcreel_market_core.comfyui.validator import validate_comfyui_definition
from lib.custom_provider.comfyui.capabilities import derive_video_capabilities, duration_is_fixed, size_is_fixed
from lib.custom_provider.comfyui.request_builder import MediaInputs, build_workflow

ROOT = Path(__file__).resolve().parents[5]
DEPLOY = ROOT / "deploy/comfyui"


@pytest.fixture
def endpoint():
    return json.loads((DEPLOY / "local-ltx-endpoint.json").read_text(encoding="utf-8"))


@pytest.fixture
def renderer():
    spec = importlib.util.spec_from_file_location("local_mac_runner", DEPLOY / "mac_quality_video/runner.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_endpoint_advertises_only_connected_capabilities(endpoint):
    assert not validate_comfyui_definition(endpoint).errors
    caps = derive_video_capabilities(endpoint)
    assert caps.first_frame
    assert caps.last_frame
    assert caps.max_reference_images == 0  # Temporal anchors are not identity-reference conditioning.
    assert caps.audio_track == "always_on"
    assert not duration_is_fixed(endpoint["bindings"])
    assert size_is_fixed(endpoint["bindings"])


@pytest.mark.parametrize(("seconds", "frames"), [(2, 49), (4, 97), (5, 121)])
@pytest.mark.parametrize("with_end", [False, True])
def test_request_reaches_real_cli_controls(endpoint, renderer, tmp_path, seconds, frames, with_end):
    start, end = tmp_path / "start.png", tmp_path / "end.png"
    start.touch()
    end.touch()
    prompt = "Remi turns toward the backpack.\nAvoid: slow motion, music, changing her face."
    built = build_workflow(
        endpoint,
        prompt=prompt,
        aspect_ratio="16:9",
        duration_seconds=seconds,
        seed=4_000_000_000,
        media=MediaInputs(start_image=start.name, end_image=end.name if with_end else None),
    )
    inputs = built.workflow["2"]["inputs"]
    assert built.workflow["5"]["class_type"] == "SaveVideo"
    assert built.workflow["4"]["inputs"]["fps"] == 24
    assert inputs["prompt"] == prompt
    assert inputs["frames"] == frames
    assert inputs["seed"] == 4_000_000_000
    assert built.workflow["1"]["inputs"]["image"] == start.name
    config = {
        "name": "contract-test",
        "mode": "distilled",
        "width": 1024,
        "height": 576,
        "recipe": "animated-dialogue",
        "frames": inputs["frames"],
        "seed": inputs["seed"],
        "prompt": inputs["prompt"],
        "image": str(start),
    }
    if with_end:
        assert built.workflow["6"]["inputs"]["image"] == end.name
        assert inputs["end_image"] == ["6", 0]
        config["end_image"] = str(end)
        model = tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
        model.mkdir(parents=True)
        for filename in ("transformer-dev.safetensors", "transformer-distilled-1.1.safetensors"):
            (model / filename).touch()
    else:
        assert "6" not in built.workflow
        assert "end_image" not in inputs
    command = renderer.build_command(config, tmp_path)
    assert command[command.index("--frames") + 1] == str(frames)
    assert command[command.index("--seed") + 1] == "4000000000"
    assert command[command.index("--prompt") + 1] == prompt
    if with_end:
        assert command[3] == "keyframe"
        assert command[command.index("--start") + 1] == str(start)
        assert command[command.index("--end") + 1] == str(end)
        assert command[command.index("--dev-transformer") + 1] == "transformer-dev.safetensors"
        assert command[command.index("--distilled-lora") + 1] == "ltx-2.3-22b-distilled-lora-384.safetensors"
        assert command[command.index("--lora-strength") + 1] == "1.0"
        assert "--image" not in command
        assert "--distilled" not in command
    else:
        anchors = [command[i + 1 : i + 4] for i, value in enumerate(command) if value == "--image"]
        assert anchors == [[str(start), "0", "1.0"]]
        assert "--distilled" in command
    assert "--low-ram" in command
    assert "--end-image" not in command  # Not supported by this installed CLI revision.


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"cfg_scale": 4}, "Unsupported renderer controls"),
        ({"audio": "voice.wav"}, "Unsupported renderer controls"),
        ({"frames": 120}, "memory preset supports"),
        ({"seed": -1}, "unsigned 32-bit"),
        ({"seed": True}, "unsigned 32-bit"),
        ({"seed": 2**32}, "unsigned 32-bit"),
        ({"mode": "hq"}, "supports distilled or two-stage"),
        ({"name": "../overwrite"}, "Run name"),
        ({"image": "missing.png"}, "Missing image"),
        ({"end_image": "end.png"}, "end anchor requires"),
    ],
)
def test_unsupported_or_invalid_controls_fail_before_launch(renderer, tmp_path, override, message):
    config = {
        "name": "test",
        "mode": "distilled",
        "width": 1024,
        "height": 576,
        "frames": 49,
        "seed": 1,
        "prompt": "A single action",
        "recipe": "animated-dialogue",
    }
    config.update(override)
    with pytest.raises(ValueError, match=message):
        renderer.build_command(config, tmp_path)


@pytest.mark.parametrize("missing", ["transformer-dev.safetensors", "transformer-distilled-1.1.safetensors"])
def test_end_anchor_never_falls_back_to_distilled_when_keyframe_weights_missing(renderer, tmp_path, missing):
    image = tmp_path / "anchor.png"
    image.touch()
    model = tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
    model.mkdir(parents=True)
    for filename in ("transformer-dev.safetensors", "transformer-distilled-1.1.safetensors"):
        if filename != missing:
            (model / filename).touch()
    config = {
        "name": "missing-model",
        "mode": "distilled",
        "width": 1024,
        "height": 576,
        "frames": 49,
        "seed": 1,
        "prompt": "Move between anchors",
        "image": str(image),
        "end_image": str(image),
    }
    with pytest.raises(ValueError, match=f"Keyframe interpolation requires installed {missing}"):
        renderer.build_command(config, tmp_path)


def test_end_anchored_animated_preset_keeps_dev_memory_admission_guard(renderer, tmp_path, monkeypatch):
    image = tmp_path / "anchor.png"
    image.touch()
    model = tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
    model.mkdir(parents=True)
    for filename in ("transformer-dev.safetensors", "transformer-distilled-1.1.safetensors"):
        (model / filename).touch()
    config = {
        "name": "memory-guard",
        "mode": "distilled",
        "width": 1024,
        "height": 576,
        "frames": 49,
        "seed": 1,
        "prompt": "Move between anchors",
        "recipe": "animated-dialogue",
        "image": str(image),
        "end_image": str(image),
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=18 * 2**30))
    )
    with pytest.raises(RuntimeError, match="At least 19 GiB"):
        renderer.run(path)
