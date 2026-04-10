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
