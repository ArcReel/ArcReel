"""ComfyUI workflow JSON builders.

Each function returns a dict that can be posted directly to ComfyUI's /prompt endpoint.
Node IDs are arbitrary integers; links are [from_node_id, output_index].
"""

from __future__ import annotations

import random
from typing import Any


def _seed() -> int:
    return random.randint(0, 2**32 - 1)


# ── Image: Text-to-Image ─────────────────────────────────────────


# Default settings (used when DB has no override)
DEFAULT_COMFYUI_SETTINGS: dict[str, Any] = {
    "sampler": "euler",
    "steps": 20,
    "cfg": 7.0,
    "negative_prompt": "",
    "clip_skip": None,
}


def get_model_preset(
    checkpoint_name: str,
    db_settings: dict | None = None,
    project_overrides: dict | None = None,
) -> dict[str, Any]:
    """Get settings for a model with priority: project > DB > defaults.

    Args:
        checkpoint_name: The model file name
        db_settings: Settings from custom_provider_model table (comfyui_* fields)
        project_overrides: Settings from project.json (comfyui_overrides)
    """
    result = DEFAULT_COMFYUI_SETTINGS.copy()

    # Apply DB settings (global model settings)
    if db_settings:
        if db_settings.get("sampler"):
            result["sampler"] = db_settings["sampler"]
        if db_settings.get("steps") is not None:
            result["steps"] = db_settings["steps"]
        if db_settings.get("cfg") is not None:
            result["cfg"] = db_settings["cfg"]
        if db_settings.get("negative_prompt"):
            result["negative_prompt"] = db_settings["negative_prompt"]
        if db_settings.get("clip_skip") is not None:
            result["clip_skip"] = db_settings["clip_skip"]

    # Apply project overrides (highest priority)
    if project_overrides:
        if project_overrides.get("sampler"):
            result["sampler"] = project_overrides["sampler"]
        if project_overrides.get("steps") is not None:
            result["steps"] = project_overrides["steps"]
        if project_overrides.get("cfg") is not None:
            result["cfg"] = project_overrides["cfg"]
        if project_overrides.get("negative_prompt"):
            result["negative_prompt"] = project_overrides["negative_prompt"]
        if project_overrides.get("clip_skip") is not None:
            result["clip_skip"] = project_overrides["clip_skip"]

    return result


def build_t2i_workflow(
    *,
    checkpoint: str,
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 768,
    seed: int | None = None,
    steps: int = 20,
    cfg: float = 7.0,
    sampler: str = "euler",
    scheduler: str = "normal",
    denoise: float = 1.0,
    batch_size: int = 1,
    checkpoint_name: str | None = None,
    clip_skip: int | None = None,
) -> dict[str, Any]:
    """Build a basic txt2img workflow (LoadCheckpoint → CLIP → KSampler → VAE Decode → Save)."""

    ckpt_name = checkpoint_name or checkpoint
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": batch_size},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": seed if seed is not None else _seed(),
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": denoise,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": "arcreel"},
        },
    }


# ── Image: Image-to-Image ────────────────────────────────────────


def build_i2i_workflow(
    *,
    checkpoint: str,
    image_path: str,
    prompt: str,
    negative_prompt: str = "",
    denoise: float = 0.7,
    seed: int | None = None,
    steps: int = 20,
    cfg: float = 7.0,
    checkpoint_name: str | None = None,
) -> dict[str, Any]:
    """Build an img2img workflow (LoadImage → VAE Encode → KSampler → VAE Decode → Save)."""
    ckpt_name = checkpoint_name or checkpoint
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "LoadImage",
            "inputs": {"image": image_path},
        },
        "5": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["5", 0],
                "seed": seed if seed is not None else _seed(),
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": denoise,
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {"images": ["7", 0], "filename_prefix": "arcreel_i2i"},
        },
    }


# ── Video: Text-to-Video (AnimateDiff / generic) ────────────────


def build_t2v_workflow(
    *,
    checkpoint: str,
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 768,
    frames: int = 16,
    seed: int | None = None,
    steps: int = 20,
    cfg: float = 7.0,
    fps: int = 8,
    checkpoint_name: str | None = None,
) -> dict[str, Any]:
    """Build a text-to-video workflow.

    Uses EmptyLatentImage with batch_size=frames to simulate video frames,
    then saves as a video via VHS_VideoCombine (if available) or as image sequence.
    Falls back to basic KSampler with large batch for AnimateDiff-style models.
    """
    ckpt_name = checkpoint_name or checkpoint
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": frames},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": seed if seed is not None else _seed(),
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": "arcreel_video"},
        },
    }


# ── Audio: Text-to-Audio ─────────────────────────────────────────


def build_audio_workflow(
    *,
    model: str,
    prompt: str,
    duration: float = 5.0,
    negative_prompt: str = "",
    seed: int | None = None,
    steps: int = 20,
    cfg: float = 7.0,
) -> dict[str, Any]:
    """Build a text-to-audio workflow using EmptyLatentAudio + KSampler.

    This works with StableAudio and ACEStep models loaded as checkpoints.
    """
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentAudio",
            "inputs": {"seconds": duration, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": seed if seed is not None else _seed(),
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveAudio",
            "inputs": {"audio": ["6", 0], "filename_prefix": "arcreel_audio"},
        },
    }


# ── Model type inference ─────────────────────────────────────────

import re

_VIDEO_MODEL_PATTERN = re.compile(
    r"wan|cogvideo|hunyuan.?video|animate.?diff|svd|sv3d|cosmos|ltx|mochi|stable.?video",
    re.IGNORECASE,
)
_AUDIO_MODEL_PATTERN = re.compile(
    r"stable.?audio|ace.?step|bark|musicgen",
    re.IGNORECASE,
)


def infer_model_type(model_id: str) -> str:
    """Infer 'image', 'video', or 'audio' from model filename."""
    if _VIDEO_MODEL_PATTERN.search(model_id):
        return "video"
    if _AUDIO_MODEL_PATTERN.search(model_id):
        return "audio"
    return "image"
