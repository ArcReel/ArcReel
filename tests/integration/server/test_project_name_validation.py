"""按项目名寻址的路由统一校验项目名：非法名一律 400，合法但不存在的项目维持 404。

路由从应用路由表枚举，新增的项目路由自动纳入断言。
"""

import re

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from lib.i18n.zh import errors as zh_errors
from server.app import app
from server.auth import CurrentUserInfo, get_current_user

_INVALID_NAME = "bad_name"
_PATH_PARAM = re.compile(r"\{[^}]+\}")


def _addresses_a_project(path: str) -> bool:
    return "{project_name}" in path or "/projects/{name}/" in f"{path}/"


def _project_routes() -> list[tuple[str, str]]:
    routes = []
    for route in iter_route_contexts(app.routes):
        path = route.path
        if not path or not route.methods or not _addresses_a_project(path):
            continue
        routes.extend((method, path) for method in sorted(route.methods))
    return routes


_PROJECT_ROUTES = _project_routes()


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_route_table_has_project_routes():
    assert len(_PROJECT_ROUTES) > 20


@pytest.mark.parametrize(("method", "path"), _PROJECT_ROUTES, ids=[f"{m} {p}" for m, p in _PROJECT_ROUTES])
def test_invalid_project_name_is_rejected_with_400(app_client, method, path):
    project_param = "{project_name}" if "{project_name}" in path else "{name}"
    url = _PATH_PARAM.sub("x1", path.replace(project_param, _INVALID_NAME))

    resp = app_client.request(method, url, json={})

    assert resp.status_code == 400
    assert resp.json()["detail"] == zh_errors.MESSAGES["invalid_project_name"].format(name=_INVALID_NAME)


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_missing_project_with_valid_name_stays_404(app_client, method):
    resp = app_client.request(method, "/api/v1/projects/missing-project")

    assert resp.status_code == 404
