"""/health 端点：返回镜像构建 commit，供部署实例确认运行版本。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client():
    from server.app import app

    # 不进入 with，不跑 lifespan：/health 不依赖启动期的数据库与运行时目录
    return TestClient(app)


def test_health_reports_build_commit_from_image(app_client, monkeypatch):
    monkeypatch.setenv("ARCREEL_BUILD_COMMIT", "3f2c1a9e8b7d6c5f4e3d2c1b0a9f8e7d6c5b4a39")

    resp = app_client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["commit"] == "3f2c1a9e8b7d6c5f4e3d2c1b0a9f8e7d6c5b4a39"


def test_health_commit_is_null_when_run_from_source(app_client, monkeypatch):
    monkeypatch.delenv("ARCREEL_BUILD_COMMIT", raising=False)

    resp = app_client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "message": "视频项目管理 WebUI 运行正常", "commit": None}


def test_health_commit_is_null_when_image_built_without_commit(app_client, monkeypatch):
    # Dockerfile 未传 BUILD_COMMIT 时，ENV 得到空串
    monkeypatch.setenv("ARCREEL_BUILD_COMMIT", "")

    assert app_client.get("/health").json()["commit"] is None
