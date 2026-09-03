"""分集规划的有效每集目标体量：显式 ``episode_target_units`` 优先，否则按单集目标时长折算。

分集规划决定「每集塞多少原文」，脚本规划决定「已切好的一集拆多少个单元」。本模块把两个阶段
使用的项目设置收敛成一个「有效目标体量」：

- ``episode_target_units`` 显式设置时优先——它是用户直接给的体量，不需要也不应该再折算；
- 否则 ``episode_target_duration`` 非 ``None`` 时按口播语速折算出阅读单位数，语速取自
  :mod:`lib.speech_rate`，与脚本规划口播时长估算共用同一真相源（含项目级覆盖），两个阶段
  不会各按一套语速走；
- 两者都没有时返回 ``None``，调用方维持「按短视频节奏自行把握」的现状。

折算值始终带 ``source`` 标明来源，供 prompt 与工具返回的核对材料写明「这是按时长折算的」：
折算与显式设置的可信度不同（前者叠了一层语速估算），主 Agent 核对体量偏差时需要能区分，
否则会把折算值当成用户给的硬指标。drama 的原文不逐字口播（原文含叙述、成片只播台词），
同一公式对它只是粗略换算，因此措辞一律写成软目标、允许浮动，不写成精确对应。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from lib.episode_target_duration import project_episode_target_duration
from lib.speech_rate import project_speech_rate_override, speech_rate_units_per_second
from lib.text_metrics import reading_unit_noun

#: 每集目标体量在 ``project.json`` 的顶层字段名（阅读单位，按 ``source_language`` 解读）。
#: 该字段名同时写死在 ``manage-project`` skill 的 ``patch_project`` 白名单说明里（机器契约），
#: 由镜像测试防漂移。
EPISODE_TARGET_UNITS_FIELD: str = "episode_target_units"


def project_episode_target_units(project: Mapping[str, Any] | None) -> int | None:
    """从 project.json 解析显式每集目标体量，未填 / 脏值 / 小于 1 一律返回 ``None``。

    只接受 ``int``：阅读单位是可数量纲。``bool`` 是 ``int`` 子类，显式排除，避免 JSON 里的
    ``true`` 被当成「每集 1 个字」。写入侧（``patch_project`` 的正整数设置白名单）已按同一
    把尺拒绝非法值，这里的守卫是对手改 project.json 与历史脏数据的读时兜底。
    """
    if not isinstance(project, Mapping):
        return None
    raw = project.get(EPISODE_TARGET_UNITS_FIELD)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw >= 1 else None


@dataclass(frozen=True)
class EpisodeTargetVolume:
    """分集规划的有效每集目标体量及其来源。

    ``source`` 为 ``"units"`` 时来自用户显式设置的 ``episode_target_units``，``seconds`` 与
    ``units_per_second`` 均为 ``None``；为 ``"duration"`` 时 ``units`` 是折算结果，两个字段
    分别记下折算所用的目标时长与语速，供措辞里写明换算过程。
    """

    units: int
    unit_noun: str
    source: Literal["units", "duration"]
    seconds: int | None = None
    units_per_second: float | None = None


def resolve_episode_target_volume(
    project: Mapping[str, Any] | None,
    *,
    language: str | None,
) -> EpisodeTargetVolume | None:
    """解析分集规划的有效每集目标体量：显式 > 时长折算 > ``None``。

    ``language`` 是项目 ``source_language``，决定阅读单位的量词与折算语速（未登记 / 缺失
    语言按 :mod:`lib.speech_rate` 的默认回退）。折算口径为「目标秒数 × 语速」并四舍五入；
    合法区间下界（10 秒 × 0.001 单位 / 秒）会舍到 0，故取 1 作下限——目标体量为 0 对规划
    没有可用语义。
    """
    unit_noun = reading_unit_noun(language)
    explicit = project_episode_target_units(project)
    if explicit is not None:
        return EpisodeTargetVolume(units=explicit, unit_noun=unit_noun, source="units")

    seconds = project_episode_target_duration(project)
    if seconds is None:
        return None
    rate = speech_rate_units_per_second(language, project_speech_rate_override(project))
    return EpisodeTargetVolume(
        units=max(1, round(seconds * rate)),
        unit_noun=unit_noun,
        source="duration",
        seconds=seconds,
        units_per_second=rate,
    )
