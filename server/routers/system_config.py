"""
System configuration APIs.

Provides a WebUI-managed global system configuration store that overrides .env
defaults and takes effect immediately without restarting the server.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.requests import Request

from lib import PROJECT_ROOT
from lib.cost_calculator import cost_calculator
from lib.gemini_client import refresh_shared_rate_limiter
from lib.system_config import (
    get_system_config_manager,
    parse_bool_env,
    resolve_vertex_credentials_path,
)

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_VERTEX_CREDENTIALS_BYTES = 1024 * 1024  # 1 MiB


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _mask_secret(value: str) -> str:
    raw = value.strip()
    if len(raw) <= 8:
        return "********"
    return f"{raw[:4]}…{raw[-4:]}"


def _effective_backend(primary: str) -> str:
    return (
        (os.environ.get(primary) or "").strip().lower()
        or (os.environ.get("GEMINI_BACKEND") or "").strip().lower()
        or "aistudio"
    )


def _effective_image_backend() -> str:
    return _effective_backend("GEMINI_IMAGE_BACKEND")


def _effective_video_backend() -> str:
    return _effective_backend("GEMINI_VIDEO_BACKEND")


def _resolve_vertex_credentials_path(project_root: Path) -> Optional[Path]:
    return resolve_vertex_credentials_path(project_root)


def _vertex_credentials_status(project_root: Path) -> dict[str, Any]:
    path = _resolve_vertex_credentials_path(project_root)
    if path is None or not path.exists():
        return {"is_set": False, "filename": None, "project_id": None}
    project_id = None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            project_id = payload.get("project_id")
    except Exception:
        project_id = None
    return {"is_set": True, "filename": path.name, "project_id": project_id}


def _has_vertex_credentials(project_root: Path) -> bool:
    return bool(_resolve_vertex_credentials_path(project_root))

def _secret_view(
    overrides: dict[str, Any],
    override_key: str,
    env_key: str,
) -> dict[str, Any]:
    env_value = os.environ.get(env_key)
    is_set = bool(env_value and env_value.strip())
    if override_key in overrides and not isinstance(overrides.get(override_key), type(None)):
        source: Literal["override", "env", "unset"] = "override"
    elif is_set:
        source = "env"
    else:
        source = "unset"
    return {
        "is_set": is_set,
        "masked": _mask_secret(env_value) if is_set else None,
        "source": source,
    }


def _options_payload() -> dict[str, list[str]]:
    return {
        "image_models": list(cost_calculator.IMAGE_COST.keys()),
        "video_models": list(cost_calculator.VIDEO_COST.keys()),
    }


def _config_payload(project_root: Path) -> dict[str, Any]:
    overrides = get_system_config_manager(project_root).read_overrides()

    image_backend = _effective_image_backend()
    video_backend = _effective_video_backend()

    image_model = os.environ.get("GEMINI_IMAGE_MODEL", cost_calculator.DEFAULT_IMAGE_MODEL)
    video_model = os.environ.get("GEMINI_VIDEO_MODEL", cost_calculator.DEFAULT_VIDEO_MODEL)

    configured_audio = parse_bool_env(os.environ.get("GEMINI_VIDEO_GENERATE_AUDIO"), True)
    audio_editable = video_backend == "vertex"
    audio_effective = configured_audio if audio_editable else True

    return {
        "image_backend": image_backend,
        "video_backend": video_backend,
        "image_model": image_model,
        "video_model": video_model,
        "video_generate_audio": configured_audio,
        "video_generate_audio_effective": audio_effective,
        "video_generate_audio_editable": audio_editable,
        "rate_limit": {
            "image_rpm": _read_int_env("GEMINI_IMAGE_RPM", 15),
            "video_rpm": _read_int_env("GEMINI_VIDEO_RPM", 10),
            "request_gap_seconds": _read_float_env("GEMINI_REQUEST_GAP", 3.1),
        },
        "performance": {
            "storyboard_max_workers": _read_int_env("STORYBOARD_MAX_WORKERS", 3),
            "video_max_workers": _read_int_env("VIDEO_MAX_WORKERS", 2),
        },
        "gemini_api_key": _secret_view(overrides, "gemini_api_key", "GEMINI_API_KEY"),
        "anthropic_api_key": _secret_view(overrides, "anthropic_api_key", "ANTHROPIC_API_KEY"),
        "vertex_credentials": _vertex_credentials_status(project_root),
    }


def _full_payload(project_root: Path) -> dict[str, Any]:
    return {"config": _config_payload(project_root), "options": _options_payload()}


class SystemConfigPatchRequest(BaseModel):
    image_backend: Optional[str] = None
    video_backend: Optional[str] = None
    gemini_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    video_generate_audio: Optional[bool] = None
    gemini_image_rpm: Optional[int] = None
    gemini_video_rpm: Optional[int] = None
    gemini_request_gap: Optional[float] = None
    storyboard_max_workers: Optional[int] = None
    video_max_workers: Optional[int] = None


def _normalize_backend(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"aistudio", "vertex"}:
        raise HTTPException(status_code=400, detail="backend 必须是 aistudio 或 vertex")
    return normalized


@router.get("/system/config")
async def get_system_config():
    return _full_payload(PROJECT_ROOT)


@router.patch("/system/config")
async def patch_system_config(req: SystemConfigPatchRequest, request: Request):
    manager = get_system_config_manager(PROJECT_ROOT)
    options = _options_payload()

    patch: dict[str, Any] = {}
    for field_name in req.model_fields_set:
        patch[field_name] = getattr(req, field_name)

    # Validate and normalize.
    if "image_backend" in patch and patch["image_backend"] not in (None, ""):
        patch["image_backend"] = _normalize_backend(str(patch["image_backend"]))
    if "video_backend" in patch and patch["video_backend"] not in (None, ""):
        patch["video_backend"] = _normalize_backend(str(patch["video_backend"]))

    if "image_model" in patch and patch["image_model"] not in (None, ""):
        value = str(patch["image_model"]).strip()
        if value not in options["image_models"]:
            raise HTTPException(status_code=400, detail="image_model 不在支持列表内")
        patch["image_model"] = value
    if "video_model" in patch and patch["video_model"] not in (None, ""):
        value = str(patch["video_model"]).strip()
        if value not in options["video_models"]:
            raise HTTPException(status_code=400, detail="video_model 不在支持列表内")
        patch["video_model"] = value

    for key, min_value in (
        ("gemini_image_rpm", 0),
        ("gemini_video_rpm", 0),
    ):
        if key in patch and patch[key] is not None:
            if int(patch[key]) < min_value:
                raise HTTPException(status_code=400, detail=f"{key} 必须 >= {min_value}")

    if "gemini_request_gap" in patch and patch["gemini_request_gap"] is not None:
        if float(patch["gemini_request_gap"]) < 0:
            raise HTTPException(status_code=400, detail="gemini_request_gap 必须 >= 0")

    for key in ("storyboard_max_workers", "video_max_workers"):
        if key in patch and patch[key] is not None:
            if int(patch[key]) < 1:
                raise HTTPException(status_code=400, detail=f"{key} 必须 >= 1")

    # If Vertex is selected for either backend, ensure credentials exist.
    final_image_backend = (
        _normalize_backend(str(patch["image_backend"]))
        if ("image_backend" in patch and patch["image_backend"] not in (None, ""))
        else _effective_image_backend()
    )
    final_video_backend = (
        _normalize_backend(str(patch["video_backend"]))
        if ("video_backend" in patch and patch["video_backend"] not in (None, ""))
        else _effective_video_backend()
    )
    if final_image_backend == "vertex" or final_video_backend == "vertex":
        if not _has_vertex_credentials(PROJECT_ROOT):
            raise HTTPException(status_code=400, detail="请先上传 Vertex AI JSON 凭证文件")

    # Persist + apply overrides to env.
    manager.update_overrides(patch)

    # Refresh shared runtime components.
    refresh_shared_rate_limiter()

    worker = getattr(request.app.state, "generation_worker", None)
    if worker is not None and hasattr(worker, "reload_limits_from_env"):
        try:
            worker.reload_limits_from_env()
        except Exception:
            logger.exception("Failed to reload GenerationWorker limits")

    return _full_payload(PROJECT_ROOT)


@router.post("/system/config/vertex-credentials")
async def upload_vertex_credentials(file: UploadFile = File(...)):
    manager = get_system_config_manager(PROJECT_ROOT)
    try:
        contents = await file.read(MAX_VERTEX_CREDENTIALS_BYTES + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="读取上传文件失败")

    if len(contents) > MAX_VERTEX_CREDENTIALS_BYTES:
        raise HTTPException(status_code=413, detail="凭证文件过大")

    try:
        payload = json.loads(contents.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 JSON 凭证文件")

    if not isinstance(payload, dict) or not payload.get("project_id"):
        raise HTTPException(status_code=400, detail="凭证文件缺少 project_id")

    dest = manager.paths.vertex_credentials_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".tmp")
    tmp_path.write_bytes(contents)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError as exc:
        logger.warning("Unable to chmod %s to 0600: %s", tmp_path, exc, exc_info=True)
    os.replace(tmp_path, dest)
    try:
        os.chmod(dest, 0o600)
    except OSError as exc:
        logger.warning("Unable to chmod %s to 0600: %s", dest, exc, exc_info=True)

    return _full_payload(PROJECT_ROOT)
