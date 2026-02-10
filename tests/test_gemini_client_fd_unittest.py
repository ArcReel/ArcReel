import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PIL import Image

from lib.gemini_client import GeminiClient


def _fd_count() -> int:
    """Return current process file descriptor count, or -1 if unavailable."""
    for fd_dir in ("/dev/fd", "/proc/self/fd"):
        try:
            return len(os.listdir(fd_dir))
        except OSError:
            continue
    return -1


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


class TestGeminiClientFdSafety(unittest.TestCase):
    def test_build_contents_with_labeled_refs_does_not_keep_file_handles_open(self):
        with TemporaryDirectory() as tmpdir:
            img_path = Path(tmpdir) / "ref.png"
            Image.new("RGB", (16, 16), (255, 0, 0)).save(img_path)

            client = object.__new__(GeminiClient)
            client.SKIP_NAME_PATTERNS = GeminiClient.SKIP_NAME_PATTERNS

            baseline = _fd_count()
            retained_contents = []
            for _ in range(40):
                retained_contents.append(
                    client._build_contents_with_labeled_refs("test prompt", [img_path])
                )
            after = _fd_count()

            # Allow a small buffer for unrelated runtime FDs.
            if baseline >= 0 and after >= 0:
                self.assertLessEqual(
                    after,
                    baseline + 5,
                    f"FD count grew unexpectedly: baseline={baseline}, after={after}",
                )

            # Explicit cleanup for test process hygiene.
            for content in retained_contents:
                for item in content:
                    if isinstance(item, Image.Image):
                        item.close()

    def test_analyze_style_image_uses_detached_image_when_input_is_path(self):
        with TemporaryDirectory() as tmpdir:
            img_path = Path(tmpdir) / "style.png"
            Image.new("RGB", (8, 8), (0, 128, 255)).save(img_path)

            client = object.__new__(GeminiClient)
            client.client = _FakeClient()

            result = client.analyze_style_image(img_path, model="fake-model")

            self.assertEqual(result, "cinematic, dramatic lighting")
            self.assertEqual(len(client.client.models.observed_fps), 1)
            self.assertIsNone(client.client.models.observed_fps[0])


if __name__ == "__main__":
    unittest.main()
