"""分享提交的 HTTP 契约：本地预检、代理创建提交、状态查询与错误码落地。

官方服务出站由 respx 在 transport 层拦截。
"""

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db import get_async_session
from lib.market.official_service import OfficialServiceGateway, get_official_service_gateway
from server.error_handlers import register_error_handlers
from server.routers import custom_endpoints, market_submissions
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition

OFFICIAL_SERVICE = "https://official.test"
SUBMISSIONS_URL = f"{OFFICIAL_SERVICE}/api/v1/market/submissions"
TOKEN = "3f1c" + "0" * 28
PR_URL = "https://github.com/ArcReel/arcreel-market/pull/101"
SQUARE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"></svg>'

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


def _client_factory(session_factory: async_sessionmaker[AsyncSession]) -> ClientFactory:
    """``base_url`` 为官方服务地址，空串即关闭。"""

    async def session_override():
        async with session_factory() as session:
            yield session

    @asynccontextmanager
    async def factory(base_url: str = OFFICIAL_SERVICE) -> AsyncGenerator[httpx.AsyncClient]:
        app = FastAPI()
        async with httpx.AsyncClient() as network:
            app.dependency_overrides[get_async_session] = session_override
            app.dependency_overrides[get_official_service_gateway] = lambda: OfficialServiceGateway(
                session_factory, base_url=base_url, http_client=lambda: network
            )
            app.include_router(market_submissions.router)
            app.include_router(custom_endpoints.router)
            register_error_handlers(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    return factory


@pytest.fixture
async def client_factory(session_factory: async_sessionmaker[AsyncSession]) -> ClientFactory:
    return _client_factory(session_factory)


@pytest.fixture
async def concurrent_client_factory(concurrent_session_factory: async_sessionmaker[AsyncSession]) -> ClientFactory:
    """请求各用独立连接，用于并发写入。"""
    return _client_factory(concurrent_session_factory)


async def _create_endpoint(client: httpx.AsyncClient, **overrides: Any) -> int:
    response = await client.post("/custom-endpoints", json=custom_endpoint_definition(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _status_body(status: str = "open", slug: str = "acme-video") -> dict[str, str]:
    return {"token": TOKEN, "type": "endpoint", "slug": slug, "status": status, "pr_url": PR_URL}


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


async def test_submission_proxies_definition_icon_and_meta(client_factory: ClientFactory):
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock(assert_all_called=True) as remote:
            create = remote.post(SUBMISSIONS_URL).respond(201, json=_status_body())
            response = await client.post(
                "/market/submissions",
                json={
                    "endpoint_id": endpoint_id,
                    "slug": "acme-video",
                    "github_username": "octo-cat",
                    "icon": {"filename": "icon.svg", "content": _b64(SQUARE_SVG)},
                },
            )
            assert response.status_code == 200, response.text
            request = create.calls.last.request
    payload = json.loads(request.content)
    assert set(payload) == {"type", "slug", "files", "meta"}
    assert payload["type"] == "endpoint"
    assert payload["slug"] == "acme-video"
    assert payload["meta"] == {"github_username": "octo-cat"}
    assert set(payload["files"]) == {"definition.json", "icon.svg"}
    assert json.loads(base64.b64decode(payload["files"]["definition.json"])) == custom_endpoint_definition()
    assert base64.b64decode(payload["files"]["icon.svg"]) == SQUARE_SVG
    assert uuid.UUID(request.headers["X-ArcReel-Instance"]).version == 4
    assert response.json() == {
        "endpoint_id": endpoint_id,
        "endpoint_key": f"ce-{endpoint_id}",
        "endpoint_display_name": "示例端点",
        "type": "endpoint",
        "slug": "acme-video",
        "status": "open",
        "pr_url": PR_URL,
        "stale": False,
    }


async def test_check_reports_local_diagnostics(client_factory: ClientFactory):
    wide_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"></svg>'
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock() as remote:
            clean = await client.post("/market/submissions/check", json={"endpoint_id": endpoint_id, "slug": "acme"})
            bad_slug = await client.post(
                "/market/submissions/check", json={"endpoint_id": endpoint_id, "slug": "Acme!"}
            )
            bad_icon = await client.post(
                "/market/submissions/check",
                json={
                    "endpoint_id": endpoint_id,
                    "slug": "acme",
                    "icon": {"filename": "icon.svg", "content": _b64(wide_svg)},
                },
            )
            assert not remote.calls
    assert clean.json() == {"diagnostics": []}
    assert [(d["file"], d["code"]) for d in bad_slug.json()["diagnostics"]] == [("", "val_market_slug_invalid")]
    assert [(d["file"], d["code"], d["message"]) for d in bad_icon.json()["diagnostics"]] == [
        ("icon.svg", "val_market_icon_not_square", "图标必须是正方形，当前 64×32")
    ]


async def test_submission_rejected_locally_without_outbound(client_factory: ClientFactory):
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock() as remote:
            response = await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "-bad"})
            assert not remote.calls
    assert response.status_code == 422
    assert [d["code"] for d in response.json()["diagnostic"]["diagnostics"]] == ["val_market_slug_invalid"]


@pytest.mark.parametrize("path", ["/market/submissions/check", "/market/submissions"])
@pytest.mark.parametrize("content", ["not base64!", "é"])
async def test_icon_that_is_not_base64_is_rejected_locally(client_factory: ClientFactory, path: str, content: str):
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock() as remote:
            response = await client.post(
                path,
                json={
                    "endpoint_id": endpoint_id,
                    "slug": "acme-video",
                    "icon": {"filename": "icon.png", "content": content},
                },
            )
            assert not remote.calls
    assert response.status_code == 422
    assert response.json()["detail"] == "图标文件内容无效"


async def test_disabled_official_service_makes_no_outbound(client_factory: ClientFactory):
    async with client_factory(base_url="") as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock() as remote:
            created = await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme"})
            listed = await client.get("/market/submissions")
            assert not remote.calls
    assert (created.status_code, listed.status_code) == (409, 409)
    assert created.json()["detail"] == "官方服务已关闭"


@pytest.mark.parametrize(
    ("status", "code", "params", "detail"),
    [
        (409, "submission_in_progress", {}, "上一次提交仍在处理中，请稍后重试"),
        (429, "submission_open_limit", {"limit": 2}, "审核中的提交已达上限（2 个），请等待审核结果后再提交"),
        (413, "payload_too_large", {"max_bytes": 2097152}, "提交内容过大（上限 2097152 字节）"),
        (422, "submission_type_unsupported", {"type": "endpoint"}, "官方市场暂不接受此类条目（endpoint）"),
    ],
)
async def test_submission_error_code_lands_in_local_response(
    client_factory: ClientFactory, status: int, code: str, params: dict[str, Any], detail: str
):
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock(assert_all_called=True) as remote:
            remote.post(SUBMISSIONS_URL).respond(status, json={"code": code, "params": params})
            response = await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme"})
        listed = await _list_without_outbound(client)
    assert response.status_code == status
    assert response.json() == {"detail": detail, "diagnostic": {"official_service": {"code": code, "params": params}}}
    assert listed == []


async def _list_without_outbound(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    with respx.mock():
        response = await client.get("/market/submissions")
    assert response.status_code == 200, response.text
    return response.json()["submissions"]


async def test_remote_schema_diagnostics_are_translated(client_factory: ClientFactory):
    params = {
        "diagnostics": [
            {
                "file": "icon.svg",
                "path": "$",
                "code": "val_market_icon_not_square",
                "params": {"width": "64", "height": "32"},
            },
        ]
    }
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock(assert_all_called=True) as remote:
            remote.post(SUBMISSIONS_URL).respond(422, json={"code": "submission_invalid", "params": params})
            response = await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme"})
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "提交内容未通过官方市场的校验"
    assert body["diagnostic"]["official_service"] == {"code": "submission_invalid", "params": params}
    assert body["diagnostic"]["diagnostics"] == [
        {
            "file": "icon.svg",
            "path": "$",
            "code": "val_market_icon_not_square",
            "message": "图标必须是正方形，当前 64×32",
        }
    ]


async def test_status_refresh_lands_in_local_response(client_factory: ClientFactory):
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock(assert_all_called=True) as remote:
            remote.post(SUBMISSIONS_URL).respond(201, json=_status_body())
            await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme-video"})
        with respx.mock(assert_all_called=True) as remote:
            status = remote.get(f"{SUBMISSIONS_URL}/{TOKEN}").respond(200, json=_status_body("merged"))
            merged = await client.get("/market/submissions")
            assert status.call_count == 1
        # 已采纳是终态，之后进入页面不再查询。
        after = await _list_without_outbound(client)
    assert [(s["endpoint_id"], s["status"], s["pr_url"], s["stale"]) for s in merged.json()["submissions"]] == [
        (endpoint_id, "merged", PR_URL, False)
    ]
    assert [s["status"] for s in after] == ["merged"]


async def test_unreachable_official_service_keeps_last_status(client_factory: ClientFactory):
    async with client_factory() as client:
        first = await _create_endpoint(client)
        second = await _create_endpoint(client, meta={"name": "另一个", "author": "ArcReel", "version": "0.1.0"})
        for endpoint_id, token in ((first, TOKEN), (second, "a" * 32)):
            with respx.mock(assert_all_called=True) as remote:
                remote.post(SUBMISSIONS_URL).respond(201, json={**_status_body(), "token": token})
                await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme-video"})
        with respx.mock(assert_all_called=True) as remote:
            route = remote.get(url__startswith=f"{SUBMISSIONS_URL}/").mock(side_effect=httpx.ConnectError("down"))
            response = await client.get("/market/submissions")
            assert route.call_count == 1
    assert response.status_code == 200
    assert [(s["endpoint_id"], s["status"], s["stale"]) for s in response.json()["submissions"]] == [
        (second, "open", True),
        (first, "open", True),
    ]


async def test_status_for_another_submission_is_not_recorded(client_factory: ClientFactory):
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock(assert_all_called=True) as remote:
            remote.post(SUBMISSIONS_URL).respond(201, json=_status_body())
            await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme-video"})
        with respx.mock(assert_all_called=True) as remote:
            other = {**_status_body("merged", slug="other"), "token": "c" * 32}
            remote.get(f"{SUBMISSIONS_URL}/{TOKEN}").respond(200, json=other)
            response = await client.get("/market/submissions")
        # 返回的不是所查询的提交：保留本地记录并标记未能刷新，之后仍按原令牌查询。
        with respx.mock(assert_all_called=True) as remote:
            again = remote.get(f"{SUBMISSIONS_URL}/{TOKEN}").respond(200, json=_status_body())
            await client.get("/market/submissions")
            assert again.call_count == 1
    assert [(s["slug"], s["status"], s["stale"]) for s in response.json()["submissions"]] == [
        ("acme-video", "open", True)
    ]


async def test_resubmission_replaces_the_endpoint_binding(client_factory: ClientFactory):
    async with client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        for status in ("closed", "open"):
            with respx.mock(assert_all_called=True) as remote:
                remote.post(SUBMISSIONS_URL).respond(201, json=_status_body(status))
                await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme-video"})
        with respx.mock(assert_all_called=True) as remote:
            remote.get(f"{SUBMISSIONS_URL}/{TOKEN}").respond(200, json=_status_body("open"))
            response = await client.get("/market/submissions")
    assert [(s["endpoint_id"], s["status"]) for s in response.json()["submissions"]] == [(endpoint_id, "open")]


@pytest.mark.parametrize("definition_factory", [custom_endpoint_definition, comfyui_endpoint_definition])
async def test_literal_credentials_block_sharing_without_outbound(client_factory: ClientFactory, definition_factory):
    definition = definition_factory(
        auth={
            "headers": {
                "Authorization": "Bearer {{ api_key }}",
                "X-API-Key": "sk-example1234567890abcdef",
            }
        }
    )
    async with client_factory() as client:
        saved = await client.post("/custom-endpoints", json=definition)
        assert saved.status_code == 201, saved.text
        body = {"endpoint_id": saved.json()["id"], "slug": "acme"}
        with respx.mock() as remote:
            checked = await client.post("/market/submissions/check", json=body)
            assert [d["code"] for d in checked.json()["diagnostics"]] == ["val_ce_auth_literal_credential"]
            submitted = await client.post("/market/submissions", json=body)
            assert not remote.calls
    assert submitted.status_code == 422
    assert submitted.json()["diagnostic"]["diagnostics"] == checked.json()["diagnostics"]


async def _checked_credential_paths(client_factory: ClientFactory, definition: dict[str, Any]) -> list[str]:
    """保存定义后预检并提交，确认两者都不出站、提交被拒；返回凭证诊断的定位路径。"""
    async with client_factory() as client:
        saved = await client.post("/custom-endpoints", json=definition)
        assert saved.status_code == 201, saved.text
        body = {"endpoint_id": saved.json()["id"], "slug": "acme"}
        with respx.mock() as remote:
            checked = await client.post("/market/submissions/check", json=body)
            submitted = await client.post("/market/submissions", json=body)
            assert not remote.calls
    assert submitted.status_code == 422
    diagnostics = checked.json()["diagnostics"]
    assert {d["code"] for d in diagnostics} == {"market_submission_literal_credential"}
    return [d["path"] for d in diagnostics]


async def test_credential_in_comfyui_workflow_blocks_sharing_without_outbound(client_factory: ClientFactory):
    definition = comfyui_endpoint_definition()
    inputs = definition["workflow"]["6"]["inputs"]
    # workflow 原样内嵌、不做模板渲染：节点要的密钥只能以字面值写进去，分享会随定义公开。
    inputs["api_key"] = "sk-live-abcdef"
    # 结构化输入里任意层级的凭证同样拦截：字典套列表套字典。
    inputs["config"] = {"providers": [{"name": "main", "authToken": "tok-123"}], "client_secret": "s3cret"}
    # 凭证名下的列表逐项拦截。
    inputs["api_keys"] = ["sk-a", ""]
    # 只看键名的分词片段：tokenizer 之类的输入名与长串模型文件名不命中。
    inputs["tokenizer"] = "flux1-dev-fp8-e4m3fn-2024"
    assert await _checked_credential_paths(client_factory, definition) == [
        "workflow.6.inputs.api_key",
        "workflow.6.inputs.config.providers[0].authToken",
        "workflow.6.inputs.config.client_secret",
        "workflow.6.inputs.api_keys[0]",
    ]


async def test_credential_in_declarative_request_body_blocks_sharing(client_factory: ClientFactory):
    definition = custom_endpoint_definition()
    # 请求体模板随定义公开；占位符引用的值不是凭证。
    definition["submit"]["body"]["options"] = {"auth": [{"token": "tok-live-123"}], "session_token": "{{ model }}"}
    assert await _checked_credential_paths(client_factory, definition) == ["submit.body.options.auth[0].token"]


async def test_credential_in_declarative_request_headers_blocks_sharing(client_factory: ClientFactory):
    definition = custom_endpoint_definition()
    definition["poll"]["headers"] = {"X-Api-Key": "sk-live-abcdef", "X-Trace": "arcreel"}
    assert await _checked_credential_paths(client_factory, definition) == ["poll.headers.X-Api-Key"]


async def test_authorization_headers_block_sharing(client_factory: ClientFactory):
    definition = custom_endpoint_definition(auth={"headers": {"X-Api-Key": "{{ api_key }}"}})
    definition["submit"]["headers"] = {
        "Authorization": "Bearer sk-live",
        "Proxy-Authorization": "Basic cHJveHk=",
        "Cookie": "session=abc",
    }
    assert await _checked_credential_paths(client_factory, definition) == [
        "submit.headers.Authorization",
        "submit.headers.Proxy-Authorization",
        "submit.headers.Cookie",
    ]


async def test_credential_shaped_header_values_block_sharing(client_factory: ClientFactory):
    definition = custom_endpoint_definition()
    # 请求头的值另按字面凭证形态判定：自定义头名下形似密钥的值命中，普通取值不命中。
    definition["poll"]["headers"] = {
        "X-Goog-Key": "AIzaSyD1234567890abcdefgh",
        "Content-Type": "application/json",
        "X-Client": "arcreel-2024",
    }
    assert await _checked_credential_paths(client_factory, definition) == ["poll.headers.X-Goog-Key"]


async def test_credential_in_declarative_result_request_blocks_sharing(client_factory: ClientFactory):
    definition = custom_endpoint_definition()
    definition["poll"]["extract"]["result_id"] = ["$.result_id"]
    definition["result"] = {
        "method": "GET",
        "url": "{{ base_url }}/v1/video/result/{{ result_id }}",
        "headers": {"X-Api-Key": "sk-live-abcdef"},
        "extract": {"video_url": ["$.url"]},
    }
    assert await _checked_credential_paths(client_factory, definition) == ["result.headers.X-Api-Key"]


async def test_credential_in_declarative_url_query_blocks_sharing(client_factory: ClientFactory):
    definition = custom_endpoint_definition()
    # 查询串按参数名判定：凭证名参数的字面值命中，占位符值与普通参数不命中。
    definition["submit"]["url"] = "{{ base_url }}/v1/video/create?model={{ model }}&access_token=tok-live-123"
    definition["poll"]["url"] = "{{ base_url }}/v1/video/fetch/{{ task_id }}?session_token={{ model }}&lang=zh"
    assert await _checked_credential_paths(client_factory, definition) == ["submit.url"]


async def test_response_extract_paths_are_not_credentials(client_factory: ClientFactory):
    definition = custom_endpoint_definition()
    # extract 的键是取值名、值是 JSONPath，不进请求：output_tokens 之类的取值名不误报。
    definition["poll"]["extract"]["usage"] = {"output_tokens": ["$.usage.output_tokens"]}
    async with client_factory() as client:
        saved = await client.post("/custom-endpoints", json=definition)
        assert saved.status_code == 201, saved.text
        checked = await client.post(
            "/market/submissions/check", json={"endpoint_id": saved.json()["id"], "slug": "acme"}
        )
    assert checked.json()["diagnostics"] == []


async def test_concurrent_submissions_for_one_endpoint_both_succeed(concurrent_client_factory: ClientFactory):
    both_arrived = asyncio.Barrier(2)

    async def accept(request: httpx.Request) -> httpx.Response:
        # 两个请求都拿到官方服务的结果后才各自落库，本地记录此前都不存在。
        await both_arrived.wait()
        return httpx.Response(201, json=_status_body())

    async with concurrent_client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock(assert_all_called=True) as remote:
            remote.post(SUBMISSIONS_URL).mock(side_effect=accept)
            async with asyncio.timeout(10):
                responses = await asyncio.gather(
                    *(
                        client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme-video"})
                        for _ in range(2)
                    )
                )
        listed = await _list_without_outbound_after_merge(client)
    assert [response.status_code for response in responses] == [200, 200]
    assert [(s["endpoint_id"], s["status"]) for s in listed] == [(endpoint_id, "merged")]


async def test_status_refresh_does_not_overwrite_a_newer_submission(concurrent_client_factory: ClientFactory):
    new_token = "b" * 32
    refreshing = asyncio.Event()
    resubmitted = asyncio.Event()

    async def old_status(request: httpx.Request) -> httpx.Response:
        # 旧令牌的状态查询在途时，同一端点完成了一次新提交。
        refreshing.set()
        await resubmitted.wait()
        return httpx.Response(200, json=_status_body("closed"))

    async with concurrent_client_factory() as client:
        endpoint_id = await _create_endpoint(client)
        with respx.mock(assert_all_called=True) as remote:
            remote.post(SUBMISSIONS_URL).respond(201, json=_status_body())
            await client.post("/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme-video"})
        with respx.mock(assert_all_called=True) as remote:
            remote.get(f"{SUBMISSIONS_URL}/{TOKEN}").mock(side_effect=old_status)
            remote.post(SUBMISSIONS_URL).respond(201, json={**_status_body(), "token": new_token})
            async with asyncio.timeout(10):
                listing = asyncio.create_task(client.get("/market/submissions"))
                try:
                    await refreshing.wait()
                    created = await client.post(
                        "/market/submissions", json={"endpoint_id": endpoint_id, "slug": "acme-video"}
                    )
                finally:
                    resubmitted.set()
                await listing
        assert created.status_code == 200
        # 官方服务不可达时列表展示本地保存的值：应是新提交的令牌与状态，而不是旧令牌的查询结果。
        with respx.mock(assert_all_called=True) as remote:
            latest = remote.get(f"{SUBMISSIONS_URL}/{new_token}").mock(side_effect=httpx.ConnectError("down"))
            response = await client.get("/market/submissions")
            assert latest.call_count == 1
    assert [(s["endpoint_id"], s["status"], s["stale"]) for s in response.json()["submissions"]] == [
        (endpoint_id, "open", True)
    ]


async def _list_without_outbound_after_merge(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """把唯一的开放中提交刷新为已采纳，再确认之后的列表不再出站。"""
    with respx.mock(assert_all_called=True) as remote:
        remote.get(f"{SUBMISSIONS_URL}/{TOKEN}").respond(200, json=_status_body("merged"))
        await client.get("/market/submissions")
    return await _list_without_outbound(client)
