"""
文件管理路由

处理文件上传和静态资源服务
"""

import asyncio
import json
import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from lib.asset_types import GLOBAL_LIBRARY_ASSET_TYPES
from lib.config.resolver import VisionCapabilityError
from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_FILENAME,
    REFERENCE_VIDEO_STEP1_LEGACY_FILENAME,
    STEP1_FILENAMES,
    episode_drafts_dir,
    step1_read_candidates,
)
from lib.i18n import Translator
from lib.image_utils import normalize_uploaded_image, validate_image_bytes
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import effective_mode, get_project_manager
from lib.source_loader import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    NormalizeResult,
    OnConflict,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from server.auth import CurrentUser

router = APIRouter()


def _require_filename(file: UploadFile, _t: Callable[..., str]) -> str:
    if not file.filename:
        raise HTTPException(status_code=400, detail=_t("missing_filename"))
    return file.filename


# 允许的文件类型
ALLOWED_EXTENSIONS = {
    "source": [".txt", ".md", ".docx", ".epub", ".pdf"],
    "character": [".png", ".jpg", ".jpeg", ".webp"],
    "character_ref": [".png", ".jpg", ".jpeg", ".webp"],
    "scene": [".png", ".jpg", ".jpeg", ".webp"],
    "prop": [".png", ".jpg", ".jpeg", ".webp"],
    "product": [".png", ".jpg", ".jpeg", ".webp"],
    "product_ref": [".png", ".jpg", ".jpeg", ".webp"],
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
    """服务 _global_assets 下的全局资产图片（仅全局库类型：character/scene/prop）"""
    if asset_type not in GLOBAL_LIBRARY_ASSET_TYPES:
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
    name: str | None = None,
    on_conflict: OnConflict = "fail",
):
    """
    上传文件

    Args:
        project_name: 项目名称
        upload_type: 上传类型 (source/character/character_ref/scene/prop/product/product_ref)
        file: 上传的文件
        name: 可选，用于角色/场景/道具/产品名称（自动更新元数据）；product_ref 必填；
            分镜/视频上传走 shot_uploads 路由
        on_conflict: source 类型独有 — fail / replace / rename
    """
    if upload_type not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=_t("invalid_upload_type", upload_type=upload_type))

    original_filename = _require_filename(file, _t)

    # 检查文件扩展名
    ext = Path(original_filename).suffix.lower()
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

            # 产品原图列表是这些文件的唯一指针：产品不存在就拒收，避免落下不可见的孤儿文件
            # （character_ref 等单图类型路径确定、可容忍资产后建，不受此约束）。
            if upload_type == "product_ref":
                products = get_project_manager().load_project(project_name).get("products") or {}
                if not name or name not in products:
                    raise HTTPException(status_code=404, detail=_t("product_not_found", name=name or ""))

            # 确定目标目录
            if upload_type == "source":
                target_dir = project_dir / "source"
                filename = original_filename
            elif upload_type == "character":
                target_dir = project_dir / "characters"
                # 统一保存为 PNG，且使用稳定文件名（避免 jpg/png 不一致导致版本还原/引用异常）
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(original_filename).stem}.png"
            elif upload_type == "character_ref":
                target_dir = project_dir / "characters" / "refs"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(original_filename).stem}.png"
            elif upload_type == "scene":
                target_dir = project_dir / "scenes"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(original_filename).stem}.png"
            elif upload_type == "prop":
                target_dir = project_dir / "props"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(original_filename).stem}.png"
            elif upload_type == "product":
                target_dir = project_dir / "products"
                if name:
                    filename = f"{name}.png"
                else:
                    filename = f"{Path(original_filename).stem}.png"
            elif upload_type == "product_ref":
                target_dir = project_dir / "products" / "refs"
                filename = ""  # 多图累积，唯一文件名在目录就绪后按序号计算
            else:
                target_dir = project_dir / upload_type
                filename = original_filename

            target_dir.mkdir(parents=True, exist_ok=True)

            # 保存文件（大于 2MB 时压缩为 JPEG，否则校验后原样保存）
            nonlocal content
            if upload_type in ("character", "character_ref", "scene", "prop", "product"):
                try:
                    content, ext = normalize_uploaded_image(content, Path(original_filename).suffix.lower())
                except ValueError:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file"))
                filename = Path(filename).with_suffix(ext).name
            elif upload_type == "product_ref":
                # 产品原图是保真验收锚点（ADR 0034）：仅校验可解码，保留原件字节与扩展名，
                # 不做阈值压缩/重编码。请求体上限由生成发送前的参考压缩环节独立保障。
                try:
                    validate_image_bytes(content)
                except ValueError:
                    raise HTTPException(status_code=400, detail=_t("invalid_image_file"))
                ref_ext = Path(original_filename).suffix.lower() or ".png"
                # 按序号取唯一文件名，用原子独占创建占位：并发上传同一产品时
                # 两个请求各拿到不同序号，避免静默互相覆盖。
                seq = 1
                while True:
                    candidate = target_dir / f"{name}_{seq}{ref_ext}"
                    try:
                        candidate.touch(exist_ok=False)
                        break
                    except FileExistsError:
                        seq += 1
                filename = candidate.name

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
            elif upload_type == "product":
                relative_path = f"products/{filename}"
            elif upload_type == "product_ref":
                relative_path = f"products/refs/{filename}"
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

            if upload_type == "product" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().update_product_sheet(
                            project_name,
                            name,
                            f"products/{filename}",
                        )
                except KeyError:
                    pass  # 产品不存在，忽略

            if upload_type == "product_ref" and name:
                try:
                    with project_change_source("webui"):
                        get_project_manager().add_product_reference_image(
                            project_name,
                            name,
                            f"products/refs/{filename}",
                        )
                except KeyError:
                    # 入口已校验产品存在；并发删除导致的窗口期竞态按 404 处理，
                    # 已落盘的文件一并清理避免孤儿
                    target_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=404, detail=_t("product_not_found", name=name))

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
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


async def _handle_source_upload(
    *,
    project_name: str,
    file: UploadFile,
    on_conflict: OnConflict,
    _t: Translator,
):
    """Source 分支：通过 SourceLoader 规范化为 UTF-8 .txt，并按需备份原始字节。"""
    original_filename = _require_filename(file, _t)

    try:
        project_dir = get_project_manager().get_project_path(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))

    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    def _sync() -> NormalizeResult:
        # 流式写入 tmp，避免把上传 body 整体拉进 Python 堆；
        # UploadFile.file 是 SpooledTemporaryFile，此处已是请求体完整到位状态。
        # 在 with 外包 try/finally：即使 copyfileobj 抛异常（如磁盘满），
        # 也要清理已创建的 tmp 文件，避免 /tmp 泄漏（delete=False 不会自动清）。
        with tempfile.NamedTemporaryFile(suffix=Path(original_filename).suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with tmp_path.open("wb") as out:
                shutil.copyfileobj(file.file, out)
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
                "products": [],
                "storyboards": [],
                "videos": [],
                "output": [],
            }

            for subdir, file_list in files.items():
                subdir_path = project_dir / subdir
                if not subdir_path.exists():
                    continue
                # source 子目录额外列出 raw 备份映射
                raw_by_stem: dict[str, str] = {}
                if subdir == "source":
                    raw_dir = subdir_path / "raw"
                    if raw_dir.exists():
                        # sorted 保证多个 raw 同 stem 时的确定性（后者覆盖前者，字典序末位胜出）
                        for raw_f in sorted(raw_dir.iterdir()):
                            if raw_f.is_file():
                                raw_by_stem[raw_f.stem] = raw_f.name
                for f in subdir_path.iterdir():
                    if f.is_file() and not f.name.startswith("."):
                        entry = {
                            "name": f.name,
                            "size": f.stat().st_size,
                            "url": f"/api/v1/files/{project_name}/{subdir}/{f.name}",
                        }
                        if subdir == "source":
                            entry["raw_filename"] = raw_by_stem.get(Path(f.name).stem)
                        file_list.append(entry)

            return {"files": files}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))
    except HTTPException:
        raise
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


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
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


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
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


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
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))


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


def _get_step_files(content_mode: str, generation_mode: str | None = None) -> dict:
    """根据 generation_mode / content_mode 获取步骤文件名映射

    ad 不走结构化 step1（与 _resolve_step1_path / status_calculator._draft_candidates 同口径显式
    排除），即便带 reference_video generation_mode 也无 step1，故先于 generation_mode 判断返回空
    映射，调用方据此给出「无此步骤」而非误落 drama / reference 文件名。reference_video 走
    split_reference_video_units 工具 → step1_reference_units.json；其他模式回落到 content_mode
    的结构化 step1 文件名（未知 content_mode 兜底 drama）。结构化文件名取自单一真相源
    STEP1_FILENAMES，新增 content_mode 自动覆盖。
    """
    if content_mode == "ad":
        return {}
    if generation_mode == "reference_video":
        return {1: REFERENCE_VIDEO_STEP1_FILENAME}
    return {1: STEP1_FILENAMES.get(content_mode, STEP1_FILENAMES["drama"])}


# 按 primary 文件名分组的优先候选（mode 感知）：先在本模式自家候选里回落，再兜底其他模式遗留文件。
# 每模式的结构化 .json + 旧版 .md 取自单一真相源；reference_video 优先结构化 .json、再旧版 .md
# （读取 / 浏览层兼认存量在制品，写盘与生成侧不认——与 narration / drama 的 legacy 语义同口径）。
_STEP1_FAMILY: dict[str, list[str]] = {
    STEP1_FILENAMES[mode]: list(step1_read_candidates(mode)) for mode in STEP1_FILENAMES
}
_STEP1_FAMILY[REFERENCE_VIDEO_STEP1_FILENAME] = [REFERENCE_VIDEO_STEP1_FILENAME, REFERENCE_VIDEO_STEP1_LEGACY_FILENAME]

# step1 实际文件候选 —— 主文件不存在时用于 fallback 探测，兼容 episode 级 generation_mode 覆盖。
# 结构化 .json 与旧 .md 候选均由单一真相源派生；各自保留旧 .md 以便存量在制品仍可浏览。
# 跨模式遗留回落的探测优先级固定为 reference_video → narration → drama（保持历史 tie-break，
# 避免收敛后跨模式选到的遗留文件与旧实现不一致）；未登记于此序列的未来 content_mode 附加在后。
_STEP1_PROBE_ORDER = [REFERENCE_VIDEO_STEP1_FILENAME, STEP1_FILENAMES["narration"], STEP1_FILENAMES["drama"]]
_STEP1_CANDIDATES = list(
    dict.fromkeys(name for key in [*_STEP1_PROBE_ORDER, *_STEP1_FAMILY] for name in _STEP1_FAMILY[key])
)


def _get_step_title(filename: str, _t: Callable[..., str]) -> str:
    """获取步骤标题：每种 step1（含旧 .md 别名）映射到其展示名 i18n key。"""
    titles: dict[str, str] = {}
    for name in step1_read_candidates("drama"):
        titles[name] = _t("normalized_script")
    for name in step1_read_candidates("narration"):
        titles[name] = _t("segment_splitting")
    titles[REFERENCE_VIDEO_STEP1_FILENAME] = _t("segment_splitting")
    titles[REFERENCE_VIDEO_STEP1_LEGACY_FILENAME] = _t("segment_splitting")
    return titles.get(filename, filename)


def _load_project_modes(project_name: str, episode: int) -> tuple[str, str | None]:
    """走 ProjectManager.load_project，派生 (content_mode, generation_mode)。

    复用 load_project 以获得文件锁和 _migrate_legacy_style 迁移；generation_mode 的
    episode→project→默认回退复用 lib.project_manager.effective_mode。
    项目不存在时返回 ("drama", None)，由调用方走 content_mode-only 分支。
    """
    try:
        data = get_project_manager().load_project(project_name)
    except FileNotFoundError:
        return "drama", None
    content_mode = data.get("content_mode", "drama")
    ep_dict = next(
        (ep for ep in (data.get("episodes") or []) if ep.get("episode") == episode),
        {},
    )
    return content_mode, effective_mode(project=data, episode=ep_dict)


def _resolve_step1_path(drafts_dir: Path, step_num: int, primary: Path) -> Path:
    """主路径不存在时按 primary 所属模式优先回落，再兜底其他模式遗留文件（mode 感知）。

    step_num != 1 或主路径已存在：原样返回 primary；调用方自行 exists() 判定。
    """
    if step_num != 1 or primary.exists():
        return primary
    family = _STEP1_FAMILY.get(primary.name, [primary.name])
    for candidate in dict.fromkeys([*family, *_STEP1_CANDIDATES]):
        alt = drafts_dir / candidate
        if alt.exists():
            return alt
    return primary


@router.get("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def get_draft_content(project_name: str, episode: int, step_num: int, _user: CurrentUser, _t: Translator):
    """获取特定步骤的草稿内容"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode, generation_mode = _load_project_modes(project_name, episode)
            step_files = _get_step_files(content_mode, generation_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=_t("invalid_step_num", step_num=step_num))

            drafts_dir = episode_drafts_dir(project_dir, episode)
            draft_path = _resolve_step1_path(drafts_dir, step_num, drafts_dir / step_files[step_num])

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
            content_mode, generation_mode = _load_project_modes(project_name, episode)
            step_files = _get_step_files(content_mode, generation_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=_t("invalid_step_num", step_num=step_num))

            drafts_dir = episode_drafts_dir(project_dir, episode)
            drafts_dir.mkdir(parents=True, exist_ok=True)

            # 写入始终落到当前模式的目标文件；fallback 仅用于读取/删除（兼容跨模式切换的旧 step1）。
            # 若写入 fallback 到老文件，切模式后后续 subagent 读 step_files[step_num] 仍为空，
            # 导致"前端保存成功但生成报缺少 step1"。
            draft_path = drafts_dir / step_files[step_num]

            # drama step1 落结构化 .json：写入前与 _load_drama_step1_content 的读取契约同口径校验
            # ——合法 JSON、顶层对象、scenes 为非空且每项为带非空 scene_id 的对象，避免任意文本 / 空剧本 /
            # 非对象场景项 / 缺失或空 scene_id 写进结构化草稿、拖到生成阶段才解析失败（前端保存成功但生成必然
            # 失败）。按目标文件名而非 content_mode 触发：_get_step_files 对未知模式回落到 drama 的
            # 结构化文件名，仅凭 content_mode 判定会让脏值绕过校验把任意文本写成 drama JSON。narration /
            # reference 的 step1 落各自文件名，不匹配此校验。
            if draft_path.name == STEP1_FILENAMES["drama"]:
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail=_t("draft_invalid_json"))
                scenes = parsed.get("scenes") if isinstance(parsed, dict) else None
                if (
                    not isinstance(parsed, dict)
                    or not isinstance(scenes, list)
                    or not scenes
                    or any(not isinstance(scene, dict) for scene in scenes)
                    or any(not isinstance(scene.get("scene_id"), str) or not scene.get("scene_id") for scene in scenes)
                ):
                    raise HTTPException(status_code=400, detail=_t("draft_invalid_json"))

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

            return {"success": True, "path": draft_path.relative_to(project_dir).as_posix()}

        return await asyncio.to_thread(_sync)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name))


@router.delete("/projects/{project_name}/drafts/{episode}/step{step_num}")
async def delete_draft(project_name: str, episode: int, step_num: int, _user: CurrentUser, _t: Translator):
    """删除草稿文件"""
    try:

        def _sync():
            project_dir = get_project_manager().get_project_path(project_name)
            content_mode, generation_mode = _load_project_modes(project_name, episode)
            step_files = _get_step_files(content_mode, generation_mode)

            if step_num not in step_files:
                raise HTTPException(status_code=400, detail=_t("invalid_step_num", step_num=step_num))

            drafts_dir = episode_drafts_dir(project_dir, episode)
            draft_path = _resolve_step1_path(drafts_dir, step_num, drafts_dir / step_files[step_num])

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
    original_filename = _require_filename(file, _t)

    # 检查文件类型
    ext = Path(original_filename).suffix.lower()
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
                content_norm, new_ext = normalize_uploaded_image(content, Path(original_filename).suffix.lower())
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
            # 更新 project.json：整段 RMW 在单一 _project_lock 内完成，避免覆盖并发写入的其它字段
            def _mutate(project_data: dict) -> None:
                project_data["style_image"] = style_filename
                project_data["style_description"] = style_description
                # 强互斥：自定义参考图与模版二选一。除了清 template_id，
                # 还需清掉之前由模板展开写入的 `style` prompt，否则生成链路会把
                # 模板 prompt 与 style_description 同时喂给 LLM，破坏二选一语义。
                project_data.pop("style_template_id", None)
                project_data["style"] = ""

            with project_change_source("webui"):
                get_project_manager().update_project(project_name, _mutate)

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
    except VisionCapabilityError as e:
        raise HTTPException(
            status_code=400,
            detail=_t("vision_model_required", provider=e.provider_id, model=e.model_id, task=e.task_type.value),
        )
    except Exception:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error"))
