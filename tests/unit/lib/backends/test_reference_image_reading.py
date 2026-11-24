"""All generation channels validate local images before any provider write."""

import ast
from pathlib import Path

import pytest
from PIL import Image

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.backends.backend_runtime import read_reference_images, read_video_image_slots
from lib.backends.image_errors import ImageCapabilityError


def test_reads_real_image_bytes_and_reports_every_unreadable_slot(tmp_path):
    good = tmp_path / "valid.png"
    Image.new("RGB", (2, 2)).save(good)
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(good.read_bytes()[:24])
    assert read_reference_images([good], "m")[0].multipart() == (good.name, good.read_bytes(), "image/png")
    with pytest.raises(ImageCapabilityError) as error:
        read_reference_images([good, bad, "", tmp_path / "gone", tmp_path], "m")
    assert error.value.code == "image_reference_images_unreadable"
    assert error.value.params["names"] == f"corrupt.png, #3, gone, {tmp_path.name}"


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        (True, True, "video_start_image_unreadable"),
        (False, True, "video_end_image_unreadable"),
        (False, False, "video_reference_images_unreadable"),
    ],
)
def test_frame_slots_preserve_the_first_failure_code_and_list_all_names(tmp_path, start, end, code):
    first = tmp_path / "start.png" if start else None
    last = tmp_path / "end.png" if end else None
    with pytest.raises(VideoCapabilityError) as error:
        read_video_image_slots(first, last, [tmp_path / "ref.png"], "m")
    assert error.value.code == code
    names = ", ".join(path.name for path in [first, last, tmp_path / "ref.png"] if path)
    assert error.value.params.get("name", error.value.params.get("names")) == names


def _direct_reads(tree: ast.AST) -> set[str]:
    """Explicitly audit raw file access and legacy encoders, including imported aliases."""
    aliases = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    reads = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for call in ast.walk(function):
            if not isinstance(call, ast.Call):
                continue
            target = ast.unparse(call.func)
            name = target.rsplit(".", 1)[-1]
            name = aliases.get(name, name)
            if name in {
                "open",
                "read_bytes",
                "file_to_data_uri",
                "image_to_base64",
                "image_to_base64_data_uri",
                "image_to_data_uri",
            }:
                reads.add(f"{function.name}: {ast.unparse(call)}")
    return reads


@pytest.mark.parametrize(
    "body",
    [
        "open(path)",
        "path.open('rb')",
        "path.read_bytes()",
        "Image.open(path)",
        "image_to_base64_data_uri(path)",
        "legacy(path)",
    ],
)
def test_direct_read_guard_catches_file_and_legacy_encoder_forms(body):
    tree = ast.parse(
        f"from lib.backends.data_uri import image_to_data_uri as legacy\ndef assemble(path):\n    return {body}"
    )
    assert _direct_reads(tree)


def test_generation_channels_read_reference_images_through_the_shared_boundary():
    root = Path(__file__).resolve().parents[4] / "lib"
    paths = [*root.glob("backends/image_backends/*.py"), *root.glob("backends/video_backends/*.py")]
    required = {}
    for path in paths:
        tree = ast.parse(path.read_text())
        if path.name != "base.py" and any(
            isinstance(node, ast.AsyncFunctionDef) and node.name == "generate" for node in ast.walk(tree)
        ):
            required[path] = {"read_reference_images" if path.parent.name == "image_backends" else "read_video_images"}
    required[root / "custom_provider/declarative_backend.py"] = {"read_reference_images", "read_video_image_slots"}
    required[root / "custom_provider/comfyui/comfyui_backend.py"] = {"read_video_images"}
    required[root / "custom_provider/comfyui/comfyui_image_backend.py"] = {"read_reference_images"}
    # Files below also read credentials, outputs or audio. An additional path to the filesystem must be reviewed.
    non_reference_reads = {
        "backends/image_backends/gemini.py": {"__init__: open(credentials_file, encoding='utf-8')"},
        "backends/video_backends/gemini.py": {
            "__init__: open(credentials_file, encoding='utf-8')",
            "_download_video: open(target, 'wb')",
            "write: open(target, 'wb')",
            "_fetch_uri: open(target, 'wb')",
        },
        "backends/image_backends/openrouter.py": {"_ensure_png: output_path.read_bytes()"},
        "backends/video_backends/dashscope.py": {"_read_reference_audio_or_none: file_to_data_uri(path, mime)"},
        "custom_provider/declarative_backend.py": {"_audio_assets: path.read_bytes()"},
    }
    for path, readers in required.items():
        tree = ast.parse(path.read_text())
        uses = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert readers <= uses, f"{path} must use the shared reference image reader"
        assert _direct_reads(tree) == non_reference_reads.get(str(path.relative_to(root)), set()), path
