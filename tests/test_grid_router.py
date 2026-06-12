"""基本路由存在性测试：验证 grids router 注册了预期路径。"""

from server.routers.grids import router


class TestGridRouterExists:
    def test_router_has_routes(self):
        paths = [r.path for r in router.routes]
        assert any("generate/grid" in p for p in paths)
        assert any("/grids" in p for p in paths)

    def test_router_has_generate_grid_endpoint(self):
        paths = [r.path for r in router.routes]
        assert any("generate/grid/{episode}" in p for p in paths)

    def test_router_has_list_grids_endpoint(self):
        paths = [r.path for r in router.routes]
        assert any(p.endswith("/grids") for p in paths)

    def test_router_has_get_grid_endpoint(self):
        paths = [r.path for r in router.routes]
        assert any("/grids/{grid_id}" in p for p in paths)

    def test_router_has_regenerate_endpoint(self):
        paths = [r.path for r in router.routes]
        assert any("regenerate" in p for p in paths)


class TestAdProjectRejected:
    def test_generate_grid_rejects_ad_project(self, monkeypatch):
        """广告/短片项目不开放宫格生视频：动作端点直接 400。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from server.auth import CurrentUserInfo, get_current_user
        from server.routers import grids

        class _AdPM:
            def load_project(self, name):
                return {"content_mode": "ad", "title": "Ad", "episodes": []}

        monkeypatch.setattr(grids, "get_project_manager", lambda: _AdPM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(grids.router, prefix="/api/v1")
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/projects/demo/generate/grid/1",
                json={"script_file": "episode_1.json"},
            )
        assert resp.status_code == 400

    def test_regenerate_grid_rejects_ad_project(self, monkeypatch):
        """重生成端点同样封禁 ad:残留的历史 grid 记录不得被重新入队。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from server.auth import CurrentUserInfo, get_current_user
        from server.routers import grids

        class _AdPM:
            def load_project(self, name):
                return {"content_mode": "ad", "title": "Ad", "episodes": []}

        monkeypatch.setattr(grids, "get_project_manager", lambda: _AdPM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(grids.router, prefix="/api/v1")
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/grids/g-1/regenerate")
        assert resp.status_code == 400
