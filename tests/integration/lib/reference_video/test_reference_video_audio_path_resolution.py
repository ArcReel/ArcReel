"""``resolve_reference_audio_paths`` 的归一化解析：真实文件系统 I/O，故与
``tests/unit/lib/reference_video/test_reference_video_prompt_render.py`` 分开成
独立文件——档位由所在目录决定，同一个文件放不下两个档位。
"""

from __future__ import annotations

import unicodedata

from lib.reference_video.prompt_render import resolve_reference_audio_paths

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
