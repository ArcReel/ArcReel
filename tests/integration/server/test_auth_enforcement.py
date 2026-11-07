"""Authentication coverage tests.

Enumerate every API operation through ``app.openapi()`` and prove unauthenticated requests return 401.
``server/app.py`` router registration declares authentication; one omitted line exposes an entire router.

Exempt endpoints skip traversal, so separate positive assertions prove their defenses: public endpoints
allow anonymous access; endpoints with their own authentication still reject anonymous requests.
"""

import os
import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server.auth as auth_module

# Traverse the production app's full routing/authentication stack; pytest marker rules classify this as integration.

# Anonymous access: login obtains tokens; static media load through <img src> / <video src>.
PUBLIC_OPERATIONS = frozenset(
    {
        "GET /health",
        "GET /api/v1/auth/status",
        "POST /api/v1/auth/token",
        "GET /api/v1/files/{project_name}/{path}",
        "GET /api/v1/global-assets/{asset_type}/{filename}",
    }
)

# Own authentication: native browser download navigation cannot carry Authorization headers; endpoints validate short-lived download tokens.
SELF_AUTH_OPERATIONS = frozenset(
    {
        "GET /api/v1/projects/{name}/export",
        "GET /api/v1/projects/{project_name}/edit-timelines/{timeline_id}/jianying-draft/download",
    }
)

EXEMPT_OPERATIONS = PUBLIC_OPERATIONS | SELF_AUTH_OPERATIONS

# SSE uses ordinary protected routers, consumed by frontend fetch with Authorization headers;
# session credentials never accept query-param form; see docs/adr/0071.
SSE_PATHS = (
    "/api/v1/projects/demo/assistant/sessions/s1/entries/stream",
    "/api/v1/projects/demo/assistant/sessions/s1/stream",
    "/api/v1/projects/demo/events/stream",
)

# Protected authentication-pass probe: its handler reads only process constants. Preserve that property when changing endpoints;
# databases or other lifespan state cause clean-environment 500 failures that obscure authentication conclusions.
_PROBE_ENDPOINT = "/api/v1/custom-providers/endpoints"

# Every OpenAPI Path Item operation field, ensuring new routes are automatically covered:
# an omitted method silently skips its endpoints instead of raising.
_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_PARAM_RE = re.compile(r"\{([^}]+)\}")
# Path-parameter placeholders need only produce valid URLs: authentication precedes parameter validation.
_PARAM_PLACEHOLDERS = {"path": "x/y.png"}


# Module-scoped shared authentication environment; clear private caches on entry/exit to avoid polluting other modules.
@pytest.fixture(scope="module", autouse=True)
def auth_env():
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
def auth_coverage_client():
    from server.app import app

    # Skip lifespan (no with): authentication reads only environment/JWT; routes/openapi are ready at import.
    # Lifespan would add unrelated DB migration, worker startup, and sandbox detection,
    # also making results depend on host support for unprivileged user namespaces.
    return TestClient(app, raise_server_exceptions=False)


def _fill(path: str) -> str:
    return _PARAM_RE.sub(lambda m: _PARAM_PLACEHOLDERS.get(m.group(1), "1"), path)


def _all_operations() -> list[str]:
    """All API operations, e.g. ``"GET /api/v1/providers"``.

    Use public ``app.openapi()`` rather than route objects: ``include_router`` installs lazy wrappers whose
    expansion touches FastAPI internals. The tradeoff is no coverage for ``include_in_schema=False`` endpoints.
    """
    from server.app import app

    spec = app.openapi()
    return [f"{m.upper()} {path}" for path, ops in spec["paths"].items() for m in ops if m in _HTTP_METHODS]


def test_every_protected_endpoint_rejects_anonymous(auth_coverage_client):
    """Every nonexempt operation returns 401 without a token."""
    operations = _all_operations()
    assert len(operations) > 100, "枚举结果异常，覆盖断言会变成空跑"

    unprotected = []
    for op in operations:
        if op in EXEMPT_OPERATIONS:
            continue
        method, path = op.split(" ", 1)
        resp = auth_coverage_client.request(method, _fill(path), json={})
        if resp.status_code != 401:
            unprotected.append(f"{op} -> {resp.status_code}")

    assert not unprotected, "以下端点未被认证保护：\n" + "\n".join(sorted(unprotected))


def test_exempt_operations_are_registered():
    """Exemptions contain no renamed/deleted endpoints; stale entries misleadingly suggest intentional public access."""
    stale = EXEMPT_OPERATIONS - set(_all_operations())
    assert not stale, f"豁免清单引用了不存在的操作：{sorted(stale)}"


def test_public_endpoints_stay_reachable(auth_coverage_client):
    """Router dependencies do not accidentally block public endpoints."""
    assert auth_coverage_client.get("/health").status_code == 200
    assert auth_coverage_client.get("/api/v1/auth/status").status_code == 200
    assert (
        auth_coverage_client.post(
            "/api/v1/auth/token",
            data={"username": "testuser", "password": "testpass"},
        ).status_code
        == 200
    )
    # Static media: nonexistent projects return 404, not 401.
    assert auth_coverage_client.get("/api/v1/files/demo/x.png").status_code != 401
    assert auth_coverage_client.get("/api/v1/global-assets/characters/x.png").status_code != 401


def _login_token(client: TestClient) -> str:
    return client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass"},
    ).json()["access_token"]


@pytest.mark.parametrize("path", SSE_PATHS)
def test_sse_endpoints_reject_query_token(auth_coverage_client, path):
    """Long-lived session JWTs cannot subscribe through ?token=: even valid JWTs return 401."""
    token = _login_token(auth_coverage_client)
    response = auth_coverage_client.get(path, params={"token": token})
    assert response.status_code == 401


@pytest.mark.parametrize("path", SSE_PATHS)
def test_sse_endpoints_accept_bearer_token(auth_coverage_client, path):
    """Router dependencies allow Bearer JWTs.

    Subsequent status belongs to the endpoint (missing session, retired interface, or project events not
    initialized without lifespan); assert only that authentication did not reject the request.
    """
    token = _login_token(auth_coverage_client)
    response = auth_coverage_client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code != 401


@pytest.mark.parametrize("path", SSE_PATHS)
def test_sse_endpoints_accept_api_key(auth_coverage_client, path):
    """Router dependencies allow ``arc-`` API Keys."""
    key = "arc-auth-coverage-key"
    key_hash = auth_module.hash_api_key(key)
    auth_module._set_api_key_cache(key_hash, {"sub": "apikey:coverage", "via": "apikey"})
    try:
        response = auth_coverage_client.get(path, headers={"Authorization": f"Bearer {key}"})
        assert response.status_code != 401
    finally:
        auth_module.invalidate_api_key_cache(key_hash)


@pytest.mark.parametrize("path", SSE_PATHS)
def test_sse_endpoints_allow_anonymous_when_auth_disabled(auth_coverage_client, path):
    """With AUTH_ENABLED=false, SSE allows anonymous requests like other protected endpoints."""
    with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
        assert auth_coverage_client.get(path).status_code != 401


def test_trial_artifact_rejects_query_token(auth_coverage_client):
    """Trial artifacts must not expose reusable session credentials in URLs."""
    token = auth_coverage_client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass"},
    ).json()["access_token"]
    response = auth_coverage_client.get(
        "/api/v1/custom-endpoints/trial-runs/missing/artifact",
        params={"token": token},
    )
    assert response.status_code == 401


def test_trial_artifact_accepts_bearer_token(auth_coverage_client):
    token = auth_coverage_client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass"},
    ).json()["access_token"]
    response = auth_coverage_client.get(
        "/api/v1/custom-endpoints/trial-runs/missing/artifact",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/demo/export?download_token=not-a-valid-token",
        "/api/v1/projects/demo/edit-timelines/tl-00000000/jianying-draft/download"
        "?download_token=not-a-valid-token&draft_path=/tmp/d",
    ],
)
def test_export_endpoints_reject_forged_token(auth_coverage_client, path):
    """Exports use short-lived download tokens; verify_download_token must reject forged tokens."""
    assert auth_coverage_client.get(path).status_code in (401, 403)


def test_authenticated_request_passes(auth_coverage_client):
    """Router dependencies allow valid tokens."""
    token = auth_coverage_client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass"},
    ).json()["access_token"]
    assert auth_coverage_client.get(_PROBE_ENDPOINT, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_auth_disabled_bypasses_enforcement(auth_coverage_client):
    """AUTH_ENABLED=false preserves local unauthenticated deployments by allowing access."""
    with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
        assert auth_coverage_client.get(_PROBE_ENDPOINT).status_code == 200


def test_download_token_rejected_as_bearer(auth_coverage_client):
    """Download tokens with purpose claims are not session credentials: protected endpoints return 401 for them as Bearer tokens."""
    token = auth_module.create_download_token("testuser", "demo")
    response = auth_coverage_client.get(_PROBE_ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_export_endpoint_accepts_download_token(auth_coverage_client):
    """Download endpoints' own validation accepts the same token; authentication allows it and endpoints decide subsequent status."""
    token = auth_module.create_download_token("testuser", "demo")
    response = auth_coverage_client.get("/api/v1/projects/demo/export", params={"download_token": token})
    assert response.status_code not in (401, 403)
