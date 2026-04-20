"""
文件管理路由

处理文件上传和静态资源服务
"""

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from lib import PROJECT_ROOT
from lib.asset_types import ASSET_TYPES
from lib.i18n import Translator
from lib.image_utils import normalize_uploaded_image
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from lib.source_loader import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    NormalizeResult,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from server.auth import CurrentUser

router = APIRouter()

# 初始化项目管理器
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


# 允许的文件类型
ALLOWED_EXTENSIONS = {
    "source": [".txt", ".md", ".docx", ".epub", ".pdf"],
    "character": [".png", ".jpg", ".jpeg", ".webp"],
    "character_ref": [".png", ".jpg", ".jpeg", ".webp"],
    "scene": [".png", ".jpg", ".jpeg", ".webp"],
    "prop": [".png", ".jpg", ".jpeg", ".webp"],
    "storyboard": [".png", ".jpg", ".jpeg", ".webp"],
}


@router.get("/files/{project_name}/{path:path}")
async def serve_project_file(project_name: str, path: str, request: Request, _t: Translator):
    """服务项目内的静态文件（图片/视频）"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            file_path = project_dir / path

            if not file_path.exists():
                raise HTTPException(status_code=404, detail=_t("file_not_found", path=path))

            # 安全检查：确保路径在项目目录内
            try:
                file_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail=_t("forbidden_access"))

            return file_path

        file_path = await asyncio.to_thread(_sync)

        # 内容寻址缓存：带 ?v= 参数或 versions/ 路径时设 immutable
        headers = {}
        if request.query_params.get("v") or path.startswith("versions/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"

        return FileResponse(file_path, headers=headers)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


@router.get("/global-assets/{asset_type}/{filename}")
async def serve_global_asset(asset_type: str, filename: str, _t: Translator):
    """服务 _global_assets 下的全局资产图片（character/scene/prop）"""
    if asset_type not in ASSET_TYPES:
        raise HTTPException(status_code=400, detail=_t("invalid_asset_type"))
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail=_t("invalid_asset_filename"))

    root = get_project_manager().get_global_assets_root()
    path = root / asset_type / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))

    # 防御性检查：即使 filename 通过了字符串校验，也要确保解析后的路径仍在 root 之内
    # （防御 symlink / URL 编码等边界场景）
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail=_t("forbidden_access"))

    return FileResponse(str(path))


@router.post("/projects/{project_name}/upload/{upload_type}")
async def upload_file(
    project_name: str,
    upload_type: str,
    _user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
    name: str = None,
    on_conflict: str = "fail",
):
    """
    上传文件

    Args:
        project_name: 项目名称
        upload_type: 上传类型 (source/character/prop/storyboard)
        file: 上传的文件
        name: 可选，用于角色/道具名称，或分镜 ID（自动更新元数据）
        on_conflict: source 类型独有 — fail / replace / rename
    """
    if upload_type not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=_t("invalid_upload_type", upload_type=upload_type))

    # 检查文件扩展名
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS[upload_type]:
        raise HTTPException(
            status_code=400,
            detail=_t("unsupported_image_type", ext=ext, allowed=", ".join(ALLOWED_EXTENSIONS[upload_type])),
        )

    # Source 分支早返 — 走 SourceLoader 规范化
    if upload_type == "source":
        return await _handle_source_upload(
            project_name=project_name,
            file=file,
            on_conflict=on_conflict,
            _t=_t,
        )

    try:
        content = await file.read()

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            # 确定目标目录
            if upload_type == "source":
                target_dir = project_dir / "source"
                filename = file.filename
            elif upload_type == "character":
                target_dir = project_dir / "characters"
                # 统一保存为 PNG，且使用稳定文件名（避免 jpg/png 不一致导致版本还原/引用异常）
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            elif upload_type == "character_ref":
                target_dir = project_dir / "characters" / "refs"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            elif upload_type == "scene":
                target_dir = project_dir / "scenes"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            elif upload_type == "prop":
                target_dir = project_dir / "props"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            elif upload_type == "storyboard":
                # 注意：目录为 storyboards（复数），而不是 storyboard
                target_dir = project_dir / "storyboards"
                if name:
                    filename = f"scene_{name}.png"
                else:
                    filename = f"{Path(file.filename).stem}.png"
            else:
                target_dir = project_dir / upload_type
                filename = file.filename

            target_dir.mkdir(parents=True, exist_ok=True)

            # 保存文件（大于 2MB 时压缩为 JPEG，否则校验后原样保存）
            nonlocal content
            if upload_type in ("character", "character_ref", "scene", "prop", "storyboard"):
                try:
                    content, ext = normalize_uploaded_image(content, Path(file.filename).suffix.lower())
                except ValueError:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file"))
                filename = Path(filename).with_suffix(ext).name

            target_path = target_dir / filename
            with open(target_path, "wb") as f:
                f.write(content)

            # 更新元数据
            if upload_type == "source":
                relative_path = f"source/{filename}"
            elif upload_type == "character":
                relative_path = f"characters/{filename}"
            elif upload_type == "character_ref":
                relative_path = f"characters/refs/{filename}"
            elif upload_type == "scene":
                relative_path = f"scenes/{filename}"
            elif upload_type == "prop":
                relative_path = f"props/{filename}"
            elif upload_type == "storyboard":
                relative_path = f"storyboards/{filename}"
            else:
                relative_path = f"{upload_type}/{filename}"

            if upload_type == "character" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_project_character_sheet(
                            project_name, name, f"characters/{filename}"
                        )
                except KeyError:
                    pass  # 角色不存在，忽略

            if upload_type == "character_ref" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_character_reference_image(
                            project_name, name, f"characters/refs/{filename}"
                        )
                except KeyError:
                    pass  # 角色不存在，忽略

            if upload_type == "scene" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_scene_sheet(
                            project_name,
                            name,
                            f"scenes/{filename}",
                        )
                except KeyError:
                    pass  # 场景不存在，忽略

            if upload_type == "prop" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_prop_sheet(
                            project_name,
                            name,
                            f"props/{filename}",
                        )
                except KeyError:
                    pass  # 道具不存在，忽略

            return {
                "success": True,
                "filename": filename,
                "path": relative_path,
                "url": f"/api/v1/files/{project_name}/{relative_path}",
            }

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


async def _handle_source_upload(
    *,
    project_name: str,
    file: UploadFile,
    on_conflict: str,
    _t: Translator,
):
    """Source 分支：通过 SourceLoader 规范化为 UTF-8 .txt，并按需备份原始字节。"""
    if on_conflict not in {"fail", "replace", "rename"}:
        raise HTTPException(status_code=400, detail="on_conflict must be fail/replace/rename")

    try:
        project_dir = get_project_manager().get_project_path(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))

    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    original_filename = file.filename

    def _sync() -> NormalizeResult:
        with tempfile.NamedTemporaryFile(suffix=Path(original_filename).suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            return SourceLoader.load(
                tmp_path,
                source_dir,
                original_filename=original_filename,
                on_conflict=on_conflict,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    try:
        result = await asyncio.to_thread(_sync)
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=400,
            detail=_t("source_unsupported_format", ext=exc.ext),
        )
    except FileSizeExceededError as exc:
        raise HTTPException(
            status_code=413,
            detail=_t(
                "source_too_large",
                filename=exc.filename,
                size_mb=round(exc.size_bytes / 1024 / 1024, 1),
                limit_mb=round(exc.limit_bytes / 1024 / 1024, 1),
            ),
        )
    except SourceDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t(
                "source_decode_failed",
                filename=exc.filename,
                tried=", ".join(exc.tried_encodings),
            ),
        )
    except CorruptFileError as exc:
        raise HTTPException(
            status_code=422,
            detail=_t("source_corrupt_file", filename=exc.filename, reason=exc.reason),
        )
    except ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "existing": exc.existing,
                "suggested_name": exc.suggested_name,
                "message": _t(
                    "source_conflict",
                    existing=exc.existing,
                    suggested=exc.suggested_name,
                ),
            },
        )

    relative_path = f"source/{result.normalized_path.name}"
    return {
        "success": True,
        "filename": result.normalized_path.name,
        "path": relative_path,
        "url": f"/api/v1/files/{project_name}/{relative_path}",
        "normalized": True,
        "original_kept": result.raw_path is not None,
        "original_filename": result.original_filename,
        "used_encoding": result.used_encoding,
        "chapter_count": result.chapter_count,
    }


@router.get("/projects/{project_name}/files")
async def list_project_files(project_name: str, _user: CurrentUser, _t: Translator):
    """列出项目中的所有文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            files = {
                "source": [],
                "characters": [],
                "scenes": [],
                "props": [],
                "storyboards": [],
                "videos": [],
                "output": [],
            }

            for subdir, file_list in files.items():
                subdir_path = project_dir / subdir
                if subdir_path.exists():
                    for f in subdir_path.iterdir():
                        if f.is_file() and not f.name.startswith("."):
                            file_list.append(
                                {
                                    "name": f.name,
                                    "size": f.stat().st_size,
                                    "url": f"/api/v1/files/{project_name}/{subdir}/{f.name}",
                                }
                            )

            return {"files": files}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_name}/source/{filename}")
async def get_source_file(project_name: str, filename: str, _user: CurrentUser, _t: Translator):
    """获取 source 文件的文本内容"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            source_path = project_dir / "source" / filename

            if not source_path.exists():
                raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))

            # 安全检查：确保路径在项目目录内
            try:
                source_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail=_t("forbidden_access"))

            return source_path.read_text(encoding="utf-8")

        content = await asyncio.to_thread(_sync)
        return PlainTextResponse(content)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail=_t("invalid_encoding"))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/projects/{project_name}/source/{filename}")
async def update_source_file(
    project_name: str,
    filename: str,
    _user: CurrentUser,
    _t: Translator,
    content: str = Body(..., media_type="text/plain"),
):
    """更新或创建 source 文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            source_dir = project_dir / "source"
            source_dir.mkdir(parents=True, exist_ok=True)
            source_path = source_dir / filename

            # 安全检查：确保路径在项目目录内
            try:
                source_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail=_t("forbidden_access"))

            source_path.write_text(content, encoding="utf-8")
            return {"success": True, "path": f"source/{filename}"}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/source/{filename}")
async def delete_source_file(project_name: str, filename: str, _user: CurrentUser, _t: Translator):
    """删除 source 文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            source_path = project_dir / "source" / filename

            # 安全检查：确保路径在项目目录内
            try:
                source_path.resolve().relative_to(project_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail=_t("forbidden_access"))

            if source_path.exists():
                source_path.unlink()
                # 级联删除原文件备份（同 stem，任意扩展名）
                raw_dir = project_dir / "source" / "raw"
                if raw_dir.exists():
                    stem = source_path.stem
                    for raw_file in raw_dir.iterdir():
                        if raw_file.is_file() and raw_file.stem == stem:
                            raw_file.unlink()
                return {"success": True}
            else:
                raise HTTPException(status_code=404, detail=_t("file_not_found", path=filename))

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 草稿文件管理 ====================


@router.get("/projects/{project_name}/drafts")
async def list_drafts(project_name: str, _user: CurrentUser, _t: Translator):
    """列出项目的所有草稿目录和文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            drafts_dir = project_dir / "drafts"

            result = {}
            if drafts_dir.exists():
                for episode_dir in sorted(drafts_dir.iterdir()):
                    if episode_dir.is_dir() and episode_dir.name.startswith("episode_"):
                        episode_num = episode_dir.name.replace("episode_", "")
                        files = []
                        for f in sorted(episode_dir.glob("*.md")):
                            files.append(
                                {
                                    "name": f.name,
                                    "step": _extract_step_number(f.name),
                                    "title": _get_step_title(f.name, _t),
                                    "size": f.stat().st_size,
                                    "modified": f.stat().st_mtime,
                                }
                            )
                        result[episode_num] = files

            return {"drafts": result}

        return await asyncio.to_thread(_sync)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


def _extract_step_number(filename: str) -> int:
    """从文件名提取步骤编号"""
    import re

    match = re.search(r"step(\d+)", filename)
    return int(match.group(1)) if match else 0


def _get_step_files(content_mode: str) -> dict:
    """根据 content_mode 获取步骤文件名映射"""
    if content_mode == "narration":
        return {1: "step1_segments.md"}
    else:
        return {1: "step1_normalized_script.md"}


def _get_step_title(filename: str, _t: Callable[..., str]) -> str:
    """获取步骤标题"""
    titles = {
        "step1_normalized_script.md": _t("normalized_script"),
        "step1_segments.md": _t("segment_splitting"),
    }
    return titles.get(filename, filename)


def _get_content_mode(project_dir: Path) -> str:
    """从 project.json 读取 content_mode"""
    project_json_path = project_dir / "project.json"
    if project_json_path.exists():
        with open(project_json_path, encoding="utf-8") as f:
            project_data = json.load(f)
            return project_data.get("content_mode", "drama")
    return "drama"


@router.get("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def get_draft_content(project_name: str, episode: int, step_num: int, _user: CurrentUser, _t: Translator):
    """获取特定步骤的草稿内容"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode = _get_content_mode(project_dir)
            step_files = _get_step_files(content_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=_t("invalid_step_num", step_num=step_num))

            draft_path = project_dir / "drafts" / f"episode_{episode}" / step_files[step_num]

            if not draft_path.exists():
                raise HTTPException(status_code=404, detail=_t("draft_file_not_found"))

            return draft_path.read_text(encoding="utf-8")

        content = await asyncio.to_thread(_sync)
        return PlainTextResponse(content)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


@router.put("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def update_draft_content(
    project_name: str,
    episode: int,
    step_num: int,
    _user: CurrentUser,
    _t: Translator,
    content: str = Body(..., media_type="text/plain"),
):
    """更新草稿内容"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode = _get_content_mode(project_dir)
            step_files = _get_step_files(content_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=_t("invalid_step_num", step_num=step_num))

            drafts_dir = project_dir / "drafts" / f"episode_{episode}"
            drafts_dir.mkdir(parents=True, exist_ok=True)

            draft_path = drafts_dir / step_files[step_num]
            is_new = not draft_path.exists()
            draft_path.write_text(content, encoding="utf-8")

            # 发射 draft 事件通知前端
            action = "created" if is_new else "updated"
            label_prefix = _t("segment_splitting") if content_mode == "narration" else _t("normalized_script")
            change = {
                "entity_type": "draft",
                "action": action,
                "entity_id": f"episode_{episode}_step{step_num}",
                "label": _t("draft_event_label", episode=episode, label_prefix=label_prefix),
                "episode": episode,
                "focus": {
                    "pane": "episode",
                    "episode": episode,
                },
                "important": is_new,
            }
            try:
                emit_project_change_batch(project_name, [change], source="worker")
            except Exception:
                logger.warning("发送 draft 事件失败 project=%s episode=%s", project_name, episode, exc_info=True)

            return {"success": True, "path": str(draft_path.relative_to(project_dir))}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


@router.delete("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def delete_draft(project_name: str, episode: int, step_num: int, _user: CurrentUser, _t: Translator):
    """删除草稿文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode = _get_content_mode(project_dir)
            step_files = _get_step_files(content_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=_t("invalid_step_num", step_num=step_num))

            draft_path = project_dir / "drafts" / f"episode_{episode}" / step_files[step_num]

            if draft_path.exists():
                draft_path.unlink()
                return {"success": True}
            else:
                raise HTTPException(status_code=404, detail=_t("draft_file_not_found"))

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


# ==================== 风格参考图管理 ====================


@router.post("/projects/{project_name}/style-image")
async def upload_style_image(project_name: str, _user: CurrentUser, _t: Translator, file: UploadFile = File(...)):
    """
    上传风格参考图并分析风格

    1. 保存图片到 projects/{project_name}/style_reference.png
    2. 调用 Gemini API 分析风格
    3. 更新 project.json 的 style_image 和 style_description 字段
    """
    # 检查文件类型
    ext = Path(file.filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(
            status_code=400,
            detail=_t("unsupported_image_type", ext=ext, allowed=".png, .jpg, .jpeg, .webp"),
        )

    try:
        content = await file.read()

        def _sync_prepare():
            project_dir = get_project_manager().get_project_path(project_name)
            try:
                content_norm, new_ext = normalize_uploaded_image(content, Path(file.filename).suffix.lower())
            except ValueError:
                raise HTTPException(status_code=400, detail=_t("invalid_image_file"))
            style_filename = f"style_reference{new_ext}"

            output_path = project_dir / style_filename
            with open(output_path, "wb") as f:
                f.write(content_norm)

            return output_path, style_filename

        output_path, style_filename = await asyncio.to_thread(_sync_prepare)

        # 调用 TextGenerator 分析风格（自动追踪用量）
        from lib.text_backends.base import ImageInput, TextGenerationRequest, TextTaskType
        from lib.text_backends.prompts import STYLE_ANALYSIS_PROMPT
        from lib.text_generator import TextGenerator

        generator = await TextGenerator.create(TextTaskType.STYLE_ANALYSIS, project_name)
        result = await generator.generate(
            TextGenerationRequest(prompt=STYLE_ANALYSIS_PROMPT, images=[ImageInput(path=output_path)]),
            project_name=project_name,
        )
        style_description = result.text

        def _sync_save():
            # 更新 project.json
            project_data = get_project_manager().load_project(project_name)
            project_data["style_image"] = style_filename
            project_data["style_description"] = style_description
            # 强互斥：自定义参考图与模版二选一。除了清 template_id，
            # 还需清掉之前由模板展开写入的 `style` prompt，否则生成链路会把
            # 模板 prompt 与 style_description 同时喂给 LLM，破坏二选一语义。
            project_data.pop("style_template_id", None)
            project_data["style"] = ""
            with project_change_source("webui"):
                get_project_manager().save_project(project_name, project_data)

        await asyncio.to_thread(_sync_save)

        return {
            "success": True,
            "style_image": style_filename,
            "style_description": style_description,
            "url": f"/api/v1/files/{project_name}/{style_filename}",
        }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
