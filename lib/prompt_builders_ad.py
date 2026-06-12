"""广告/短片模式（content_mode=ad）剧本生成 Prompt 构建器。

产出平铺 ``shots[]`` 的带货镜头脚本 prompt：按目标总时长选择带货八段框架的
时长配比档位（15/30/60/90 秒，经维护者审定的配比表，数字依据见
docs/research/arcreel-ad-section-timing-research.md），非四档整数取最近档位
按比例适配。``products`` 为空时自动分流为通用短片 prompt（无带货框架）。

设计原则与 narration/drama 构建器一致：
- 不重复 schema 已声明的枚举；让 response_schema 直接约束。
- 配比表数字是审定真相源，本模块只照表搬运，不计算、不改写。
- 字段说明给写作指引而非"必须/禁止"清单。
"""

from lib.prompt_builders_script import (
    _ACTION_WRITING_GUIDE,
    _AMBIANCE_AUDIO_WRITING_GUIDE,
    _AMBIANCE_WRITING_GUIDE,
    _LIGHTING_WRITING_GUIDE,
    _SCENE_WRITING_GUIDE,
    _format_aspect_ratio_desc,
    _format_duration_constraint,
    _format_names,
)
from lib.script_models import REFERENCE_SHOT_DURATION_RANGE

# ---------------------------------------------------------------------------
# 审定配比表（数字真相源，逐字照搬，不得修改）
# ---------------------------------------------------------------------------

#: 四个审定档位（秒）。非四档整数取最近档位按比例适配；
#: 等距时取更接近默认推荐档（30 秒）的一侧。
AD_DURATION_TIERS: tuple[int, ...] = (15, 30, 60, 90)

#: 默认推荐档（秒），档位等距 tie-break 的锚点。
AD_DEFAULT_TIER = 30

#: section 八值引导（不硬枚举，留给 prompt 约束；与审定配比表用词一致）。
AD_SECTION_VALUES: tuple[str, ...] = (
    "hook",
    "pain_point",
    "product_reveal",
    "selling_point",
    "demo",
    "trust",
    "price_promo",
    "cta",
)

_AD_GENERAL_RULES = """\
通用规则（适用于全部档位）：

- hook 与 cta 是绝对时长段（hook 2-4s、cta 3-6s），不随档位等比放大；加长的秒数优先给 selling_point/demo，其次 trust
- price_promo 永远紧贴 cta 构成「促单收尾块」
- 即使 hook 不是产品画面，产品也应在前 3 秒内入画（文字/局部/手持均可）
- 单 section 超过 6 秒必须拆成多个镜头；全片平均 3-5 秒/镜，开头允许 2-3 秒快切；镜头数宁多勿少（多场景多角度有平台官方数据背书）
- 30 秒档为默认推荐档；90 秒档用「小故事」组织而非平铺卖点，仅适合高客单/需教育的产品
- 中文口播按约 4 字/秒折算台词长度（15s≈60 字 / 30s≈120 字 / 60s≈240 字 / 90s≈360 字；此换算为推断值，后续按 TTS 实测校定）"""

_AD_TIER_TABLES: dict[int, str] = {
    15: """\
15 秒档（冲动型/投流款，5-6 镜头；只保五段：pain_point 并入 hook、trust 砍掉、price_promo 并入 cta）：

| section | 秒数 | 累计 | 镜头数 |
|---|---|---|---|
| hook（兼任 pain_point） | 3 | 0-3 | 1 |
| product_reveal | 2 | 3-5 | 1 |
| selling_point（1 个核心卖点） | 3 | 5-8 | 1 |
| demo | 4 | 8-12 | 1-2 |
| cta（可带一句促销词兼任 price_promo） | 3 | 12-15 | 1 |""",
    30: """\
30 秒档（标准带货位，默认推荐，8-10 镜头；八段全保，trust 与 price_promo 压成一句话镜头；产品极低客单且无可信背书时首砍 trust、秒数还给 demo）：

| section | 秒数 | 累计 | 镜头数 |
|---|---|---|---|
| hook | 3 | 0-3 | 1 |
| pain_point | 4 | 3-7 | 1-2 |
| product_reveal | 3 | 7-10 | 1 |
| selling_point（1-2 个） | 6 | 10-16 | 2 |
| demo | 6 | 16-22 | 2 |
| trust（一句话社证） | 3 | 22-25 | 1 |
| price_promo | 2 | 25-27 | 1 |
| cta | 3 | 27-30 | 1 |""",
    60: """\
60 秒档（完整说服链，13-16 镜头；八段全保各自成块，增量给 selling_point+demo）：

| section | 秒数 | 累计 | 镜头数 |
|---|---|---|---|
| hook | 3 | 0-3 | 1 |
| pain_point（1-2 个具体情境） | 7 | 3-10 | 2 |
| product_reveal | 5 | 10-15 | 1-2 |
| selling_point（2-3 个，每个 4-6s 一镜） | 12 | 15-27 | 3 |
| demo（多角度/前后对比） | 15 | 27-42 | 3-4 |
| trust（评价+销量/资质） | 8 | 42-50 | 2 |
| price_promo（原价锚定→到手价→限时） | 5 | 50-55 | 1-2 |
| cta（行动指令+重申核心利益） | 5 | 55-60 | 1 |""",
    90: """\
90 秒档（叙事型/高客单，18-22 镜头；八段全保，增量给 demo/selling_point/trust/pain_point）：

| section | 秒数 | 累计 | 镜头数 |
|---|---|---|---|
| hook（可用悬念/故事钩） | 4 | 0-4 | 1 |
| pain_point（人物+冲突小叙事） | 10 | 4-14 | 2-3 |
| product_reveal（转折点：遇见产品） | 6 | 14-20 | 1-2 |
| selling_point（3 个，逐个展开） | 20 | 20-40 | 3-4 |
| demo（多场景使用过程，核心块） | 24 | 40-64 | 4-6 |
| trust（证言/检测报告/销量） | 12 | 64-76 | 2-3 |
| price_promo（价格锚定+优惠拆解） | 8 | 76-84 | 2 |
| cta（紧迫感收口） | 6 | 84-90 | 1 |""",
}


def nearest_ad_tier(target_duration: int) -> int:
    """取最近的审定档位；等距时取更接近默认推荐档（30 秒）的一侧。"""
    return min(AD_DURATION_TIERS, key=lambda t: (abs(t - target_duration), abs(t - AD_DEFAULT_TIER)))


def _format_pacing_block(target_duration: int) -> str:
    """渲染配比段：通用规则 + 命中档位的审定表；非四档整数附按比例适配说明。"""
    tier = nearest_ad_tier(target_duration)
    parts = [_AD_GENERAL_RULES, _AD_TIER_TABLES[tier]]
    if tier != target_duration:
        parts.append(
            f"目标总时长 {target_duration} 秒不在审定档位内，按最近档位 {tier} 秒的配比模板"
            f"按比例适配到 {target_duration} 秒：hook 与 cta 是绝对时长段维持原秒数，"
            "伸缩量按通用规则优先给 selling_point/demo，其次 trust。"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 上下文块渲染
# ---------------------------------------------------------------------------


def _format_products(products: dict) -> str:
    """渲染产品信息块：名称 / 品牌 / 描述 / 卖点（selling_points）。"""
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


def _shot_duration_constraint(generation_mode: str, supported_durations: list[int] | None) -> str:
    """按 generation_mode 渲染单镜头时长约束文本。

    storyboard 路径：供应商 supported_durations 硬枚举（与 response_schema 的
    enum 约束同口径）；reference_video 路径：1-15 秒自由整数（短切节奏赖此成立），
    区间真相源与 reference 路径剧本模型同在 ``lib.script_models``。
    """
    if generation_mode == "reference_video":
        low, high = REFERENCE_SHOT_DURATION_RANGE
        return _format_duration_constraint(list(range(low, high + 1)), None)
    if not supported_durations:
        raise ValueError("storyboard 路径必须提供 supported_durations（视频模型的合法时长集合）")
    return _format_duration_constraint(supported_durations, None)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


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
    generation_mode: str,
    supported_durations: list[int] | None,
    episode: int = 1,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建广告/短片模式的剧本生成 prompt。

    ``products`` 非空走带货八段框架 + 审定配比表；为空自动分流通用短片 prompt
    （无带货框架，不设显式子模式开关）。
    """
    if not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
        raise ValueError(f"target_duration 必须为正整数秒，当前为 {target_duration!r}")

    duration_constraint = _shot_duration_constraint(generation_mode, supported_durations)
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    product_names = list(products.keys())

    common_header = f"""**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}（{_format_aspect_ratio_desc(aspect_ratio)}）
</style>

<brief>
{brief or "（未提供，按产品信息与常识自行设计）"}
</brief>

<characters>
{_format_names(characters)}
</characters>

<scenes>
{_format_names(scenes)}
</scenes>

<props>
{_format_names(props)}
</props>"""

    common_constraints = f"""<episode_constraints>
本片为单视频（恒第 {episode} 集）。所有 shot_id 必须严格使用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01、E{episode}S02），按播放顺序连续编号，不得使用其他集号前缀。
</episode_constraints>"""

    common_field_guides = f"""## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个镜头只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：仅当镜头内有出镜人物开口说话时填写（口播旁白写在 voiceover_text，不要重复进 dialogue）；speaker 必须出现在 characters_in_shot。"""

    if not products:
        return f"""# 角色与任务

你是一位资深的短视频编导，精通把一段创作诉求转写为可直接驱动 AI 图像 / 视频生成的结构化镜头脚本。
你的任务：基于下方创作 brief，产出一支约 {target_duration} 秒的通用短片镜头脚本（平铺 shots[]），符合 schema 的 JSON。

{common_header}

# 时长与节奏

- 全片目标总时长 {target_duration} 秒，各镜头 duration_seconds 之和应贴近该值。
- 单镜头{duration_constraint}；全片平均 3-5 秒/镜，按内容节奏自行切分。

{common_constraints}

# 字段写作指引

## 基础字段

- **section**：本片无带货框架，按内容自拟简短英文段落标签（如 opening/development/climax/ending），用于标记镜头在叙事中的位置。
- **voiceover_text**：每镜头的口播文案，必须完整可照稿配音；无口播的纯画面镜头填空字符串。中文口播按约 4 字/秒折算台词长度。
- **characters_in_shot** / **scenes** / **props**：仅列出此镜头画面中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。
- **products_in_shot**：本项目无产品资产，所有镜头一律填空数组。

{common_field_guides}

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的短片脚本。忠于创作 brief、保留情绪张力。
"""

    return f"""# 角色与任务

你是一位资深的带货短视频编导，精通把产品卖点与创作诉求转写为可直接驱动 AI 图像 / 视频生成的结构化镜头脚本。
你的任务：基于下方产品信息与创作 brief，按带货八段框架与时长配比，产出一支约 {target_duration} 秒的带货短视频镜头脚本（平铺 shots[]），符合 schema 的 JSON。

{common_header}

<products>
{_format_products(products)}
</products>

# 带货八段框架与时长配比

带货短视频按八个段落组织：{" → ".join(AD_SECTION_VALUES)}。
下方配比表经审定，是各段时长与镜头数的执行标准：

{_format_pacing_block(target_duration)}

{common_constraints}

# 字段写作指引

对每个镜头，按下列章节填写字段。

## 基础字段

- **section**：该镜头所属的带货框架段落标签，使用上方八值（如 hook、pain_point）；同段多镜头重复同一标签。
- **voiceover_text**：每镜头的口播文案，必须完整可照稿配音、与画面同步；无口播的纯画面镜头填空字符串。全片口播连起来应是一篇完整流畅的带货话术，长度按约 4 字/秒折算。
- **duration_seconds**：单镜头{duration_constraint}；各段合计遵循配比表，全片总和贴近 {target_duration} 秒。
- **products_in_shot**：该镜头画面中实际出现的产品名称列表（产品入画即列出，含局部/手持/包装）；氛围镜头填空数组。
  - 候选 products：[{", ".join(product_names)}]
  - 不要发明候选之外的名称。
- **characters_in_shot** / **scenes** / **props**：仅列出此镜头画面中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。

{common_field_guides}

# 创作目标

输出可直接驱动 AI 生成的、产品忠实、节奏紧凑的带货镜头脚本。卖点表达贴合 <products> 的 selling_points，不夸大、不虚构功效。
"""
