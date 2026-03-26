from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from lib.gemini_client import GeminiClient


class _FakeModels:
    def __init__(self):
        self.observed_fps = []

    def generate_content(self, model, contents):
        image_obj = contents[0]
        self.observed_fps.append(getattr(image_obj, "fp", None))
        return SimpleNamespace(text="cinematic, dramatic lighting")


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


class TestGeminiClientFdSafety:
    def test_analyze_style_image_uses_detached_image_when_input_is_path(self, tmp_path):
        img_path = tmp_path / "style.png"
        Image.new("RGB", (8, 8), (0, 128, 255)).save(img_path)

        client = object.__new__(GeminiClient)
        client.client = _FakeClient()

        result = client.analyze_style_image(img_path, model="fake-model")

        assert result == "cinematic, dramatic lighting"
        assert len(client.client.models.observed_fps) == 1
        assert client.client.models.observed_fps[0] is None
