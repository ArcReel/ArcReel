"""角色管理路由（CRUD 由 _asset_router_factory 统一生成）。"""

from fastapi import HTTPException

from lib.i18n import Translator
from lib.project_manager import get_project_manager
from server.routers._asset_router_factory import build_asset_router
from server.services.project_character_images import (
    ProjectCharacterImageConflict,
    ProjectCharacterImageError,
    ProjectCharacterMainImageMissing,
    move_character_main_to_reference,
)

# late-binding 必需：测试通过 monkeypatch.setattr(characters, "get_project_manager", ...) 替换模块属性
router = build_asset_router(asset_type="character", pm_getter=lambda: get_project_manager())  # noqa: PLW0108


@router.post("/projects/{project_name}/characters/{character_name}/main-to-reference")
async def move_main_to_reference(project_name: str, character_name: str, _t: Translator):
    """Move the character card's current main image into its reference slot."""

    try:
        result = await move_character_main_to_reference(
            project_name,
            character_name,
            manager=get_project_manager(),
        )
    except ProjectCharacterMainImageMissing as exc:
        raise HTTPException(
            status_code=409,
            detail=_t("character_main_image_missing", name=character_name),
        ) from exc
    except ProjectCharacterImageConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=_t("character_main_image_changed", name=character_name),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_t("project_not_found", name=project_name)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_t("character_not_found", name=character_name)) from exc
    except ProjectCharacterImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "project_asset": result.project_asset,
        "source": result.source,
        "reference_path": result.reference_path,
    }
