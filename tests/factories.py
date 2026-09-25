"""Test data factories — reduce boilerplate when constructing common objects."""

from __future__ import annotations

import subprocess
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from server.agent_runtime.models import SessionMeta


def make_translator(locale: str = "zh") -> Callable[..., str]:
    """Create a translator function bound to a fixed locale for testing."""
    from lib.i18n import _ as i18n_translate

    def translate(key: str, **kwargs) -> str:
        return i18n_translate(key, locale=locale, **kwargs)

    return translate


def wav_bytes(duration_seconds: float, sample_rate: int = 8000) -> bytes:
    """纯 stdlib 生成 wav 字节（不依赖 ffmpeg），供不要求真实音频编解码的用例使用。"""
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(duration_seconds * sample_rate))
    return buf.getvalue()


def make_test_video(path: Path, *, duration_sec: float = 1.0, fps: int = 30) -> None:
    """使用 ffmpeg 生成极短测试视频（64x64 像素）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=black:size=64x64:duration={duration_sec}:rate={fps}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def make_test_video_with_audio_tail(
    path: Path,
    *,
    video_duration_sec: float = 1.0,
    audio_duration_sec: float = 1.5,
    fps: int = 30,
) -> None:
    """生成音轨/容器尾部比视频轨更长的极短 MP4。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=black:size=64x64:duration={video_duration_sec}:rate={fps}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={audio_duration_sec}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def make_session_meta(**overrides) -> SessionMeta:
    """Build a SessionMeta with sensible defaults.

    Any keyword argument overrides the corresponding default field.
    """
    defaults = {
        "id": "session-1",
        "project_name": "demo",
        "title": "demo",
        "status": "running",
        "created_at": datetime(2026, 2, 9, 8, 0, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 2, 9, 8, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return SessionMeta(**defaults)


def make_task_params(**overrides) -> dict:
    """Build a dict of parameters suitable for ``GenerationQueue.enqueue_task()``.

    Any keyword argument overrides the corresponding default.
    """
    defaults = {
        "project_name": "demo",
        "task_type": "storyboard",
        "media_type": "image",
        "resource_id": "E1S01",
        "payload": {"prompt": "test"},
        "script_file": "episode_01.json",
        "source": "webui",
    }
    defaults.update(overrides)
    return defaults


def make_sdk_transcript_entry(
    uuid: str, parent: str | None, entry_type: str, session_id: str, text: str
) -> dict[str, Any]:
    """一条 SDK 形态的 transcript 条目，供前缀分叉相关的测试搭建原会话历史。"""
    return {
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "type": entry_type,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": entry_type, "content": text},
    }


def make_transcript_entry(
    msg_type: str = "assistant",
    text: str = "hello",
    *,
    uuid: str = "msg-1",
    tool_use_id: str | None = None,
    tool_name: str | None = None,
    **extra,
) -> dict:
    """Build a single transcript JSONL entry dict.

    ``msg_type`` is one of ``"user"``, ``"assistant"``, ``"result"``.
    """
    if msg_type == "user":
        content = text
    elif msg_type == "result":
        entry: dict = {
            "type": "result",
            "subtype": extra.get("subtype", "success"),
            "is_error": extra.get("is_error", False),
            "uuid": uuid,
        }
        entry.update(extra)
        return entry
    else:
        if tool_use_id:
            content = [{"type": "tool_use", "id": tool_use_id, "name": tool_name or "Tool", "input": {}}]
        else:
            content = [{"type": "text", "text": text}]

    entry = {"type": msg_type, "message": {"content": content}, "uuid": uuid}
    entry.update(extra)
    return entry


def custom_endpoint_definition(**overrides: Any) -> dict[str, Any]:
    """最小可用的声明式调用端点定义：单张首帧、提交 + 轮询、扁平取值，校验零错误零警告。

    用例就地改出反例或补上可选构造；``overrides`` 覆盖顶层键（如换 ``meta`` 造重复血统）。
    """
    definition: dict[str, Any] = {
        "kind": "declarative",
        "schema_version": "1.1.0",
        "meta": {"name": "示例端点", "author": "ArcReel", "version": "0.1.0"},
        "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
        "inputs": {"first_frame": {"source": "start_image", "encoding": "data_uri"}},
        "enum_maps": {"duration": {"5": 5, "10": 10}},
        "submit": {
            "method": "POST",
            "url": "{{ base_url }}/v1/video/create",
            "body": {
                "model": "{{ model }}",
                "prompt": "{{ prompt }}",
                "image": "{{ inputs.first_frame }}",
                "duration": "{{ duration }}",
            },
            "extract": {"task_id": ["$.task_id"], "error": ["$.error.message"]},
        },
        "poll": {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/fetch/{{ task_id }}",
            "extract": {"status": ["$.status"], "video_url": ["$.video_url"], "error": ["$.error"]},
        },
        "status_map": {"pending": "queued", "processing": "running", "completed": "succeeded", "failed": "failed"},
        "capabilities": {"first_frame": True},
    }
    definition.update(overrides)
    return definition


def comfyui_api_workflow() -> dict[str, Any]:
    """最小可用的 ComfyUI「Export (API)」导出物：文生视频一条链路，节点 id 与真实导出同为数字串。

    每类字段各有一例：字面值（``6.text``）、连线（``6.clip``）、可绑的数值（``5.width``、``3.seed``）、
    产物节点（``9``）。用例就地改出反例。
    """
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 123456,
                "steps": 20,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
            "_meta": {"title": "KSampler"},
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "wan_2_2.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 480, "batch_size": 1}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "一只猫", "clip": ["4", 1]},
            "_meta": {"title": "正向"},
        },
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}, "_meta": {"title": "负向"}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveVideo", "inputs": {"images": ["8", 0], "fps": 16}, "_meta": {"title": "存视频"}},
    }


def comfyui_endpoint_definition(**overrides: Any) -> dict[str, Any]:
    """最小可用的 ComfyUI 端点定义：绑定齐备、校验零错误。

    ``overrides`` 覆盖顶层键；改 ``bindings`` 或 ``media_type`` 即可造出各类反例。
    ``media_type="image"`` 且未自带 ``bindings`` 时不含 ``fps`` 绑定：图像端点没有帧率这一维。
    """
    definition: dict[str, Any] = {
        "kind": "comfyui",
        "schema_version": "1.0.0",
        "meta": {"name": "示例 ComfyUI 端点", "author": "ArcReel", "version": "0.1.0"},
        "media_type": "video",
        "workflow": comfyui_api_workflow(),
        "bindings": {
            "prompt": [{"node": "6", "input": "text", "class_type": "CLIPTextEncode", "title": "正向"}],
            "negative_prompt": [{"node": "7", "input": "text", "class_type": "CLIPTextEncode", "title": "负向"}],
            "width": [{"node": "5", "input": "width", "class_type": "EmptyLatentImage", "step": 16}],
            "height": [{"node": "5", "input": "height", "class_type": "EmptyLatentImage", "step": 16}],
            "seed": [{"node": "3", "input": "seed", "class_type": "KSampler", "policy": "random"}],
            "fps": [{"node": "9", "input": "fps", "class_type": "SaveVideo", "direction": "read"}],
            "output": [{"node": "9", "class_type": "SaveVideo", "title": "存视频"}],
        },
    }
    definition.update(overrides)
    if definition["media_type"] == "image" and "bindings" not in overrides:
        del definition["bindings"]["fps"]
    return definition


async def seed_endpoint_fixed_video_model(db_factory, *, reference_images: bool = False) -> str:
    """在测试库里建一个时长由端点固定的 ComfyUI 视频模型（``supported_durations`` 为空集），返回 ``provider/model``。

    端点绑定首帧输入，``reference_images=True`` 时再绑定参考图输入，使该模型同时满足 i2v 与 r2v 桶的能力闸。
    """
    from lib.custom_provider import make_provider_id
    from lib.db.models.custom_endpoint import CustomEndpoint
    from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

    image_binding = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
    definition = comfyui_endpoint_definition()
    definition["bindings"]["start_image"] = image_binding
    if reference_images:
        definition["bindings"]["reference_images"] = image_binding
    async with db_factory() as session:
        endpoint = CustomEndpoint(
            definition=definition, kind="comfyui", schema_version="1.0.0", media_type="video", display_name="ComfyUI"
        )
        provider = CustomProvider(
            display_name="Comfy", discovery_format="comfyui", base_url="http://comfy.test:8188", api_key=""
        )
        session.add_all([endpoint, provider])
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="wan-workflow",
                display_name="Workflow",
                endpoint=f"ce-{endpoint.id}",
                supported_durations="[]",
                is_default=True,
                is_enabled=True,
            )
        )
        await session.commit()
    return f"{make_provider_id(provider.id)}/wan-workflow"


def make_video_request_facts(**overrides: Any):
    """分镜路线、Veo 3.1、未设分辨率的视频请求事实；消费方测试按需覆盖字段，不手搭能力 dict。"""
    from lib.generation.video_request_facts import VideoRequestFacts

    fields: dict[str, Any] = {
        "route": "storyboard",
        "generation_type": "i2v",
        "provider_id": "gemini-aistudio",
        "model_id": "veo-3.1-generate-preview",
        "resolution": None,
        "supported_durations": (4, 6, 8),
        "allowed_durations": (4, 6, 8),
        "excluded_durations": (),
        "duration_endpoint_fixed": False,
        "requested_generate_audio": True,
        "generate_audio": True,
        "has_audio_track": True,
        "audio_switch_controllable": False,
        "max_reference_images": 3,
        "text_to_video": True,
        "first_frame": True,
        "voice_consistency": "soft",
        "max_reference_audio_count": 0,
        "reference_audio_per_image": False,
    }
    fields.update(overrides)
    return VideoRequestFacts(**fields)


def activate_reference_project(project_dir: Path, project: dict[str, Any]) -> dict[str, Any]:
    """把 v7 形态的参考生视频项目写盘并迁到当前 schema，返回迁移后的项目字典。

    迁移时已登记路径、带 ``description`` 且文件在盘上的资产图由补录认领进产物清单；此后再登记的
    资产图即使文件在盘上，清单也不认领它。``project`` 的键覆盖缺省骨架；``scripts/episode_1.json`` 不存在时写一份空单元剧本。
    """
    import json

    from lib.project.project_migrations.runner import migrate_project_dir
    from lib.project.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8

    payload: dict[str, Any] = {
        "schema_version": 7,
        "title": "T",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
        **project,
    }
    (project_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (project_dir / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    script_file = project_dir / "scripts" / "episode_1.json"
    if not script_file.exists():
        script_file.write_text(
            json.dumps({"episode": 1, "generation_mode": "reference_video", "video_units": []}, ensure_ascii=False),
            encoding="utf-8",
        )
    migrate_v7_to_v8(project_dir)
    migrate_project_dir(project_dir)
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
