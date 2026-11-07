"""Normalized ``resolve_reference_audio_paths`` uses real filesystem I/O, so it is separate from ``tests/unit/lib/script/reference_video/test_reference_video_prompt_render.py``. Test tiers follow directories; one file cannot belong to both."""

from __future__ import annotations

import unicodedata

from lib.script.reference_video.prompt_render import resolve_reference_audio_paths

_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


def test_resolve_reference_audio_paths_keys_are_normalized_for_binding(tmp_path):
    """``resolve_reference_audio_paths`` keys directly compare speakers with ``audio_ready``. Returning raw NFD registration keys would mismatch normalized names and silently mark audio unavailable."""
    refs_audio = tmp_path / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "x.wav").write_bytes(b"RIFF")
    project = {"characters": {_NAME_NFD: {"reference_audio": "characters/refs_audio/x.wav"}}}

    resolved = resolve_reference_audio_paths(project, tmp_path)

    assert set(resolved) == {_NAME_NFC}
