"""产品管理路由（CRUD 由 _asset_router_factory 统一生成）。"""

from lib.project_manager import get_project_manager
from server.routers._asset_router_factory import build_asset_router

# late-binding 必需：测试通过 monkeypatch.setattr(products, "get_project_manager", ...) 替换模块属性
router = build_asset_router(asset_type="product", pm_getter=lambda: get_project_manager())  # noqa: PLW0108
