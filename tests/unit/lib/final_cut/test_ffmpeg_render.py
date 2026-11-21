"""成片渲染的落盘失败：路径太长要给出明确的问题码，而不是裸的文件系统异常。"""

from pathlib import Path

import pytest

from lib.final_cut import ffmpeg_render
from lib.final_cut.errors import FinalCutError


def _refuse_hardlink(monkeypatch) -> None:
    def refuse(self, target, **kwargs) -> None:
        raise OSError("hard link is not available here")

    monkeypatch.setattr(Path, "hardlink_to", refuse)


def _refuse_copy(monkeypatch, message: str) -> None:
    def refuse(*args, **kwargs) -> None:
        raise OSError(message)

    monkeypatch.setattr(ffmpeg_render.shutil, "copyfile", refuse)


def test_stage_subtitles_reports_path_too_long(tmp_path: Path, monkeypatch) -> None:
    """字体放不进工作目录时按路径长度给出 final_cut_path_too_long，而不是让人以为字体没随包。"""
    monkeypatch.setattr(ffmpeg_render, "path_limit", lambda: 8)
    _refuse_hardlink(monkeypatch)
    _refuse_copy(monkeypatch, "copy failed")

    with pytest.raises(FinalCutError) as caught:
        ffmpeg_render._stage_subtitles(tmp_path, "[Events]\n")

    assert caught.value.code == "final_cut_path_too_long"
    assert caught.value.params["limit"] == 8
    assert caught.value.params["length"] > 8
    assert caught.value.params["path"].endswith("SourceHanSansCN-Bold.otf")


def test_stage_subtitles_reports_render_failed_for_other_errors(tmp_path: Path, monkeypatch) -> None:
    """不是路径长度的问题时按渲染失败报告，原始异常留在消息里。"""
    _refuse_hardlink(monkeypatch)
    _refuse_copy(monkeypatch, "disk is full")

    with pytest.raises(FinalCutError) as caught:
        ffmpeg_render._stage_subtitles(tmp_path, "[Events]\n")

    assert caught.value.code == "final_cut_render_failed"
    assert "disk is full" in str(caught.value)
