"""图像 / 视频 / 资产 prompt 的统一真相源。

WebUI（server/services/generation_tasks.py）和 Skill（agent_runtime_profile/.claude/skills/generate-assets）
都从这里取最终 prompt 文本，确保入口一致、不漂移。

设计要点：
- 无 backend 锁定：纯文本拼接，由调用方决定走哪个 image/video provider。
- 反向提示词统一以「画面避免：xxx」追加到 prompt 末尾，不再使用各 backend 的 negative_prompt 参数通道
  （image backends 大多 silent 丢弃，参数化反而增加分叉）。
- 防崩与反向短语精简：只保关键项，避免 CFG 权重稀释。
- 反向提示词按图种各自定义，内容相同也不合并：合并后若要单独调整其中一类仍需先拆分常量，
  且合并的常量无法表达各图种之间是必须一致还是恰好相同。
"""

from __future__ import annotations

from collections.abc import Sequence

# ---------------------------------------------------------------------------
# 内部常量：防崩 / 反向 / 布局 / 风格前缀
# ---------------------------------------------------------------------------

# 角色图采用 issue #353 的四视图 16:9 布局。
_CHARACTER_LAYOUT = (
    "横版 16:9 四格布局，纯白 (#FFFFFF) 背景：左侧约 40% 宽为胸像特写（清晰展示面部、发型、配饰、上装），"
    "右侧三个等宽面板分别为正面 / 四分之三侧面 / 背面的 A-Pose 全身视图。"
)
_SCENE_LAYOUT = "主画面占四分之三区域展示环境整体外观与氛围，右下角嵌入关键细节小图。"
_PROP_LAYOUT = "三视图水平排列于纯净浅灰背景：左侧正面全视图、中间 45° 侧视图体现立体感、右侧关键细节特写。"
_PRODUCT_LAYOUT = (
    "标准多角度产品参考图，纯净浅灰背景、均匀棚拍布光：正面、45° 侧面、背面三视图水平排列，"
    "下方一排关键细节特写（logo、文字、材质、接缝）。"
)

# 正向防崩（按资产类型差异化）。
_CHARACTER_GUARD = "四个面板中角色面部、发型、服装、配饰完全一致；五官对称、手指完整为五指、肢体比例协调。"
# 场景 description 由剧本提取，常包含人物动作与剧情事件，仅靠末尾的反向提示词不足以抵消
# 描述中的正向叙述，因此在正向语句中再声明一次无人。道具是纯文生图、description 描述的是
# 物件本身，layout 也已限定纯净背景多视图，不存在同类冲突，只需反向提示词；产品另有实拍
# 参考图这条通道，其正向声明见 _PRODUCT_GUARD。
_SCENE_GUARD = "画面中没有人物出镜，空间透视正常，陈设固定，光影统一。"
_PROP_GUARD = "外观结构完整，焦点清晰。"
# 产品保真核心句：sheet 生成守卫与镜头注入指令共用，调优措辞只改这一处。
_PRODUCT_FIDELITY_CORE = "logo、文字、配色、材质、比例与结构不得改变或臆造"
# product sheet 由实拍原图整理而来，原图全量作为 i2i 参考注入（generation_tasks.py 的
# _DESIGN_REFERENCE_COLLECTORS），手持与模特展示是电商原图的常见形态。参考图里的真人是强
# 正向视觉条件，末尾的反向提示词压不住，因此在守卫句中正面声明只呈现产品本体。这句只作用于
# sheet 生成；产品出现在镜头里时本就可以被人拿着，不能走 _PRODUCT_FIDELITY_CORE 共用。
_PRODUCT_GUARD = (
    f"产品外观必须忠实于参考图中的真实产品：{_PRODUCT_FIDELITY_CORE}；各视图为同一件产品。"
    "参考图中的手部、模特及其他出镜人物一律不保留，画面只呈现产品本体；"
    "包装上印刷的人像图案属于产品外观，须原样保留。"
)

# 反向提示词：只列实体排除项，不写质量词（质量词对现代生成模型近于噪声，且稀释 CFG 权重）。
# 人物排除项仅用于展示环境或物件的图种；角色图与分镜图的画面主体本身就是人物，加入该排除项
# 会损害生成结果。写「出镜人物」而非「人物」，是为了把排除范围限定在进入画面的人：画像、造像、
# 人偶这类道具，以及包装上印有人物图案的产品，其人像属于物件本体，与 _PROP_GUARD /
# _PRODUCT_GUARD 要求的外观忠实一致，排除项不应波及。
_NEGATIVE_TAIL_CHARACTER = "画面避免：水印、多余文字、Logo。"
_NEGATIVE_TAIL_SCENE = "画面避免：出镜人物、水印、多余文字、Logo。"
_NEGATIVE_TAIL_PROP = "画面避免：出镜人物、水印、多余文字、Logo。"
_NEGATIVE_TAIL_PRODUCT = "画面避免：出镜人物、水印、多余文字、Logo。"
_NEGATIVE_TAIL_STORYBOARD = "画面避免：水印、多余文字、Logo。"
_NEGATIVE_TAIL_VIDEO = "禁止出现：BGM、文字字幕、水印。"

DEFAULT_CHARACTER_ASSET_PROMPT = "\n\n".join((_CHARACTER_LAYOUT, _CHARACTER_GUARD, _NEGATIVE_TAIL_CHARACTER))
DEFAULT_SCENE_ASSET_PROMPT = "\n\n".join((_SCENE_LAYOUT, _SCENE_GUARD, _NEGATIVE_TAIL_SCENE))
DEFAULT_PROP_ASSET_PROMPT = "\n\n".join((_PROP_LAYOUT, _PROP_GUARD, _NEGATIVE_TAIL_PROP))


def _style_prefix(style: str = "", style_description: str = "") -> str:
    """组合视觉风格前缀。两者都为空时返回空串。"""
    parts = []
    if style:
        parts.append(f"风格：{style}")
    if style_description:
        parts.append(f"描述：{style_description}")
    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"


def _asset_prompt_instructions(configured: str | None, default: str) -> str:
    """Use a non-empty global override, otherwise retain the built-in prompt."""

    override = (configured or "").strip()
    return override or default


# ---------------------------------------------------------------------------
# 资产 prompt（character / scene / prop）
# ---------------------------------------------------------------------------


def build_character_prompt(
    name: str,
    description: str,
    style: str = "",
    style_description: str = "",
    system_prompt: str | None = None,
) -> str:
    """角色设计图 prompt（issue #353 四视图 16:9）。"""
    style_block = _style_prefix(style, style_description)
    instructions = _asset_prompt_instructions(system_prompt, DEFAULT_CHARACTER_ASSET_PROMPT)
    return f"{style_block}角色「{name}」的设计参考图。\n\n{description}\n\n{instructions}"


def build_scene_prompt(
    name: str,
    description: str,
    style: str = "",
    style_description: str = "",
    system_prompt: str | None = None,
) -> str:
    """场景设计图 prompt（主+细节）。"""
    style_block = _style_prefix(style, style_description)
    instructions = _asset_prompt_instructions(system_prompt, DEFAULT_SCENE_ASSET_PROMPT)
    return f"{style_block}标志性场景「{name}」的视觉参考。\n\n{description}\n\n{instructions}"


def build_prop_prompt(
    name: str,
    description: str,
    style: str = "",
    style_description: str = "",
    system_prompt: str | None = None,
) -> str:
    """道具设计图 prompt（三视图）。"""
    style_block = _style_prefix(style, style_description)
    instructions = _asset_prompt_instructions(system_prompt, DEFAULT_PROP_ASSET_PROMPT)
    return f"{style_block}道具「{name}」的多视角展示。\n\n{description}\n\n{instructions}"


def build_product_prompt(
    name: str,
    description: str,
    style: str = "",
    style_description: str = "",
    system_prompt: str | None = None,
) -> str:
    """产品标准参考图（product sheet）prompt（多角度 + 保真守卫）。

    产品 sheet 的使命是把用户随手拍的原图整理成标准多角度设计图，产品形象必须
    忠实于真品（原图作为参考注入），不沿用项目画风前缀——画风统一由项目级 style
    机制在分镜阶段承载，产品参考图保持写实中性。
    """
    del style, style_description, system_prompt  # 与其它 design prompt builder 签名对齐；产品 sheet 不注入画风
    return (
        f"产品「{name}」的标准参考图。\n\n"
        f"{description}\n\n"
        f"{_PRODUCT_LAYOUT}\n\n"
        f"{_PRODUCT_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_PRODUCT}"
    )


# ---------------------------------------------------------------------------
# 分镜 / 视频 prompt 末尾增强
# ---------------------------------------------------------------------------


def append_product_fidelity_tail(prompt: str, product_names: Sequence[str] | None) -> str:
    """给产品镜头的生成 prompt 追加高保真还原指令。

    仅在产品参考图实际注入请求时调用（分镜图与视频两层共用同一份指令文本）——
    指令指向"产品参考图"，参考缺席时追加只会误导模型。``product_names`` 为空
    （含 None 脏数据）返回原 prompt；重复调用幂等。误传单个字符串按单产品名处理
    （str 本身满足 Sequence[str]，按字符迭代会拼出逐字括注的畸形指令）。
    """
    if not product_names:
        return prompt
    if isinstance(product_names, str):
        product_names = (product_names,)
    names = "".join(f"「{name}」" for name in product_names if name)
    if not names:
        return prompt
    tail = (
        f"产品高保真还原（最高优先级）：画面中的产品{names}必须与产品参考图完全一致——"
        f"{_PRODUCT_FIDELITY_CORE}，不得重新设计或美化产品本身；"
        "项目画风只作用于产品以外的画面元素。"
    )
    if not prompt or not prompt.strip():
        return tail
    if tail in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{tail}"


def append_image_negative_tail(prompt: str) -> str:
    """给分镜图生成 prompt 追加统一的图像反向提示词。

    资产图在各 build_*_prompt 内已拼接各自的反向提示词；分镜图 prompt 由 LLM 产出、
    经归一化后交 image backend，在归一化出口过一遍此函数，保持各图像路径一致。
    """
    if not prompt or not prompt.strip():
        return _NEGATIVE_TAIL_STORYBOARD
    if _NEGATIVE_TAIL_STORYBOARD in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{_NEGATIVE_TAIL_STORYBOARD}"


def append_video_negative_tail(prompt: str) -> str:
    """给视频生成 prompt 追加统一的反向提示词。

    调用方拿到分镜 video_prompt 文本后，在交给 video backend 之前过一遍此函数；
    避免在每个 caller 各自拼接、导致漂移。
    """
    if not prompt or not prompt.strip():
        return _NEGATIVE_TAIL_VIDEO
    if _NEGATIVE_TAIL_VIDEO in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{_NEGATIVE_TAIL_VIDEO}"
