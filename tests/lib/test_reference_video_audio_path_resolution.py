"""``resolve_reference_audio_paths`` 的归一化解析：真实文件系统 I/O，与
``tests/lib/test_reference_video_prompt_render.py`` 分开成独立文件，避免继承其
module-level ``pytest.mark.unit``（该 marker 语义是「不碰真实 I/O」，与本文件用
``tmp_path`` 写盘的测试性质冲突——marker 是可加性的，混在同一模块无法为单个测试
剥离继承的 unit 标记）。
"""

from __future__ import annotations

import unicodedata

import pytest

from lib.reference_video.prompt_render import resolve_reference_audio_paths

pytestmark = pytest.mark.integration

_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


def test_resolve_reference_audio_paths_keys_are_normalized_for_binding(tmp_path):
    """``resolve_reference_audio_paths`` 的 key 直接作为 ``audio_ready`` 与说话人判等：
    资产表以 NFD 落盘时若原样返回，绑定判定两侧不同形，音频会被静默判成不可用。"""
    refs_audio = tmp_path / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "x.wav").write_bytes(b"RIFF")
    project = {"characters": {_NAME_NFD: {"reference_audio": "characters/refs_audio/x.wav"}}}

    resolved = resolve_reference_audio_paths(project, tmp_path)

    assert set(resolved) == {_NAME_NFC}
