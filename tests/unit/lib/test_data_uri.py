"""lib.data_uri 单元测试 — 出站素材编码的 MIME 查表与回落语义。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from lib.data_uri import file_to_data_uri, image_to_data_uri
from lib.video_backends.base import VideoCapabilityError, reference_audio_to_data_uri

_IMAGE_MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mp3"}


class TestFileToDataUri:
    def test_encodes_bytes_with_given_mime(self, tmp_path: Path):
        path = tmp_path / "blob.bin"
        path.write_bytes(b"\x00\x01hello")
        expected = base64.b64encode(b"\x00\x01hello").decode("ascii")
        assert file_to_data_uri(path, "application/octet-stream") == f"data:application/octet-stream;base64,{expected}"

    def test_unreadable_file_raises_oserror(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match=r"No such file or directory"):
            file_to_data_uri(tmp_path / "missing.bin", "image/png")


class TestImageToDataUri:
    @pytest.mark.parametrize(("name", "mime"), [("a.JPG", "image/jpeg"), ("b.webp", "image/webp")])
    def test_suffix_lookup_is_case_insensitive(self, tmp_path: Path, name: str, mime: str):
        path = tmp_path / name
        path.write_bytes(b"img")
        assert image_to_data_uri(path, _IMAGE_MIME_TYPES).startswith(f"data:{mime};base64,")

    def test_unlisted_suffix_falls_back_to_png(self, tmp_path: Path):
        path = tmp_path / "c.tiff"
        path.write_bytes(b"img")
        assert image_to_data_uri(path, _IMAGE_MIME_TYPES).startswith("data:image/png;base64,")


class TestReferenceAudioToDataUri:
    def test_encodes_listed_suffix(self, tmp_path: Path):
        path = tmp_path / "voice.WAV"
        path.write_bytes(b"riff")
        expected = base64.b64encode(b"riff").decode("ascii")
        uri = reference_audio_to_data_uri(path, model="m", mime_types=_AUDIO_MIME_TYPES)
        assert uri == f"data:audio/wav;base64,{expected}"

    def test_unlisted_suffix_fails_loud(self, tmp_path: Path):
        # 音频不回落也不跳过：编号按条目顺序，静默丢一段会把音色错配到别的角色。
        path = tmp_path / "voice.ogg"
        path.write_bytes(b"ogg")
        with pytest.raises(VideoCapabilityError) as exc:
            reference_audio_to_data_uri(path, model="m", mime_types=_AUDIO_MIME_TYPES)
        assert exc.value.code == "video_reference_audio_format_unsupported"
        assert exc.value.params["supported"] == ".mp3, .wav"

    def test_unreadable_file_fails_loud(self, tmp_path: Path):
        with pytest.raises(VideoCapabilityError) as exc:
            reference_audio_to_data_uri(tmp_path / "gone.wav", model="m", mime_types=_AUDIO_MIME_TYPES)
        assert exc.value.code == "video_reference_audio_unreadable"
