"""ad 模式参考直出的派生分组器：平铺 shots[] → video_unit 轻量索引。

ad 剧本骨架唯一（shots 是内容唯一真相，见 docs/adr/0033）；reference_video
路径不更换骨架，而是把镜头**派生分组**为 video_unit——连续镜头、每 unit
不超过 4 个 shot、unit 总时长受供应商时长上限约束、继承镜头参考集。分组
结果以轻量索引（unit → shot_ids + 参考集）持久于剧本 JSON，仅引用 shot_id
不复制镜头内容；分组为纯函数，同样的 shots 与约束必然产出同样的分组，
重生成单个 unit 时分组可复现。
"""

from __future__ import annotations

import hashlib
import json

from lib.asset_types import normalize_asset_name
from lib.script_models import GeneratedAssets, ad_shot_duration_seconds, get_generated_assets

#: 单个 video_unit 最多容纳的镜头数，与 ``ReferenceVideoUnit.shots`` 的
#: ``max_length=4`` 同口径（一个 unit 是一次视频生成调用的最小粒度）。
AD_UNIT_MAX_SHOTS = 4


#: 镜头字段 → 参考类型，按注入优先级排列：产品绝对优先（注入二元规则——
#: ``products_in_shot`` 非空即产品镜头，产品参考全量进入参考集且排在所有
#: 其它参考之前），其余沿用 character → scene → prop 的既有解析顺序。
_REFERENCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("products_in_shot", "product"),
    ("characters_in_shot", "character"),
    ("scenes", "scene"),
    ("props", "prop"),
)


def ad_unit_references(shots: list[dict]) -> list[dict]:
    """unit 参考集：成员镜头参考的并集，产品在前，类型内按首次出现顺序去重。

    shots 是内容唯一真相：派生索引、来源签名与执行期请求都从成员镜头现算参考集，
    三处同源——索引里持久化的 ``references`` 只是展示用缓存，镜头参考字段被编辑后
    未重新派生时它会落后于镜头，拿它送生成会让产物依据的参考与签名记录的不一致。

    去重按归一名（:func:`lib.asset_types.normalize_asset_name`）而非裸字符串：同一资产在不同
    镜头里可能以 NFC/NFD 两种等价编码写入，裸比对判不相等会让它派生出两条 reference——画布
    上重复显示同一资产，并各占一个参考图槽位挤掉真正不同的参考。条目本身保留镜头里的原始
    形式，判等交给读取侧的归一（与 ``_resolve_unit_references`` 的去重同构）。
    """
    references: list[dict] = []
    for field, ref_type in _REFERENCE_FIELDS:
        seen: set[str] = set()
        for shot in shots:
            names = shot.get(field)
            if not isinstance(names, list):
                continue
            for name in names:
                if not isinstance(name, str) or not name:
                    continue
                canonical = normalize_asset_name(name)
                if canonical in seen:
                    continue
                seen.add(canonical)
                references.append({"type": ref_type, "name": name})
    return references


def derive_ad_reference_units(
    shots: object,
    *,
    episode: int,
    max_unit_duration: int | None = None,
) -> list[dict]:
    """把 ad 剧本的 shots 按顺序派生为 video_unit 轻量索引（纯函数）。

    分组只取连续镜头，不重排；每 unit 最多 ``AD_UNIT_MAX_SHOTS`` 个 shot；
    ``max_unit_duration``（供应商单次生成时长上限，秒）给定时，unit 内镜头
    时长之和不超过该上限。单镜头自身超上限时无法再拆，独立成 unit，留给
    执行层 clamp + warning 软处理。

    每个 unit 继承成员镜头的参考集（产品全量且绝对优先，见 ``ad_unit_references``）。

    Returns:
        ``[{"unit_id": "E{episode}U{n}", "shot_ids": [...], "references": [...]}, ...]``
    """
    if not isinstance(shots, list):
        return []
    # 非正上限是无意义约束（上游解析对 0/缺失已归一为 None，这里兜防御）：
    # 若按字面执行会把所有镜头逼成单镜头 unit，按"无上限"处理
    if max_unit_duration is not None and max_unit_duration <= 0:
        max_unit_duration = None

    groups: list[list[dict]] = []
    current: list[dict] = []
    current_duration = 0
    for shot in shots:
        # 脏数据（非 dict / 缺 shot_id）确定性跳过：Agent 可裸写 script JSON，
        # 分组必须对降级保存的原始 dict 也稳健且可复现。
        if not isinstance(shot, dict) or not isinstance(shot.get("shot_id"), str) or not shot["shot_id"]:
            continue
        duration = ad_shot_duration_seconds(shot)
        over_count = len(current) >= AD_UNIT_MAX_SHOTS
        over_duration = (
            max_unit_duration is not None and bool(current) and current_duration + duration > max_unit_duration
        )
        if over_count or over_duration:
            groups.append(current)
            current = []
            current_duration = 0
        current.append(shot)
        current_duration += duration
    if current:
        groups.append(current)

    return [
        {
            "unit_id": f"E{episode}U{n}",
            "shot_ids": [s["shot_id"] for s in group],
            "references": ad_unit_references(group),
        }
        for n, group in enumerate(groups, start=1)
    ]


def _reference_signature(entries: object) -> list[tuple[str, str]]:
    """references 的比较坐标系：(type, NFC 归一名) 有序列表。

    落盘条目保留镜头里的原始编码形式（见 ``ad_unit_references``），同一资产可能以
    NFC/NFD 两种等价形式出现——参考集是否变化必须按归一名判定，裸字节比较会把
    编码形式差异误判为语义变化。脏条目（非 dict、非字符串字段）确定性跳过/降级，
    与派生侧对脏数据的稳健口径一致。
    """
    if not isinstance(entries, list):
        return []
    signature: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        signature.append((str(entry.get("type")), normalize_asset_name(name) if isinstance(name, str) else str(name)))
    return signature


def merge_ad_reference_units(existing: object, derived: list[dict]) -> list[dict]:
    """把新派生的分组与剧本中已持久化的索引合并（纯函数，不改入参）。

    剧本编辑只改剧本：合并按 ``unit_id`` 沿用旧条目的 ``generated_assets``，
    从不清空产物指针——产物内容与指针只由成功的生成覆盖（finalize 单一写点）。
    合并不判定产物是否过期：stale 是读时派生属性（``is_ad_unit_stale``，比较
    当前编排签名与产物落盘签名），不落盘；旧条目上残留的历史标记位随条目重建
    自然丢弃。
    """
    existing_by_id: dict[str, dict] = {}
    if isinstance(existing, list):
        for entry in existing:
            if isinstance(entry, dict) and isinstance(entry.get("unit_id"), str):
                existing_by_id[entry["unit_id"]] = entry

    merged: list[dict] = []
    for unit in derived:
        prev = existing_by_id.get(unit["unit_id"])
        # 损坏值经 get_generated_assets 归一化为空 dict，与下面的 `assets or 模板` 汇合到同一结果。
        assets = dict(get_generated_assets(prev)) if isinstance(prev, dict) else {}
        merged.append({**unit, "generated_assets": assets or GeneratedAssets().model_dump()})
    return merged


def ad_unit_source_signature(script: dict, unit: dict) -> str:
    """unit 的编排 + 参考集签名：规范化 JSON 的 sha256（十六进制）。

    比较坐标系与合并派生同源：只含成员镜头 ID 序列与从 shots（内容唯一真相）
    现算的参考集（NFC 归一），镜头正文与时长不进签名——纯文案编辑不作废产物。
    成员镜头缺失（索引悬空）时按现存成员计算：与生成时全员在场的签名必然不同，
    读时自然判为偏离。
    """
    return _source_signature(ad_shots_by_id(script), unit)


def _source_signature(by_id: dict[str, dict], unit: dict) -> str:
    """按已建好的镜头索引算签名——批量判定共用一份索引，不逐 unit 重建。"""
    # shot_ids 整体非 list（Agent 裸写的脏索引）与元素非字符串同口径降级为空成员集，
    # 而不是让读时派生在 GET /units、导出预检这些只读路径上抛 TypeError。
    shot_ids = unit.get("shot_ids")
    member_ids = [
        sid for sid in (shot_ids if isinstance(shot_ids, list) else []) if isinstance(sid, str) and sid in by_id
    ]
    payload = {
        "shot_ids": member_ids,
        "references": _reference_signature(ad_unit_references([by_id[sid] for sid in member_ids])),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_ad_unit_stale(script: dict, unit: dict) -> bool:
    """读时派生 stale：产物落盘签名与当前编排签名不一致即偏离。

    无成片谈不上产物过期；无签名的存量产物视为非 stale（签名机制引入前生成，
    无从比较），随下一次生成补齐签名。
    """
    return _is_stale(ad_shots_by_id(script), unit)


def _is_stale(by_id: dict[str, dict], unit: dict) -> bool:
    """按已建好的镜头索引判 stale，语义同 ``is_ad_unit_stale``。"""
    assets = get_generated_assets(unit)
    if not assets.get("video_clip"):
        return False
    recorded = assets.get("source_signature")
    if not isinstance(recorded, str) or not recorded:
        return False
    return recorded != _source_signature(by_id, unit)


def annotate_ad_unit_staleness(script: dict, units: object) -> list:
    """给对外透出的 unit 列表注入读时派生的 ``stale``（返回浅拷贝，不落盘）。

    剧本中的条目不承载 stale——历史剧本残留的 stale 键在此剥除，偏离的 unit
    仅在返回副本上携带 ``stale: True``（与旧口径一致：非 stale 不带该键）。
    脏条目（非 dict）原样透传，交由消费方的既有降级分支处理。

    镜头索引在进入循环前建一次并贯穿全部 unit：逐 unit 走
    ``is_ad_unit_stale`` 会按 unit 数重复扫描 shots。
    """
    if not isinstance(units, list):
        return []
    by_id = ad_shots_by_id(script)
    annotated: list = []
    for unit in units:
        if not isinstance(unit, dict):
            annotated.append(unit)
            continue
        entry = {k: v for k, v in unit.items() if k != "stale"}
        if _is_stale(by_id, unit):
            entry["stale"] = True
        annotated.append(entry)
    return annotated


def ad_stale_unit_ids(script: dict, units: object) -> list[str]:
    """偏离当前编排的 unit_id 清单，判定与 ``annotate_ad_unit_staleness`` 同源。

    只要 id 清单的调用方走这里，就与注入路径共用同一份镜头索引，也不必为读一个
    布尔位构造整份对外副本。清单是要给人看的（智能体把它拼进提示文案），无
    ``unit_id`` 的脏条目按跳过处理——转成字符串会让 ``None`` 混进去冒充真实 unit ID。
    """
    if not isinstance(units, list):
        return []
    by_id = ad_shots_by_id(script)
    return [
        u["unit_id"] for u in units if isinstance(u, dict) and isinstance(u.get("unit_id"), str) and _is_stale(by_id, u)
    ]


def sync_ad_reference_units(
    script: dict,
    *,
    episode: int,
    max_unit_duration: int | None = None,
) -> list[dict]:
    """从 shots 重新派生分组并写回 ``script["reference_units"]``，返回合并后的索引。

    shots 是内容唯一真相：索引始终由本函数从 shots 重算，``generated_assets``
    按 unit_id 沿用、从不清空；产物是否偏离编排由读取侧派生
    （见 ``is_ad_unit_stale``），本函数不打标。
    """
    derived = derive_ad_reference_units(script.get("shots"), episode=episode, max_unit_duration=max_unit_duration)
    merged = merge_ad_reference_units(script.get("reference_units"), derived)
    script["reference_units"] = merged
    return merged


def ad_shots_by_id(script: dict) -> dict[str, dict]:
    """按 shot_id 索引 shots（内容唯一真相）；脏条目（非 dict / 缺 shot_id）跳过。

    索引水合（``resolve_ad_unit_shots``）与剪映导出的字幕对齐共用此构造。
    """
    shots = script.get("shots")
    by_id: dict[str, dict] = {}
    if isinstance(shots, list):
        for shot in shots:
            if isinstance(shot, dict) and isinstance(shot.get("shot_id"), str) and shot["shot_id"]:
                by_id[shot["shot_id"]] = shot
    return by_id


def resolve_ad_unit_shots(script: dict, unit: dict) -> list[dict]:
    """按索引条目的 shot_ids 从 shots（内容唯一真相）水合成员镜头，保持索引顺序。

    Raises:
        ValueError: 任一 shot_id 在 shots 中不存在——索引已过期（镜头被删除/改 ID
            后未重新派生），调用方应提示重新派生分组。
    """
    by_id = ad_shots_by_id(script)

    resolved: list[dict] = []
    missing: list[str] = []
    for sid in unit.get("shot_ids") or []:
        shot = by_id.get(sid)
        if shot is None:
            missing.append(str(sid))
        else:
            resolved.append(shot)
    if missing:
        unit_id = unit.get("unit_id")
        raise ValueError(f"分组索引已过期：unit {unit_id} 引用的镜头 {', '.join(missing)} 不存在，请重新派生分组")
    return resolved


def _shot_prompt_text(shot: dict) -> str:
    """单镜头的画面描述文本：静态画面 + 动作 + 运镜 + 环境音 + 台词。

    口播文案（voiceover_text）是后期配音的输入，刻意不进画面生成 prompt。
    脏数据（非 dict 的 prompt 字段、非字符串值）按空处理。
    """
    raw_image = shot.get("image_prompt")
    raw_video = shot.get("video_prompt")
    image_prompt: dict = raw_image if isinstance(raw_image, dict) else {}
    video_prompt: dict = raw_video if isinstance(raw_video, dict) else {}

    def _text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    parts: list[str] = []
    if scene := _text(image_prompt.get("scene")):
        parts.append(scene)
    if action := _text(video_prompt.get("action")):
        parts.append(action)
    if camera := _text(video_prompt.get("camera_motion")):
        parts.append(f"运镜：{camera}")
    if audio := _text(video_prompt.get("ambiance_audio")):
        parts.append(f"环境音：{audio}")
    dialogue = video_prompt.get("dialogue")
    if isinstance(dialogue, list):
        for entry in dialogue:
            if not isinstance(entry, dict):
                continue
            speaker = _text(entry.get("speaker"))
            # 归一到与 derive_voice_bindings 相同的坐标系（NFC）：该函数对说话人名归一后
            # 产出音色绑定声明 `<X>的台词音色参考 @音频N`，这里的台词句式若仍用未归一的
            # 原始字节形式，两处的 `<X>` 会字节不同，供应商侧无法把参考音色与这句台词对上。
            speaker = normalize_asset_name(speaker) if speaker else speaker
            line = _text(entry.get("line"))
            if line:
                # 台词句式与 narration/drama 参考路径的第二段统一（<X>说 {台词}），无 speaker
                # 的裸台词行归入画外音句式，见
                # lib.reference_video.prompt_render.render_ad_backend_prompt。
                parts.append(f"<{speaker}>说 {{{line}}}" if speaker else f"画外音说 {{{line}}}")
    return "；".join(parts)


def render_ad_unit_prompt(shots: list[dict], *, style: str | None = None) -> str:
    """把 unit 的成员镜头渲染为多镜头视频生成 prompt。

    每个镜头一行 ``Shot {n} ({duration}s): {画面描述}``，显式传达切镜节奏与单镜头
    时长——ad 骨架的时长仍挂在 shot 上（unit 是派生分组，时长取成员求和），与
    narration/drama 参考路径的 unit 级单时长不同源；项目风格以 ``Style:`` 头行注入。
    所有镜头都无画面内容时返回空串，让入队守卫
    （``TaskSpec.from_request``）当场拒绝，而非把纯结构头发给供应商。
    """
    from lib.prompt_utils import normalize_style

    lines: list[str] = []
    for n, shot in enumerate(shots, start=1):
        text = _shot_prompt_text(shot)
        if not text:
            continue
        lines.append(f"Shot {n} ({ad_shot_duration_seconds(shot)}s): {text}")
    if not lines:
        return ""
    if style and (normalized := normalize_style(style)):
        lines.insert(0, f"Style: {normalized}")
    return "\n".join(lines)
