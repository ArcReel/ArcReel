"""
文件管理路由

处理文件上传和静态资源服务
"""

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from lib import PROJECT_ROOT
from lib.i18n import get_translator
from lib.image_utils import normalize_uploaded_image
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from server.auth import CurrentUser

router = APIRouter()

# 初始化项目管理器
pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


# 允许的文件类型
ALLOWED_EXTENSIONS = {
    "source": [".txt", ".md", ".doc", ".docx"],
    "character": [".png", ".jpg", ".jpeg", ".webp"],
    "character_ref": [".png", ".jpg", ".jpeg", ".webp"],
    "clue": [".png", ".jpg", ".jpeg", ".webp"],
    "storyboard": [".png", ".jpg", ".jpeg", ".webp"],
}


@router.get("/files/{project_name}/{path:path}")
async def serve_project_file(project_name: str, path: str, request: Request, _t=Depends(get_translator)):
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


@router.post("/projects/{project_name}/upload/{upload_type}")
async def upload_file(
    project_name: str,
    upload_type: str,
    _user: CurrentUser,
    file: UploadFile = File(...),
    name: str = None,
    _t=Depends(get_translator),
):
    """
    上传文件

    Args:
        project_name: 项目名称
        upload_type: 上传类型 (source/character/clue/storyboard)
        file: 上传的文件
        name: 可选，用于角色/线索名称，或分镜 ID（自动更新元数据）
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
            elif upload_type == "clue":
                target_dir = project_dir / "clues"
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
            if upload_type in ("character", "character_ref", "clue", "storyboard"):
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
            elif upload_type == "clue":
                relative_path = f"clues/{filename}"
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

            if upload_type == "clue" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_clue_sheet(
                            project_name,
                            name,
                            f"clues/{filename}",
                        )
                except KeyError:
                    pass  # 线索不存在，忽略

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


@router.get("/projects/{project_name}/files")
async def list_project_files(project_name: str, _user: CurrentUser, _t=Depends(get_translator)):
    """列出项目中的所有文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            files = {
                "source": [],
                "characters": [],
                "clues": [],
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
async def get_source_file(project_name: str, filename: str, _user: CurrentUser, _t=Depends(get_translator)):
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
    content: str = Body(..., media_type="text/plain"),
    _t=Depends(get_translator),
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
async def delete_source_file(project_name: str, filename: str, _user: CurrentUser, _t=Depends(get_translator)):
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
async def list_drafts(project_name: str, _user: CurrentUser, _t=Depends(get_translator)):
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


def _get_step_title(filename: str, _t) -> str:
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
async def get_draft_content(
    project_name: str, episode: int, step_num: int, _user: CurrentUser, _t=Depends(get_translator)
):
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
    content: str = Body(..., media_type="text/plain"),
    _t=Depends(get_translator),
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
            label_prefix = "片段拆分" if content_mode == "narration" else "规范化剧本"
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
async def delete_draft(project_name: str, episode: int, step_num: int, _user: CurrentUser, _t=Depends(get_translator)):
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
async def upload_style_image(
    project_name: str, _user: CurrentUser, file: UploadFile = File(...), _t=Depends(get_translator)
):
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


@router.delete("/projects/{project_name}/style-image")
async def delete_style_image(project_name: str, _user: CurrentUser, _t=Depends(get_translator)):
    """
    删除风格参考图及相关字段
    """
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)

            # 删除图片文件（兼容所有可能的后缀）
            for suffix in (".jpg", ".jpeg", ".png", ".webp"):
                image_path = project_dir / f"style_reference{suffix}"
                if image_path.exists():
                    image_path.unlink()

            # 清除 project.json 中的相关字段
            project_data = get_project_manager().load_project(project_name)
            project_data.pop("style_image", None)
            project_data.pop("style_description", None)
            with project_change_source("webui"):
                get_project_manager().save_project(project_name, project_data)

            return {"success": True}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/style-description")
async def update_style_description(
    project_name: str, _user: CurrentUser, style_description: str = Body(..., embed=True), _t=Depends(get_translator)
):
    """
    更新风格描述（手动编辑）
    """
    try:

        def _sync():
            project_data = get_project_manager().load_project(project_name)
            project_data["style_description"] = style_description
            with project_change_source("webui"):
                get_project_manager().save_project(project_name, project_data)

            return {"success": True, "style_description": style_description}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(e))
