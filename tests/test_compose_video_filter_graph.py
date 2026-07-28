"""compose_video.py 滤镜图构造与 fps 解析的纯函数单测。

不依赖 ffmpeg / ffprobe，覆盖以下回归断言：

- `_resolve_fps`：avg_frame_rate `"0/0"`/`"0"`/`""` 显式回退到 r_frame_rate，
  而不是被 `or` 链当作真值通过（issue #562、#564 第 5 条）
- `_build_xfade_filter_complex`：
  - 全 cut → 返回 None（调用方 fallback 到 concatenate_final）
  - 多片段 xfade chain + acrossfade chain 音画对齐（#564 第 3 条）
  - cut+xfade 混用按 cut 分组、组内 offset 不跨 cut 累加（#564 评论补充）
  - 短片段边界自动降级为 cut（避免负 offset）
  - audio 输入标签用空串连接而非 `;` 分隔（#564 第 1 条核心回归）
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT / "agent_runtime_profile" / ".claude" / "skills" / "compose-video" / "scripts" / "compose_video.py"
)

# compose_video.py 顶部会 `from lib.project_manager import ProjectManager`，
# 需保证 REPO_ROOT 在 sys.path（pytest 默认会注入，这里二次防御）
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module():
    """以独立模块名加载脚本，避免和别处的 compose_video 冲突。"""
    spec = importlib.util.spec_from_file_location("_compose_video_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose_video = _load_module()


# ---------------------------------------------------------------------------
# _resolve_fps
# ---------------------------------------------------------------------------


class TestResolveFps:
    def test_avg_0_over_0_falls_back_to_r(self) -> None:
        """#562 核心场景：avg 为 '0/0' 时必须回退 r，不能直接被 `or` 当真值通过。"""
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

    ffprobe 对部分 webm / 流式封装会返回 `stream.duration="N/A"`，
    这是真值字符串但不是数值；旧实现 `stream.duration or format.duration` 会
    选中 "N/A" 然后 float() 抛错，导致正常视频被拒。这里覆盖该回归。
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
# _build_xfade_filter_complex
# ---------------------------------------------------------------------------


class TestBuildXfadeFilterComplex:
    def test_single_clip_returns_none(self) -> None:
        """n<2 → None，调用方走 concatenate_final 单段路径。"""
        assert compose_video._build_xfade_filter_complex([5.0], [], 0.5) is None

    def test_all_cut_returns_none(self) -> None:
        """全 cut → None，调用方 fallback 到纯 concat。"""
        result = compose_video._build_xfade_filter_complex([5.0, 5.0, 5.0], ["cut", "cut"], 0.5)
        assert result is None

    def test_all_short_clips_returns_none(self) -> None:
        """所有边界都因短片段降级为 cut → None。"""
        result = compose_video._build_xfade_filter_complex([0.3, 0.3, 0.3], ["fade", "fade"], 0.5)
        assert result is None

    def test_two_clip_fade_single_group(self) -> None:
        """两段全 fade：单 group，xfade + acrossfade 各一条，输出 null/anull 重命名为 vout/aout。"""
        result = compose_video._build_xfade_filter_complex([5.0, 5.0], ["fade"], 0.5)
        assert result is not None
        # video xfade
        assert "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.500[g0v]" in result
        # audio acrossfade（关键：使用 acrossfade 而非 concat 硬拼接）
        assert "[0:a][1:a]acrossfade=d=0.5:c1=tri:c2=tri[g0a]" in result
        # 单 group 收尾用 null/anull 改名
        assert "[g0v]null[vout]" in result
        assert "[g0a]anull[aout]" in result

    def test_three_clip_fade_offset_chain(self) -> None:
        """三段全 fade：单 group，第二个 xfade offset 应等于 D0+D1-2*dur。"""
        result = compose_video._build_xfade_filter_complex([5.0, 5.0, 5.0], ["fade", "fade"], 0.5)
        assert result is not None
        # 第一 xfade offset = 5 - 0.5 = 4.500
        assert "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.500[g0v1]" in result
        # 第二 xfade offset = 5+5 - 2*0.5 = 9.000，并改名为最终 [g0v]
        assert "[g0v1][2:v]xfade=transition=fade:duration=0.5:offset=9.000[g0v]" in result

    def test_no_semicolon_inside_audio_input_labels(self) -> None:
        """#564 第 1 条核心回归：audio 输入标签之间不能出现 `;` 分隔。

        旧实现用 `";".join([f"[{i}:a]"...])` 拼接成 `[0:a];[1:a];[2:a]concat=...`，
        分号会被 ffmpeg 当作 filter chain 分隔符，导致 concat 输入参数不足报错。
        新实现走 acrossfade 链，自然不会出现 `[N:a];[M:a]` 这种相邻片段。
        """
        result = compose_video._build_xfade_filter_complex([5.0, 5.0, 5.0], ["fade", "fade"], 0.5)
        assert result is not None
        # 任意两个相邻 audio 输入标签都不应该被 `;` 直接连起来
        assert "[0:a];[1:a]" not in result
        assert "[1:a];[2:a]" not in result

    def test_cut_xfade_mix_groups_by_cut(self) -> None:
        """#564 评论补充：cut+xfade 混用按 cut 分组。

        durations=[5,5,5,5], transitions=["fade","cut","fade"]
        → group A=[0,1], group B=[2,3]，组间 concat 串联
        """
        result = compose_video._build_xfade_filter_complex([5.0, 5.0, 5.0, 5.0], ["fade", "cut", "fade"], 0.5)
        assert result is not None

        # 组 0：[0:v][1:v] xfade，offset 应为 4.500（组内累加，不跨 cut）
        assert "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=4.500[g0v]" in result
        # 组 1：[2:v][3:v] xfade，**关键**：offset 应该重新从 4.500 起算，
        # 而不是按全局公式 sum(durs[:3]) - 3*0.5 = 13.500
        assert "[2:v][3:v]xfade=transition=fade:duration=0.5:offset=4.500[g1v]" in result
        assert "offset=13.500" not in result

        # 组间 concat=v=1:a=1
        assert "concat=n=2:v=1:a=1[vout][aout]" in result
        assert "[g0v][g0a][g1v][g1a]" in result

    def test_cut_xfade_mix_audio_groups_match_video(self) -> None:
        """混用场景下 audio 也按相同 group 分链（不跨 cut 用 acrossfade）。"""
        result = compose_video._build_xfade_filter_complex([5.0, 5.0, 5.0, 5.0], ["fade", "cut", "fade"], 0.5)
        assert result is not None

        # 组 0 audio
        assert "[0:a][1:a]acrossfade=d=0.5:c1=tri:c2=tri[g0a]" in result
        # 组 1 audio：不能出现跨 cut 的 acrossfade，例如 [1:a][2:a]acrossfade
        assert "[1:a][2:a]acrossfade" not in result
        # 应有 [2:a][3:a] 的 acrossfade
        assert "[2:a][3:a]acrossfade=d=0.5:c1=tri:c2=tri[g1a]" in result

    def test_single_clip_group_passes_through(self) -> None:
        """单片段 group 直接透传 [i:v]/[i:a] 给 concat，无需 xfade。

        durations=[5,5,5], transitions=["cut","fade"]
        → group A=[0]（单片段透传），group B=[1,2]（xfade）
        """
        result = compose_video._build_xfade_filter_complex([5.0, 5.0, 5.0], ["cut", "fade"], 0.5)
        assert result is not None

        # 组 1 内 xfade，offset 必须从 0 起算（即 5-0.5=4.500）
        assert "[1:v][2:v]xfade=transition=fade:duration=0.5:offset=4.500[g1v]" in result

        # concat 串联，第一组直接用 [0:v][0:a]，第二组用 [g1v][g1a]
        assert "[0:v][0:a][g1v][g1a]concat=n=2:v=1:a=1[vout][aout]" in result

    def test_short_clip_downgrade_at_boundary(self) -> None:
        """duration <= transition_duration 的片段所触边界自动降级 cut。

        durations=[0.3, 5, 5], transitions=["fade","fade"], transition_duration=0.5
        → 边界 0（涉及 d[0]=0.3）降级 cut，剩下边界 1 仍 fade
        → group A=[0]（单段透传），group B=[1,2]（xfade）
        """
        result = compose_video._build_xfade_filter_complex([0.3, 5.0, 5.0], ["fade", "fade"], 0.5)
        assert result is not None
        # 不能出现 [0:v][1:v]xfade（边界 0 应已降级）
        assert "[0:v][1:v]xfade" not in result
        # 应有 [1:v][2:v]xfade
        assert "[1:v][2:v]xfade=transition=fade:duration=0.5:offset=4.500[g1v]" in result

    def test_wipe_maps_to_wipeleft(self) -> None:
        result = compose_video._build_xfade_filter_complex([5.0, 5.0], ["wipe"], 0.5)
        assert result is not None
        assert "xfade=transition=wipeleft" in result

    def test_unknown_transition_falls_back_to_fade(self) -> None:
        """未知 transition 值按 fade 处理（保留原行为）。"""
        result = compose_video._build_xfade_filter_complex([5.0, 5.0], ["nonexistent-transition"], 0.5)
        assert result is not None
        assert "xfade=transition=fade" in result

    def test_transitions_shorter_than_boundaries_defaults_fade(self) -> None:
        """transitions 比边界数少时，多出来的边界按 fade 处理。"""
        result = compose_video._build_xfade_filter_complex([5.0, 5.0, 5.0], ["fade"], 0.5)
        assert result is not None
        # 边界 1 没在 transitions 里 → 默认 fade
        assert "[g0v1][2:v]xfade=transition=fade" in result

    def test_middle_clip_both_sided_xfade_downgrades_left(self) -> None:
        """中段两侧 xfade 且 td < dur < 2*td：左侧降 cut，保留右侧。

        durations=[10, 3, 10], transitions=["fade","fade"], td=2.0
        → 中段 3s 须承担 2+2=4s 转场但单边界守卫各自放行（3 > 2）
        → 左侧边界 0 降 cut，等价 boundary_xfade=[None, "fade"]
        → group A=[0]（单段透传），group B=[1,2]（xfade）
        """
        result = compose_video._build_xfade_filter_complex([10.0, 3.0, 10.0], ["fade", "fade"], 2.0)
        assert result is not None
        # 边界 0 降 cut：不应出现 [0:v][1:v]xfade
        assert "[0:v][1:v]xfade" not in result
        # 边界 1 保留 fade：offset = dur[1] - td = 3 - 2 = 1.000
        assert "[1:v][2:v]xfade=transition=fade:duration=2.0:offset=1.000[g1v]" in result
        # cut 分组 → 组间 concat
        assert "concat=n=2:v=1:a=1[vout][aout]" in result

    def test_chained_short_middle_clips_downgrade_left_to_right(self) -> None:
        """链式短中段：从左向右逐个降左侧，最终只保留最右 xfade。

        durations=[10, 3, 3, 10], transitions=["fade","fade","fade"], td=2.0
        → 中段 1、2 均为 3s（< 4s）双侧 xfade
        → i=1 降边界 0，i=2 降边界 1，等价 boundary_xfade=[None, None, "fade"]
        → group A=[0]、B=[1]（均单段透传），C=[2,3]（xfade）
        """
        result = compose_video._build_xfade_filter_complex([10.0, 3.0, 3.0, 10.0], ["fade", "fade", "fade"], 2.0)
        assert result is not None
        # 边界 0、1 降 cut
        assert "[0:v][1:v]xfade" not in result
        assert "[1:v][2:v]xfade" not in result
        # 只剩最右边界 2：offset = dur[2] - td = 3 - 2 = 1.000
        assert "[2:v][3:v]xfade=transition=fade:duration=2.0:offset=1.000[g2v]" in result
        # 三个 group 串联
        assert "concat=n=3:v=1:a=1[vout][aout]" in result

    def test_middle_clip_exactly_two_transition_durations_keeps_both(self) -> None:
        """中段恰好等于 2*td：视为足够，双侧 xfade 都保留（严格 < 才降级）。

        durations=[10, 4, 10], transitions=["fade","fade"], td=2.0
        → 4 == 2*2，不降级；单 group=[0,1,2]，链式 xfade，无 concat
        """
        result = compose_video._build_xfade_filter_complex([10.0, 4.0, 10.0], ["fade", "fade"], 2.0)
        assert result is not None
        # 第一 xfade offset = 10 - 2 = 8.000
        assert "[0:v][1:v]xfade=transition=fade:duration=2.0:offset=8.000[g0v1]" in result
        # 第二 xfade offset = 10+4 - 2*2 = 10.000
        assert "[g0v1][2:v]xfade=transition=fade:duration=2.0:offset=10.000[g0v]" in result
        # 单 group 走 null/anull，不出现 concat
        assert "concat" not in result

    def test_short_end_clip_not_affected_by_middle_guard(self) -> None:
        """端片短（td < dur < 2*td）但只有一个 xfade 边界，本守卫不触发。

        durations=[3, 10, 10], transitions=["fade","fade"], td=2.0
        → 端片 0 为 3s，只承担边界 0 单个转场（> td 足够）
        → 中段 1 为 10s（>= 4s），不降级 → 双侧 xfade 全保留
        """
        result = compose_video._build_xfade_filter_complex([3.0, 10.0, 10.0], ["fade", "fade"], 2.0)
        assert result is not None
        # 端片短不触发降级，边界 0 仍 fade
        assert "[0:v][1:v]xfade=transition=fade:duration=2.0:offset=1.000[g0v1]" in result
        # 中段 10s 不降级，边界 1 仍 fade
        assert "[g0v1][2:v]xfade=transition=fade" in result
        assert "concat" not in result

    def test_no_negative_xfade_offset_after_guard(self) -> None:
        """守卫生效后，xfade chain 中不出现负 offset（解析 offset 字符串断言 >= 0）。"""
        cases = [
            ([10.0, 3.0, 10.0], ["fade", "fade"], 2.0),
            ([10.0, 3.0, 3.0, 10.0], ["fade", "fade", "fade"], 2.0),
            ([10.0, 4.0, 10.0], ["fade", "fade"], 2.0),
            ([3.0, 10.0, 10.0], ["fade", "fade"], 2.0),
        ]
        for durations, transitions, td in cases:
            result = compose_video._build_xfade_filter_complex(durations, transitions, td)
            assert result is not None
            offsets = [float(m) for m in re.findall(r"offset=(-?\d+\.\d+)", result)]
            assert offsets, f"应至少有一个 xfade offset: {durations}"
            assert all(o >= 0 for o in offsets), f"出现负 offset {offsets}: {durations}"


# ---------------------------------------------------------------------------
# concatenate_final 单段路径
# ---------------------------------------------------------------------------


class TestConcatenateFinalSingleSegment:
    def test_single_clip_skips_concat_filter(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """单段输入应走 `-c copy + faststart` 直接 remux，不走 concat filter。

        #564 第 2 条：concat=n=1 会让 ffmpeg 报参数错误。
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
        assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
        assert "-movflags" in cmd and cmd[cmd.index("-movflags") + 1] == "+faststart"
        # 不能出现 concat filter
        assert not any("concat=" in arg for arg in cmd)
        assert "-filter_complex" not in cmd

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="没有可用的视频片段"):
            compose_video.concatenate_final([], Path(tempfile.gettempdir()) / "unused.mp4")
