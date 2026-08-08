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
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

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


# ---------------------------------------------------------------------------
# 字幕：utterances → spans
# ---------------------------------------------------------------------------


class TestSubtitleSpansFromUtterances:
    """drama 场景 utterances → 字幕时间片（与剪映草稿导出同口径）。"""

    def test_dialogue_and_voiceover_in_order(self) -> None:
        utterances = [
            {"kind": "dialogue", "speaker": "A", "text": "你好世界"},
            {"kind": "voiceover", "speaker": "", "text": "这是一段旁白"},
        ]
        spans = compose_video._subtitle_spans_from_utterances(utterances, "zh")
        assert [s["text"] for s in spans] == ["你好世界", "这是一段旁白"]
        assert spans[0]["offset_seconds"] == pytest.approx(0.0)
        assert spans[0]["duration_seconds"] == pytest.approx(0.8)  # 4 字 @ 5/秒
        assert spans[1]["offset_seconds"] == pytest.approx(0.8)
        assert spans[1]["duration_seconds"] == pytest.approx(1.2)  # 6 字 @ 5/秒

    def test_language_changes_rate(self) -> None:
        zh = compose_video._subtitle_spans_from_utterances([{"text": "你好世界"}], "zh")
        en = compose_video._subtitle_spans_from_utterances([{"text": "hello world"}], "en")
        assert zh[0]["duration_seconds"] == pytest.approx(0.8)  # 4 字 @ 5/秒
        assert en[0]["duration_seconds"] == pytest.approx(0.8)  # 2 词 @ 2.5/秒

    def test_blank_text_skipped_without_consuming_offset(self) -> None:
        utterances = [
            {"text": "   "},
            {"text": ""},
            {"text": "你好"},
            {"text": "\n\t"},
        ]
        spans = compose_video._subtitle_spans_from_utterances(utterances, "zh")
        assert len(spans) == 1
        assert spans[0]["text"] == "你好"
        assert spans[0]["offset_seconds"] == pytest.approx(0.0)

    def test_halfwidth_punctuation_only_skipped(self) -> None:
        """半角标点无阅读单位 → 估时 0 → 不产字幕也不占 offset。"""
        utterances = [{"text": "?!"}, {"text": "..."}, {"text": "你好"}]
        spans = compose_video._subtitle_spans_from_utterances(utterances, "zh")
        assert len(spans) == 1
        assert spans[0]["offset_seconds"] == pytest.approx(0.0)

    def test_non_dict_and_non_string_entries_skipped(self) -> None:
        utterances = ["bad", 42, None, {"text": None}, {"text": 123}, {"text": "你好"}]
        spans = compose_video._subtitle_spans_from_utterances(utterances, "zh")
        assert [s["text"] for s in spans] == ["你好"]

    def test_non_list_input_returns_empty(self) -> None:
        assert compose_video._subtitle_spans_from_utterances(None, "zh") == []
        assert compose_video._subtitle_spans_from_utterances({"text": "你好"}, "zh") == []


# ---------------------------------------------------------------------------
# 字幕：SRT 渲染
# ---------------------------------------------------------------------------


class TestSrtRendering:
    def test_timestamp_format(self) -> None:
        assert compose_video._format_srt_timestamp(0) == "00:00:00,000"
        assert compose_video._format_srt_timestamp(1.234) == "00:00:01,234"
        assert compose_video._format_srt_timestamp(3661.5) == "01:01:01,500"
        assert compose_video._format_srt_timestamp(-3) == "00:00:00,000"

    def test_render_srt_structure(self) -> None:
        spans = [
            {"offset_seconds": 0.0, "duration_seconds": 0.8, "text": "你好"},
            {"offset_seconds": 0.8, "duration_seconds": 1.2, "text": "这是一段旁白"},
        ]
        rendered = compose_video._render_srt(spans)
        assert rendered.startswith("1\n00:00:00,000 --> 00:00:00,800\n你好")
        assert "\n\n2\n00:00:00,800 --> 00:00:02,000\n这是一段旁白\n" in rendered
        assert rendered.endswith("\n")

    def test_render_srt_empty(self) -> None:
        assert compose_video._render_srt([]) == ""


# ---------------------------------------------------------------------------
# 字幕：subtitles 滤镜路径转义
# ---------------------------------------------------------------------------


class TestSubtitlesPathEscaping:
    def test_forward_slash_relative_end(self) -> None:
        escaped = compose_video._escape_subtitles_filter_path(Path("scripts") / "subs.srt")
        assert escaped.startswith("filename='")
        assert escaped.endswith("/scripts/subs.srt'")

    @pytest.mark.skipif(os.name != "nt", reason="Windows 盘符路径形态")
    def test_windows_drive_colon_escaped(self) -> None:
        escaped = compose_video._escape_subtitles_filter_path(Path(r"C:\Users\test\subs.srt"))
        assert escaped == "filename='C\\:/Users/test/subs.srt'"

    def test_quote_in_path_raises(self) -> None:
        with pytest.raises(ValueError, match="单引号"):
            compose_video._escape_subtitles_filter_path(Path(r"C:\O'Brien\subs.srt"))


# ---------------------------------------------------------------------------
# normalize_clip 字幕烧录
# ---------------------------------------------------------------------------


class TestNormalizeClipSubtitles:
    def test_subtitles_filter_appended(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        captured: dict[str, list[str]] = {}

        def fake_probe(path: Path) -> dict[str, object]:
            return {"width": 320, "height": 240, "fps": "25/1", "duration": 2.0, "has_audio": True}

        def fake_run_ffmpeg(cmd: list[str], _error_prefix: str) -> None:
            captured["cmd"] = cmd

        monkeypatch.setattr(compose_video, "probe_media", fake_probe)
        monkeypatch.setattr(compose_video, "run_ffmpeg", fake_run_ffmpeg)

        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 16)
        output = tmp_path / "normalized.mp4"
        srt = tmp_path / "subs.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

        compose_video.normalize_clip(
            source, output, target_width=320, target_height=240, target_fps="25/1", subtitles_path=srt
        )

        filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
        assert "subtitles=filename='" in filter_complex
        assert "setpts=PTS-STARTPTS,subtitles=" in filter_complex

    def test_no_subtitles_keeps_plain_filter(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        captured: dict[str, list[str]] = {}

        def fake_probe(path: Path) -> dict[str, object]:
            return {"width": 320, "height": 240, "fps": "25/1", "duration": 2.0, "has_audio": True}

        def fake_run_ffmpeg(cmd: list[str], _error_prefix: str) -> None:
            captured["cmd"] = cmd

        monkeypatch.setattr(compose_video, "probe_media", fake_probe)
        monkeypatch.setattr(compose_video, "run_ffmpeg", fake_run_ffmpeg)

        source = tmp_path / "clip.mp4"
        source.write_bytes(b"\x00" * 16)
        output = tmp_path / "normalized.mp4"

        compose_video.normalize_clip(source, output, target_width=320, target_height=240, target_fps="25/1")

        filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
        assert "subtitles=" not in filter_complex


# ---------------------------------------------------------------------------
# 字幕音频对齐：silencedetect 解析 + 时间轴对齐
# ---------------------------------------------------------------------------


class TestParseSilencedetectOutput:
    """silencedetect stderr → 有声区间（含首/中/尾静音、无静音等形态）。"""

    def test_no_silence_returns_full_segment(self) -> None:
        assert compose_video._parse_silencedetect_output("frame=...", 10.0) == [(0.0, 10.0)]

    def test_leading_and_inner_silence(self) -> None:
        stderr = (
            "  Duration: 00:00:12.00, start: 0.000000, bitrate: 6000 kb/s\n"
            "[silencedetect @ 0] silence_start: 0\n"
            "[silencedetect @ 0] silence_end: 0.64 | silence_duration: 0.64\n"
            "[silencedetect @ 0] silence_start: 3.95\n"
            "[silencedetect @ 0] silence_end: 4.44 | silence_duration: 0.49\n"
        )
        assert compose_video._parse_silencedetect_output(stderr, 12.0) == [(0.64, 3.95), (4.44, 12.0)]

    def test_trailing_silence_capped_at_duration(self) -> None:
        stderr = "silence_start: 9.05\nsilence_end: 9.37 | silence_duration: 0.32\n"
        assert compose_video._parse_silencedetect_output(stderr, 10.08) == [(0.0, 9.05), (9.37, 10.08)]

    def test_unmatched_start_treated_as_fully_voiced(self) -> None:
        """只报 silence_start 不报 end（流在静音中结束的极端情况）→ 忽略，整体有声。"""
        assert compose_video._parse_silencedetect_output("silence_start: 5.0", 8.0) == [(0.0, 8.0)]


class TestDetectVoicedSegments:
    """_detect_voiced_segments：subprocess 失败回退、Duration 从同一份 stderr 解析。"""

    def test_ffmpeg_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResult:
            returncode = 1
            stderr = "no such file"

        monkeypatch.setattr(compose_video.subprocess, "run", lambda *a, **k: FakeResult())
        assert compose_video._detect_voiced_segments(Path("missing.mp4")) == ([], None)

    def test_parses_duration_from_same_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stderr = (
            "  Duration: 00:00:10.08, start: 0.000000, bitrate: 6610 kb/s\n"
            "[silencedetect @ 0] silence_start: 1.69\n"
            "[silencedetect @ 0] silence_end: 2.01 | silence_duration: 0.32\n"
        )

        fake_result = type("FakeResult", (), {"returncode": 0, "stderr": stderr})
        monkeypatch.setattr(compose_video.subprocess, "run", lambda *a, **k: fake_result)
        voiced, duration = compose_video._detect_voiced_segments(Path("scene.mp4"))
        assert voiced == [(0.0, 1.69), (2.01, 10.08)]
        assert duration == pytest.approx(10.08)

    def test_cmd_uses_silencedetect_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, list[str]] = {}

        class FakeResult:
            returncode = 1
            stderr = ""

        def fake_run(cmd: list[str], **kwargs: object) -> FakeResult:
            captured["cmd"] = cmd
            return FakeResult()

        monkeypatch.setattr(compose_video.subprocess, "run", fake_run)
        compose_video._detect_voiced_segments(Path("scene.mp4"))
        af = captured["cmd"][captured["cmd"].index("-af") + 1]
        assert "silencedetect=noise=-25dB:d=0.3" in af


class TestAlignSubtitleSpansToAudio:
    """估算时间轴 → 音频对齐：溢出缩放、开场静音偏移、条间留白、失败回退。"""

    @staticmethod
    def _spans(durations: list[float]) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        offset = 0.0
        for index, duration in enumerate(durations):
            out.append({"offset_seconds": offset, "duration_seconds": duration, "text": f"L{index}"})
            offset += duration
        return out

    def test_single_span_within_scene_unchanged(self) -> None:
        out = compose_video._align_subtitle_spans_to_audio(self._spans([2.0]), 10.0, [(0.0, 10.0)])
        assert out[0]["offset_seconds"] == pytest.approx(0.0)
        assert out[0]["duration_seconds"] == pytest.approx(2.0)

    def test_overflow_scaled_and_gap_applied(self) -> None:
        spans = self._spans([6.4, 3.2])
        out = compose_video._align_subtitle_spans_to_audio(spans, 4.8, [(0.0, 4.8)])
        # 估算 9.6s 塞进 4.8s（scale=0.5）；首条扣 0.25s 留白，末条展示到场景结束
        assert out[0]["offset_seconds"] == pytest.approx(0.0)
        assert out[0]["duration_seconds"] == pytest.approx(3.2 - 0.25)
        assert out[1]["offset_seconds"] == pytest.approx(3.2)
        assert out[1]["duration_seconds"] == pytest.approx(1.6)

    def test_leading_silence_shifts_and_compresses(self) -> None:
        spans = self._spans([5.0, 5.0])
        out = compose_video._align_subtitle_spans_to_audio(spans, 10.0, [(2.0, 10.0)])
        # 开场静音 2s：可用 8s，scale=0.8；offset 从 2.0 起
        assert out[0]["offset_seconds"] == pytest.approx(2.0)
        assert out[0]["duration_seconds"] == pytest.approx(4.0 - 0.25)
        assert out[1]["offset_seconds"] == pytest.approx(6.0)
        assert out[1]["duration_seconds"] == pytest.approx(4.0)

    def test_small_lead_ignored(self) -> None:
        out = compose_video._align_subtitle_spans_to_audio(self._spans([2.0]), 10.0, [(0.1, 10.0)])
        assert out[0]["offset_seconds"] == pytest.approx(0.0)

    def test_empty_voiced_falls_back_to_duration_scale(self) -> None:
        out = compose_video._align_subtitle_spans_to_audio(self._spans([5.0]), 2.5, [])
        assert out[0]["duration_seconds"] == pytest.approx(2.5)

    def test_empty_spans_passthrough(self) -> None:
        assert compose_video._align_subtitle_spans_to_audio([], 10.0, [(0.0, 10.0)]) == []

    def test_short_span_keeps_min_display(self) -> None:
        out = compose_video._align_subtitle_spans_to_audio(self._spans([0.2]), 10.0, [(0.0, 10.0)])
        assert out[0]["duration_seconds"] == pytest.approx(0.2)

    def test_zero_duration_video_returns_same_list(self) -> None:
        spans = self._spans([2.0])
        assert compose_video._align_subtitle_spans_to_audio(spans, 0.0, [(0.0, 10.0)]) is spans


# ---------------------------------------------------------------------------
# 字幕：ASS 渲染（显式样式 + 硬换行，替代 SRT 默认样式的贴边/溢出问题）
# ---------------------------------------------------------------------------


class TestWrapSubtitleText:
    def test_wraps_cjk_at_max_units(self) -> None:
        text = "大哥，做兄弟的和你结义之时，说什么来？"
        wrapped = compose_video._wrap_subtitle_text(text, 12)
        assert wrapped == "大哥，做兄弟的和你结义之\\N时，说什么来？"

    def test_long_text_never_overflows_a_line(self) -> None:
        # 20 个全角字、max_units=6：宁可多拆几行，也不让任何一行超过 max_units
        text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸"
        wrapped = compose_video._wrap_subtitle_text(text, 6)
        lines = wrapped.split("\\N")
        assert lines == ["一二三四五六", "七八九十甲乙", "丙丁戊己庚辛", "壬癸"]
        assert all(len(line) <= 6 for line in lines)

    def test_blank_text_passthrough(self) -> None:
        assert compose_video._wrap_subtitle_text("   ", 12) == ""
        assert compose_video._wrap_subtitle_text("", 12) == ""

    def test_narrow_chars_consume_less_units(self) -> None:
        # 2 个全角 + 10 个半角 = 8 单位 ≤ 8 → 单行
        wrapped = compose_video._wrap_subtitle_text("你好helloworld", 8)
        assert "\\N" not in wrapped


class TestEscapeAssText:
    def test_braces_and_backslash_escaped(self) -> None:
        assert compose_video._escape_ass_text("a{b}c\\d") == "a\\{b\\}c\\\\d"
        assert compose_video._escape_ass_text("普通文本") == "普通文本"


class TestFormatAssTimestamp:
    def test_format(self) -> None:
        assert compose_video._format_ass_timestamp(0) == "0:00:00.00"
        assert compose_video._format_ass_timestamp(1.234) == "0:00:01.23"
        assert compose_video._format_ass_timestamp(3661.5) == "1:01:01.50"
        assert compose_video._format_ass_timestamp(-3) == "0:00:00.00"


class TestRenderAss:
    def test_structure_with_playres_and_style(self) -> None:
        rendered = compose_video._render_ass(
            [{"offset_seconds": 1.8, "duration_seconds": 3.2, "text": "你好"}], 720, 1280
        )
        assert "PlayResX: 720\nPlayResY: 1280" in rendered
        assert "WrapStyle: 2" in rendered
        assert "Style: Default," in rendered
        assert "Dialogue: 0,0:00:01.80,0:00:05.00,Default,,0,0,0,,你好" in rendered

    def test_style_numbers_scale_with_width(self) -> None:
        small = compose_video._render_ass([{"offset_seconds": 0.0, "duration_seconds": 1.0, "text": "x"}], 540, 960)
        large = compose_video._render_ass([{"offset_seconds": 0.0, "duration_seconds": 1.0, "text": "x"}], 1080, 1920)
        small_style = next(line for line in small.splitlines() if line.startswith("Style: Default,"))
        large_style = next(line for line in large.splitlines() if line.startswith("Style: Default,"))
        small_font = int(small_style.split(",")[2])
        large_font = int(large_style.split(",")[2])
        assert small_font < large_font

    def test_long_text_wrapped_with_hard_breaks(self) -> None:
        text = "大哥，做兄弟的和你结义之时，说什么来？有福同享，有难同当！"
        rendered = compose_video._render_ass(
            [{"offset_seconds": 0.0, "duration_seconds": 5.0, "text": text}], 720, 1280
        )
        assert "\\N" in rendered
        # 整段文本仍在同一 Dialogue 行内（ASS 硬换行不是真实换行）
        dialogue = next(line for line in rendered.splitlines() if line.startswith("Dialogue: 0,"))
        assert dialogue.count("\\N") >= 1

    def test_long_monologue_wrapped_within_width(self) -> None:
        text = (
            "大哥，做兄弟的和你结义之时，说什么来？咱俩有福同享，有难同当，"
            "不愿同年同月同日生，但愿同年同月同日死。今日大哥有难，兄弟焉能苟且偷生？"
        )
        rendered = compose_video._render_ass(
            [{"offset_seconds": 0.0, "duration_seconds": 12.0, "text": text}], 720, 1280
        )
        style = next(line for line in rendered.splitlines() if line.startswith("Style: Default,"))
        parts = style.split(",")
        font_size = int(parts[2])
        margin_lr = int(parts[19])
        dialogue = next(line for line in rendered.splitlines() if line.startswith("Dialogue: 0,"))
        body = dialogue.rsplit(",,", 1)[-1]
        max_units = (720 - 2 * margin_lr - 8) / font_size
        for line in body.split("\\N"):
            units = sum(compose_video._subtitle_char_units(ch) for ch in line)
            assert units <= max_units + 0.001
        assert body.count("\\N") >= 4

    def test_very_long_text_shrinks_font_to_stay_in_bounds(self) -> None:
        text = "好" * 120
        rendered = compose_video._render_ass(
            [{"offset_seconds": 0.0, "duration_seconds": 30.0, "text": text}], 720, 1280
        )
        style = next(line for line in rendered.splitlines() if line.startswith("Style: Default,"))
        font_size = int(style.split(",")[2])
        dialogue = next(line for line in rendered.splitlines() if line.startswith("Dialogue: 0,"))
        body = dialogue.rsplit(",,", 1)[-1]
        lines = body.split("\\N")
        assert font_size < 50
        assert len(lines) <= 5

    def test_empty_spans_renders_no_dialogue(self) -> None:
        rendered = compose_video._render_ass([], 720, 1280)
        assert "Dialogue:" not in rendered
        assert "[Events]" in rendered


# ---------------------------------------------------------------------------
# 字幕：faster-whisper 语音识别时间戳 + 台词配对
# ---------------------------------------------------------------------------


class TestAsrWordTimestamps:
    """_asr_word_timestamps：模型可用/不可用/抽取失败/转写异常四类路径。"""

    @staticmethod
    def _fake_words() -> list[tuple[float, float, str]]:
        return [(0.1, 0.4, "你"), (0.4, 0.8, "好")]

    def test_returns_words_from_fake_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeWord:
            def __init__(self, start: float, end: float, word: str) -> None:
                self.start, self.end, self.word = start, end, word

        class FakeSegment:
            def __init__(self, words: list[FakeWord]) -> None:
                self.words = words

        class FakeModel:
            def transcribe(self, path: str, **kwargs: object) -> tuple[object, object]:
                return (iter([FakeSegment([FakeWord(0.1, 0.4, "你"), FakeWord(0.4, 0.8, "好")])]), object())

        monkeypatch.setattr(compose_video, "_asr_load_model", lambda: FakeModel())
        monkeypatch.setattr(compose_video, "_extract_audio_for_asr", lambda video, wav: None)
        assert compose_video._asr_word_timestamps(Path("scene.mp4"), "zh") == self._fake_words()

    def test_model_unavailable_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(compose_video, "_asr_load_model", lambda: None)
        assert compose_video._asr_word_timestamps(Path("scene.mp4"), "zh") == []

    def test_extract_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeModel:
            def transcribe(self, path: str, **kwargs: object) -> tuple[object, object]:
                raise AssertionError("不应走到转写")

        monkeypatch.setattr(compose_video, "_asr_load_model", lambda: FakeModel())
        monkeypatch.setattr(
            compose_video, "_extract_audio_for_asr", lambda video, wav: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        assert compose_video._asr_word_timestamps(Path("scene.mp4"), "zh") == []

    def test_transcribe_failure_falls_back_to_no_vad(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, object]] = []

        class FakeModel:
            def transcribe(self, path: str, **kwargs: object) -> tuple[object, object]:
                calls.append(kwargs)
                if kwargs.get("vad_filter") is True:
                    raise RuntimeError("onnxruntime missing")
                return (iter([]), object())

        monkeypatch.setattr(compose_video, "_asr_load_model", lambda: FakeModel())
        monkeypatch.setattr(compose_video, "_extract_audio_for_asr", lambda video, wav: None)
        assert compose_video._asr_word_timestamps(Path("scene.mp4"), "zh") == []
        assert [c["vad_filter"] for c in calls] == [True, False]


class TestFindBestWordWindow:
    def test_finds_matching_window(self) -> None:
        words = [(0.0, 0.4, "大哥"), (0.4, 0.8, "做"), (0.8, 1.2, "兄弟"), (1.2, 1.6, "的"), (1.6, 2.0, "你")]
        window, score = compose_video._find_best_word_window("做兄弟的和你", words, 0)
        assert window == (0.0, 2.0, 5)
        assert score >= compose_video._ASR_MATCH_THRESHOLD

    def test_no_match_returns_none(self) -> None:
        words = [(0.0, 0.5, "music"), (0.5, 1.0, "noise")]
        window, score = compose_video._find_best_word_window("完全无关的台词", words, 0)
        assert window is None

    def test_empty_target_or_exhausted_index(self) -> None:
        assert compose_video._find_best_word_window("", [(0.0, 0.5, "x")], 0) == (None, 0.0)
        assert compose_video._find_best_word_window("台词", [(0.0, 0.5, "x")], 5) == (None, 0.0)


class TestAlignSubtitleSpansWithAsr:
    @staticmethod
    def _spans(texts: list[str]) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        offset = 0.0
        for text in texts:
            out.append({"offset_seconds": offset, "duration_seconds": 1.0, "text": text})
            offset += 1.0
        return out

    def test_matched_spans_take_asr_boundaries_with_pads(self) -> None:
        words = [
            (0.1, 0.5, "大哥"),
            (0.5, 0.9, "做"),
            (0.9, 1.3, "兄弟"),
            (1.3, 1.7, "的"),
            (1.7, 2.1, "和你"),
            (5.0, 5.4, "有福"),
            (5.4, 5.8, "同享"),
        ]
        out = compose_video._align_subtitle_spans_with_asr(self._spans(["大哥做兄弟的和你", "有福同享"]), words, 10.0)
        assert out[0]["offset_seconds"] == pytest.approx(0.0)  # 0.1-0.1 前导
        assert out[0]["duration_seconds"] == pytest.approx(2.25)  # 2.1+0.15 尾
        assert out[1]["offset_seconds"] == pytest.approx(4.9)
        assert out[1]["duration_seconds"] == pytest.approx(1.05)

    def test_unmatched_span_falls_back_after_cursor(self) -> None:
        words = [(0.5, 0.9, "music"), (0.9, 1.3, "only")]
        spans = self._spans(["完全无关的台词", "另一句"])
        out = compose_video._align_subtitle_spans_with_asr(spans, words, 10.0)
        # 首条未命中 → 估算 1.0s 从 0 起；次条从 1.0 起
        assert out[0]["offset_seconds"] == pytest.approx(0.0)
        assert out[0]["duration_seconds"] == pytest.approx(1.0)
        assert out[1]["offset_seconds"] == pytest.approx(1.0)

    def test_clamps_to_video_duration(self) -> None:
        words = [(8.0, 8.3, "末"), (8.3, 8.6, "句"), (9.0, 9.5, "尾巴")]
        out = compose_video._align_subtitle_spans_with_asr(self._spans(["末尾台词尾巴"]), words, 9.4)
        end = out[0]["offset_seconds"] + out[0]["duration_seconds"]
        assert end <= 9.4 + 1e-9

    def test_empty_words_returns_same_list(self) -> None:
        spans = self._spans(["台词"])
        assert compose_video._align_subtitle_spans_with_asr(spans, [], 10.0) is spans

    def test_monotonic_non_overlapping(self) -> None:
        words = [(0.1, 0.4, "一"), (0.4, 0.8, "二"), (0.8, 1.2, "三"), (2.0, 2.4, "四")]
        out = compose_video._align_subtitle_spans_with_asr(self._spans(["一二三", "四"]), words, 10.0)
        for prev, cur in zip(out, out[1:]):
            assert prev["offset_seconds"] + prev["duration_seconds"] <= cur["offset_seconds"] + 1e-9


# ---------------------------------------------------------------------------
# _subtitle_spans_from_unit_shots
# ---------------------------------------------------------------------------


class TestSubtitleSpansFromUnitShots:
    def test_dialogue_and_voiceover_lines(self) -> None:
        """整行对话/画外音提取为字幕时间片，offset 在 unit 内累计。"""
        shots = [
            {"text": ("中景，平视构图。@[顾家老宅·客厅]内，窗外铅灰色云层压低。\n@[苏小凤]:{这个家……怎么这么冷。}")},
            {"text": "{风雨欲来}"},
            {"text": "纯镜头描述行，不烧字幕。"},
        ]
        spans = compose_video._subtitle_spans_from_unit_shots(shots, None)
        assert [s["text"] for s in spans] == ["这个家……怎么这么冷。", "风雨欲来"]
        assert spans[0]["offset_seconds"] == 0
        assert spans[1]["offset_seconds"] == pytest.approx(spans[0]["duration_seconds"])
        assert all(s["duration_seconds"] > 0 for s in spans)

    def test_no_dialogue_yields_no_spans(self) -> None:
        shots = [{"text": "全景，固定机位，人物缓步走入。"}]
        assert compose_video._subtitle_spans_from_unit_shots(shots, None) == []

    def test_invalid_shapes_are_skipped(self) -> None:
        shots = ["bad", None, {"text": 123}, {"no_text": True}]
        assert compose_video._subtitle_spans_from_unit_shots(shots, None) == []

    def test_dialogue_mixed_into_description_line_is_not_subtitle(self) -> None:
        """台词与描述混在同一行不算规范台词行，与 shot_parser 语义一致。"""
        shots = [{"text": "画面内容 @[苏小凤]:{这不是台词} 混合行"}]
        assert compose_video._subtitle_spans_from_unit_shots(shots, None) == []

    def test_empty_or_blank_lines_are_skipped(self) -> None:
        shots = [{"text": ""}, {"text": "   "}, {"text": None}]
        assert compose_video._subtitle_spans_from_unit_shots(shots, None) == []
