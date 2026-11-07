"""Prop routes (CRUD generated centrally by _asset_router_factory)."""

from lib.project.project_manager import get_project_manager
from server.routers._asset_router_factory import build_asset_router

# Late binding required: tests use monkeypatch.setattr(props, "get_project_manager", ...) to replace module attributes
router = build_asset_router(asset_type="prop", pm_getter=lambda: get_project_manager())
