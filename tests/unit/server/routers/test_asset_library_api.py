"""Provider asset-library API: control-plane accounts, inference-credential bindings, asset browsing.

Replace provider network boundaries only: respx intercepts control-plane requests, asserting actual hosts/signatures/bodies.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.asset_library.bindings import resolve_bound_scope
from lib.db import get_async_session
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.error_handlers import register_error_handlers
from server.routers import asset_library
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.http_capture import capture_http, only_request, request_json

AK = "TEST-CONTROL-PLANE-AK-0001"
SK = "TEST-CONTROLPLANE-SECRET-0001"
VOLC_HOST = "https://ark.cn-beijing.volcengineapi.com/"


@pytest.fixture
def asset_library_sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def asset_api(asset_library_sessions) -> Generator[TestClient]:
    app = FastAPI()

    async def _session():
        async with asset_library_sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    override_auth(app)
    app.include_router(asset_library.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    with TestClient(app) as c:
        yield c


def _create_account(asset_api: TestClient, **overrides) -> dict:
    body = {
        "site": "volcengine",
        "name": "主账号",
        "project_name": "film",
        "access_key_id": AK,
        "secret_access_key": SK,
    }
    body.update(overrides)
    resp = asset_api.post("/api/v1/asset-library/accounts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestAccounts:
    def test_secrets_are_masked_and_region_comes_from_site(self, asset_api: TestClient):
        created = _create_account(asset_api)
        listed = asset_api.get("/api/v1/asset-library/accounts").json()

        assert created["region"] == "cn-beijing"
        assert {s["id"] for s in listed["sites"]} == {"volcengine", "byteplus"}
        assert listed["accounts"][0]["access_key_id_masked"] == f"{AK[:4]}…{AK[-4:]}"
        assert AK not in str(listed)
        assert SK not in str(listed)

    def test_rejects_unregistered_region(self, asset_api: TestClient):
        resp = asset_api.post(
            "/api/v1/asset-library/accounts",
            json={"site": "byteplus", "name": "x", "region": "eu-west-1", "access_key_id": AK, "secret_access_key": SK},
        )
        assert resp.status_code == 422


class TestBindings:
    async def _seed_credentials(self, asset_library_sessions) -> tuple[int, int]:
        async with asset_library_sessions() as session:
            ark = await CredentialRepository(session).create(
                provider="ark", name="Ark 主 Key", api_key="ark-key-0001-abcdef"
            )
            custom = await CustomProviderRepository(session).create_provider(
                display_name="中转",
                discovery_format="openai",
                base_url="https://relay.example.com/api/v3",
                api_key="relay-key-1",
            )
            await session.commit()
            return ark.id, custom.id

    async def test_binding_requires_reconfirmation_after_credential_change(
        self, asset_api: TestClient, asset_library_sessions
    ):
        ark_id, custom_id = await self._seed_credentials(asset_library_sessions)
        account = _create_account(asset_api)

        listed = asset_api.get("/api/v1/asset-library/bindings").json()["credentials"]
        assert {(c["credential_type"], c["credential_id"]) for c in listed} == {
            ("builtin", ark_id),
            ("custom", custom_id),
        }
        assert all(c["binding"] is None for c in listed)
        assert "ark-key-0001-abcdef" not in str(listed)

        resp = asset_api.put(f"/api/v1/asset-library/bindings/builtin/{ark_id}", json={"account_id": account["id"]})
        assert resp.json() == {"account_id": account["id"], "status": "confirmed"}

        async with asset_library_sessions() as session:
            scope = await resolve_bound_scope(
                session,
                credential_type="builtin",
                credential_id=ark_id,
                api_key="ark-key-0001-abcdef",
                base_url=None,
                default_base_url="https://ark.cn-beijing.volces.com/api/v3",
            )
            assert scope is not None
            assert (scope.site, scope.region, scope.project_name) == ("volcengine", "cn-beijing", "film")
            # Actual invocation endpoints differ from confirmation: not applicable.
            assert (
                await resolve_bound_scope(
                    session,
                    credential_type="builtin",
                    credential_id=ark_id,
                    api_key="ark-key-0001-abcdef",
                    base_url="https://other.example.com/api/v3",
                )
                is None
            )
            # Replacing Keys on the same record never inherits consent, even with unchanged record IDs.
            await CredentialRepository(session).update(ark_id, api_key="ark-key-of-another-account")
            await session.commit()

        binding = next(
            c
            for c in asset_api.get("/api/v1/asset-library/bindings").json()["credentials"]
            if c["credential_id"] == ark_id and c["credential_type"] == "builtin"
        )
        assert binding["binding"] == {"account_id": account["id"], "status": "needs_confirmation"}
        async with asset_library_sessions() as session:
            assert (
                await resolve_bound_scope(
                    session,
                    credential_type="builtin",
                    credential_id=ark_id,
                    api_key="ark-key-of-another-account",
                    base_url=None,
                    default_base_url="https://ark.cn-beijing.volces.com/api/v3",
                )
                is None
            )

        asset_api.put(f"/api/v1/asset-library/bindings/builtin/{ark_id}", json={"account_id": account["id"]})
        async with asset_library_sessions() as session:
            assert (
                await resolve_bound_scope(
                    session,
                    credential_type="builtin",
                    credential_id=ark_id,
                    api_key="ark-key-of-another-account",
                    base_url=None,
                    default_base_url="https://ark.cn-beijing.volces.com/api/v3",
                )
                is not None
            )

    async def test_changing_account_scope_requires_reconfirmation(self, asset_api: TestClient, asset_library_sessions):
        _, custom_id = await self._seed_credentials(asset_library_sessions)
        account = _create_account(asset_api)
        asset_api.put(f"/api/v1/asset-library/bindings/custom/{custom_id}", json={"account_id": account["id"]})

        asset_api.patch(f"/api/v1/asset-library/accounts/{account['id']}", json={"project_name": "another"})

        binding = next(
            c
            for c in asset_api.get("/api/v1/asset-library/bindings").json()["credentials"]
            if c["credential_type"] == "custom"
        )
        assert binding["binding"]["status"] == "needs_confirmation"

        assert asset_api.delete(f"/api/v1/asset-library/bindings/custom/{custom_id}").status_code == 204
        binding = next(
            c
            for c in asset_api.get("/api/v1/asset-library/bindings").json()["credentials"]
            if c["credential_type"] == "custom"
        )
        assert binding["binding"] is None


_LIST_RESULT = {
    "ResponseMetadata": {"RequestId": "req-1", "Action": "ListAssets", "Version": "2024-01-01"},
    "Result": {
        "Items": [
            {
                "Id": "asset-20260101-a",
                "Name": "主角正面",
                "URL": "https://ark-asset.example.com/a.png?X-Tos-Signature=signed",
                "GroupId": "group-1",
                "AssetType": "Image",
                "Status": "Active",
                "ProjectName": "film",
                "CreateTime": "2026-01-01T00:00:00Z",
            },
            {
                "Id": "asset-20260101-b",
                "Name": "失败的图",
                "GroupId": "group-1",
                "AssetType": "Image",
                "Status": "Failed",
                "Error": {"Code": "InputImageSensitiveContentDetected", "Message": "sensitive"},
            },
        ],
        "TotalCount": 42,
        "PageNumber": 2,
        "PageSize": 2,
    },
}


class TestBrowseAssets:
    def test_lists_scope_assets_from_the_site_host_with_signed_request(self, asset_api: TestClient, caplog):
        account = _create_account(asset_api)
        caplog.set_level(logging.DEBUG)
        with capture_http() as router:
            route = router.post(VOLC_HOST).respond(json=_LIST_RESULT)
            resp = asset_api.get(
                f"/api/v1/asset-library/accounts/{account['id']}/assets", params={"page": 2, "page_size": 2}
            )

        request = only_request(route)
        assert request.url.params["Action"] == "ListAssets"
        assert request.url.params["Version"] == "2024-01-01"
        assert request.headers["Authorization"].startswith(f"HMAC-SHA256 Credential={AK}/")
        assert "/cn-beijing/ark/request" in request.headers["Authorization"]
        assert request_json(request) == {
            "Filter": {"GroupType": "AIGC"},
            "PageNumber": 2,
            "PageSize": 2,
            "SortBy": "CreateTime",
            "SortOrder": "Desc",
            "ProjectName": "film",
        }
        body = resp.json()
        assert resp.status_code == 200
        assert body["total"] == 42
        assert body["page"] == 2
        assert [(i["id"], i["asset_type"], i["status"], i["error_code"]) for i in body["items"]] == [
            ("asset-20260101-a", "Image", "active", None),
            ("asset-20260101-b", "Image", "failed", "InputImageSensitiveContentDetected"),
        ]
        # Never return provider signed download URLs.
        assert "X-Tos-Signature" not in resp.text
        assert SK not in caplog.text
        assert SK not in str(request.headers)

    def test_empty_library(self, asset_api: TestClient):
        account = _create_account(asset_api)
        with capture_http() as router:
            router.post(VOLC_HOST).respond(json={"ResponseMetadata": {}, "Result": {"Items": [], "TotalCount": 0}})
            resp = asset_api.get(f"/api/v1/asset-library/accounts/{account['id']}/assets")
        assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}

    def test_byteplus_account_uses_byteplus_control_plane(self, asset_api: TestClient):
        account = _create_account(asset_api, site="byteplus")
        with capture_http() as router:
            route = router.post("https://ark.ap-southeast-1.byteplusapi.com/").respond(json={"Result": {"Items": []}})
            asset_api.get(f"/api/v1/asset-library/accounts/{account['id']}/assets")
        assert "/ap-southeast-1/ark/request" in only_request(route).headers["Authorization"]

    def test_provider_rejection_returns_localized_reason_with_folded_original(self, asset_api: TestClient):
        account = _create_account(asset_api)
        error = {"ResponseMetadata": {"RequestId": "req-9", "Error": {"Code": "InvalidAccessKey", "Message": "bad ak"}}}
        with capture_http() as router:
            router.post(VOLC_HOST).respond(status_code=401, json=error)
            resp = asset_api.get(
                f"/api/v1/asset-library/accounts/{account['id']}/assets", headers={"Accept-Language": "zh"}
            )
        assert resp.status_code == 502
        assert "控制面凭证" in resp.json()["detail"]
        assert resp.json()["diagnostic"]["code"] == "InvalidAccessKey"
        assert SK not in resp.text

    def test_network_failure(self, asset_api: TestClient):
        account = _create_account(asset_api)
        with capture_http() as router:
            router.post(VOLC_HOST).mock(side_effect=httpx.ConnectError("boom"))
            resp = asset_api.get(
                f"/api/v1/asset-library/accounts/{account['id']}/assets", headers={"Accept-Language": "zh"}
            )
        assert resp.status_code == 502
        assert "无法连接素材库" in resp.json()["detail"]

    def test_missing_account(self, asset_api: TestClient):
        assert asset_api.get("/api/v1/asset-library/accounts/999/assets").status_code == 404
