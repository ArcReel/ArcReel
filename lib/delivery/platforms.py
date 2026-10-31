"""平台交付规格表与交付检查。

``PLATFORMS`` 是交付规格的单一数据源：渲染任务按画幅推导默认平台跑一遍检查，
状态接口可按需重查；``SafeZone`` 供烧入字幕的版式避让平台 UI 遮挡。

规格数值为 v1 标定值：平台公开上传规格（上限/编码/大小）+ 竖屏播放页布局估算
（安全区），各平台条目注释写明来源，平台规则随版本变化，修订时更新
``calibrated_on`` 与来源说明。检查只报告，不改渲染（ADR 0108）。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.infra.media_probe import MediaProbe

DeliveryStatus = Literal["pass", "warn", "fail"]

_CALIBRATED_ON = "2026-10-31"
_GIB = 1024**3
_MIB = 1024**2


@dataclass(frozen=True, slots=True)
class SafeZone:
    """平台 UI 在画面上的遮挡区，比例相对画布：上状态栏、下底栏、右侧操作列、左侧预留。"""

    top: float
    bottom: float
    right: float
    left: float = 0.0
    source: str = ""
    calibrated_on: str = _CALIBRATED_ON


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    """一个发布平台的交付规格；字段为 None 表示该平台不限/不检查。"""

    platform: str
    name_key: str
    aspect_ratios: tuple[str, ...]
    min_short_edge: int | None = None
    max_duration_seconds: float | None = None
    soft_duration_seconds: float | None = None
    near_limit_ratio: float = 0.9
    fps_range: tuple[float, float] | None = None
    container: str = "mp4"
    video_codec: str = "h264"
    audio_codec: str = "aac"
    max_file_size_bytes: int | None = None
    target_lufs: float | None = None
    lufs_tolerance: float = 2.0
    max_true_peak_dbtp: float | None = None
    shorts_max_duration_seconds: float | None = None
    safe_zone: SafeZone | None = None


def _vertical_player_zone(top: float, bottom: float, right: float) -> SafeZone:
    return SafeZone(
        top=top,
        bottom=bottom,
        right=right,
        source="v1 估算：竖屏播放页标准布局（顶部状态栏与导航、底部标题区、右侧操作列）",
    )


PLATFORMS: dict[str, PlatformSpec] = {
    spec.platform: spec
    for spec in (
        PlatformSpec(
            # 公开规格：9:16 为主、最长 15 分钟、mp4/h264/aac、约 4GB 上限；
            # 响度按平台响度归一目标 -14 LUFS 校核，真峰限 -1 dBTP。
            platform="douyin",
            name_key="delivery_platform_douyin",
            aspect_ratios=("9:16", "1:1", "16:9"),
            min_short_edge=720,
            max_duration_seconds=900.0,
            fps_range=(24, 60),
            max_file_size_bytes=4 * _GIB,
            target_lufs=-14.0,
            max_true_peak_dbtp=-1.0,
            safe_zone=_vertical_player_zone(0.08, 0.14, 0.16),
        ),
        PlatformSpec(
            # 公开规格：9:16 为主、标准档最长 10 分钟、mp4/h264/aac；
            # 布局与抖音竖屏播放页同源。
            platform="tiktok",
            name_key="delivery_platform_tiktok",
            aspect_ratios=("9:16", "1:1", "16:9"),
            min_short_edge=720,
            max_duration_seconds=600.0,
            fps_range=(24, 60),
            max_file_size_bytes=4 * _GIB,
            target_lufs=-14.0,
            max_true_peak_dbtp=-1.0,
            safe_zone=_vertical_player_zone(0.08, 0.14, 0.16),
        ),
        PlatformSpec(
            # 公开规格：16:9 为主、最长 12 小时、mp4/h264/aac、256GB 上限；
            # 竖屏 Shorts 形态 180 秒内；响度归一目标 -14 LUFS。
            platform="youtube",
            name_key="delivery_platform_youtube",
            aspect_ratios=("16:9", "9:16", "1:1"),
            min_short_edge=480,
            max_duration_seconds=43200.0,
            fps_range=(24, 60),
            max_file_size_bytes=256 * _GIB,
            target_lufs=-14.0,
            max_true_peak_dbtp=-1.0,
            shorts_max_duration_seconds=180.0,
            safe_zone=_vertical_player_zone(0.08, 0.14, 0.16),
        ),
        PlatformSpec(
            # 公开规格：支持横竖屏、mp4/h264/aac、8GB 上限；
            # 短剧场景时长校核取 30 分钟。
            platform="bilibili",
            name_key="delivery_platform_bilibili",
            aspect_ratios=("16:9", "9:16", "1:1", "3:4"),
            min_short_edge=720,
            max_duration_seconds=1800.0,
            fps_range=(24, 60),
            max_file_size_bytes=8 * _GIB,
            target_lufs=-14.0,
            max_true_peak_dbtp=-1.0,
            safe_zone=SafeZone(
                top=0.06,
                bottom=0.12,
                right=0.15,
                source="v1 估算：B 站移动端竖屏播放布局（顶部状态栏、底部标题、右侧点赞操作列）",
            ),
        ),
        PlatformSpec(
            # 公开规格：免费档 140 秒 / Premium 10 分钟、免费档 512MB；
            # 16:9 为主，竖屏与方形也接受。
            platform="x",
            name_key="delivery_platform_x",
            aspect_ratios=("16:9", "9:16", "1:1", "4:5"),
            min_short_edge=480,
            max_duration_seconds=600.0,
            soft_duration_seconds=140.0,
            fps_range=(24, 60),
            max_file_size_bytes=512 * _MIB,
            target_lufs=-14.0,
            max_true_peak_dbtp=-1.0,
            safe_zone=None,
        ),
        PlatformSpec(
            # 公开规格：Reels 3 分钟、9:16 推荐、mp4/h264/aac；
            # 竖屏播放页布局与抖音同源。
            platform="instagram",
            name_key="delivery_platform_instagram",
            aspect_ratios=("9:16", "1:1", "4:5"),
            min_short_edge=720,
            max_duration_seconds=180.0,
            fps_range=(24, 60),
            max_file_size_bytes=4 * _GIB,
            target_lufs=-14.0,
            max_true_peak_dbtp=-1.0,
            safe_zone=_vertical_player_zone(0.10, 0.14, 0.16),
        ),
    )
}


def default_platform(width: int, height: int) -> str:
    """按画幅推导默认平台：横屏 → youtube，竖屏与方屏 → douyin。"""
    return "youtube" if width > height else "douyin"


class DeliveryRow(BaseModel):
    """一条交付检查行：结构化字段 + 状态 + 期望/实测 + 文案键与参数（前端按 locale 渲染）。"""

    model_config = ConfigDict(frozen=True)

    field: str
    status: DeliveryStatus
    expected: str | None = None
    actual: str | None = None
    hint_key: str | None = None
    hint_params: dict[str, Any] = Field(default_factory=dict)


class DeliveryCheck(BaseModel):
    """一次平台交付检查的完整结果。"""

    model_config = ConfigDict(frozen=True)

    platform: str
    rows: tuple[DeliveryRow, ...]

    @property
    def fail_count(self) -> int:
        return sum(row.status == "fail" for row in self.rows)

    @property
    def warn_count(self) -> int:
        return sum(row.status == "warn" for row in self.rows)

    @property
    def ok(self) -> bool:
        return self.fail_count == 0 and self.warn_count == 0


def _named_ratio(width: int, height: int) -> str:
    """实测宽高比 → 规格表里的命名画幅（1% 容差），无法命名时返回原始像素。"""
    measured = Fraction(width, height)
    for name in ("9:16", "1:1", "16:9", "3:4", "4:5"):
        w, h = name.split(":")
        if abs(measured - Fraction(int(w), int(h))) <= Fraction(1, 100) * measured:
            return name
    return f"{width}x{height}"


def _fmt_seconds(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:g} s"
    if seconds < 5400:
        return f"{seconds / 60:g} min"
    return f"{seconds / 3600:g} h"


def _fmt_bytes(n: int) -> str:
    return f"{n / _GIB:.1f} GB" if n >= _GIB else f"{n / _MIB:.0f} MB"


def check_delivery(
    probe: MediaProbe,
    platform: str,
    *,
    file_size_bytes: int,
    integrated_lufs: float | None = None,
    true_peak_dbtp: float | None = None,
) -> DeliveryCheck:
    """按 ``PLATFORMS[platform]`` 对成片探测结果逐项校核，返回 PASS/WARN/FAIL 行列表。

    输入是 P0 的 ``MediaProbe`` 与 P1 的实测响度/真峰；``probe`` 缺字段时对应行记 FAIL
    （无法判定即不通过），``integrated_lufs``/``true_peak_dbtp`` 为 None 时跳过该两项。
    """
    spec = PLATFORMS[platform]
    video = probe.first_stream("video")
    audio = probe.first_stream("audio")
    rows: list[DeliveryRow] = []

    width, height = (video.width, video.height) if video is not None else (None, None)
    if width and height:
        ratio = _named_ratio(width, height)
        rows.append(
            DeliveryRow(
                field="aspect_ratio",
                status="pass" if ratio in spec.aspect_ratios else "fail",
                expected="、".join(spec.aspect_ratios),
                actual=ratio,
                hint_key=None if ratio in spec.aspect_ratios else "delivery_hint_aspect_ratio_unsupported",
                hint_params={
                    "ratio": ratio,
                    "allowed": "、".join(spec.aspect_ratios),
                },
            )
        )
    else:
        rows.append(
            DeliveryRow(
                field="aspect_ratio",
                status="fail",
                expected="、".join(spec.aspect_ratios),
                hint_key="delivery_hint_probe_incomplete",
            )
        )

    if spec.min_short_edge is not None and width and height:
        short_edge = min(width, height)
        rows.append(
            DeliveryRow(
                field="resolution",
                status="pass" if short_edge >= spec.min_short_edge else "fail",
                expected=f"短边 ≥ {spec.min_short_edge} px",
                actual=f"{width}x{height}",
                hint_key=None if short_edge >= spec.min_short_edge else "delivery_hint_resolution_low",
                hint_params={
                    "short_edge": short_edge,
                    "min_edge": spec.min_short_edge,
                },
            )
        )

    duration = video.duration_seconds if video is not None else None
    if duration is not None and spec.max_duration_seconds is not None:
        status: DeliveryStatus = "pass"
        hint_key: str | None = None
        if duration > spec.max_duration_seconds:
            status = "fail"
            hint_key = "delivery_hint_duration_over"
        elif spec.soft_duration_seconds is not None and duration > spec.soft_duration_seconds:
            status = "warn"
            hint_key = "delivery_hint_duration_soft_limit"
        elif duration >= spec.near_limit_ratio * spec.max_duration_seconds:
            status = "warn"
            hint_key = "delivery_hint_duration_near_limit"
        elif (
            spec.shorts_max_duration_seconds is not None
            and width
            and height
            and width < height
            and duration > spec.shorts_max_duration_seconds
        ):
            status = "warn"
            hint_key = "delivery_hint_shorts_over"
        rows.append(
            DeliveryRow(
                field="duration",
                status=status,
                expected=f"≤ {_fmt_seconds(spec.max_duration_seconds)}",
                actual=_fmt_seconds(duration),
                hint_key=hint_key,
                hint_params={
                    "actual": _fmt_seconds(duration),
                    "max": _fmt_seconds(spec.max_duration_seconds),
                    "soft": _fmt_seconds(spec.soft_duration_seconds) if spec.soft_duration_seconds is not None else "",
                    "shorts": _fmt_seconds(spec.shorts_max_duration_seconds)
                    if spec.shorts_max_duration_seconds is not None
                    else "",
                },
            )
        )

    fps = video.fps if video is not None else None
    if fps is not None and spec.fps_range is not None:
        lo, hi = spec.fps_range
        rows.append(
            DeliveryRow(
                field="fps",
                status="pass" if lo <= fps <= hi else "fail",
                expected=f"{lo:g}–{hi:g} fps",
                actual=f"{fps:g} fps",
                hint_key=None if lo <= fps <= hi else "delivery_hint_fps_out_of_range",
                hint_params={"actual": f"{fps:g} fps", "range": f"{lo:g}–{hi:g} fps"},
            )
        )

    if "mp4" in probe.container_formats:
        rows.append(DeliveryRow(field="container", status="pass", expected=spec.container, actual=spec.container))
    else:
        rows.append(
            DeliveryRow(
                field="container",
                status="fail",
                expected=spec.container,
                actual="、".join(sorted(probe.container_formats)) or "未识别",
                hint_key="delivery_hint_container_unsupported",
                hint_params={
                    "actual": "、".join(sorted(probe.container_formats)) or "未识别",
                    "expected": spec.container,
                },
            )
        )

    if video is not None and video.codec:
        rows.append(
            DeliveryRow(
                field="video_codec",
                status="pass" if video.codec == spec.video_codec else "fail",
                expected=spec.video_codec,
                actual=video.codec,
                hint_key=None if video.codec == spec.video_codec else "delivery_hint_video_codec_unsupported",
                hint_params={"actual": video.codec, "expected": spec.video_codec},
            )
        )
    else:
        rows.append(
            DeliveryRow(
                field="video_codec",
                status="fail",
                expected=spec.video_codec,
                hint_key="delivery_hint_probe_incomplete",
            )
        )

    if audio and audio.codec:
        rows.append(
            DeliveryRow(
                field="audio_codec",
                status="pass" if audio.codec == spec.audio_codec else "fail",
                expected=spec.audio_codec,
                actual=audio.codec,
                hint_key=None if audio.codec == spec.audio_codec else "delivery_hint_audio_codec_unsupported",
                hint_params={"actual": audio.codec, "expected": spec.audio_codec},
            )
        )
    else:
        rows.append(
            DeliveryRow(
                field="audio_codec",
                status="fail",
                expected=spec.audio_codec,
                actual="无音轨",
                hint_key="delivery_hint_audio_codec_unsupported",
                hint_params={"actual": "无音轨", "expected": spec.audio_codec},
            )
        )

    if spec.max_file_size_bytes is not None:
        rows.append(
            DeliveryRow(
                field="file_size",
                status="pass" if file_size_bytes <= spec.max_file_size_bytes else "fail",
                expected=f"≤ {_fmt_bytes(spec.max_file_size_bytes)}",
                actual=_fmt_bytes(file_size_bytes),
                hint_key=None if file_size_bytes <= spec.max_file_size_bytes else "delivery_hint_file_size_over",
                hint_params={"actual": _fmt_bytes(file_size_bytes), "max": _fmt_bytes(spec.max_file_size_bytes)},
            )
        )

    if integrated_lufs is not None and spec.target_lufs is not None:
        if not integrated_lufs > -70:
            rows.append(
                DeliveryRow(
                    field="loudness",
                    status="pass",
                    expected=f"{spec.target_lufs:g} LUFS ± {spec.lufs_tolerance:g}",
                    actual="静音",
                )
            )
        else:
            off = abs(integrated_lufs - spec.target_lufs) > spec.lufs_tolerance
            rows.append(
                DeliveryRow(
                    field="loudness",
                    status="warn" if off else "pass",
                    expected=f"{spec.target_lufs:g} LUFS ± {spec.lufs_tolerance:g}",
                    actual=f"{integrated_lufs:.1f} LUFS",
                    hint_key="delivery_hint_loudness_off" if off else None,
                    hint_params={
                        "actual": f"{integrated_lufs:.1f}",
                        "target": f"{spec.target_lufs:g}",
                        "tolerance": f"{spec.lufs_tolerance:g}",
                    },
                )
            )

    if true_peak_dbtp is not None and true_peak_dbtp > -70 and spec.max_true_peak_dbtp is not None:
        high = true_peak_dbtp > spec.max_true_peak_dbtp
        rows.append(
            DeliveryRow(
                field="true_peak",
                status="warn" if high else "pass",
                expected=f"≤ {spec.max_true_peak_dbtp:g} dBTP",
                actual=f"{true_peak_dbtp:.1f} dBTP",
                hint_key="delivery_hint_true_peak_high" if high else None,
                hint_params={"actual": f"{true_peak_dbtp:.1f}", "max": f"{spec.max_true_peak_dbtp:g}"},
            )
        )

    return DeliveryCheck(platform=platform, rows=tuple(rows))
