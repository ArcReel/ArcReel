from pathlib import Path
from lib.image_backends.base import (
    ImageCapability, ImageGenerationRequest, ImageGenerationResult, ReferenceImage,
)

def test_image_capability_is_str_enum():
    assert ImageCapability.TEXT_TO_IMAGE == "text_to_image"
    assert ImageCapability.IMAGE_TO_IMAGE == "image_to_image"

def test_reference_image_defaults():
    ref = ReferenceImage(path="/tmp/test.png")
    assert ref.path == "/tmp/test.png"
    assert ref.label == ""

def test_image_generation_request_defaults():
    req = ImageGenerationRequest(prompt="hello", output_path=Path("/tmp/out.png"))
    assert req.aspect_ratio == "9:16"
    assert req.image_size == "1K"
    assert req.reference_images == []
    assert req.project_name is None
    assert req.seed is None

def test_image_generation_result():
    result = ImageGenerationResult(
        image_path=Path("/tmp/out.png"), provider="grok", model="grok-imagine-image",
    )
    assert result.image_uri is None
    assert result.seed is None
    assert result.usage_tokens is None
