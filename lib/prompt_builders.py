"""图像 / 视频 / 资产 prompt 的统一真相源。

WebUI（server/services/generation_tasks.py）和 Skill（agent_runtime_profile/.claude/skills/generate-assets）
都从这里取最终 prompt 文本，确保入口一致、不漂移。

设计要点：
- 无 backend 锁定：纯文本拼接，由调用方决定走哪个 image/video provider。
- 反向提示词统一以「画面避免：xxx」追加到 prompt 末尾，不再使用各 backend 的 negative_prompt 参数通道
  （image backends 大多 silent 丢弃，参数化反而增加分叉）。
- 防崩短语精简：扁平 4 项内核，避免 CFG 权重稀释。
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
_SCENE_GUARD = "空间透视正常，陈设固定，光影统一。"
_PROP_GUARD = "外观结构完整，焦点清晰。"
# 产品保真核心句：sheet 生成守卫与镜头注入指令共用，调优措辞只改这一处。
_PRODUCT_FIDELITY_CORE = "logo、文字、配色、材质、比例与结构不得改变或臆造"
_PRODUCT_GUARD = f"产品外观必须忠实于参考图中的真实产品：{_PRODUCT_FIDELITY_CORE}；各视图为同一件产品。"

# 反向提示词：精简到核心 4 项，避免 CFG 权重稀释。
_NEGATIVE_TAIL_ASSET = "画面避免：水印、多余文字、低分辨率、手指畸形。"
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
    """角色设计图 prompt（issue #353 四视图 16:9）。"""
    style_block = _style_prefix(style, style_description)
    return (
        f"{style_block}"
        f"角色「{name}」的设计参考图。\n\n"
        f"{description}\n\n"
        f"{_CHARACTER_LAYOUT}\n\n"
        f"{_CHARACTER_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_ASSET}"
    )


def build_scene_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """场景设计图 prompt（主+细节）。"""
    style_block = _style_prefix(style, style_description)
    return (
        f"{style_block}"
        f"标志性场景「{name}」的视觉参考。\n\n"
        f"{description}\n\n"
        f"{_SCENE_LAYOUT}\n\n"
        f"{_SCENE_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_ASSET}"
    )


def build_prop_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """道具设计图 prompt（三视图）。"""
    style_block = _style_prefix(style, style_description)
    return (
        f"{style_block}"
        f"道具「{name}」的多视角展示。\n\n"
        f"{description}\n\n"
        f"{_PROP_LAYOUT}\n\n"
        f"{_PROP_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_ASSET}"
    )


def build_product_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """产品标准参考图（product sheet）prompt（多角度 + 保真守卫）。

    产品 sheet 的使命是把用户随手拍的原图整理成标准多角度设计图，产品形象必须
    忠实于真品（原图作为参考注入），不沿用项目画风前缀——画风统一由项目级 style
    机制在分镜阶段承载，产品参考图保持写实中性。
    """
    del style, style_description  # 与其它 design prompt builder 签名对齐；产品 sheet 不注入画风
    return (
        f"产品「{name}」的标准参考图。\n\n"
        f"{description}\n\n"
        f"{_PRODUCT_LAYOUT}\n\n"
        f"{_PRODUCT_GUARD}\n\n"
        f"{_NEGATIVE_TAIL_ASSET}"
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


def build_storyboard_suffix(content_mode: str = "narration", *, aspect_ratio: str | None = None) -> str:
    """分镜图构图后缀。优先 aspect_ratio，缺省按 content_mode 推导。"""
    if aspect_ratio is None:
        ratio = "9:16" if content_mode in {"narration", "ad"} else "16:9"
    else:
        ratio = aspect_ratio
    if ratio == "9:16":
        return "竖屏构图。"
    if ratio == "16:9":
        return "横屏构图。"
    return ""
