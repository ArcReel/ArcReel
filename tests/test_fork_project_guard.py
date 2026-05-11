"""fork-private: 项目级访问守卫端到端测试。

覆盖：
1. 路由 dep 注入覆盖率：项目级路径都应被守卫，``/export*`` / 全局列表等不应被守卫。
2. 上游 path param 改名漂移检测：若上游改了 ``project_name`` 字面量，覆盖率会下降。
3. POST /projects body owner 前缀注入：admin 与 user 都自动加 ``<sub>__``。
4. GET /projects 响应过滤：admin 见全部，user 仅见自己；并注入 ``owner`` 字段。
5. 项目级路径鉴权：admin 可任意访问；user 仅能访问 ``<self>__*``，对他人 403、对无 owner 历史项目 403。
"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from server.app import app
from server.fork_project_guard import (
    _FLEXIBLE_PATH_RE,
    _PROJECT_PARAMS,
    _route_has_project_param,
    _should_skip,
)


def _project_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute) and _route_has_project_param(r) and not _should_skip(r)]


class TestRouteCoverage:
    """守卫挂载覆盖率 — 上游改路径会让本类断言失败提醒维护者。"""

    def test_at_least_one_project_route_attached(self):
        assert len(_project_routes()) >= 10, (
            "项目级路由数量明显偏低，怀疑上游已改 path param 名 "
            f"（当前匹配 {_PROJECT_PARAMS}），请同步更新 server/fork_project_guard.py"
        )

    def test_export_paths_are_skipped(self):
        export_paths = [
            r.path_format
            for r in app.routes
            if isinstance(r, APIRoute) and re.match(r"^/api/v1/projects/\{name\}/export", r.path_format)
        ]
        assert export_paths, "上游已删 export 路径？请重新评估守卫白名单"
        for r in app.routes:
            if isinstance(r, APIRoute) and r.path_format in export_paths:
                assert _should_skip(r), f"{r.path_format} 应被守卫白名单跳过"

    def test_flexible_routes_detected(self):
        """SSE 类路径 (`/stream` 结尾) 应识别为 flexible — 兼容 EventSource ?token= 鉴权。"""
        sse_routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path_format.endswith("/stream")]
        assert sse_routes, "上游已删 SSE 路径？"
        for r in sse_routes:
            assert _FLEXIBLE_PATH_RE.search(r.path_format), f"{r.path_format} 应被识别为 flexible"


def _token(username: str, role: str, monkeypatch) -> str:
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-secret-key-that-is-at-least-32-bytes")
    from server import auth as auth_module

    auth_module._cached_token_secret = None
    return auth_module.create_token(username, role=role)


class TestProjectAccess:
    """端到端：user 仅可访问 ``<self>__*``，admin 任意。"""

    def test_user_cannot_access_others_project(self, monkeypatch):
        token = _token("bob", "user", monkeypatch)
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(
                "/api/v1/projects/alice__demo",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 403

    def test_user_cannot_access_legacy_unprefixed_project(self, monkeypatch):
        token = _token("bob", "user", monkeypatch)
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(
                "/api/v1/projects/legacy-demo",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 403

    def test_admin_can_access_any(self, monkeypatch):
        token = _token("admin", "admin", monkeypatch)
        with TestClient(app, raise_server_exceptions=False) as c:
            # 项目不存在 → 404；只要不是 403/401 即守卫放行
            r = c.get(
                "/api/v1/projects/alice__nonexistent",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code not in (401, 403)


class TestExportPathBypass:
    """`/export*` 用 download_token 自校验，不应被 Bearer 守卫拦截。"""

    def test_export_route_does_not_require_bearer(self, monkeypatch):
        # 不带 Authorization；应跑到 handler 自己的 download_token 校验（缺 token → 401，
        # 但来源是 verify_download_token 而非守卫；这里只关心不会因守卫导致 403）。
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/projects/anyname/export")
            assert r.status_code != 403


class TestListAndCreateRewrite:
    """``GET /projects`` 过滤 + ``POST /projects`` 前缀注入。"""

    def test_list_response_filters_and_injects_owner(self, monkeypatch, tmp_path):
        token = _token("bob", "user", monkeypatch)
        # 准备两个项目目录：bob__visible / alice__hidden / legacy-unprefixed
        from lib import project_manager as pm_module

        # 临时项目根
        root = tmp_path / "projects"
        root.mkdir()
        for name in ("bob__visible", "alice__hidden", "legacy-unprefixed"):
            (root / name).mkdir()
        monkeypatch.setattr(pm_module, "PROJECT_ROOT_DEFAULT_USED", True, raising=False)

        # monkeypatch routers 的 pm 实例
        from server.routers import projects as proj_module

        monkeypatch.setattr(proj_module, "pm", pm_module.ProjectManager(root))

        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
            names = {p["name"] for p in r.json().get("projects", [])}
            assert names == {"bob__visible"}
            # 每条都带 owner 字段
            for p in r.json().get("projects", []):
                assert "owner" in p

    def test_create_post_injects_owner_prefix(self, monkeypatch, tmp_path):
        token = _token("bob", "user", monkeypatch)

        from lib import project_manager as pm_module
        from server.routers import projects as proj_module

        root = tmp_path / "projects"
        root.mkdir()
        monkeypatch.setattr(proj_module, "pm", pm_module.ProjectManager(root))

        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post(
                "/api/v1/projects",
                json={"name": "my-project", "style": "Anime", "content_mode": "narration"},
                headers={"Authorization": f"Bearer {token}"},
            )
            # 不关心业务细节；只关心 name 已被前缀化
            data = r.json()
            assert r.status_code == 200, data
            assert data.get("name") == "bob__my-project"
