"""
Prompt 工具函数

提供结构化 Prompt 到 YAML 格式的转换功能。
"""

import logging
import re
from typing import Any, get_args

import yaml

from lib.script_models import CameraMotion, ShotType

logger = logging.getLogger(__name__)

# 风格值开头的「画风：」前缀（全角/半角冒号）。新版风格模版已去前缀，此处兼容存量 project.json。
_STYLE_PREFIX_RE = re.compile(r"^画风[：:]\s*")


def normalize_style(style: str | None) -> str:
    """去掉风格值开头的「画风：」前缀并 strip 两端空白；幂等（已无前缀则原样返回）。

    存量项目的 style 取自旧版风格模版（值以「画风：」开头），叠加英文 ``Style:`` 标签会渲染成
    ``Style: 画风：...`` 的中英混叠。新版模版已去前缀，本函数在注入前兜底清理存量值。
    """
    return _STYLE_PREFIX_RE.sub("", (style or "").strip())


# 预设选项：真相源是 lib.script_models 的 Literal 词表，此处派生避免双写漂移
SHOT_TYPES: list[str] = list(get_args(ShotType))
CAMERA_MOTIONS: list[str] = list(get_args(CameraMotion))


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
                "dialogue": [{"speaker": "角色名", "line": "台词"}],
                "voice_profiles": [{"Speaker": "角色名", "Voice_Style": "声音风格"}]
            }

    Returns:
        YAML 格式字符串，用于 Veo API 调用
    """
    dialogue = [{"Speaker": d["speaker"], "Line": d["line"]} for d in video_prompt.get("dialogue", [])]
    voice_profiles = video_prompt.get("voice_profiles") or []

    ordered: dict[str, Any] = {}
    # Voice_Profiles 是集中声明段，须在顶部：调用方已按 dialogue speaker ∩ 非空 voice_style
    # 角色资产派生好列表，此处只负责按序注入，不做二次过滤。
    if voice_profiles:
        ordered["Voice_Profiles"] = voice_profiles
    ordered["Action"] = video_prompt["action"]
    ordered["Camera_Motion"] = video_prompt["camera_motion"]
    ordered["Ambiance_Audio"] = video_prompt.get("ambiance_audio", "")

    # 仅在有对话时添加 Dialogue 字段
    if dialogue:
        ordered["Dialogue"] = dialogue

    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False, sort_keys=False)


def strip_voice_profiles(video_prompt: dict[str, Any]) -> dict[str, Any]:
    """剥离入参自带的 ``voice_profiles`` 键。

    ``Voice_Profiles`` 声明段由编排层从角色资产机械派生，剧本 JSON / 调用方请求体均不
    承载它——两处注入点（worker 执行路径、SDK 入队路径）在决定是否调用
    :func:`build_drama_video_prompt` 之前都须先过一遍本函数，drama 之外的 content_mode
    或缺 ``utterances`` 的条目才不会让调用方自带（或剧本残留）的 ``voice_profiles`` 绕过
    C 类（真无声）门控直接进入 YAML。
    """
    return {k: v for k, v in video_prompt.items() if k != "voice_profiles"}


def build_drama_video_prompt(
    video_prompt: dict[str, Any],
    utterances: object,
    *,
    characters: dict[str, Any] | None,
) -> dict[str, Any]:
    """drama video_prompt 的 dialogue 与 Voice_Profiles 唯一注入出口。

    worker 执行路径与 SDK 入队路径共用本函数，两处注入点的产出同构由此在结构上成立，而非
    各写一份靠约定对齐。

    ``characters`` 为 ``None`` 表示不注入 Voice_Profiles（C 类真无声模型）。无论注入
    与否都先剥离入参自带的 ``voice_profiles``：该声明段由编排层从角色资产机械派生，剧本
    JSON 不承载它，剧本中的残留值不得绕过 C 类门控进入 YAML。
    """
    prompt = strip_voice_profiles(video_prompt)
    dialogue = utterances_to_dialogue(utterances)
    return _attach_voice_profiles(prompt, dialogue, characters=characters)


def build_drama_video_prompt_from_legacy_dialogue(
    video_prompt: dict[str, Any],
    *,
    characters: dict[str, Any] | None,
) -> dict[str, Any]:
    """legacy drama 场景（utterances 迁移前的旧剧本，台词仍留在 ``video_prompt.dialogue``、
    无场景级 ``utterances`` 字段）的 Voice_Profiles 注入出口。

    ``load_script`` 按原始 JSON 读盘、不过 pydantic，这类存量剧本不会被
    ``DramaScene._migrate_legacy`` 自动补齐 ``utterances``；调用方须按 ``"utterances" not
    in item`` 识别后改走本函数，而非 :func:`build_drama_video_prompt`——旧结构的
    ``dialogue`` 已是 ``{speaker, line}`` 目标形态，无需 :func:`utterances_to_dialogue`
    转换，直接派生 Voice_Profiles。
    """
    prompt = strip_voice_profiles(video_prompt)
    dialogue = prompt.get("dialogue")
    if not isinstance(dialogue, list):
        dialogue = []
    return _attach_voice_profiles(prompt, dialogue, characters=characters)


def _attach_voice_profiles(
    prompt: dict[str, Any], dialogue: list[dict[str, str]], *, characters: dict[str, Any] | None
) -> dict[str, Any]:
    prompt["dialogue"] = dialogue
    if characters is not None:
        voice_profiles = _build_voice_profiles(dialogue, characters)
        if voice_profiles:
            prompt["voice_profiles"] = voice_profiles
    return prompt


def _build_voice_profiles(dialogue: list[dict[str, str]], characters: dict[str, Any]) -> list[dict[str, str]]:
    """从 dialogue speakers 与角色资产派生 Voice_Profiles 声明段。

    仅收录角色资产存在且 ``voice_style`` 非空者，一个 speaker 一条（按 dialogue 中首次
    出现的顺序去重）；speaker 未命中角色资产或资产 ``voice_style`` 为空，静默跳过
    （不建面向用户的提示通道，调用方按需记 logger）。

    ``characters`` 来自明文 project.json，用户手编或外部脚本可能写成非 dict，逐层做类型
    收窄而非直接下标（同 ``lib.config.resolver._safe_dict`` 的取向）。
    """
    if not isinstance(characters, dict):
        return []
    seen: set[str] = set()
    profiles: list[dict[str, str]] = []
    for entry in dialogue:
        speaker = entry.get("speaker") if isinstance(entry, dict) else None
        if not isinstance(speaker, str) or not speaker or speaker in seen:
            continue
        seen.add(speaker)
        character = characters.get(speaker)
        if not isinstance(character, dict):
            logger.debug("Voice_Profiles 跳过：speaker %r 未命中角色资产", speaker)
            continue
        voice_style = character.get("voice_style")
        if isinstance(voice_style, str) and voice_style.strip():
            profiles.append({"Speaker": speaker, "Voice_Style": voice_style.strip()})
        else:
            logger.debug("Voice_Profiles 跳过：角色 %r 的 voice_style 为空", speaker)
    return profiles


def utterances_to_dialogue(utterances: object) -> list[dict[str, str]]:
    """drama 口型音轨出口：从有序 ``utterances`` 取 dialogue-kind 条目，转成 video YAML 的
    ``{speaker, line}`` 列表（保留时序）。

    voiceover-kind 不进视频提示词（无 speaker，留给字幕 / TTS）。对脏数据稳健：非 list 整体、
    非 dict/object 元素、非 dialogue 条目一律跳过；dialogue 须 speaker 与 line 同时非空才进口型音轨，
    缺 speaker 的脏 dialogue（契约要求 dialogue 必带非空 speaker）不重新喂给 lip-sync / video prompt。

    兼容两种条目形态：原始 JSON ``dict`` 与已实例化的 Pydantic ``Utterance`` 模型对象（取同名属性）。
    speaker / text 用 ``isinstance(_, str)`` 显式取值，非字符串（如数字）按空处理、不 ``str()`` 强转，
    避免脏类型被静默字符串化进 YAML。
    """
    dialogue: list[dict[str, str]] = []
    if not isinstance(utterances, list):
        return dialogue
    for entry in utterances:
        if isinstance(entry, dict):
            kind = entry.get("kind")
            speaker_val = entry.get("speaker")
            text_val = entry.get("text")
        elif hasattr(entry, "kind"):
            kind = getattr(entry, "kind", None)
            speaker_val = getattr(entry, "speaker", None)
            text_val = getattr(entry, "text", None)
        else:
            continue

        if kind != "dialogue":
            continue

        speaker = speaker_val.strip() if isinstance(speaker_val, str) else ""
        line = text_val.strip() if isinstance(text_val, str) else ""
        if speaker and line:
            dialogue.append({"speaker": speaker, "line": line})
    return dialogue


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
