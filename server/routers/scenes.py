"""Scene routes (CRUD generated centrally by _asset_router_factory)."""

from lib.project.project_manager import get_project_manager
from server.routers._asset_router_factory import build_asset_router

# Late binding required: tests use monkeypatch.setattr(scenes, "get_project_manager", ...) to replace module attributes
router = build_asset_router(asset_type="scene", pm_getter=lambda: get_project_manager())
