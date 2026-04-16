"""道具管理路由（CRUD 由 _bucket_router_factory 统一生成）。"""

from lib import PROJECT_ROOT
from lib.project_manager import ProjectManager
from server.routers._bucket_router_factory import build_bucket_router

pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


router = build_bucket_router(
    bucket_key="props",
    sheet_field="prop_sheet",
    path_segment="props",
    result_key="prop",
    i18n_exists_key="prop_already_exists",
    i18n_not_found_key="prop_not_found",
    i18n_deleted_key="prop_deleted",
    add_method=lambda m, project, name, desc: m.add_prop(project, name, desc),
    pm_getter=lambda: get_project_manager(),
)
