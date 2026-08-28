"""ComfyUIVideoBackend — 本地 ComfyUI 视频生成后端（MiniMax H3 工作流）。

对接 ComfyUI 的 HTTP API：参考素材上传 ``POST /upload/image`` → 提交 API-format 工作流
``POST /prompt`` 取 prompt_id → 轮询 ``GET /history/{prompt_id}`` 至执行完成 →
``GET /view`` 拉取成片文件。内置三个 MiniMax H3 工作流模板（ref2va / 8step /
8step-fp8，模板逻辑移植自 omnishift 已实战验证的实现），用户也可用自定义工作流覆盖
（覆盖模板必须包含 MiniMaxH3ReferenceToVideo 与 SaveVideo 节点，运行时仅注入
prompt / 分辨率 / 时长帧数 / seed / 参考素材引用，不改图结构）。

能力约束：MiniMax H3 单机工作流要求 1–9 张参考图片（``MAX_REFERENCE_IMAGES``），
最多 3 段参考视频与 3 段参考音频（``MAX_REFERENCE_VIDEOS`` / ``MAX_REFERENCE_AUDIOS``）；
时长 5–15 秒（``MIN/MAX_DURATION_SECONDS``），帧数按 24fps 取整后对齐 17n+5
（MiniMax H3 官方帧数约束）；分辨率按档位（480p→约 0.4MP / 其余→约 0.9MP）× 宽高比
解出像素尺寸并对齐 32 的倍数。首帧图按参考图并入同一组（ComfyUI 模板把首帧与参考图
统一挂在 ``ref_images`` 输入下），无首帧即文生视频，不支持尾帧。

resume 契约：submit 成功即持久化 prompt_id（``ProviderJobIdPersistenceMixin``），
续跑仅轮询 + 下载，不重新提交（ADR 0007）；``/history/{prompt_id}`` 返回 404 视为
历史过期，抛 ``ResumeExpiredError``。
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_COMFYUI
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

# 三个内置模型与三个内置工作流模板一一对应：
# - minimax-h3-ref2va           → build_minimax_h3_workflow            （标准 20 步 Ref2VA）
# - minimax-h3-ref2va-8step     → build_minimax_h3_eight_step_workflow （8 步 Turbo LoRA，INT8 UNet）
# - minimax-h3-ref2va-8step-fp8 → build_minimax_h3_eight_step_fp8_workflow（8 步 Turbo LoRA，FP8 UNet）
BUILTIN_COMFYUI_MODELS: tuple[str, ...] = (
    "minimax-h3-ref2va",
    "minimax-h3-ref2va-8step",
    "minimax-h3-ref2va-8step-fp8",
)

# 内置模板引用的模型文件（ComfyUI 工作流输入），8 步 LoRA 单独成组。
_MODEL_FILES: dict[str, str] = {
    "diffusion": "minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
    "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
}
_EIGHT_STEP_LORA: dict[str, str | float] = {
    "diffusion": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "name": "minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors",
    "strength": 0.75,
}

# 参考素材上限（MiniMax H3 单机工作流），与模板 ref_* 节点数一致。
# 模板层另支持参考视频（ref_videos / ref_video_audios，上限 3 段），当前
# VideoGenerationRequest 无参考视频字段，generate 路径暂不启用。
_MAX_REFERENCE_IMAGES = 9
_MAX_REFERENCE_AUDIOS = 3

# 内置模板默认输出前缀；提交时可被覆盖模板改写。
_DEFAULT_FILENAME_PREFIX = "video/MiniMax_H3/arcreel"

# 时长边界（秒），与 normalize_duration 的截断范围一致：请求时长越界 fail-loud，
# 而非静默截断——否则 30s 请求实际只生成约 15s 却按原请求秒数计费。
_MIN_DURATION_SECONDS = 5
_MAX_DURATION_SECONDS = 15

_FPS = 24
# MiniMax H3 官方帧数约束：帧数 = 17n + 5。
_FRAME_STEP = 17
_FRAME_OFFSET = 5

# 尺寸约束：长宽对齐 32 的倍数（ComfyUI 视频模型对 latent 尺寸的整除要求）。
_VIDEO_ROUND_TO = 32
# 分辨率档 → 目标像素量：480p ≈ 0.4MP，其余（720p/768p/1080p/2K）≈ 0.9MP。
_480P_MEGAPIXELS = 0.4
_DEFAULT_MEGAPIXELS = 0.9

# 宽高比 → 像素比对照表（dimensions_for 的档位；未登记比例回落 16:9）。
_ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (1, 1),
    "2:3": (2, 3),
    "3:2": (3, 2),
    "3:4": (3, 4),
    "4:3": (4, 3),
    "9:16": (9, 16),
    "16:9": (16, 9),
    "21:9": (21, 9),
}

# submit 超时 ~300s：覆盖 ComfyUI 在排队/加载模型时的长阻塞，避免可重试的繁忙被
# ReadTimeout 包成终态歧义失败。
_SUBMIT_TIMEOUT_SECONDS = 300.0
# 轮询 / 下载用较短超时（幂等 GET 正常秒级返回）。
_POLL_HTTP_TIMEOUT_SECONDS = 60.0
# 单张素材上传超时：参考素材可能数 MB，允许更长阻塞。
_UPLOAD_TIMEOUT_SECONDS = 300.0

_H3_NODE_CLASS = "MiniMaxH3ReferenceToVideo"
_SAVE_VIDEO_NODE_CLASS = "SaveVideo"
_RANDOM_NOISE_NODE_CLASS = "RandomNoise"

# 覆盖模板上运行时注入的 ref_* 前缀：参数化时清空旧引用，运行时按请求素材重建。
_REF_INPUT_PREFIX_RE = re.compile(r"^ref_(images|videos|video_audios|audios)\.")


# ── 模板参数化：时长 / 尺寸 / prompt ─────────────────────────────────


def normalize_duration(duration: int | None) -> tuple[int, int]:
    """请求秒数 → (实际秒数, 帧数)：秒数截断到 [5, 15]，帧数按 24fps 取整后对齐 17n+5。

    MiniMax H3 官方帧数约束为 ``17n + 5``（与通用 ComfyUI 视频的 8n+1 不同），
    5 秒下限与 15 秒上限与 H3 模板的一致口径。
    """
    value = duration if duration is not None else 5
    try:
        rounded = int(value)
    except (TypeError, ValueError):
        rounded = 5
    seconds = min(_MAX_DURATION_SECONDS, max(_MIN_DURATION_SECONDS, rounded))
    base_frames = max(_FRAME_OFFSET, round(seconds * _FPS))
    frames = base_frames + ((_FRAME_OFFSET - (base_frames % _FRAME_STEP)) + _FRAME_STEP) % _FRAME_STEP
    return seconds, frames


def dimensions_for(aspect_ratio: str | None, resolution: str | None) -> tuple[int, int]:
    """(宽, 高)：宽高比精确解像素量（480p ≈ 0.4MP / 其余 ≈ 0.9MP），长宽对齐 32。

    未登记的宽高比回落 16:9；``resolution`` 只在是否 480p 上分支。
    """
    rw, rh = _ASPECT_RATIOS.get(str(aspect_ratio or "16:9"), (16, 9))
    megapixels = _480P_MEGAPIXELS if resolution == "480p" else _DEFAULT_MEGAPIXELS
    scale = math.sqrt((megapixels * 1024 * 1024) / (rw * rh))

    def nearest(value: float) -> int:
        return max(_VIDEO_ROUND_TO, round(value / _VIDEO_ROUND_TO) * _VIDEO_ROUND_TO)

    return nearest(rw * scale), nearest(rh * scale)


def normalize_minimax_h3_prompt(prompt: str) -> str:
    """把 ``@图片N`` / ``@视频N`` / ``@音频N`` 指认转成 MiniMax H3 的 ``<Picture N>`` 标记。

    指认编号（N）必须原样保留：模型按编号定位参考素材，改写编号会让角色/场景错位。
    替换结果带尾随空格（与参考实现 ``'<Picture $1> '`` 同形），末位 trim 收尾。
    """
    text = str(prompt or "")
    for token, marker in (("@图片", "Picture"), ("@视频", "Video"), ("@音频", "Audio")):
        text = re.sub(rf"{re.escape(token)}(\d+)", rf"<{marker} \1> ", text)
    return text.strip()


def _random_seed(seed: int | None) -> int:
    """请求 seed 或随机 seed：随机值按 JS ``Math.random() * MAX_SAFE_INTEGER`` 同口径。"""
    if seed is not None:
        try:
            return int(seed)
        except (TypeError, ValueError):
            pass
    return random.SystemRandom().randrange(0, 2**53)


def _node_inputs(node: dict[str, Any]) -> dict[str, Any]:
    """API-format 节点的 inputs 字段；结构非法返回空 dict。"""
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


# ── 工作流模板构建 ──────────────────────────────────────────────────


def _attach_reference_nodes(
    workflow: dict[str, dict[str, Any]],
    h3_node: dict[str, Any],
    *,
    image_files: list[str],
    video_files: list[str],
    audio_files: list[str],
) -> None:
    """把上传后的参考素材以 Load* 节点 + ``ref_*.ref_*_{i}`` 输入挂到 H3 节点上。

    视频素材走 ``LoadVideo → GetVideoComponents`` 两级，同时提供 ``ref_videos`` 与
    ``ref_video_audios`` 两个输入（画面 + 该视频自带的音轨）。节点 ID 分区：
    图片 200+ / 视频 300+ 与 320+ / 音频 400+，互不重叠。
    """
    for index, file in enumerate(image_files):
        node_id = str(200 + index)
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": file}}
        h3_node["inputs"][f"ref_images.ref_image_{index}"] = [node_id, 0]

    for index, file in enumerate(video_files):
        load_node_id = str(300 + index)
        components_node_id = str(320 + index)
        workflow[load_node_id] = {"class_type": "LoadVideo", "inputs": {"file": file}}
        workflow[components_node_id] = {
            "class_type": "GetVideoComponents",
            "inputs": {"video": [load_node_id, 0]},
        }
        h3_node["inputs"][f"ref_videos.ref_video_{index}"] = [components_node_id, 0]
        h3_node["inputs"][f"ref_video_audios.ref_video_audio_{index}"] = [components_node_id, 1]

    for index, file in enumerate(audio_files):
        node_id = str(400 + index)
        workflow[node_id] = {"class_type": "LoadAudio", "inputs": {"audio": file}}
        h3_node["inputs"][f"ref_audios.ref_audio_{index}"] = [node_id, 0]


def build_minimax_h3_workflow(
    *,
    prompt: str,
    image_files: list[str],
    video_files: list[str],
    audio_files: list[str],
    duration: int | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    seed: int | None = None,
    filename_prefix: str | None = None,
) -> dict[str, dict[str, Any]]:
    """标准 20 步 MiniMax H3 Ref2VA 内置模板（API-format 工作流 JSON）。

    节点图固定（VAELoader × 2 / CLIPLoader / UNETLoader / PathchSageAttentionKJ /
    RandomNoise / MiniMaxH3ReferenceToVideo / KSamplerSelect / BasicScheduler /
    BasicGuider / SamplerCustomAdvanced / VAEDecode × 2 / CreateVideo / SaveVideo），
    仅 prompt / 分辨率 / 时长帧数 / seed / 参考素材引用运行时注入。
    """
    _, frames = normalize_duration(duration)
    width, height = dimensions_for(aspect_ratio, resolution)
    noise_seed = _random_seed(seed)

    workflow: dict[str, dict[str, Any]] = {
        "119": {"class_type": "VAELoader", "inputs": {"vae_name": _MODEL_FILES["video_vae"]}},
        "120": {"class_type": "VAELoader", "inputs": {"vae_name": _MODEL_FILES["audio_vae"]}},
        "128": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": _MODEL_FILES["text_encoder"], "type": "minimax", "device": "default"},
        },
        "127": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": _MODEL_FILES["diffusion"], "weight_dtype": "default"},
        },
        "141": {
            "class_type": "PathchSageAttentionKJ",
            "inputs": {"model": ["127", 0], "sage_attention": "auto", "allow_compile": False},
        },
        "129": {"class_type": _RANDOM_NOISE_NODE_CLASS, "inputs": {"noise_seed": noise_seed}},
        "136": {
            "class_type": _H3_NODE_CLASS,
            "inputs": {
                "clip": ["128", 0],
                "vae": ["119", 0],
                "audio_vae": ["120", 0],
                "prompt": normalize_minimax_h3_prompt(prompt),
                "width": width,
                "height": height,
                "length": frames,
                "ref_image_size": "match",
            },
        },
        "123": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "124": {
            "class_type": "BasicScheduler",
            "inputs": {"model": ["141", 0], "scheduler": "simple", "steps": 20, "denoise": 1},
        },
        "126": {"class_type": "BasicGuider", "inputs": {"model": ["141", 0], "conditioning": ["136", 0]}},
        "125": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["129", 0],
                "guider": ["126", 0],
                "sampler": ["123", 0],
                "sigmas": ["124", 0],
                "latent_image": ["136", 1],
            },
        },
        "122": {"class_type": "VAEDecode", "inputs": {"samples": ["125", 0], "vae": ["119", 0]}},
        "121": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["125", 0], "vae": ["120", 0]}},
        "130": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["122", 0], "audio": ["121", 0], "fps": _FPS, "bit_depth": 8},
        },
        "92": {
            "class_type": _SAVE_VIDEO_NODE_CLASS,
            "inputs": {
                "video": ["130", 0],
                "filename_prefix": filename_prefix or _DEFAULT_FILENAME_PREFIX,
                "format": "auto",
                "codec": "auto",
            },
        },
    }

    _attach_reference_nodes(
        workflow,
        workflow["136"],
        image_files=image_files,
        video_files=video_files,
        audio_files=audio_files,
    )
    return workflow


def build_minimax_h3_eight_step_workflow(
    *,
    prompt: str,
    image_files: list[str],
    video_files: list[str],
    audio_files: list[str],
    duration: int | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    seed: int | None = None,
    filename_prefix: str | None = None,
) -> dict[str, dict[str, Any]]:
    """8 步 Turbo LoRA 模板（INT8 UNet）：在标准模板上替换 UNet 并插入 LoraLoaderModelOnly。

    LoRA 输出接管 UNETLoader 输出（节点 127）的消费方：PathchSageAttentionKJ 与
    BasicScheduler 的 ``model`` 输入改指 LoRA 节点（148），采样步数收敛为 8。
    """
    workflow = build_minimax_h3_workflow(
        prompt=prompt,
        image_files=image_files,
        video_files=video_files,
        audio_files=audio_files,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        seed=seed,
        filename_prefix=filename_prefix,
    )
    workflow["127"]["inputs"]["unet_name"] = str(_EIGHT_STEP_LORA["diffusion"])
    workflow["148"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["127", 0],
            "lora_name": str(_EIGHT_STEP_LORA["name"]),
            "strength_model": float(_EIGHT_STEP_LORA["strength"]),
        },
    }
    workflow["141"]["inputs"]["model"] = ["148", 0]
    workflow["124"]["inputs"]["model"] = ["148", 0]
    workflow["124"]["inputs"]["steps"] = 8
    return workflow


def build_minimax_h3_eight_step_fp8_workflow(
    *,
    prompt: str,
    image_files: list[str],
    video_files: list[str],
    audio_files: list[str],
    duration: int | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    seed: int | None = None,
    filename_prefix: str | None = None,
) -> dict[str, dict[str, Any]]:
    """8 步 LoRA 模板的 FP8 UNet 变体：仅把 UNet 文件名换回 FP8 缩放版。"""
    workflow = build_minimax_h3_eight_step_workflow(
        prompt=prompt,
        image_files=image_files,
        video_files=video_files,
        audio_files=audio_files,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        seed=seed,
        filename_prefix=filename_prefix,
    )
    workflow["127"]["inputs"]["unet_name"] = _MODEL_FILES["diffusion"]
    return workflow


_BUILTIN_MODEL_BUILDERS: dict[str, Callable[..., dict[str, dict[str, Any]]]] = {
    "minimax-h3-ref2va": build_minimax_h3_workflow,
    "minimax-h3-ref2va-8step": build_minimax_h3_eight_step_workflow,
    "minimax-h3-ref2va-8step-fp8": build_minimax_h3_eight_step_fp8_workflow,
}


def _find_node_by_class(
    workflow: dict[str, dict[str, Any]], class_type: str, *, error_message: str
) -> tuple[str, dict[str, Any]]:
    for node_id, node in workflow.items():
        if node.get("class_type") == class_type:
            return node_id, node
    raise ComfyUIWorkflowError(error_message)


def _next_node_id(workflow: dict[str, dict[str, Any]], offset: int) -> str:
    max_id = max((int(node_id) for node_id in workflow if str(node_id).isdigit()), default=0)
    return str(max_id + offset)


def _deep_copy(value: object) -> Any:
    """深拷贝工作流值（dict/list 递归）；连接引用 ``["127", 0]`` 是 list，需整体复制。"""
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value


def build_configured_minimax_h3_workflow(
    template: dict[str, dict[str, Any]],
    *,
    prompt: str,
    image_files: list[str],
    video_files: list[str],
    audio_files: list[str],
    duration: int | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    seed: int | None = None,
    filename_prefix: str | None = None,
) -> dict[str, dict[str, Any]]:
    """把用户覆盖的工作流模板按运行时输入参数化：保留图结构与节点选择，仅注入运行时值。

    模板必须已通过 :func:`validate_comfyui_workflows`（含 MiniMaxH3ReferenceToVideo 与
    SaveVideo 节点）；此处逐层深拷贝，不改动模板原件。注入的内容：
    prompt / width / height / length / seed（RandomNoise 节点存在时）/ filename_prefix，
    并按请求素材重建 ref_* 输入（先清空模板里的旧引用，避免残留幽灵素材节点）。
    新增参考素材节点用模板最大节点 ID 之后的自增 ID，避免与既有节点撞号。
    """
    workflow: dict[str, dict[str, Any]] = {
        node_id: {
            "class_type": str(node.get("class_type", "")),
            "inputs": _deep_copy(node.get("inputs", {})),
        }
        for node_id, node in template.items()
    }
    _, h3_node = _find_node_by_class(
        workflow, _H3_NODE_CLASS, error_message="ComfyUI 工作流缺少 MiniMaxH3ReferenceToVideo 节点"
    )
    _, save_node = _find_node_by_class(
        workflow, _SAVE_VIDEO_NODE_CLASS, error_message="ComfyUI 工作流缺少 SaveVideo 输出节点"
    )
    _, frames = normalize_duration(duration)
    width, height = dimensions_for(aspect_ratio, resolution)

    h3_inputs = _node_inputs(h3_node)
    h3_inputs["prompt"] = normalize_minimax_h3_prompt(prompt)
    h3_inputs["width"] = width
    h3_inputs["height"] = height
    h3_inputs["length"] = frames
    for key in [k for k in h3_inputs if _REF_INPUT_PREFIX_RE.match(k)]:
        del h3_inputs[key]

    for node in workflow.values():
        if node.get("class_type") == _RANDOM_NOISE_NODE_CLASS:
            _node_inputs(node)["noise_seed"] = _random_seed(seed)

    _node_inputs(save_node)["filename_prefix"] = filename_prefix or _DEFAULT_FILENAME_PREFIX

    for index, file in enumerate(image_files):
        node_id = _next_node_id(workflow, index + 1)
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": file}}
        h3_inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
    for index, file in enumerate(video_files):
        load_node_id = _next_node_id(workflow, index * 2 + 1)
        workflow[load_node_id] = {"class_type": "LoadVideo", "inputs": {"file": file}}
        components_node_id = _next_node_id(workflow, index * 2 + 1)
        workflow[components_node_id] = {
            "class_type": "GetVideoComponents",
            "inputs": {"video": [load_node_id, 0]},
        }
        h3_inputs[f"ref_videos.ref_video_{index}"] = [components_node_id, 0]
        h3_inputs[f"ref_video_audios.ref_video_audio_{index}"] = [components_node_id, 1]
    for index, file in enumerate(audio_files):
        node_id = _next_node_id(workflow, index + 1)
        workflow[node_id] = {"class_type": "LoadAudio", "inputs": {"audio": file}}
        h3_inputs[f"ref_audios.ref_audio_{index}"] = [node_id, 0]

    return workflow


# ── 工作流模板校验 ──────────────────────────────────────────────────


class ComfyUIWorkflowError(ValueError):
    """用户覆盖工作流模板校验失败（缺节点 / 结构非法）。"""


def validate_comfyui_workflows(raw: object) -> list[tuple[str, dict[str, dict[str, Any]]]]:
    """校验用户配置的覆盖工作流列表，返回 ``[(model, workflow), ...]``。

    校验规则（与内置模型的管理口径对齐）：
    - 必须是数组；每个条目含非空 model 与 API-format 工作流 JSON（非空 dict）。
    - 覆盖的 model 不得与三个内置模型重名（内置模板不需要重复配置）。
    - 每个节点必须是 ``{"class_type": str, "inputs": dict}`` 形态。
    - 工作流必须同时含 MiniMaxH3ReferenceToVideo 与 SaveVideo 节点。
    违规一律抛 ``ComfyUIWorkflowError``（ValueError 子类）。
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ComfyUIWorkflowError("ComfyUI 工作流配置必须是数组")

    seen: set[str] = set()
    result: list[tuple[str, dict[str, dict[str, Any]]]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ComfyUIWorkflowError(f"第 {index + 1} 个 ComfyUI 工作流配置无效")
        model = str(item.get("model") or "").strip()
        if not model:
            raise ComfyUIWorkflowError(f"第 {index + 1} 个 ComfyUI 工作流缺少模型名称")
        if model in BUILTIN_COMFYUI_MODELS:
            raise ComfyUIWorkflowError(f"{model} 是内置工作流，不需要重复配置")
        if model in seen:
            raise ComfyUIWorkflowError(f"ComfyUI 工作流模型名称重复: {model}")
        seen.add(model)

        workflow = item.get("workflow")
        if not isinstance(workflow, dict) or not workflow:
            raise ComfyUIWorkflowError(f"{model} 缺少 API Format 工作流 JSON")
        for node_id, node in workflow.items():
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("class_type"), str)
                or not isinstance(node.get("inputs"), dict)
            ):
                raise ComfyUIWorkflowError(f"{model} 的节点 {node_id} 不是有效的 API Format 节点")
        if not any(node.get("class_type") == _H3_NODE_CLASS for node in workflow.values()):
            raise ComfyUIWorkflowError(f"{model} 未找到 {_H3_NODE_CLASS} 节点")
        if not any(node.get("class_type") == _SAVE_VIDEO_NODE_CLASS for node in workflow.values()):
            raise ComfyUIWorkflowError(f"{model} 未找到 {_SAVE_VIDEO_NODE_CLASS} 输出节点")
        result.append((model, workflow))
    return result


# ── 历史解析辅助 ────────────────────────────────────────────────────


def _find_output_media(entry: object) -> dict[str, Any] | None:
    """从 history 条目的 outputs 里找成片媒体（videos/video/gifs/images），命中即返回。

    只在文件名是 mp4/mov/webm/mkv 时算命中——H3 模板的成片走 SaveVideo，格式 mp4。
    """
    if not isinstance(entry, dict):
        return None
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict):
        return None
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        for key in ("videos", "video", "gifs", "images"):
            values = output.get(key)
            if not isinstance(values, list):
                continue
            for media in values:
                if not isinstance(media, dict):
                    continue
                filename = media.get("filename")
                if isinstance(filename, str) and re.search(r"\.(mp4|mov|webm|mkv)$", filename, re.I):
                    return media
    return None


def _history_error(entry: object) -> str:
    """从 history 条目取执行错误描述：execution_error 消息优先，回落到 status.error。

    消息按时间逆序取最新的 execution_error（ComfyUI 可能报多条）。exception_message
    与 exception_type 都取不到时用通用文案。
    """
    if not isinstance(entry, dict):
        return "ComfyUI 工作流执行失败"
    status = entry.get("status")
    messages = status.get("messages") if isinstance(status, dict) else None
    if isinstance(messages, list):
        for message in reversed(messages):
            if not (isinstance(message, list) and len(message) >= 2 and message[0] == "execution_error"):
                continue
            details = message[1]
            if isinstance(details, dict):
                message_text = details.get("exception_message")
                if isinstance(message_text, str) and message_text.strip():
                    return message_text.strip()
                exception_type = details.get("exception_type")
                if isinstance(exception_type, str) and exception_type.strip():
                    return exception_type.strip()
            return "ComfyUI 工作流执行失败"
    if isinstance(status, dict) and isinstance(status.get("error"), str) and status["error"].strip():
        return status["error"].strip()
    return "ComfyUI 工作流执行失败"


def _entry_is_failed(entry: object) -> bool:
    """history 条目是否以失败状态收尾（status.status_str ∈ {error, failed}）。"""
    if not isinstance(entry, dict):
        return False
    status = entry.get("status")
    if not isinstance(status, dict):
        return False
    return str(status.get("status_str") or "").lower() in ("error", "failed")


def _entry_is_done(entry: object) -> bool:
    """history 条目是否已到终态（success/completed/error/failed）。

    ComfyUI 的 /history 对仍在排队或执行中的 prompt 同样返回条目
    （``status_str`` ∈ {queued, running}，无 outputs）；只有终态才视为轮询完成。
    """
    if not isinstance(entry, dict):
        return False
    status = entry.get("status")
    if not isinstance(status, dict):
        return False
    status_str = str(status.get("status_str") or "").lower()
    return status_str in ("success", "completed", "error", "failed")


def _mime_for_path(path: Path) -> str:
    """按扩展名回填素材 MIME（ComfyUI 上传表单用）；未知扩展名回 application/octet-stream。"""
    known: dict[str, str] = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".m4v": "video/x-m4v",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
    }
    return known.get(path.suffix.lower(), "application/octet-stream")


# ── 出站 HTTP 交互 ─────────────────────────────────────────────────


class ComfyUIVideoBackend(ProviderJobIdPersistenceMixin):
    """ComfyUI 视频后端（异步 submit/poll，参考素材直传，支持 resume）。

    ``configured_workflows`` 是用户覆盖工作流列表（[{model, workflow}, ...]），
    校验在构造期完成（fail-fast）；模型不在三个内置模型里时按配置查找覆盖模板。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = _POLL_HTTP_TIMEOUT_SECONDS,
        configured_workflows: list[Mapping[str, Any]] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("ComfyUIVideoBackend 需要 base_url")
        self._api_key = api_key or ""
        self._base_url = base_url.rstrip("/")
        self._model = model or BUILTIN_COMFYUI_MODELS[0]
        self._http_timeout = http_timeout
        # 校验在构造期完成：配置非法立刻暴露，不拖到提交前才炸。
        self._configured_models: dict[str, dict[str, dict[str, Any]]] = {
            model_id: workflow for model_id, workflow in validate_comfyui_workflows(configured_workflows)
        }

    @property
    def name(self) -> str:
        return PROVIDER_COMFYUI

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 backend（无需 base_url）。

        MiniMax H3 单机工作流三条执行路径：
        - 文生视频（无参考图）：模板不带首帧图，``text_to_video=True``；
        - 首帧 / 多图参考：模板把参考素材统一挂在 ``ref_images`` 输入下（1–9 张），
          首帧图按参考图并入同一组，故 ``first_frame=True`` 且与参考图叠加不冲突；
        - 尾帧：模板无尾帧输入，``last_frame=False``。
        参考音频按音轨参考（``ref_audios``）直传，上限 3 段、无总时长聚合约束声明。
        成片由模板 CreateVideo 节点合成，恒带音轨且请求体无音轨开关，``audio_track``
        取 ``ALWAYS_ON``（与 MiniMax H3 API 端点同口径，见 minimax.py 的 H3 分支）。
        """
        return VideoCapabilities(
            first_frame=True,
            last_frame=False,
            max_reference_images=_MAX_REFERENCE_IMAGES,
            reference_audio_mode=ReferenceAudioMode.DIRECT,
            max_reference_audio_count=_MAX_REFERENCE_AUDIOS,
            audio_track=VideoAudioMode.ALWAYS_ON,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        images, reference_audios = self._prepare_reference_media(request)
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            image_files = await self._upload_group(client, images, request.task_id, "image")
            audio_files = await self._upload_group(client, reference_audios, request.task_id, "audio")

            workflow = self._build_workflow(request, image_files, audio_files)
            logger.info(
                "调用 %s 视频 API model=%s body=%s",
                self.name,
                self._model,
                format_kwargs_for_log(self._safe_workflow_for_log(workflow)),
            )
            prompt_id = await self._submit_prompt(client, workflow, request.task_id)
            logger.info("ComfyUI 任务已提交: prompt_id=%s model=%s", prompt_id, self._model)
            # 一并写回实际提交域名：续跑据此回放原主机轮询 /view 下载，用户在途改 base_url
            # 后按新域名查旧 prompt 会 404，被误判成历史过期（与 dashscope 同口径）。
            await self._persist_provider_job_id(request, prompt_id, provider=PROVIDER_COMFYUI, endpoint=self._base_url)
            return await self._poll_and_build(client, prompt_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 ComfyUI prompt：仅轮询 + 下载，不重新提交（ADR 0007）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    # ── 参考素材准备与上传 ──────────────────────────────────────────

    def _prepare_reference_media(self, request: VideoGenerationRequest) -> tuple[list[Path], list[Path]]:
        """请求素材归一化 + 越界/缺失校验（fail-loud），返回 (图片, 音频) 两组。

        - 首帧图按参考图并入同一组（模板 ref_images 输入合并承载首帧与多图参考）。
        - 参考音频越界抛 ``video_reference_audio_exceeded``（数值上限由
          ``_MAX_REFERENCE_AUDIOS`` 给出）。
        - 任一素材缺失/不可读 fail-loud，不静默丢弃后照常计费。
        """
        self._reject_out_of_range_duration(request.duration_seconds)
        images = [p for v in (request.reference_images or []) if (p := self._existing_path(v)) is not None]
        start = self._existing_path(request.start_image)
        if start is not None:
            images.insert(0, start)
        reference_audios = [
            p for v in (request.reference_audio_files or []) if (p := self._existing_path(v)) is not None
        ]

        if len(images) > _MAX_REFERENCE_IMAGES:
            raise VideoCapabilityError(
                "video_reference_images_exceeded",
                model=self._model,
                count=len(images),
                limit=_MAX_REFERENCE_IMAGES,
            )
        if len(reference_audios) > _MAX_REFERENCE_AUDIOS:
            raise VideoCapabilityError(
                "video_reference_audio_exceeded",
                model=self._model,
                count=len(reference_audios),
                limit=_MAX_REFERENCE_AUDIOS,
            )

        missing_images = [p for p in images if not p.is_file()]
        if missing_images:
            raise VideoCapabilityError(
                "video_reference_images_unreadable",
                model=self._model,
                names=", ".join(p.name or str(p) for p in missing_images),
            )
        missing_audios = [p for p in reference_audios if not p.is_file()]
        if missing_audios:
            raise VideoCapabilityError(
                "video_reference_audio_unreadable",
                model=self._model,
                names=", ".join(p.name or str(p) for p in missing_audios),
            )
        return images, reference_audios

    async def _upload_group(
        self, client: httpx.AsyncClient, sources: list[Path], task_id: str | None, label: str
    ) -> list[str]:
        """上传一组素材到 ComfyUI /upload/image，返回 ComfyUI 侧路径列表。

        素材按 ``label-{i}`` 命名（图片/视频/音频分桶），``subfolder`` 带任务 id 区分
        任务目录，避免跨任务覆盖同名文件。ComfyUI 的 /upload/image 同时接收图片/视频/
        音频（multipart 表单 + ``type=input``）；返回体携带 ``subfolder`` 时以
        ``subfolder/name`` 作为 Load* 节点的输入值。
        """
        subfolder = f"arcreel/task-{task_id or 'local'}"
        uploaded: list[str] = []
        for index, path in enumerate(sources):
            filename = f"{label}-{index + 1}{path.suffix or ''}"
            # 素材可能数 MB，读盘 offload 到线程避免阻塞共享 worker 事件循环。
            data_bytes = await asyncio.to_thread(path.read_bytes)
            files = {"image": (filename, data_bytes, _mime_for_path(path))}
            data = {"type": "input", "subfolder": subfolder, "overwrite": "true"}
            resp = await client.post(
                f"{self._base_url}/upload/image",
                files=files,
                data=data,
                headers=self._auth_headers(),
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"上传 {label} 参考素材到 ComfyUI 失败: HTTP {resp.status_code} {resp.text[:200]}")
            body = resp.json()
            name = body.get("name", filename)
            media_subfolder = str(body.get("subfolder", "") or "").rstrip("/")
            uploaded.append(f"{media_subfolder}/{name}" if media_subfolder else name)
        return uploaded

    def _build_workflow(
        self,
        request: VideoGenerationRequest,
        image_files: list[str],
        audio_files: list[str],
    ) -> dict[str, dict[str, Any]]:
        """选模板（内置 / 覆盖）并参数化，返回 /prompt 的 API-format 工作流。

        非内置模型未配置覆盖模板时抛错（ComfyUI 模型没有绑定工作流）。
        """
        kwargs = {
            "prompt": request.prompt,
            "image_files": image_files,
            "video_files": [],
            "audio_files": audio_files,
            "duration": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "seed": request.seed,
            "filename_prefix": f"video/MiniMax_H3/arcreel_{request.task_id or 'local'}",
        }
        builder = _BUILTIN_MODEL_BUILDERS.get(self._model)
        if builder is not None:
            return builder(**kwargs)
        configured = self._configured_models.get(self._model)
        if configured is None:
            raise ComfyUIWorkflowError(f"ComfyUI 模型 {self._model} 没有绑定工作流")
        return build_configured_minimax_h3_workflow(configured, **kwargs)

    # ── HTTP submit / poll / download ─────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _submit_prompt(self, client: httpx.AsyncClient, workflow: dict, task_id: str | None) -> str:
        # 非幂等的「提交 + 计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError
        # 终态失败，避免重试重复建任务 + 重复计费；>=400 抛 HTTPStatusError 交
        # should_retry_submit 按状态码分流（5xx/408/429 重试，确定性 4xx 快失败）。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}/prompt",
                json={"prompt": workflow, "client_id": f"arcreel-{task_id or 'local'}"},
                headers=self._json_headers(),
                timeout=_SUBMIT_TIMEOUT_SECONDS,
            ),
            provider=PROVIDER_COMFYUI,
        )
        body = resp.json()
        prompt_id = body.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id: {body}")
        return prompt_id

    async def _poll_once(self, client: httpx.AsyncClient, prompt_id: str, base_url: str) -> dict:
        resp = await client.get(
            f"{base_url}/history/{prompt_id}",
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """下载成片 URL（幂等 GET），独立的下载重试范围，不回退到重跑生成 POST。"""
        await download_video(video_url, output_path)

    def _view_url(self, media: dict[str, Any], base_url: str) -> str:
        """按 history 输出媒体条目拼 /view 下载 URL（域名按提交时回放）。"""
        query = urlencode(
            {
                "filename": str(media.get("filename", "")),
                "subfolder": str(media.get("subfolder", "")),
                "type": str(media.get("type", "output")),
            }
        )
        return f"{base_url}/view?{query}"

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        prompt_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        # 续跑轮询/下载回放提交时的域名：prompt 只在创建它的主机上可查，用户在途改 base_url
        # 后按当下配置解析出的新域名去轮旧任务会 404，被下方的 404 分支误判成过期。
        base_url = request.submitted_base_url or self._base_url

        # resume 路径下 404 直接转 ResumeExpiredError：should_retry_poll 把轮询 404 当
        # 「短暂未就绪」重试，对已过期的 resume 任务会一直重到超时、永不落终态，故在此
        # 一击转终态异常。非 resume 的 4xx 原样抛出，交 should_retry_poll 按 status_code 分流。
        async def _gated_poll() -> dict:
            try:
                return await self._poll_once(client, prompt_id, base_url)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=prompt_id, provider=PROVIDER_COMFYUI) from exc
                raise

        def _entry_state(state: dict) -> dict | None:
            entry = state.get(prompt_id)
            if isinstance(entry, dict):
                return entry
            # 容忍回包不带 prompt_id 键（取首条）——与参考实现的 parsePollResponse 同口径。
            values = list(state.values())
            return values[0] if values and isinstance(values[0], dict) else None

        def _is_failed(state: dict) -> str | None:
            entry = _entry_state(state)
            if entry is None or not _entry_is_failed(entry):
                return None
            return _history_error(entry)

        final = await poll_with_retry(
            poll_fn=_gated_poll,
            is_done=lambda state: _entry_is_done(_entry_state(state)),
            is_failed=_is_failed,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label="ComfyUI",
            on_progress=lambda v, elapsed: logger.info("ComfyUI 视频生成中... elapsed=%ds", int(elapsed)),
        )

        entry = _entry_state(final)
        media = _find_output_media(entry)
        if media is None:
            raise RuntimeError("ComfyUI 已完成，但历史记录中没有找到视频输出")
        video_url = self._view_url(media, base_url)
        await self._download_with_retry(video_url, request.output_path)
        logger.info("ComfyUI 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_COMFYUI,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=video_url,
            task_id=prompt_id,
            seed=request.seed,
            # 模板由 CreateVideo 合成音轨，成片恒有声且请求体没有音轨开关，
            # 与 video_capabilities 的 audio_track=ALWAYS_ON 同口径。
            generate_audio=True,
        )

    # ── 请求辅助 ─────────────────────────────────────────────────────

    def _reject_out_of_range_duration(self, duration_seconds: int) -> None:
        """时长越界 [_MIN, _MAX] 时 fail-loud；模板会截断，避免静默截帧 + 错记计费时长。"""
        if not _MIN_DURATION_SECONDS <= duration_seconds <= _MAX_DURATION_SECONDS:
            raise VideoCapabilityError(
                "video_duration_not_supported",
                model=self._model,
                duration=duration_seconds,
                supported=f"{_MIN_DURATION_SECONDS}-{_MAX_DURATION_SECONDS}",
            )

    @staticmethod
    def _existing_path(value: str | Path | None) -> Path | None:
        """把首帧/参考图入参归一化为 Path；未声明该槽位（None/空/``Path("")``）返回 None。

        文件存在性不在此判定——声明了却读不到要 fail-loud 报出具体槽位。
        """
        if value is None:
            return None
        text = str(value)
        if not text or text == ".":
            return None
        return Path(text)

    @staticmethod
    def _safe_workflow_for_log(workflow: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """工作流的日志安全视图：只统计节点数与参考素材引用数，不回显 prompt 全文。

        工作流里的 prompt 是用户文本、Load* 节点携带的是服务端文件名（无 base64），
        但整份 JSON 体积大且含 prompt，按计数折叠入日志。
        """
        h3_node = next((node for node in workflow.values() if node.get("class_type") == _H3_NODE_CLASS), None)
        ref_count = (
            sum(1 for key in _node_inputs(h3_node) if _REF_INPUT_PREFIX_RE.match(key)) if h3_node is not None else 0
        )
        return {
            "node_count": len(workflow),
            "ref_inputs": ref_count,
            "class_types": sorted(
                class_type
                for node in workflow.values()
                if isinstance(node.get("class_type"), str) and (class_type := node["class_type"])
            ),
        }

    def _auth_headers(self) -> dict[str, str]:
        """ComfyUI 的鉴权头（可选）：配置了 api_key 才带 Bearer，否则空头。"""
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _json_headers(self) -> dict[str, str]:
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"
        return headers
