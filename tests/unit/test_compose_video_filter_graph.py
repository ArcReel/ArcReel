"""compose_video.py 滤镜图构造与 fps 解析的纯函数单测。

不依赖 ffmpeg / ffprobe，覆盖以下断言：

- `_resolve_fps`：avg_frame_rate `"0/0"`/`"0"`/`""` 显式回退到 r_frame_rate，
  而不是被 `or` 链当作真值通过
- `_coerce_numeric_duration`：ffprobe duration 字段的容错解析
- `concatenate_final`：单段路径
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPO_ROOT / "agent_runtime_profile" / ".claude" / "skills" / "compose-video" / "scripts" / "compose_video.py"
)

# compose_video.py 顶部会 `from lib.project.project_manager import ProjectManager`，
# 需保证 REPO_ROOT 在 sys.path（pytest 默认会注入，这里二次防御）
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module():
    """以独立模块名加载脚本，避免和别处的 compose_video 冲突。"""
    spec = importlib.util.spec_from_file_location("_compose_video_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose_video = _load_module()


# ---------------------------------------------------------------------------
# _resolve_fps
# ---------------------------------------------------------------------------


class TestResolveFps:
    def test_avg_0_over_0_falls_back_to_r(self) -> None:
        """avg 为 '0/0' 时必须回退 r，不能直接被 `or` 当真值通过。"""
        assert compose_video._resolve_fps("0/0", "24/1") == "24/1"

    def test_avg_0_falls_back_to_r(self) -> None:
        assert compose_video._resolve_fps("0", "24/1") == "24/1"

    def test_avg_empty_string_falls_back_to_r(self) -> None:
        assert compose_video._resolve_fps("", "30000/1001") == "30000/1001"

    def test_both_invalid_returns_30(self) -> None:
        assert compose_video._resolve_fps("0/0", "0/0") == "30"

    def test_both_none_returns_30(self) -> None:
        assert compose_video._resolve_fps(None, None) == "30"

    def test_both_empty_returns_30(self) -> None:
        assert compose_video._resolve_fps("", "") == "30"

    def test_valid_avg_wins(self) -> None:
        """合法 avg 直接返回，不读 r。"""
        assert compose_video._resolve_fps("30/1", "24/1") == "30/1"

    def test_avg_none_uses_r(self) -> None:
        assert compose_video._resolve_fps(None, "24/1") == "24/1"

    def test_fractional_passthrough(self) -> None:
        assert compose_video._resolve_fps("30000/1001", None) == "30000/1001"

    def test_strips_whitespace(self) -> None:
        assert compose_video._resolve_fps(" 24/1 ", None) == "24/1"


# ---------------------------------------------------------------------------
# _coerce_numeric_duration
# ---------------------------------------------------------------------------


class TestCoerceNumericDuration:
    """ffprobe duration 字段的容错解析。

    ffprobe 对部分 webm / 流式封装会返回 `stream.duration="N/A"`：这是真值字符串但不是
    数值，`or` 链会选中它并让 float() 抛错。非数值真值必须在转换前被拒并回退到下一个来源。
    """

    def test_numeric_string_parses(self) -> None:
        assert compose_video._coerce_numeric_duration("12.34") == 12.34

    def test_na_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration("N/A") is None

    def test_na_lowercase_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration("n/a") is None

    def test_empty_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration("   ") is None

    def test_none_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration(None) is None

    def test_non_numeric_garbage_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration("not-a-number") is None

    def test_strips_whitespace(self) -> None:
        assert compose_video._coerce_numeric_duration(" 5.5 ") == 5.5

    def test_nan_returns_none(self) -> None:
        """nan 会让 `nan <= transition_duration` 是 False，绕过短片段降级，
        把 nan 喂给 xfade offset。必须在 helper 层拒掉。"""
        assert compose_video._coerce_numeric_duration("nan") is None
        assert compose_video._coerce_numeric_duration("NaN") is None

    def test_inf_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration("inf") is None
        assert compose_video._coerce_numeric_duration("Infinity") is None
        assert compose_video._coerce_numeric_duration("-inf") is None

    def test_zero_returns_none(self) -> None:
        """duration=0 没意义，回退到 format.duration 试一次。"""
        assert compose_video._coerce_numeric_duration("0") is None
        assert compose_video._coerce_numeric_duration("0.0") is None

    def test_negative_returns_none(self) -> None:
        assert compose_video._coerce_numeric_duration("-1.5") is None


# ---------------------------------------------------------------------------
# concatenate_final 单段路径
# ---------------------------------------------------------------------------


class TestConcatenateFinalSingleSegment:
    def test_single_clip_skips_concat_filter(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """单段输入应走 `-c copy + faststart` 直接 remux，不走 concat filter。

        concat=n=1 会让 ffmpeg 报参数错误。
        """
        captured: list[list[str]] = []

        def fake_run_ffmpeg(cmd: list[str], _error_prefix: str) -> None:
            captured.append(cmd)

        monkeypatch.setattr(compose_video, "run_ffmpeg", fake_run_ffmpeg)

        clip = tmp_path / "normalized_000.mp4"
        clip.write_bytes(b"\x00" * 16)
        output = tmp_path / "final.mp4"

        compose_video.concatenate_final([clip], output)

        assert len(captured) == 1
        cmd = captured[0]
        # 关键不变量
        assert "-c" in cmd
        assert cmd[cmd.index("-c") + 1] == "copy"
        assert "-movflags" in cmd
        assert cmd[cmd.index("-movflags") + 1] == "+faststart"
        # 不能出现 concat filter
        assert not any("concat=" in arg for arg in cmd)
        assert "-filter_complex" not in cmd

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="没有可用的视频片段"):
            compose_video.concatenate_final([], Path(tempfile.gettempdir()) / "unused.mp4")
