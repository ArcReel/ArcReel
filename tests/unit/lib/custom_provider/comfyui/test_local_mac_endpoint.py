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


@pytest.fixture
def motion_config(tmp_path):
    files = {name: tmp_path / name for name in ("image", "control_lora", "control_video", "distilled_lora")}
    for path in files.values():
        path.touch()
    model = tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
    model.mkdir(parents=True)
    (model / "transformer-dev.safetensors").touch()
    return {
        "name": "motion-qualification",
        "mode": "motion-track",
        "width": 1024,
        "height": 576,
        "frames": 49,
        "seed": 20261025,
        "prompt": "Remi raises her hand.",
        **{key: str(path) for key, path in files.items()},
    }


@pytest.mark.parametrize(
    ("mode", "width", "height", "frames"),
    [
        ("motion-track", 1024, 576, 49),
        ("motion-track", 768, 448, 121),
        ("identity-reference", 768, 448, 121),
        ("structural-control", 768, 448, 49),
    ],
)
def test_control_reaches_single_stage_cli_with_dev_recipe_and_reference(
    renderer, motion_config, tmp_path, mode, width, height, frames
):
    motion_config.update(mode=mode, width=width, height=height, frames=frames)
    command = renderer.build_command(motion_config, tmp_path)
    assert command[3] == "ic-lora"
    assert command[command.index("--lora") + 1 : command.index("--lora") + 3] == [
        motion_config["control_lora"],
        "1.0",
    ]
    assert command[command.index("--video-conditioning") + 1 : command.index("--video-conditioning") + 3] == [
        motion_config["control_video"],
        "1.0",
    ]
    assert command[command.index("--image") + 1 : command.index("--image") + 4] == [
        motion_config["image"],
        "0",
        "1.0",
    ]
    assert "--single-stage" in command
    assert "--low-ram" in command
    assert command[command.index("--dev-transformer") + 1] == "transformer-dev.safetensors"
    assert command[command.index("--distilled-lora") + 1] == motion_config["distilled_lora"]
    assert command[command.index("--distilled-lora-strength") + 1] == "0.5"
    assert "--two-stage" not in command


def test_identity_reference_can_condition_a_new_composition_without_a_start_image(renderer, motion_config, tmp_path):
    motion_config.update(mode="identity-reference", width=768, height=448, frames=121)
    motion_config.pop("image")
    command = renderer.build_command(motion_config, tmp_path)
    assert command[3] == "ic-lora"
    assert command[command.index("--video-conditioning") + 1] == motion_config["control_video"]
    assert "--image" not in command


@pytest.fixture
def ingredients_distilled_config(motion_config, tmp_path):
    motion_config.update(mode="identity-reference", width=768, height=448, frames=121, recipe="ingredients-distilled")
    motion_config.pop("image")
    motion_config.pop("distilled_lora")
    model = tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
    (model / "transformer-dev.safetensors").unlink()
    (model / "transformer-distilled-1.1.safetensors").touch()
    return motion_config


def test_published_ingredients_recipe_uses_full_distilled_checkpoint(renderer, ingredients_distilled_config, tmp_path):
    command = renderer.build_command(ingredients_distilled_config, tmp_path)
    assert command[3] == "ic-lora"
    assert command[command.index("--lora") + 2] == "1.4"
    assert command[command.index("--video-conditioning") + 1] == ingredients_distilled_config["control_video"]
    assert "--single-stage" in command
    assert not {"--dev-transformer", "--distilled-lora", "--image", "--sampler"}.intersection(command)


@pytest.mark.parametrize(
    "override",
    [
        {"mode": "structural-control"},
        {"image": "unused.png"},
        {"end_image": "unused.png"},
        {"distilled_lora": "unused.safetensors"},
        {"sampler": "euler_ancestral_cfg_pp"},
        {"control_video": None},
    ],
)
def test_ingredients_recipe_does_not_silently_mix_other_controls(
    renderer, ingredients_distilled_config, tmp_path, override
):
    ingredients_distilled_config.update(override)
    with pytest.raises(ValueError, match=r"[Ii]ngredients"):
        renderer.build_command(ingredients_distilled_config, tmp_path)


def test_ingredients_recipe_refuses_missing_distilled_checkpoint(renderer, ingredients_distilled_config, tmp_path):
    (tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1/transformer-distilled-1.1.safetensors").unlink()
    with pytest.raises(ValueError, match=r"requires installed transformer-distilled-1\.1"):
        renderer.build_command(ingredients_distilled_config, tmp_path)


def test_explicit_cfgpp_sampler_reaches_cli_without_changing_default(renderer, motion_config, tmp_path):
    assert "--sampler" not in renderer.build_command(motion_config, tmp_path)
    motion_config.update(sampler="euler_ancestral_cfg_pp", negative_prompt="unwanted movement")
    command = renderer.build_command(motion_config, tmp_path)
    assert command[command.index("--sampler") + 1] == "euler_ancestral_cfg_pp"
    assert command[command.index("--negative-prompt") + 1] == "unwanted movement"
    assert "--single-stage" in command


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"sampler": "other"}, "Unsupported qualification sampler"),
        ({"mode": "distilled", "sampler": "euler"}, "require IC-LoRA"),
        ({"negative_prompt": 3}, "must be text"),
        ({"negative_prompt": "blur"}, "requires euler_ancestral_cfg_pp"),
    ],
)
def test_sampler_controls_are_never_silently_dropped(renderer, motion_config, tmp_path, override, message):
    motion_config.update(override)
    with pytest.raises(ValueError, match=message):
        renderer.build_command(motion_config, tmp_path)


@pytest.mark.parametrize(("mode", "frames"), [("identity-reference", 121), ("structural-control", 49)])
def test_control_keeps_both_temporal_anchors_in_the_ic_pipeline(renderer, motion_config, tmp_path, mode, frames):
    end = tmp_path / "end.png"
    end.touch()
    motion_config.update(mode=mode, width=768, height=448, frames=frames, end_image=str(end))
    command = renderer.build_command(motion_config, tmp_path)
    anchors = [command[i + 1 : i + 4] for i, arg in enumerate(command) if arg == "--image"]
    assert command[3] == "ic-lora"
    assert anchors == [[motion_config["image"], "0", "1.0"], [str(end), str(frames - 1), "1.0"]]
    assert command[command.index("--video-conditioning") + 1] == motion_config["control_video"]
    assert command[command.index("--distilled-lora") + 1] == motion_config["distilled_lora"]
    assert "--start" not in command
    assert "--end" not in command


@pytest.mark.parametrize("missing", ["image", "end_image"])
@pytest.mark.parametrize("mode", ["identity-reference", "structural-control"])
def test_control_temporal_anchors_require_a_real_start_and_end(renderer, motion_config, tmp_path, missing, mode):
    end = tmp_path / "end.png"
    end.touch()
    motion_config.update(mode=mode, width=768, height=448, frames=121, end_image=str(end))
    if missing == "image":
        motion_config.pop("image")
        message = "requires a starting image"
    else:
        end.unlink()
        message = "Missing end_image"
    with pytest.raises(ValueError, match=message):
        renderer.build_command(motion_config, tmp_path)


@pytest.mark.parametrize("override", [{"frames": 49}, {"frames": 97}, {"width": 1024, "height": 576}])
def test_identity_rejects_motion_presets_outside_its_trained_bucket(renderer, motion_config, tmp_path, override):
    motion_config.update(mode="identity-reference", width=768, height=448, frames=121)
    motion_config.update(override)
    with pytest.raises(ValueError, match="Ingredients qualification requires 121 frames at 768x448"):
        renderer.build_command(motion_config, tmp_path)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"control_video": None}, "requires control_lora"),
        ({"control_lora": None}, "requires control_lora"),
        ({"distilled_lora": None}, "requires control_lora"),
        ({"image": None}, "requires a starting image"),
        ({"end_image": "end.png"}, "cannot use an end anchor"),
        ({"recipe": "animated-dialogue"}, "cannot use another recipe"),
        ({"control_video": "missing.mp4"}, "Missing control_video"),
        ({"control_lora": "missing.safetensors"}, "Missing control_lora"),
        ({"distilled_lora": "missing.safetensors"}, "Missing distilled_lora"),
        ({"frames": 120}, "supports 49/97/121"),
        ({"width": 1000}, "supports 49/97/121"),
        ({"mode": "distilled"}, "must not be ignored"),
    ],
)
def test_motion_control_invalid_or_dropped_inputs_fail_before_launch(
    renderer, motion_config, tmp_path, override, message
):
    motion_config.update(override)
    with pytest.raises(ValueError, match=message):
        renderer.build_command(motion_config, tmp_path)


@pytest.mark.parametrize("mode", ["motion-track", "identity-reference", "structural-control"])
def test_control_never_falls_back_when_dev_model_is_missing(renderer, motion_config, tmp_path, mode):
    motion_config.update(mode=mode, width=768, height=448, frames=121)
    (tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1/transformer-dev.safetensors").unlink()
    with pytest.raises(ValueError, match="IC-LoRA qualification requires installed transformer-dev"):
        renderer.build_command(motion_config, tmp_path)


@pytest.mark.parametrize("mode", ["motion-track", "identity-reference", "structural-control"])
def test_control_keeps_stricter_memory_admission(renderer, motion_config, tmp_path, monkeypatch, mode):
    motion_config.update(mode=mode, width=768, height=448, frames=121)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(motion_config))
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    if mode == "identity-reference":
        stream = {
            "width": 768,
            "height": 448,
            "nb_read_frames": "121",
            "r_frame_rate": "24/1",
            "avg_frame_rate": "24/1",
            "duration": "5.041667",
        }
        monkeypatch.setattr(
            renderer.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({"streams": [stream]})),
        )
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=18 * 2**30))
    )
    with pytest.raises(RuntimeError, match="At least 19 GiB"):
        renderer.run(path)


def test_structural_test_requires_a_start_anchor(renderer, motion_config, tmp_path):
    motion_config.update(mode="structural-control", image=None)
    with pytest.raises(ValueError, match="requires a starting image"):
        renderer.build_command(motion_config, tmp_path)


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
