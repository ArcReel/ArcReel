"""ad 模式参考直出的派生分组器：平铺 shots[] → video_unit 轻量索引。

ad 剧本骨架唯一（shots 是内容唯一真相，见 docs/adr/0033）；reference_video
路径不更换骨架，而是把镜头**派生分组**为 video_unit——连续镜头、每 unit
不超过 4 个 shot、unit 总时长受供应商时长上限约束、继承镜头参考集。分组
结果以轻量索引（unit → shot_ids + 参考集）持久于剧本 JSON，仅引用 shot_id
不复制镜头内容；分组为纯函数，同样的 shots 与约束必然产出同样的分组，
重生成单个 unit 时分组可复现。
"""

from __future__ import annotations

from lib.script_models import GeneratedAssets, ad_shot_duration_seconds

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


def _unit_references(shots: list[dict]) -> list[dict]:
    """unit 参考集：成员镜头参考的并集，产品在前，类型内按首次出现顺序去重。"""
    references: list[dict] = []
    for field, ref_type in _REFERENCE_FIELDS:
        seen: set[str] = set()
        for shot in shots:
            names = shot.get(field)
            if not isinstance(names, list):
                continue
            for name in names:
                if not isinstance(name, str) or not name or name in seen:
                    continue
                seen.add(name)
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

    每个 unit 继承成员镜头的参考集（产品全量且绝对优先，见 ``_unit_references``）。

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
            "references": _unit_references(group),
        }
        for n, group in enumerate(groups, start=1)
    ]


def merge_ad_reference_units(existing: object, derived: list[dict]) -> list[dict]:
    """把新派生的分组与剧本中已持久化的索引合并（纯函数，不改入参）。

    unit 的身份是「位置 + 成员 + 参考集」：``unit_id``、``shot_ids``、``references``
    全部一致时沿用旧条目的 ``generated_assets``（产物文件按 unit_id 命名，三者
    任一变化都意味着旧产物指针不再可信，重置为全新待生成状态）。
    """
    existing_by_id: dict[str, dict] = {}
    if isinstance(existing, list):
        for entry in existing:
            if isinstance(entry, dict) and isinstance(entry.get("unit_id"), str):
                existing_by_id[entry["unit_id"]] = entry

    merged: list[dict] = []
    for unit in derived:
        prev = existing_by_id.get(unit["unit_id"])
        assets = None
        if (
            isinstance(prev, dict)
            and prev.get("shot_ids") == unit["shot_ids"]
            and prev.get("references") == unit["references"]
            and isinstance(prev.get("generated_assets"), dict)
        ):
            assets = dict(prev["generated_assets"])
        merged.append({**unit, "generated_assets": assets or GeneratedAssets().model_dump()})
    return merged


def sync_ad_reference_units(
    script: dict,
    *,
    episode: int,
    max_unit_duration: int | None = None,
) -> list[dict]:
    """从 shots 重新派生分组并写回 ``script["reference_units"]``，返回合并后的索引。

    shots 是内容唯一真相：索引始终由本函数从 shots 重算，成员与参考集未变的
    unit 保留既有 ``generated_assets``（见 ``merge_ad_reference_units``）。
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
            line = _text(entry.get("line"))
            if line:
                parts.append(f"台词 {speaker}：「{line}」" if speaker else f"台词：「{line}」")
    return "；".join(parts)


def render_reference_legend(labels: list[str]) -> str:
    """把最终注入的参考图标签渲染成 ``[图N]`` 对照表（N 与 backend 实收顺序严格对齐）。

    ad 派生 unit 的 prompt 不写 @mention，参考与画面的绑定靠此对照表传达；
    必须在参考裁剪**之后**调用，否则 [图N] 会指向不存在的图。
    """
    if not labels:
        return ""
    lines = ["参考图对照："]
    lines.extend(f"[图{n}] {label}" for n, label in enumerate(labels, start=1))
    return "\n".join(lines)


def render_ad_unit_prompt(shots: list[dict], *, style: str | None = None) -> str:
    """把 unit 的成员镜头渲染为多镜头视频生成 prompt。

    每个镜头一行 ``Shot {n} ({duration}s): {画面描述}``，显式传达切镜节奏与
    单镜头时长（与 narration/drama 参考路径 step1 的书写格式同形）；项目风格
    以 ``Style:`` 头行注入。所有镜头都无画面内容时返回空串，让入队守卫
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
