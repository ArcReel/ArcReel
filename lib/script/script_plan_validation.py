"""Read-only business validation for script plans; callers own persistence and capability lookup."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Literal, NamedTuple

from lib.episode.episode_paths import episode_source_path, episode_source_relpath
from lib.episode.episode_sources import SourceOrigin, episode_entry, episode_source_origin
from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.i18n import _ as translate
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.asset_types import BUCKET_KEY, asset_name_comparison_key
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.draft_violation import DraftViolation, collect_violations, speech_problem_violation
from lib.script.plan_new_assets import NEW_ASSETS_FIELD, new_asset_violations, planning_project
from lib.script.reference_video.draft_validation import (
    validate_dialogue_load,
    validate_source_text_anchor,
    validate_unit_text,
)
from lib.script.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_EPISODE,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_UNIT_WITHOUT_SCENE,
    derive_utterances,
    derive_voice_bindings,
    unit_lacks_scene_reference,
)
from lib.script.reference_video.unit_capabilities import hydrate_reference_units
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import NARRATION_PLAN_SEGMENT_ID_PATTERN
from lib.script.script_plan_entries import ScriptPlanKind, entry_id_field, plan_variant
from lib.script.script_skeleton import rewrite_episode_prefix
from lib.speech.speech_composition import admit_script_unit
from lib.speech.speech_rate import project_speech_rate_override

_REFERENCE_LIST_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "drama": (("characters_in_scene", "character"), ("scenes", "scene"), ("props", "prop")),
    "narration": (("characters_in_segment", "character"), ("scenes", "scene"), ("props", "prop")),
}


def _unregistered_reference_violations(
    route: Literal["drama", "narration"],
    item: Mapping[str, Any],
    catalog: ReferenceCatalog,
    *,
    label: str,
    item_index: int,
    item_id: str | None,
) -> list[DraftViolation]:
    """一个条目的引用数组里既未登记、也不在本次新增项中的资产名。报告回显模型写的原名而非归一形式。"""
    violations: list[DraftViolation] = []
    for field, asset_type in _REFERENCE_LIST_FIELDS[route]:
        names = item.get(field) or []
        registered = catalog.reference_names(asset_type)
        bad = sorted({str(name) for name in names if asset_name_comparison_key(str(name)) not in registered})
        if bad:
            violations.append(
                DraftViolation(
                    f"{label} 的 {field} 引用了未登记的资产名: {bad}；"
                    "资产名必须逐字取自已登记资产，或把它列进 new_assets 并给出处理决定",
                    code="unregistered_asset",
                    label=label,
                    item_index=item_index,
                    item_id=item_id,
                    params={"label": label, "field": field, "names": bad},
                )
            )
    return violations


def collect_drama_violations(
    scenes: list[Any], *, project: Mapping[str, Any], new_assets: object
) -> list[DraftViolation]:
    """drama script_plan 的资产违约：引用须已登记或在本次新增项中，新增项的处理决定须解析得出。"""
    catalog = build_reference_catalog(planning_project(project, new_assets))
    violations: list[DraftViolation] = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        scene_id = scene.get("scene_id")
        scene_id = scene_id if isinstance(scene_id, str) else None
        violations.extend(
            _unregistered_reference_violations(
                "drama", scene, catalog, label=f"scene {scene_id or index + 1}", item_index=index, item_id=scene_id
            )
        )
    violations.extend(new_asset_violations(project, new_assets))
    return violations


class ReferenceSplitCaps(NamedTuple):
    """rv 拆分用的视频能力：两套逐 unit 档位 + 派生上限 + 用户偏好 + 声音输入档。

    ``reference_durations`` / ``text_durations`` 是有 / 没有可用参考图的 unit 各自的生效档位，
    ``durations`` 是二者的并集——schema 枚举与 prompt 候选集合取并集，因为落在任一套内的时长都
    可能合法；归属哪一套要等正文里的 `@[名称]` 提及按此刻可用的参考图水合后才知道。三者相等即该型号
    在当前分辨率下未声明生效的「参考图↔时长」联动约束，多数型号如此。

    ``voice`` 是 r2v 桶视频请求事实派生出的声音输入档，供声音相关的容忍 warning 消费——与时长档位
    同源于这一次求值；``text_voice`` 是 i2v 桶声音档，解析失败时为 None，原因沿用 ``text_problem``。
    逐 unit 的说话人数上限按引用状态取对应声音档。分两次查会让同一份产物的档位与声音提示
    描述不同时刻的配置。携带值对象而非事实对象本身：下游只需要声音那几位，穿整个事实过接口
    会把字段名耦合扩散到消费侧。
    """

    default_duration: int | None
    durations: list[int]
    reference_durations: list[int]
    text_durations: list[int]
    text_problem: VideoRequestFactsFailure | None
    max_duration: int
    max_refs: int | None
    voice: VoiceRenderSettings
    text_voice: VoiceRenderSettings | None

    def tiers_for(self, *, has_references: bool) -> list[int]:
        """该引用状态下的生效档位。"""
        return self.reference_durations if has_references else self.text_durations


def _validate_unit_duration_tier(label: str, duration: int, *, has_references: bool, caps: ReferenceSplitCaps) -> None:
    """按该 unit 此刻是否有可用参考图判时长是否落在生效档位内，出档抛 ``DraftViolation``。

    schema 的枚举卡的是两套档位的并集，一个带可用参考图的 unit 因此仍可能取到只有无图 unit 才
    合法的秒数——那样的 unit 执行期申请不到，等到入队才失败已无统一纠正入口。错误消息给出
    两条出路（换档位 / 去引用），与 prompt 里的教学同一口径。

    抛的是内容违约而非 ``ValueError``：这一类同样是 Agent 改一改草稿就能修好的，走草稿
    的修复闭环，不该退回丢弃重抽。
    """
    if not has_references and caps.text_problem is not None:
        failure = caps.text_problem
        params: dict[str, Any] = failure.parameters()
        raise DraftViolation(
            f"{label}：{translate(failure.code, **params)}（{failure.summary()}）",
            code=failure.code,
            label=label,
            params=params,
        )
    tiers = caps.tiers_for(has_references=has_references)
    if duration in tiers:
        return
    state = "带可用参考图" if has_references else "无可用参考图"
    remedy = (
        "；请改取该档位内的时长，或把次要资产融入描述文字、不用 `@` 引用"
        if has_references
        else "；请改取该档位内的时长"
    )
    raise DraftViolation(
        f"{label} 时长 {duration}s 不在{state}的 unit 的生效档位 {tiers} 内{remedy}",
        code="duration_off_tier",
        label=label,
        params={"label": label, "duration": duration, "tiers": tiers},
    )


def _reference_unit_id(episode: int, index: int, unit_ids: Sequence[str] | None = None) -> str:
    """扁平产出第 ``index`` 个 unit（1 起）的 ``unit_id``。

    落盘内容、违约报告与降级提示三者指的必须是同一个 unit：各自拼一遍格式串时，改一处编号
    口径就会让报告指偏到另一个 unit。落盘前由服务端发号（``ProjectManager.reserve_item_ids``，
    编号只分配、不复用），发出的编号经 ``unit_ids`` 传入；还没发号的扁平草稿按位置给出定位用的编号。
    """
    if unit_ids is not None:
        return unit_ids[index - 1]
    return f"E{episode}U{index:02d}"


def _duplicate_plan_ids(entries: Sequence[Any], *, id_field: str, episode: int) -> list[str]:
    ids = [entry[id_field] for entry in entries if isinstance(entry, dict) and isinstance(entry.get(id_field), str)]
    raw_dupes = {item_id for item_id, count in Counter(ids).items() if count > 1}
    rewritten_dupes = {
        item_id
        for item_id, count in Counter(str(rewrite_episode_prefix(raw_id, episode)) for raw_id in ids).items()
        if count > 1
    }
    return sorted(raw_dupes | rewritten_dupes)


def collect_reference_flat_violations(
    flat_units: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    project_path: Path,
    episode: int,
    novel_text: str | None,
    caps: ReferenceSplitCaps | None,
    source_language: str | None,
    new_assets: object,
) -> list[DraftViolation]:
    """逐 unit 收齐 script_plan 扁平产出的全部违约（不在首个违约处中断）。

    schema 已卡死时长枚举与外层形状；此处补依赖运行时能力值 / 项目登记表 / 源文的约束——
    时长落在该 unit 引用状态对应的生效档位内、原文锚是源文逐字子串、正文语法与资产引用合法、
    台词量念得完。收齐而非首个即抛：报告要能一次列全所有坏 unit，否则 Agent 每修一处就要再跑
    一轮才知道下一处。

    时长档位与正文合并为一个入口：适用哪套档位取决于该 unit 正文提及的引用此刻有没有可用参考图
    （文件存在且产物清单认领，与内容确认面板、执行同判据）——正文解析不出时无从判档位，此时报出的
    也只会是同一个问题的另一种说法。

    正文里的 ``@[名称]`` 须已登记或在本次新增项中：校验对着叠加了新增项的项目视图判，新增项
    自身的处理决定另行收进整集违约。
    """
    speech_rate_override = project_speech_rate_override(project)
    planning = planning_project(project, new_assets)
    hydrations = hydrate_reference_units(planning, project_path, flat_units)
    violations: list[DraftViolation] = []
    if dupes := _duplicate_plan_ids(flat_units, id_field="unit_id", episode=episode):
        violations.append(
            DraftViolation(
                f"unit_id 重复: {dupes}；每个视频单元的 ID 须全集唯一",
                code="duplicate_unit_id",
                params={"ids": dupes},
            )
        )
    for index, (flat, hydration) in enumerate(zip(flat_units, hydrations, strict=True), start=1):
        unit_id = flat.get("unit_id") or _reference_unit_id(episode, index)
        label = f"unit {unit_id}"
        duration = flat["duration_seconds"]
        source_text = flat.get("source_text", "")
        text = flat["text"]
        with_reference_images = hydration.hydrated_generation_type == "r2v"

        def _check_text_and_tier(
            la: str = label, tx: str = text, d: int = duration, with_images: bool = with_reference_images
        ) -> None:
            voice = (caps.voice if with_images else caps.text_voice) if caps is not None else None
            validate_unit_text(
                la,
                tx,
                planning,
                max_refs=caps.max_refs if caps is not None else None,
                max_speakers=voice.planning_speaker_limit if voice is not None else None,
            )
            if caps is not None:
                _validate_unit_duration_tier(la, d, has_references=with_images, caps=caps)

        source_checks = (
            [] if novel_text is None else [partial(validate_source_text_anchor, label, source_text, novel_text)]
        )
        violations.extend(
            collect_violations(
                [
                    *source_checks,
                    _check_text_and_tier,
                    lambda la=label, tx=text, d=duration: validate_dialogue_load(
                        la, tx, d, source_language, speech_rate_override
                    ),
                ],
                item_index=index - 1,
                item_id=unit_id,
            )
        )
    violations.extend(new_asset_violations(project, new_assets))
    return violations


_TOLERATED_VOICE_WARNINGS = (
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SILENT_EPISODE,
)


@dataclass(frozen=True, slots=True)
class SoftViolation:
    """一条软违约（降级提示）：``code`` 即文案 key，``params`` 为插值参数，定位到草稿条目的下标与 ID。"""

    code: str
    params: dict[str, Any]
    item_index: int
    item_id: str


def _reference_voice_warnings(
    unit_texts: list[str],
    project: dict[str, Any],
    voice: VoiceRenderSettings,
    *,
    episode: int,
    unit_ids: Sequence[str] | None,
) -> list[SoftViolation]:
    """逐 unit 派生声音绑定，取容忍类 warning。

    逐 unit 而非把全集正文拼起来判：unit 就是一次生成调用，参考音频段数上限按调用计——拼起来
    判会把「每个 unit 各两个说话人」误报成超限。与编辑器预览、执行期渲染共用
    ``derive_voice_bindings``，三处对同一份文稿给出的声音结论因此不会分叉。

    ``requires_reference_image`` 在本处一律关掉：该位的判定要配 ``speakers_with_reference_image``
    才有意义，而拆分阶段的 unit 尚未确定随请求发出的参考图。开着而不给图集合，等于把每个说话人
    都判成「无画面可挂」，那条 warning 又不在容忍列表内会被丢弃——结果是「未设参考音频」「超出
    段数上限」这些该让 Agent 看见的提示反被吞掉。
    """
    characters = project.get(BUCKET_KEY["character"]) or {}
    settings = replace(voice, requires_reference_image=False)
    found: list[SoftViolation] = []
    for index, text in enumerate(unit_texts, start=1):
        utterances, _syntax_warnings = derive_utterances(text)
        bindings = derive_voice_bindings(utterances, characters, settings)
        for warning in bindings.warnings:
            key = str(warning["key"])
            if key in _TOLERATED_VOICE_WARNINGS:
                found.append(
                    SoftViolation(key, dict(warning["params"]), index - 1, _reference_unit_id(episode, index, unit_ids))
                )
    return found


def _reference_scene_warnings(
    unit_texts: list[str], project: dict[str, Any], *, episode: int, unit_ids: Sequence[str] | None
) -> list[SoftViolation]:
    """逐 unit 取「未引用任何场景资产」的提示。

    与声音降级同属容忍类：地点由模型自由决定不是格式错误，正文照常落盘；但室内外交替的相邻
    unit 会各自发挥，不在产出当时说，Agent 与用户都要等看到成片才发现。

    判据取自 ``unit_lacks_scene_reference``，与编辑器预览的同名 warning 共用一个出口——同一份
    正文在回执与面板上必须给出同一个结论。
    """
    return [
        SoftViolation(WARN_UNIT_WITHOUT_SCENE, {}, index - 1, _reference_unit_id(episode, index, unit_ids))
        for index, text in enumerate(unit_texts, start=1)
        if unit_lacks_scene_reference(text, project)
    ]


def reference_soft_violations(
    unit_texts: list[str],
    project: dict[str, Any],
    *,
    episode: int,
    voice: VoiceRenderSettings,
    unit_ids: Sequence[str] | None = None,
) -> list[SoftViolation]:
    """一份扁平产出的全部软违约（声音降级 + 未引用场景），拆分与草稿流共用的单一出口。

    软违约不阻断落盘、不进违约报告，但每条呈现路径都要给出同一组结论：拆分回执、晋升回执、
    拆分 / 晋升被违约挡下时回给 Agent 的报告，以及 Web 上展示条目的地方。派生须留在本函数内，
    新增一类软违约才会同时到达每条路，而不是只被接到其中一条上、其余继续沉默。

    顺序固定为「声音在前、场景在后」：报告与回执并排比对时，同一份产物在不同路径上给出的
    行序不该抖动。
    """
    return _reference_voice_warnings(
        unit_texts, project, voice, episode=episode, unit_ids=unit_ids
    ) + _reference_scene_warnings(unit_texts, project, episode=episode, unit_ids=unit_ids)


def _narration_segment_id(segment: dict[str, Any]) -> str | None:
    """违约条目定位用的 ``segment_id``；缺失或空白时为 None，定位只剩下标。"""
    sid = segment.get("segment_id")
    return sid if isinstance(sid, str) and sid.strip() else None


def _narration_segment_label(segment: dict[str, Any], index: int) -> str:
    """违约条目的定位前缀。

    ``segment_id`` 缺失或空白时退回数组下标：那本身就是一条违约，但报告仍要能指到具体哪一项，
    否则 Agent 拿到的是一条无处下手的消息。
    """
    sid = segment.get("segment_id")
    return f"segment {sid}" if isinstance(sid, str) and sid.strip() else f"segments[{index}]"


def _normalize_for_coverage(text: str) -> str:
    """Unicode NFC 归一后把连续空白折叠为单个空格，只消除编码与空白差异，不删除空白本身。

    NFC 与 ``lib.episode.episode_ledger.normalize_source_text`` 定义的源文坐标系一致，也与参考生视频
    ``_normalize_for_anchor`` 同口径：带组合附加符的语种（如 vi）源文可能以 NFD 落盘、模型
    回写 NFC，不归一会把纯编码形式差异判成删字改字，而覆盖违约会落成草稿、堵住内容确认
    确认与 prompt_authoring 生成。
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def coverage_source_scope(source: str | None, *, episode: int) -> str:
    """覆盖判定所依据的源文范围的人话描述，供违约消息指名。

    覆盖判定是「分镜拼接 == 整份源文」的全等式，判定结果因此既取决于分镜正文、也取决于源文
    范围本身：范围取错了（如拿别集的源文件判这一集的分镜表），分镜一个字没改也判不过。不把
    范围写进消息，Agent 只会反复去改分镜正文，而问题不在那里。
    """
    return f"源文件 {source or episode_source_relpath(episode)}"


def _covers_source_verbatim(parts: list[str], source: str) -> bool:
    """各分镜是否按序、逐字、完整覆盖 *source*；分镜交界处允许至多一个空格。

    分镜两端的空白已由 :func:`_normalize_for_coverage` 剥掉，故交界处的一个空格只可能是分隔符，
    贪心跳过即可、无须回溯：分镜自身不可能以空格开头去争这个字符。

    不用「逐段 ``re.escape`` 后以 ``" ?"`` 拼成一条正则」：那样 pattern 长度与整篇源文同阶（可达
    数百 KB），而本判定在拆分、晋升与内容确认的读时重算三处各跑一次，内容确认那次还在请求
    协程里——每次都按源文规模编译一条正则，代价压在事件循环上。游标扫描是同一判定的线性写法。
    """
    cursor = 0
    for index, part in enumerate(parts):
        if index and source.startswith(" ", cursor):
            cursor += 1
        if not source.startswith(part, cursor):
            return False
        cursor += len(part)
    return cursor == len(source)


def collect_narration_violations(
    segments: list[dict[str, Any]],
    *,
    episode: int,
    supported_durations: list[int] | None,
    project: Mapping[str, Any],
    new_assets: object,
    novel_text: str | None,
    source_scope: str,
    source_path: str = "",
) -> list[DraftViolation]:
    """逐分镜收齐 narration script_plan 产出的全部违约（不在首个违约处中断）。

    资产引用须已登记，或在本次规划的新增项 ``new_assets`` 中；新增项的处理决定解析不出时另报整集违约。

    schema（``NarrationScriptPlanDraft``）已卡死字段与外层形状；此处补依赖运行时能力值 / 项目登记表 /
    源文的约束——segment_id 全集唯一、novel_text 非空白、时长落在当前档位内、资产名已登记、
    各分镜正文按序逐字完整覆盖源文。收齐而非首个即抛：报告要一次列全所有坏分镜，否则 agent
    每修一处就要再跑一轮才知道下一处。

    抛的是内容违约而非 ``ValueError``：这些都是 Agent 改一改草稿就能修好的，走草稿的修复
    闭环，不该退回丢弃重抽。

    ``source_scope`` 是 ``novel_text`` 那份文本的来源描述（见 :func:`coverage_source_scope`），
    只落进覆盖违约的消息里：覆盖判定同时取决于源文范围，范围本身不写出来就没法从报告里判断
    该改分镜还是该改范围。
    """
    violations: list[DraftViolation] = []

    prefix = f"E{episode}S"
    for index, segment in enumerate(segments):
        segment_id = segment.get("segment_id")
        if (
            not isinstance(segment_id, str)
            or not segment_id.startswith(prefix)
            or re.fullmatch(NARRATION_PLAN_SEGMENT_ID_PATTERN, segment_id) is None
        ):
            label = _narration_segment_label(segment, index)
            violations.append(
                DraftViolation(
                    f"{label} 的 segment_id 必须为 E{episode}S## 格式且集号匹配",
                    code="invalid_segment_id",
                    label=label,
                    item_index=index,
                    params={"label": label, "episode": episode},
                )
            )

    dupes = sorted(str(sid) for sid, count in Counter(s.get("segment_id") for s in segments).items() if count > 1)
    if dupes:
        violations.append(
            DraftViolation(
                f"segment_id 重复: {dupes}；每个分镜的 id 须全集唯一（prompt_authoring 视觉层按 id 与分镜对齐）",
                code="duplicate_segment_id",
                params={"ids": dupes},
            )
        )

    allowed = None if supported_durations is None else {int(d) for d in supported_durations}
    catalog = build_reference_catalog(planning_project(project, new_assets))
    for index, segment in enumerate(segments):
        label = _narration_segment_label(segment, index)

        if not str(segment.get("novel_text") or "").strip():
            violations.append(
                DraftViolation(
                    f"{label} 的 novel_text 为空白；每个分镜必须携带逐字取自原文的旁白正文",
                    code="blank_novel_text",
                    label=label,
                    item_index=index,
                    item_id=_narration_segment_id(segment),
                    params={"label": label},
                )
            )

        duration = segment.get("duration_seconds")
        if allowed is not None and (not isinstance(duration, int) or duration not in allowed):
            violations.append(
                DraftViolation(
                    f"{label} 的时长 {duration} 不在模型档位 {sorted(allowed)} 内；请改取该档位内的时长",
                    code="duration_off_tier",
                    label=label,
                    item_index=index,
                    item_id=_narration_segment_id(segment),
                    params={"label": label, "duration": duration, "tiers": sorted(allowed)},
                )
            )

        violations.extend(
            _unregistered_reference_violations(
                "narration",
                segment,
                catalog,
                label=label,
                item_index=index,
                item_id=_narration_segment_id(segment),
            )
        )

    parts = [_normalize_for_coverage(str(s.get("novel_text") or "")) for s in segments]
    if novel_text is not None and not _covers_source_verbatim(parts, _normalize_for_coverage(novel_text)):
        violations.append(
            DraftViolation(
                "各分镜的 novel_text 未按序、逐字、完整覆盖小说原文（存在删减、改写或重排）；"
                "分镜正文须原样复制原文、不要转述，且按序拼接后即是整篇原文。"
                f"本次判定依据的源文范围：{source_scope}",
                code="novel_text_coverage",
                params={"source": source_path or episode_source_relpath(episode)},
            )
        )
    violations.extend(new_asset_violations(project, new_assets))
    return violations


@dataclass(frozen=True, slots=True)
class EpisodePlanSource:
    text: str | None = None
    path: str | None = None
    problem: DraftViolation | None = None


def read_episode_plan_source(project_path: Path, project: Mapping[str, Any], episode: int) -> EpisodePlanSource:
    """Read the canonical episode text according to its ledger entry."""
    entry = episode_entry(project, episode)
    if entry is None or episode_source_origin(entry) is SourceOrigin.NONE:
        return EpisodePlanSource()

    path = episode_source_path(project_path, episode)
    relpath = episode_source_relpath(episode)
    try:
        if not path.is_symlink() and path.is_file():
            text = safe_join(project_path, relpath).read_text(encoding="utf-8")
            if text.strip():
                return EpisodePlanSource(text, relpath)
    except (OSError, UnicodeDecodeError, PathTraversalError):
        pass
    return EpisodePlanSource(
        path=relpath,
        problem=DraftViolation(
            f"分集账本声明本集有原文，但无法读取非空原文 {relpath}；请恢复本集原文后重试",
            code="episode_source_unavailable",
            params={"path": relpath},
        ),
    )


@dataclass(frozen=True, slots=True)
class ScriptPlanValidation:
    blocking_issues: list[DraftViolation]
    soft_violations: list[SoftViolation]


def _drama_content_violations(
    scenes: list[dict[str, Any]], durations: Sequence[int] | None, source_text: str | None, *, episode: int
) -> list[DraftViolation]:
    issues: list[DraftViolation] = []
    if dupes := _duplicate_plan_ids(scenes, id_field="scene_id", episode=episode):
        issues.append(
            DraftViolation(
                f"scene_id 重复: {dupes}；每个分镜的 ID 须全集唯一",
                code="duplicate_scene_id",
                params={"ids": dupes},
            )
        )
    for index, scene in enumerate(scenes):
        scene_id = scene["scene_id"]
        label = f"scene {scene_id}"
        duration = scene["duration_seconds"]
        if durations is not None and duration not in durations:
            issues.append(
                DraftViolation(
                    f"{label} 的时长 {duration} 不在模型档位 {list(durations)} 内；请改取该档位内的时长",
                    code="duration_off_tier",
                    label=label,
                    item_index=index,
                    item_id=scene_id,
                    params={"label": label, "duration": duration, "tiers": list(durations)},
                )
            )
        anchor = scene.get("source_text") or ""
        if source_text is not None and anchor.strip():
            issues.extend(
                collect_violations(
                    [partial(validate_source_text_anchor, label, anchor, source_text)],
                    item_index=index,
                    item_id=scene_id,
                )
            )
        admission = admit_script_unit("scenes", scene, ignore_marker=True)
        for problem in admission.problems:
            issue = speech_problem_violation(problem, label=label)
            issue.item_index = index
            issue.item_id = scene_id
            issues.append(issue)
    return issues


def validate_script_plan(
    kind: ScriptPlanKind,
    content: Mapping[str, Any],
    *,
    project: dict[str, Any],
    project_path: Path,
    episode: int,
    source: EpisodePlanSource,
    capabilities: list[int] | ReferenceSplitCaps | VideoRequestFactsFailure | None,
) -> ScriptPlanValidation:
    """实时校验已通过结构检查的脚本规划，仅读取当前项目事实，不修改项目或候选正文。

    已保存规划传入编辑器模型验证后的内容，源文由 ``read_episode_plan_source`` 读取。
    生成侧可传入本次请求冻结的源文和已解析的模型输出。能力解析失败时传入具体失败，
    其余有依据的规则继续计算。
    """
    variant = plan_variant(kind)
    entries = content[variant.plan_items_key]
    assets = content.get(NEW_ASSETS_FIELD)
    issues = [source.problem] if source.problem is not None else []
    soft: list[SoftViolation] = []
    if not entries:
        issues.append(DraftViolation("脚本规划没有条目；请至少添加一条", code="empty_script_plan", params={}))
    if entries and isinstance(capabilities, VideoRequestFactsFailure):
        params: dict[str, Any] = capabilities.parameters()
        issues.append(
            DraftViolation(
                f"{translate(capabilities.code, **params)}（{capabilities.summary()}）",
                code=capabilities.code,
                params=params,
            )
        )

    if kind == "reference_video":
        caps = capabilities if isinstance(capabilities, ReferenceSplitCaps) else None
        issues.extend(
            collect_reference_flat_violations(
                entries,
                project,
                project_path=project_path,
                episode=episode,
                novel_text=source.text,
                caps=caps,
                source_language=project.get("source_language"),
                new_assets=assets,
            )
        )
        if caps is not None:
            ids = [
                item.get(entry_id_field(kind)) or _reference_unit_id(episode, index)
                for index, item in enumerate(entries, 1)
            ]
            soft = reference_soft_violations(
                [item["text"] for item in entries],
                planning_project(project, assets),
                episode=episode,
                voice=caps.voice,
                unit_ids=ids,
            )
    elif kind == "narration":
        issues.extend(
            collect_narration_violations(
                entries,
                episode=episode,
                supported_durations=capabilities if isinstance(capabilities, list) else None,
                project=project,
                new_assets=assets,
                novel_text=source.text if entries else None,
                source_scope=coverage_source_scope(source.path, episode=episode),
                source_path=source.path or episode_source_relpath(episode),
            )
        )
    else:
        issues.extend(collect_drama_violations(entries, project=project, new_assets=assets))
        issues.extend(
            _drama_content_violations(
                entries, capabilities if isinstance(capabilities, list) else None, source.text, episode=episode
            )
        )
    return ScriptPlanValidation(issues, soft)
