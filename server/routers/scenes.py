"""场景管理路由（CRUD 由 _bucket_router_factory 统一生成）。"""

from lib import PROJECT_ROOT
from lib.project_manager import ProjectManager
from server.routers._bucket_router_factory import build_bucket_router

pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


router = build_bucket_router(
    bucket_key="scenes",
    sheet_field="scene_sheet",
    path_segment="scenes",
    result_key="scene",
    i18n_exists_key="project_scene_already_exists",
    i18n_not_found_key="project_scene_not_found",
    i18n_deleted_key="project_scene_deleted",
    add_method=ProjectManager.add_project_scene,
    # late-binding 必需：测试通过 monkeypatch.setattr(scenes, "get_project_manager", ...) 替换模块属性
    pm_getter=lambda: get_project_manager(),  # noqa: PLW0108
)
