"""场景管理路由（CRUD 由 _asset_router_factory 统一生成）。"""

from lib.project.project_manager import get_project_manager
from server.routers._asset_router_factory import build_asset_router

router = build_asset_router(asset_type="scene", pm_getter=lambda: get_project_manager())
