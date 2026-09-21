"""广告/短片（content_mode=ad）剧本生成 Prompt 构建器。

分镜图生视频产出平铺 ``shots[]``，参考生视频单阶段产出自包含 ``video_units[]`` 的扁平
引用语法正文。两种生成模式都按目标总时长选择带货八段框架的时长配比档位（15/30/60/90 秒，
经维护者审定的配比表），非四档整数取距离最小的档位按比例适配。
``products`` 为空时自动分流为通用短片 prompt（无带货框架）。

两条路线经内置整段模版渲染；本模块只产出槽位值：档位取整、单分镜时长约束、口播语速、
商品信息块与候选名单。通用规则与四档配比表是 ``shared/ad_pacing`` 片段，按档位解析命名变体。

已有正式剧本时，待编写的分镜 / 单元由提示词编写补写：输入是整份剧本（待编写条目带标记，
其余条目作前后文），只产出待编写条目的视觉层或正文。
"""

from collections.abc import Collection, Mapping

from lib.infra.schema_guards import is_int
from lib.infra.text_metrics import reading_unit_noun
from lib.prompts.prompt_builders_script import (
    _format_aspect_ratio_desc,
    _neutralize_tags,
    _overview_slot,
    format_duration_constraint,
)
from lib.prompts.prompt_rules.asset_appearance import asset_reference_names
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.script.script_models import REFERENCE_UNIT_DURATION_RANGE
from lib.speech.speech_rate import speech_rate_units_per_second

# ---------------------------------------------------------------------------
# 审定档位
# ---------------------------------------------------------------------------

#: 四个审定档位（秒）。非四档整数取距离最小的档位按比例适配；
#: 等距时取更接近默认推荐档（30 秒）的一侧。
AD_DURATION_TIERS: tuple[int, ...] = (15, 30, 60, 90)

#: 默认推荐档（秒），档位等距 tie-break 的锚点。
AD_DEFAULT_TIER = 30


def nearest_ad_tier(target_duration: int) -> int:
    """取距离最小的审定档位；等距时取更接近默认推荐档（30 秒）的一侧。"""
    return min(AD_DURATION_TIERS, key=lambda t: (abs(t - target_duration), abs(t - AD_DEFAULT_TIER)))


# ---------------------------------------------------------------------------
# 上下文块渲染
# ---------------------------------------------------------------------------


def _format_products(products: dict) -> str:
    """渲染商品信息块：名称 / 品牌 / 描述 / 卖点（selling_points）。"""
    lines: list[str] = []
    for name, data in products.items():
        data = data if isinstance(data, dict) else {}
        lines.append(f"### {name}")
        brand = data.get("brand")
        if brand:
            lines.append(f"品牌：{brand}")
        desc = data.get("description")
        if desc:
            lines.append(f"描述：{desc}")
        # 只接受字符串列表：绕过白名单写入的脏值（如整串字符串）会被逐字符迭代成碎片卖点
        raw_points = data.get("selling_points")
        points = [p for p in raw_points if isinstance(p, str) and p.strip()] if isinstance(raw_points, list) else []
        if points:
            lines.append("卖点：")
            lines.extend(f"- {point}" for point in points)
        lines.append("")
    return "\n".join(lines).strip()


def _shot_duration_constraint(generation_mode: str | None, supported_durations: list[int] | None) -> str:
    """渲染分镜图生视频的单分镜时长约束；参考生视频须走自包含 unit 构建器。"""
    if generation_mode == "reference_video":
        raise ValueError("reference_video 路径须使用 build_ad_reference_prompt")
    if not supported_durations:
        raise ValueError("storyboard 路径必须提供 supported_durations（视频模型的合法时长集合）")
    return format_duration_constraint(supported_durations, None)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _pacing_slots(target_duration: int) -> dict[str, object]:
    """配比段的档位轴值与适配说明所需的目标秒数；目标恰为档位时无需适配说明。"""
    if not is_int(target_duration, minimum=1):
        raise ValueError(f"target_duration 必须为正整数秒，当前为 {target_duration!r}")
    tier = nearest_ad_tier(target_duration)
    return {
        "target_duration": target_duration,
        "ad_duration_tier": str(tier),
        "off_tier_target_duration": None if tier == target_duration else target_duration,
    }


def build_ad_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    target_duration: int,
    generation_mode: str | None,
    supported_durations: list[int] | None,
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    speech_rate_override: float | None = None,
    instructions: str | None = None,
) -> str:
    """构建广告/短片剧本生成 prompt。

    ``products`` 非空走带货八段框架 + 审定配比表；为空自动分流通用短片 prompt
    （无带货框架，不设显式子模式开关）。``speech_rate_override`` 是项目级语速覆盖
    （由调用方经 ``project_speech_rate_override`` 解析），None 即回退语言默认。
    """
    pacing = _pacing_slots(target_duration)
    duration_constraint = _shot_duration_constraint(generation_mode, supported_durations)
    # 口播字数→时长折算从 lib.speech.speech_rate 单一真相源取（与 drama script_plan 下界、字幕派生同口径）：
    # 项目级覆盖优先，否则按语言默认。语速表按语言代码（zh / en / vi）登记；target_language 是
    # 自由文本（默认「中文」），未登记值回退默认语速（zh 口径），量词（字 / 词）由
    # reading_unit_noun 同源派生。
    speech_rate = speech_rate_units_per_second(target_language, speech_rate_override)
    return builtin_templates.render(
        "text/ad_storyboard_script",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        products=_format_products(products) if products else None,
        product_names=list(products),
        duration_constraint=duration_constraint,
        speech_rate=f"{speech_rate:g}",
        unit_noun=reading_unit_noun(target_language),
        episode=episode,
        instructions=instructions or None,
        **pacing,
    )


def build_ad_reference_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    target_duration: int,
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """广告/短片的参考生视频单阶段生成 prompt；直接输出含引用语法正文的扁平 unit。"""
    pacing = _pacing_slots(target_duration)
    min_unit_duration, max_unit_duration = REFERENCE_UNIT_DURATION_RANGE
    return builtin_templates.render(
        "text/ad_reference_video_script",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        products=_format_products(products) if products else None,
        product_names=list(products),
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        min_unit_duration=min_unit_duration,
        max_unit_duration=max_unit_duration,
        episode=episode,
        instructions=instructions or None,
        **pacing,
    )


def _names(value: object) -> str:
    names = [name for name in value if isinstance(name, str)] if isinstance(value, list) else []
    return _neutralize_tags("、".join(names) or "无")


def _prompt_summary(value: object, field: str) -> str:
    """已有提示词的一句摘要：结构化取 ``field``，文本形态取原文；供前后文参照，不求完整。"""
    if isinstance(value, Mapping):
        return str(value.get(field) or "")
    return value if isinstance(value, str) else ""


_PENDING_MARK = "【待编写】"


def render_ad_shots_for_prompt_authoring(shots: list[dict], target_ids: Collection[str]) -> str:
    """按播放顺序渲染整份广告分镜：待编写分镜带标记，其余分镜附已有画面与动作摘要作前后文。"""
    blocks: list[str] = []
    for shot in shots:
        shot_id = str(shot.get("shot_id") or "?")
        pending = shot_id in target_ids
        header = f"### {_neutralize_tags(shot_id)}{_PENDING_MARK if pending else ''}（时长 {shot.get('duration_seconds', '?')} 秒）"
        lines = [
            header,
            f"段落：{_neutralize_tags(str(shot.get('section') or '无'))}",
            f"出场：角色 [{_names(shot.get('characters_in_shot'))}]、场景 [{_names(shot.get('scenes'))}]、"
            f"道具 [{_names(shot.get('props'))}]、商品 [{_names(shot.get('products_in_shot'))}]",
        ]
        voiceover = str(shot.get("voiceover_text") or "").strip()
        lines.append(f"口播：{_neutralize_tags(voiceover) if voiceover else '（无）'}")
        if not pending:
            scene = _prompt_summary(shot.get("image_prompt"), "scene").strip()
            action = _prompt_summary(shot.get("video_prompt"), "action").strip()
            if scene:
                lines.append(f"已有画面：{_neutralize_tags(scene)}")
            if action:
                lines.append(f"已有动作：{_neutralize_tags(action)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "（无分镜）"


def render_ad_units_for_prompt_authoring(units: list[dict], target_ids: Collection[str]) -> str:
    """按播放顺序渲染整份广告视频单元：待编写单元带标记，正文可能为空（手动新增）。"""
    blocks: list[str] = []
    for index, unit in enumerate(units, start=1):
        pending = str(unit.get("unit_id") or "") in target_ids
        body = str(unit.get("text") or "").strip()
        header = (
            f"#### unit {index}{_PENDING_MARK if pending else ''}（时长 {int(unit.get('duration_seconds') or 0)}s）"
        )
        blocks.append(f"{header}\n{body or '（正文为空）'}")
    return "\n\n".join(blocks) or "（无单元）"


def build_ad_shot_prompt_authoring_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    shots: list[dict],
    target_ids: Collection[str],
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """广告分镜的提示词编写 prompt：只为 ``target_ids`` 里的分镜产出 image_prompt / video_prompt。"""
    return builtin_templates.render(
        "text/ad_storyboard_prompt_authoring",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        products=_format_products(products) if products else None,
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        shots_content=render_ad_shots_for_prompt_authoring(shots, target_ids),
        episode=episode,
        instructions=instructions or None,
    )


def build_ad_reference_prompt_authoring_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    products: dict,
    brief: str,
    units: list[dict],
    target_ids: Collection[str],
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
    instructions: str | None = None,
) -> str:
    """广告参考生视频的提示词编写 prompt：只为 ``target_ids`` 里的单元按播放顺序产出正文。"""
    return builtin_templates.render(
        "text/ad_reference_video_prompt_authoring",
        target_language=target_language,
        project_overview=_overview_slot(project_overview),
        style=style,
        style_description=style_description,
        aspect_ratio=aspect_ratio,
        aspect_ratio_label=_format_aspect_ratio_desc(aspect_ratio),
        brief=brief or None,
        products=_format_products(products) if products else None,
        product_names=list(products),
        character_names=asset_reference_names("character", characters),
        scene_names=asset_reference_names("scene", scenes),
        prop_names=asset_reference_names("prop", props),
        units_content=render_ad_units_for_prompt_authoring(units, target_ids),
        pending_count=sum(1 for unit in units if str(unit.get("unit_id") or "") in target_ids),
        episode=episode,
        instructions=instructions or None,
    )


__all__ = [
    "build_ad_prompt",
    "build_ad_reference_prompt",
    "build_ad_reference_prompt_authoring_prompt",
    "build_ad_shot_prompt_authoring_prompt",
    "nearest_ad_tier",
]
