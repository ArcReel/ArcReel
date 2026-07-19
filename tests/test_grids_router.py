"""宫格图路由的「未预期异常 → 通用 500 且不泄露内部细节」回归测试。

每个端点内最早调用 get_project_manager()，把它 monkeypatch 成抛 RuntimeError
（带唯一哨兵串），异常沿 app 级 exception handler 统一收口为通用 500。断言响应 500
且哨兵串不出现在响应体里——验证内部异常细节仅落服务端日志、不泄露给客户端。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import grids


def _client(monkeypatch, **patches):
    for name, fn in patches.items():
        monkeypatch.setattr(grids, name, fn)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(grids.router, prefix="/api/v1")
    register_error_handlers(app)
    # app 级 Exception handler 已把未预期异常收口为 500；关闭 TestClient 的默认重抛，
    # 以便断言收口后的响应体（而非让异常穿透到测试栈）。
    return TestClient(app, raise_server_exceptions=False)


def test_generate_grid_unexpected_error_no_leak(monkeypatch):
    # generate_grid 末端 catch-all：load_project 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_generate")),
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 500
        assert "LEAK_generate" not in resp.text


def test_list_grids_unexpected_error_no_leak(monkeypatch):
    # list_grids 末端 catch-all：get_project_path 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_list")),
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids")
        assert resp.status_code == 500
        assert "LEAK_list" not in resp.text


def test_get_grid_unexpected_error_no_leak(monkeypatch):
    # get_grid 末端 catch-all：get_project_path 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_get")),
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids/grid-123")
        assert resp.status_code == 500
        assert "LEAK_get" not in resp.text


def test_regenerate_grid_unexpected_error_no_leak(monkeypatch):
    # regenerate_grid 末端 catch-all：load_project 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_regen")),
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        assert resp.status_code == 500
        assert "LEAK_regen" not in resp.text
