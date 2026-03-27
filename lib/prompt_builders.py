"""
统一的图像生成 Prompt 构建函数

所有 Prompt 模板集中在此文件管理，确保 WebUI 和 Skill 使用相同的逻辑。

模块职责:
- 角色设计图 Prompt 构建
- 线索设计图 Prompt 构建（道具类/环境类）
- 分镜图 Prompt 后缀

使用方:
- webui/server/routers/generate.py
- .claude/skills/generate-characters/scripts/generate_character.py
- .claude/skills/generate-clues/scripts/generate_clue.py
"""


def build_character_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """
    构建角色设计图 Prompt

    遵循 nano-banana 最佳实践：使用叙事性段落描述，而非关键词列表。

    Args:
        name: 角色名称
        description: 角色外貌描述（应为叙事性段落）
        style: 项目风格
        style_description: AI 分析的风格描述

    Returns:
        完整的 Prompt 字符串
    """
    style_part = f"，{style}" if style else ""

    # 构建风格前缀
    style_prefix = ""
    if style_description:
        style_prefix = f"Visual style: {style_description}\n\n"

    return f"""{style_prefix}角色设计参考图{style_part}。

「{name}」的全身立绘。

{description}

构图要求：单一角色全身像，姿态自然，面向镜头。
背景：纯净浅灰色，无任何装饰元素。
光线：柔和均匀的摄影棚照明，无强烈阴影。
画质：高清，细节清晰，色彩准确。"""


def build_clue_prompt(
    name: str,
    description: str,
    clue_type: str = "prop",
    style: str = "",
    style_description: str = ""
) -> str:
    """
    构建线索设计图 Prompt

    根据线索类型选择对应的模板。

    Args:
        name: 线索名称
        description: 线索描述
        clue_type: 线索类型 ("prop" 道具 或 "location" 环境)
        style: 项目风格
        style_description: AI 分析的风格描述

    Returns:
        完整的 Prompt 字符串
    """
    if clue_type == "location":
        return build_location_prompt(name, description, style, style_description)
    else:
        return build_prop_prompt(name, description, style, style_description)


def build_prop_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """
    构建道具类线索 Prompt

    使用三视图构图：正面全视图、45度侧视图、细节特写。

    Args:
        name: 道具名称
        description: 道具描述
        style: 项目风格
        style_description: AI 分析的风格描述

    Returns:
        完整的 Prompt 字符串
    """
    style_suffix = f"，{style}" if style else ""

    # 构建风格前缀
    style_prefix = ""
    if style_description:
        style_prefix = f"Visual style: {style_description}\n\n"

    return f"""{style_prefix}一张专业的道具设计参考图{style_suffix}。

道具「{name}」的多视角展示。{description}

三个视图水平排列在纯净浅灰背景上：左侧正面全视图、中间45度侧视图展示立体感、右侧关键细节特写。柔和均匀的摄影棚照明，高清质感，色彩准确。"""


def build_location_prompt(name: str, description: str, style: str = "", style_description: str = "") -> str:
    """
    构建环境类线索 Prompt

    使用 3/4 主画面 + 右下角细节特写的构图。

    Args:
        name: 场景名称
        description: 场景描述
        style: 项目风格
        style_description: AI 分析的风格描述

    Returns:
        完整的 Prompt 字符串
    """
    style_suffix = f"，{style}" if style else ""

    # 构建风格前缀
    style_prefix = ""
    if style_description:
        style_prefix = f"Visual style: {style_description}\n\n"

    return f"""{style_prefix}一张专业的场景设计参考图{style_suffix}。

标志性场景「{name}」的视觉参考。{description}

主画面占据四分之三区域展示环境整体外观与氛围，右下角小图为细节特写。柔和自然光线。"""


def build_storyboard_suffix(content_mode: str = "narration") -> str:
    """
    获取分镜图 Prompt 后缀

    根据内容模式返回对应的构图后缀。

    Args:
        content_mode: 内容模式 ("narration" 说书模式 或 "drama" 剧集模式)

    Returns:
        构图后缀字符串
    """
    if content_mode == "narration":
        return "竖屏构图。"
    else:
        return ""


def build_style_prompt(project_data: dict) -> str:
    """
    构建风格描述 Prompt 片段

    合并 style（用户手动填写）和 style_description（AI 分析生成）。

    Args:
        project_data: project.json 数据

    Returns:
        风格描述字符串，用于拼接到生成 Prompt 中
    """
    parts = []

    # 基础风格标签
    style = project_data.get('style', '')
    if style:
        parts.append(f"Style: {style}")

    # AI 分析的风格描述
    style_description = project_data.get('style_description', '')
    if style_description:
        parts.append(f"Visual style: {style_description}")

    return '\n'.join(parts)
