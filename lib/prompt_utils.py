"""
Prompt 工具函数

提供结构化 Prompt 到 YAML 格式的转换功能。
"""

import re

import yaml

# 风格值开头的「画风：」前缀（全角/半角冒号）。新版风格模版已去前缀，此处兼容存量 project.json。
_STYLE_PREFIX_RE = re.compile(r"^画风[：:]\s*")


def normalize_style(style: str | None) -> str:
    """去掉风格值开头的「画风：」前缀并 strip 两端空白；幂等（已无前缀则原样返回）。

    存量项目的 style 取自旧版风格模版（值以「画风：」开头），叠加英文 ``Style:`` 标签会渲染成
    ``Style: 画风：...`` 的中英混叠。新版模版已去前缀，本函数在注入前兜底清理存量值。
    """
    return _STYLE_PREFIX_RE.sub("", (style or "").strip())


# 预设选项定义
SHOT_TYPES = [
    "Extreme Close-up",
    "Close-up",
    "Medium Close-up",
    "Medium Shot",
    "Medium Long Shot",
    "Long Shot",
    "Extreme Long Shot",
    "Over-the-shoulder",
    "Point-of-view",
]

CAMERA_MOTIONS = [
    "Static",
    "Pan Left",
    "Pan Right",
    "Tilt Up",
    "Tilt Down",
    "Zoom In",
    "Zoom Out",
    "Tracking Shot",
]


def image_prompt_to_yaml(image_prompt: dict, project_style: str) -> str:
    """
    将 imagePrompt 结构转换为 YAML 格式字符串

    Args:
        image_prompt: segment 中的 image_prompt 对象，结构为：
            {
                "scene": "场景描述",
                "composition": {
                    "shot_type": "镜头类型",
                    "lighting": "光线描述",
                    "ambiance": "氛围描述"
                }
            }
        project_style: 项目级风格设置（从 project.json 读取）

    Returns:
        YAML 格式字符串，用于 Gemini API 调用
    """
    ordered = {
        "Style": normalize_style(project_style),
        "Scene": image_prompt["scene"],
        "Composition": {
            "shot_type": image_prompt["composition"]["shot_type"],
            "lighting": image_prompt["composition"]["lighting"],
            "ambiance": image_prompt["composition"]["ambiance"],
        },
    }
    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False, sort_keys=False)


def video_prompt_to_yaml(video_prompt: dict) -> str:
    """
    将 videoPrompt 结构转换为 YAML 格式字符串

    Args:
        video_prompt: segment 中的 video_prompt 对象，结构为：
            {
                "action": "动作描述",
                "camera_motion": "摄像机运动",
                "ambiance_audio": "环境音效描述",
                "dialogue": [{"speaker": "角色名", "line": "台词"}]
            }

    Returns:
        YAML 格式字符串，用于 Veo API 调用
    """
    dialogue = [{"Speaker": d["speaker"], "Line": d["line"]} for d in video_prompt.get("dialogue", [])]

    ordered = {
        "Action": video_prompt["action"],
        "Camera_Motion": video_prompt["camera_motion"],
        "Ambiance_Audio": video_prompt.get("ambiance_audio", ""),
    }

    # 仅在有对话时添加 Dialogue 字段
    if dialogue:
        ordered["Dialogue"] = dialogue

    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False, sort_keys=False)


def is_structured_image_prompt(image_prompt) -> bool:
    """
    检查 image_prompt 是否为结构化格式

    Args:
        image_prompt: image_prompt 字段值

    Returns:
        True 如果是结构化格式（dict），False 如果是旧的字符串格式
    """
    return isinstance(image_prompt, dict) and "scene" in image_prompt


def is_structured_video_prompt(video_prompt) -> bool:
    """
    检查 video_prompt 是否为结构化格式

    Args:
        video_prompt: video_prompt 字段值

    Returns:
        True 如果是结构化格式（dict），False 如果是旧的字符串格式
    """
    return isinstance(video_prompt, dict) and "action" in video_prompt


def validate_shot_type(shot_type: str) -> bool:
    """验证镜头类型是否为预设选项"""
    return shot_type in SHOT_TYPES


def validate_camera_motion(camera_motion: str) -> bool:
    """验证摄像机运动是否为预设选项"""
    return camera_motion in CAMERA_MOTIONS
