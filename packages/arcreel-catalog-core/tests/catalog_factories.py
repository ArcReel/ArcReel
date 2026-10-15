"""构造一份合法的目录数据，覆盖四种媒体类型、两栖模型与价目表的各种形状。

每次调用返回新的副本，用例在副本上改出要验证的情形。
"""

from __future__ import annotations

from typing import Any

DATASET_VERSION = "2026-10-14T08:00:00Z"


def text_entry() -> dict[str, Any]:
    return {
        "maker": "google",
        "name": "gemini-3.1-pro",
        "media_type": "text",
        "display_name": "Gemini 3.1 Pro",
        "aliases": ["gemini-3.1-pro-latest"],
        "context_length": 1048576,
        "facts": {"image_input": True, "structured_output": True, "max_output_tokens": 65536},
        "price": {
            "currency": "USD",
            "meters": [
                {
                    "meter": "uncached_input_tokens",
                    "dims": ["context_tier"],
                    "rows": [
                        {"when": {"context_tier": 0}, "price": 2.0},
                        {"when": {"context_tier": 200000}, "price": 4.0},
                    ],
                },
                {
                    "meter": "cache_read_tokens",
                    "dims": ["context_tier"],
                    "rows": [
                        {"when": {"context_tier": 0}, "price": 0.2},
                        {"when": {"context_tier": 200000}, "price": 0.4},
                    ],
                },
                {"meter": "output_tokens", "dims": [], "rows": [{"when": {}, "price": 12.0}]},
            ],
        },
    }


def video_entry() -> dict[str, Any]:
    return {
        "maker": "kuaishou",
        "name": "kling-v3-omni",
        "media_type": "video",
        "display_name": "可灵 V3 Omni",
        "facts": {
            "durations": [5, 10],
            "resolutions": ["720p", "1080p"],
            "duration_constraints": {"by_resolution": {"1080p": [5]}, "with_reference_images": [5]},
        },
        "price": {
            "currency": "CNY",
            "meters": [
                {
                    "meter": "output_seconds",
                    "dims": ["resolution", "audio"],
                    "rows": [
                        {"when": {"resolution": "720p", "audio": False}, "price": 0.6},
                        {"when": {"resolution": "720p", "audio": True}, "price": 0.9},
                        {"when": {"resolution": "1080p"}, "price": 1.2},
                    ],
                    "defaults": {"resolution": "720p"},
                },
                {"meter": "input_images", "dims": [], "rows": [{"when": {}, "price": 0.1}], "free_allowance": 2},
            ],
        },
    }


def amphibious_image_entry() -> dict[str, Any]:
    """与 ``video_entry`` 同名的图片条目：两栖模型的另一半。"""
    return {
        "maker": "kuaishou",
        "name": "kling-v3-omni",
        "media_type": "image",
        "display_name": "可灵 V3 Omni（图片）",
        "facts": {"text_to_image": True, "image_to_image": True, "resolutions": ["1K", "2K"]},
        "price": {
            "currency": "CNY",
            "meters": [
                {
                    "meter": "output_images",
                    "dims": ["resolution"],
                    "rows": [{"when": {"resolution": "2K"}, "price": 0.2}],
                }
            ],
        },
    }


def audio_entry() -> dict[str, Any]:
    return {
        "maker": "alibaba",
        "name": "qwen3-tts-flash",
        "media_type": "audio",
        "display_name": "Qwen3 TTS Flash",
        "price": {
            "currency": "CNY",
            "meters": [{"meter": "characters", "dims": [], "rows": [{"when": {}, "price": 0.8}], "cjk_double": True}],
        },
    }


def catalog() -> dict[str, Any]:
    return {
        "dataset_version": DATASET_VERSION,
        "entries": [text_entry(), video_entry(), amphibious_image_entry(), audio_entry()],
        "builtin_providers": {
            "gemini-aistudio": {
                "models": [{"api_model_name": "gemini-3.1-pro", "entry": "google/gemini-3.1-pro"}],
                "defaults": {"text": "gemini-3.1-pro"},
            },
            "kling": {
                "models": [
                    {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "video"},
                    {
                        "api_model_name": "kling-v3-omni",
                        "entry": "kuaishou/kling-v3-omni",
                        "media_type": "image",
                        "multiplier": 1.0,
                        "min_app_version": "0.34.0",
                    },
                ],
                "defaults": {"video": "kling-v3-omni", "image": "kling-v3-omni"},
            },
            "dashscope": {
                "models": [{"api_model_name": "qwen3-tts-flash", "entry": "alibaba/qwen3-tts-flash"}],
                "defaults": {"audio": "qwen3-tts-flash"},
            },
        },
    }
