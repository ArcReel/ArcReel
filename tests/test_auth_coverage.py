"""认证覆盖测试。

从 ``app.openapi()`` 枚举全部 API 操作，逐个发未认证请求实证 401——认证要求声明在
``server/app.py`` 的 router 注册区块，漏挂一行整个 router 就会裸奔，这里是发现它的地方。

豁免清单里的端点会跳过上述遍历，所以每一条都另有正面断言证明它并非不设防：
公开端点断言匿名可达，自带认证端点断言匿名请求仍被拒。
"""

import os
import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server.auth as auth_module

# 穿过生产 app 的完整路由与认证栈，不是隔离单元，按 pytest markers 纪律属 integration。
pytestmark = pytest.mark.integration

# 匿名可达：登录入口是拿 token 的前提；静态媒体经 <img src> / <video src> 加载。
PUBLIC_OPERATIONS = frozenset(
    {
        "GET /health",
        "GET /api/v1/auth/status",
        "POST /api/v1/auth/token",
        "GET /api/v1/files/{project_name}/{path}",
        "GET /api/v1/global-assets/{asset_type}/{filename}",
    }
)

# 自带认证：浏览器直发请求带不了 Authorization header，端点内自行校验凭证。
SELF_AUTH_OPERATIONS = frozenset(
    {
        "GET /api/v1/projects/{project_name}/assistant/sessions/{session_id}/entries/stream",
        "GET /api/v1/projects/{project_name}/assistant/sessions/{session_id}/stream",
        "GET /api/v1/projects/{project_name}/events/stream",
        "GET /api/v1/projects/{name}/export",
        "GET /api/v1/projects/{name}/export/jianying-draft",
    }
)

EXEMPT_OPERATIONS = PUBLIC_OPERATIONS | SELF_AUTH_OPERATIONS

# 验证「放行」的探针端点：受保护，且处理函数只读进程内常量。换端点时要保持这个性质——
# 依赖数据库等 lifespan 建立的状态，会让这两个用例在干净环境里以 500 失败，掩盖认证结论。
_PROBE_ENDPOINT = "/api/v1/custom-providers/endpoints"

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_PARAM_RE = re.compile(r"\{([^}]+)\}")
# 路径参数占位值：认证在参数校验之前执行，取值只需能拼出合法 URL。
_PARAM_PLACEHOLDERS = {"path": "x/y.png"}


# module 级：整个文件共用一套认证环境；私有缓存在进出时清空，不污染其它测试模块。
@pytest.fixture(scope="module", autouse=True)
def _auth_env():
    auth_module._cached_token_secret = None
    auth_module._cached_password_hash = None
    with patch.dict(
        os.environ,
        {
            "AUTH_ENABLED": "true",
            "AUTH_USERNAME": "testuser",
            "AUTH_PASSWORD": "testpass",
            "AUTH_TOKEN_SECRET": "test-auth-coverage-secret-key-at-least-32-bytes",
        },
    ):
        yield
    auth_module._cached_token_secret = None
    auth_module._cached_password_hash = None


@pytest.fixture(scope="module")
def client():
    from server.app import app

    # 不进 lifespan（不用 with）：认证依赖只读环境变量与 JWT，路由和 openapi 在 import 时即就绪。
    # 跑 lifespan 会把 DB 迁移、worker 启动、沙箱探测一并拖进来，与本文件要验证的东西无关，
    # 还会让测试结果取决于 host 能否创建非特权 user namespace。
    return TestClient(app, raise_server_exceptions=False)


def _fill(path: str) -> str:
    return _PARAM_RE.sub(lambda m: _PARAM_PLACEHOLDERS.get(m.group(1), "1"), path)


def _all_operations() -> list[str]:
    """全部 API 操作，形如 ``"GET /api/v1/providers"``。

    走公开的 ``app.openapi()`` 而非路由对象——``include_router`` 挂的是惰性包装，
    展开它要碰 FastAPI 内部表示。代价是 ``include_in_schema=False`` 的端点不在覆盖内。
    """
    from server.app import app

    spec = app.openapi()
    return [f"{m.upper()} {path}" for path, ops in spec["paths"].items() for m in ops if m in _HTTP_METHODS]


def test_every_protected_endpoint_rejects_anonymous(client):
    """豁免清单之外的操作，无 token 请求一律 401。"""
    operations = _all_operations()
    assert len(operations) > 100, "枚举结果异常，覆盖断言会变成空跑"

    unprotected = []
    for op in operations:
        if op in EXEMPT_OPERATIONS:
            continue
        method, path = op.split(" ", 1)
        resp = client.request(method, _fill(path), json={})
        if resp.status_code != 401:
            unprotected.append(f"{op} -> {resp.status_code}")

    assert not unprotected, "以下端点未被认证保护：\n" + "\n".join(sorted(unprotected))


def test_exempt_operations_are_registered():
    """豁免清单不含已改名或删除的端点——过期条目会让人误以为某处仍有意开放。"""
    stale = EXEMPT_OPERATIONS - set(_all_operations())
    assert not stale, f"豁免清单引用了不存在的操作：{sorted(stale)}"


def test_public_endpoints_stay_reachable(client):
    """公开端点不被 router 级依赖误伤。"""
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/auth/status").status_code == 200
    assert (
        client.post(
            "/api/v1/auth/token",
            data={"username": "testuser", "password": "testpass"},
        ).status_code
        == 200
    )
    # 静态媒体：项目不存在时是 404，不该是 401。
    assert client.get("/api/v1/files/demo/x.png").status_code != 401
    assert client.get("/api/v1/global-assets/characters/x.png").status_code != 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/demo/assistant/sessions/s1/entries/stream",
        "/api/v1/projects/demo/assistant/sessions/s1/stream",
        "/api/v1/projects/demo/events/stream",
    ],
)
def test_sse_endpoints_reject_anonymous(client, path):
    """SSE 不挂 router 级依赖，其 CurrentUserFlexible 必须仍拦住无 token 的请求。"""
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/demo/export?download_token=not-a-valid-token",
        "/api/v1/projects/demo/export/jianying-draft?download_token=not-a-valid-token&episode=1&draft_path=/tmp/d",
    ],
)
def test_export_endpoints_reject_forged_token(client, path):
    """导出走短时效下载 token，伪造的必须被 verify_download_token 拒绝。"""
    assert client.get(path).status_code in (401, 403)


def test_authenticated_request_passes(client):
    """带合法 token 时 router 级依赖放行。"""
    token = client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass"},
    ).json()["access_token"]
    assert client.get(_PROBE_ENDPOINT, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_auth_disabled_bypasses_enforcement(client):
    """AUTH_ENABLED=false 时不拦截，保持本地无认证部署可用。"""
    with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
        assert client.get(_PROBE_ENDPOINT).status_code == 200
