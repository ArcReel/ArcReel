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

from lib.prompt_utils import image_prompt_to_yaml, project_storyboard_image_prompt
from lib.schema_guards import is_str

# ---------------------------------------------------------------------------
# 内部常量：防崩 / 反向 / 布局 / 风格前缀
# ---------------------------------------------------------------------------

_CHARACTER_LAYOUT = "横版 16:9 三视图，纯白背景：正面 / 正侧（90° 侧视图）/ 背面水平排列。"
_SCENE_LAYOUT = "单张环境全景建立镜头。"
_PROP_LAYOUT = "单张道具资产图，纯净浅灰背景。"
_PRODUCT_LAYOUT = "单张商品资产图，纯净浅灰背景、均匀棚拍布光。"

# 正向防崩（按资产类型差异化）。
_CHARACTER_GUARD = "三个面板中角色面部、发型、服装、配饰完全一致。"
# 场景 description 由剧本提取，常包含人物动作与剧情事件，仅靠末尾的反向提示词不足以抵消
# 描述中的正向叙述，因此在正向语句中再声明一次无人。道具是纯文生图、description 描述的是
# 物件本身，layout 也已限定纯净背景，不存在同类冲突，只需反向提示词；商品另有实拍
# 参考图这条通道，其正向声明见 _PRODUCT_GUARD。
_SCENE_GUARD = "画面中没有人物出镜。"
# 衍生资产图是对本体资产图的图片编辑，守卫句限定「只改被描述到的部分」：版式与其余外观
# 保持不变，否则同一角色的两种形态会在分镜里长成两个人。
_CHARACTER_DERIVATIVE_GUARD = (
    "保持原图的三视图版式（正面 / 正侧 / 背面水平排列）、构图、比例、取景与纯白背景不变；"
    "除上述变化外，角色的面部、发型、体型及其余外观一律与原图保持一致。"
)
_PROP_GUARD = ""
# 商品保真核心句：sheet 生成守卫与分镜注入指令共用，调优措辞只改这一处。
_PRODUCT_FIDELITY_CORE = "logo、文字、配色、材质、比例与结构不得改变或臆造"
# product sheet 由实拍原图整理而来，原图全量作为 i2i 参考注入（generation_tasks.py 的
# _DESIGN_REFERENCE_COLLECTORS），手持与模特展示是电商原图的常见形态。参考图里的真人是强
# 正向视觉条件，末尾的反向提示词压不住，因此在守卫句中正面声明只呈现商品本体。这句只作用于
# sheet 生成；商品出现在分镜里时本就可以被人拿着，不能走 _PRODUCT_FIDELITY_CORE 共用。
_PRODUCT_GUARD = (
    f"商品外观必须忠实于参考图中的真实商品：{_PRODUCT_FIDELITY_CORE}。"
    "参考图中的手部、模特及其他出镜人物一律不保留，画面只呈现商品本体；"
    "包装上印刷的人像图案属于商品外观，须原样保留。"
)

# 反向提示词：只列实体排除项，不写质量词（质量词对现代生成模型近于噪声，且稀释 CFG 权重）。
# 人物排除项仅用于展示环境或物件的图种；角色图与分镜图的画面主体本身就是人物，加入该排除项
# 会损害生成结果。写「出镜人物」而非「人物」，是为了把排除范围限定在进入画面的人：画像、造像、
# 人偶这类道具，以及包装上印有人物图案的商品，其人像属于物件本体，与 _PROP_GUARD /
# _PRODUCT_GUARD 要求的外观忠实一致，排除项不应波及。
_NEGATIVE_TAIL_CHARACTER = "画面避免：水印、多余文字、Logo。"
_NEGATIVE_TAIL_SCENE = "画面避免：出镜人物、水印、多余文字、Logo。"
_NEGATIVE_TAIL_PROP = "画面避免：出镜人物、水印、多余文字、Logo。"
_NEGATIVE_TAIL_PRODUCT = "画面避免：出镜人物、水印、多余文字、Logo。"
_NEGATIVE_TAIL_STORYBOARD = "画面避免：水印、多余文字、Logo。"
_NEGATIVE_TAIL_VIDEO = "禁止出现：BGM、文字字幕、水印。"


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


# ---------------------------------------------------------------------------
# 资产 prompt（character / scene / prop）
# ---------------------------------------------------------------------------


def build_character_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """角色资产图 prompt（三视图 16:9）。"""
    style_block = _style_prefix(style, style_description)
    return (
        f"{style_block}"
        f"角色「{name}」的资产图。\n\n"
        f"{description}\n\n"
        f"{_CHARACTER_LAYOUT}\n\n"
        f"{_CHARACTER_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_CHARACTER}"
    )


def build_character_derivative_prompt(description: str) -> str:
    """角色衍生资产图 prompt：对本体资产图的一次编辑指令。

    衍生只写相对本体的外观变化，其余一切（三视图版式、构图、未被描述改动的外观）由
    守卫句钉住；不注入项目画风——画风已由被编辑的本体资产图自身承载。
    """
    return f"{description}\n\n{_CHARACTER_DERIVATIVE_GUARD}\n\n{_NEGATIVE_TAIL_CHARACTER}"


def build_scene_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """场景资产图 prompt（单图）。"""
    style_block = _style_prefix(style, style_description)
    return (
        f"{style_block}"
        f"场景「{name}」的资产图。\n\n"
        f"{description}\n\n"
        f"{_SCENE_LAYOUT}\n\n"
        f"{_SCENE_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_SCENE}"
    )


def build_prop_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """道具资产图 prompt（单图）。"""
    style_block = _style_prefix(style, style_description)
    guard_block = f"{_PROP_GUARD}\n\n" if _PROP_GUARD else ""
    return (
        f"{style_block}"
        f"道具「{name}」的资产图。\n\n"
        f"{description}\n\n"
        f"{_PROP_LAYOUT}\n\n"
        f"{guard_block}"
        f"{_NEGATIVE_TAIL_PROP}"
    )


def build_product_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """商品资产图（product sheet）prompt（单图 + 保真守卫）。

    商品资产图的使命是把用户随手拍的原图整理成标准资产图，商品形象必须
    忠实于真品（原图作为参考注入），不沿用项目画风前缀——画风统一由项目级 style
    机制在分镜阶段承载，商品资产图保持写实中性。
    """
    del style, style_description  # 与其它 design prompt builder 签名对齐；商品 sheet 不注入画风
    return (
        f"商品「{name}」的标准资产图。\n\n"
        f"{description}\n\n"
        f"{_PRODUCT_LAYOUT}\n\n"
        f"{_PRODUCT_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_PRODUCT}"
    )


# ---------------------------------------------------------------------------
# 分镜 / 视频 prompt 末尾增强
# ---------------------------------------------------------------------------


def build_storyboard_prompt(
    image_prompt: object,
    style: str = "",
    style_description: str = "",
) -> str:
    """Render canonical storyboard semantics into the exact provider prompt."""

    if not is_str(style_description):
        raise TypeError("style_description must be a string")
    projected, normalized_style = project_storyboard_image_prompt(image_prompt, style)

    rendered = image_prompt_to_yaml(projected, normalized_style) if isinstance(projected, dict) else projected
    style_parts: list[str] = []
    if normalized_style and isinstance(projected, str):
        style_parts.append(f"Style: {normalized_style}")
    normalized_description = style_description.strip()
    if normalized_description:
        style_parts.append(f"Visual style: {normalized_description}")
    # 文本形态才按内容判重：它以「当前渲染结果」为初值，正文本身就带着这些声明，按前缀相等
    # 判定会漏判而叠出第二份。结构形态的正文是本函数刚渲染出的 YAML，一律注入。
    if isinstance(projected, str):
        style_parts = [part for part in style_parts if part not in rendered]
    if style_parts:
        rendered = "\n".join(style_parts) + "\n\n" + rendered
    return append_image_negative_tail(rendered)


def render_storyboard_image_prompt(
    image_prompt: object,
    *,
    style: str = "",
    style_description: str = "",
    product_names: Sequence[str] | None = None,
) -> str:
    """分镜图最终提示词文本的唯一出口。

    执行路径与预览接口共用本函数：结构形态经项目风格投影为 YAML、文本形态原样作提示词主体，
    两者同样注入项目风格与反向约束，商品分镜再追加高保真还原指令（``product_names`` 取实际
    注入了参考图的商品，与执行期同口径）。
    """

    return append_product_fidelity_tail(build_storyboard_prompt(image_prompt, style, style_description), product_names)


def append_product_fidelity_tail(prompt: str, product_names: Sequence[str] | None) -> str:
    """给商品分镜的生成 prompt 追加高保真还原指令。

    仅在商品参考图实际注入请求时调用（分镜图与视频两层共用同一份指令文本）——
    指令指向"商品参考图"，参考缺席时追加只会误导模型。``product_names`` 为空
    （含 None 脏数据）返回原 prompt；重复调用幂等。误传单个字符串按单商品名处理
    （str 本身满足 Sequence[str]，按字符迭代会拼出逐字括注的畸形指令）。

    显式声明优先于此前反向约束里的文字/Logo 禁止项（``_NEGATIVE_TAIL_*`` /
    ``_WATERMARK_PACK`` 等通用反向提示词）——那些约束防的是画面里凭空多出的水印/Logo，
    与「商品参考图本身自带的品牌标识须原样保留」并不矛盾，但两条指令的字面文本在同一
    prompt 里共存时对模型是冲突信号，需要显式排出优先级。
    """
    if not product_names:
        return prompt
    if isinstance(product_names, str):
        product_names = (product_names,)
    names = "".join(f"「{name}」" for name in product_names if name)
    if not names:
        return prompt
    tail = (
        f"商品高保真还原（最高优先级，优先于前述文字/Logo 禁止项）：画面中的商品{names}"
        f"必须与商品参考图完全一致——{_PRODUCT_FIDELITY_CORE}，不得重新设计或美化商品本身；"
        "前述文字/Logo 禁止项仅指画面中不得凭空新增文字或 Logo，商品参考图自带的文字与 Logo"
        "须原样保留；项目画风只作用于商品以外的画面元素。"
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
