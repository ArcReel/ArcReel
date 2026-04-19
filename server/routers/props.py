"""道具管理路由（CRUD 由 _asset_router_factory 统一生成）。"""

from lib import PROJECT_ROOT
from lib.project_manager import ProjectManager
from server.routers._asset_router_factory import build_asset_router

pm = ProjectManager(PROJECT_ROOT / "projects")


def get_project_manager() -> ProjectManager:
    return pm


# late-binding 必需：测试通过 monkeypatch.setattr(props, "get_project_manager", ...) 替换模块属性
router = build_asset_router(asset_type="prop", pm_getter=lambda: get_project_manager())  # noqa: PLW0108
