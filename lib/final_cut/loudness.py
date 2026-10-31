"""成片整集音频的 EBU R128 响度归一。

混音后先实测，再二遍重编码统一到成片的积分响度与真峰目标：实测值提升到目标不超真峰上限时，
走 ``loudnorm`` 二遍线性归一（只施加常数增益）；超限时先按常数增益把积分响度提到目标，再用
``alimiter`` 把峰值压回上限。两条路径都保持采样数与时间戳不变。几乎无声的混音保持原样（与 BGM
登记的静默判据一致），不放大噪声底。成片验收时重测交付文件的积分响度与真峰并记入验收结果：测不出、
或偏离目标超出容差，只记警告，不让验收失败。
"""

from __future__ import annotations

import math

from lib.bgm.library import SILENCE_LOUDNESS_LUFS
from lib.bgm.loudness import LoudnessSummary

TARGET_INTEGRATED_LUFS = -14.0
"""成片整集音频归一到的积分响度（LUFS），社媒短视频的主流目标。"""

TARGET_TRUE_PEAK_DBTP = -1.5
"""成片整集音频归一到的真峰上限（dBTP），给编码过冲留余量。"""

LOUDNORM_LRA_LU = 11.0
"""loudnorm 的目标响度范围（LU）。"""

INTEGRATED_WARNING_TOLERANCE_LUFS = 0.5
"""验收实测积分响度偏离目标超出该值（LUFS）时记警告。"""

TRUE_PEAK_WARNING_TOLERANCE_DBTP = 0.5
"""验收实测真峰超出目标该值（dBTP）以上时记警告。"""

_LIMIT = 10.0 ** (TARGET_TRUE_PEAK_DBTP / 20.0)
"""限幅器的峰值上限（线性振幅），与真峰目标同值。"""


def is_silent_loudness(integrated_lufs: float) -> bool:
    """积分响度不高于 EBU R128 的绝对门限视为无声，与 BGM 登记的静默判据一致。"""
    return integrated_lufs <= SILENCE_LOUDNESS_LUFS


def normalize_gain_db(measurement: LoudnessSummary) -> float:
    """把实测积分响度提到目标所需的常数增益（dB）。"""
    return TARGET_INTEGRATED_LUFS - measurement.integrated_lufs


def _limiter_filter() -> str:
    return f"alimiter=limit={_LIMIT:.6f}:level=false:latency=true"


def _volume_filter(measurement: LoudnessSummary) -> str:
    """常数增益提到积分响度目标，超限峰值由限幅器压回真峰上限。"""
    return f"volume={normalize_gain_db(measurement):g}dB,{_limiter_filter()}"


def linear_loudnorm_filter(measurement: LoudnessSummary) -> str:
    """loudnorm 二遍线性归一参数：实测值齐全且常数增益不超真峰上限时使用。

    实测积分、门限、响度范围按滤镜选项的取值边界裁剪后传入：裁剪只影响线性分支的进入判定
    与线性增益的微小舍入，线性分支下不参与其余计算。
    """
    integrated = min(max(measurement.integrated_lufs, -99.0), -0.01)
    lra = measurement.lra_lu if measurement.lra_lu is not None else 0.01
    threshold = measurement.threshold_lufs if measurement.threshold_lufs is not None else -69.99
    lra = min(max(lra, 0.01), LOUDNORM_LRA_LU)
    threshold = min(max(threshold, -69.99), -0.5)
    return (
        f"loudnorm=I={TARGET_INTEGRATED_LUFS:g}:TP={TARGET_TRUE_PEAK_DBTP:g}:LRA={LOUDNORM_LRA_LU:g}"
        f":measured_I={integrated:g}:measured_TP={measurement.true_peak_dbtp:g}"
        f":measured_LRA={lra:g}:measured_thresh={threshold:g}:linear=true"
    )


def normalize_audio_filter(measurement: LoudnessSummary) -> str:
    """整集混音归一的重编码滤镜链：常数增益提到积分响度目标，必要时限幅压回真峰上限。

    优先走二遍线性 loudnorm；实测值缺失或线性归一会超真峰上限时退回常数增益加限幅，
    两者都不改动采样数与时间戳。
    """
    gain = normalize_gain_db(measurement)
    if not math.isfinite(gain):
        return _limiter_filter()
    true_peak = measurement.true_peak_dbtp
    lra = measurement.lra_lu
    threshold = measurement.threshold_lufs
    if (
        true_peak is None
        or not math.isfinite(true_peak)
        or lra is None
        or not math.isfinite(lra)
        or threshold is None
        or not math.isfinite(threshold)
        or true_peak + gain > TARGET_TRUE_PEAK_DBTP
    ):
        return _volume_filter(measurement)
    return linear_loudnorm_filter(measurement)


def loudness_warnings(measurement: LoudnessSummary | None) -> tuple[str, ...]:
    """成片响度验收的警告：测不出、或实测偏离目标超出容差；无声成片不告警。"""
    if measurement is None:
        return ("成片响度无法实测",)
    if is_silent_loudness(measurement.integrated_lufs):
        return ()
    warnings: list[str] = []
    if abs(measurement.integrated_lufs - TARGET_INTEGRATED_LUFS) > INTEGRATED_WARNING_TOLERANCE_LUFS:
        warnings.append(
            f"成片积分响度 {measurement.integrated_lufs:.1f} LUFS 偏离目标 {TARGET_INTEGRATED_LUFS:g} LUFS 超出容差"
        )
    if (
        measurement.true_peak_dbtp is not None
        and measurement.true_peak_dbtp > TARGET_TRUE_PEAK_DBTP + TRUE_PEAK_WARNING_TOLERANCE_DBTP
    ):
        warnings.append(
            f"成片真峰 {measurement.true_peak_dbtp:.1f} dBTP 超出目标 {TARGET_TRUE_PEAK_DBTP:g} dBTP 超出容差"
        )
    return tuple(warnings)


__all__ = [
    "INTEGRATED_WARNING_TOLERANCE_LUFS",
    "LOUDNORM_LRA_LU",
    "TARGET_INTEGRATED_LUFS",
    "TARGET_TRUE_PEAK_DBTP",
    "TRUE_PEAK_WARNING_TOLERANCE_DBTP",
    "is_silent_loudness",
    "linear_loudnorm_filter",
    "loudness_warnings",
    "normalize_audio_filter",
    "normalize_gain_db",
]
