"""ComfyUI 节点绑定推断接口。

与导入入口同形：请求体是载荷原样，带 ``kind`` 的按端点定义收、其余按原始 API workflow 收。
路径挂在 ``/custom-endpoints/comfyui/infer``，须先于 ``/{endpoint_id}`` 匹配。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import custom_endpoints
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import comfyui_api_workflow, comfyui_endpoint_definition, custom_endpoint_definition

INFER_PATH = "/api/v1/custom-endpoints/comfyui/infer"


@pytest.fixture
def inference_app() -> FastAPI:
    """只挂端点路由：推断接口不读库，也不在服务端留任何状态。"""
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    app.include_router(custom_endpoints.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return app


@pytest.fixture
def inference_client(inference_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(inference_app) as client:
        yield client


def codes(response) -> set[str]:
    return {error["code"] for error in response.json()["diagnostic"]["errors"]}


def test_a_raw_api_workflow_is_wrapped_and_then_inferred(inference_client: TestClient):
    response = inference_client.post(INFER_PATH, json=comfyui_api_workflow())
    assert response.status_code == 200
    body = response.json()
    assert body["import_shape"] == "comfyui_api_workflow"
    assert body["wrapped_definition"]["kind"] == "comfyui"
    assert body["media_type"] == "video"
    assert body["bindings"]["prompt"]["state"] == "auto_selected"
    assert body["bindings"]["prompt"]["candidates"][0]["target"]["node"] == "6"


def test_every_candidate_comes_back_with_a_score_and_a_readable_signal(inference_client: TestClient):
    body = inference_client.post(INFER_PATH, json=comfyui_api_workflow()).json()
    candidate = body["bindings"]["output"]["candidates"][0]
    assert candidate["score"] > 0
    assert candidate["signals"]
    assert all(signal["message"] and not signal["message"].startswith("val_ce_") for signal in candidate["signals"])


def test_signal_text_follows_the_request_language(inference_client: TestClient):
    body = inference_client.post(INFER_PATH, json=comfyui_api_workflow(), headers={"Accept-Language": "en"}).json()
    messages = [signal["message"] for signal in body["bindings"]["prompt"]["candidates"][0]["signals"]]
    assert any("Traced back" in message for message in messages)


def test_an_endpoint_definition_is_taken_as_is_and_its_bindings_are_rematched(inference_client: TestClient):
    response = inference_client.post(INFER_PATH, json=comfyui_endpoint_definition())
    assert response.status_code == 200
    body = response.json()
    assert body["import_shape"] == "endpoint_definition"
    assert body["wrapped_definition"] is None
    assert body["bindings"]["prompt"]["candidates"][0]["origin"] == "kept"
    assert body["savable"] is True


def test_a_definition_whose_bindings_no_longer_fit_is_still_accepted(inference_client: TestClient):
    """重导入的正是「结构立得住、绑定对不上」的定义，拒绝它就没有重匹配可言。"""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["106"] = definition["workflow"].pop("6")
    definition["workflow"]["3"]["inputs"]["positive"] = ["106", 0]
    response = inference_client.post(INFER_PATH, json=definition)
    assert response.status_code == 200
    assert response.json()["bindings"]["prompt"]["candidates"][0]["origin"] == "rematched"


def test_the_media_type_query_only_applies_to_a_raw_workflow(inference_client: TestClient):
    wrapped = inference_client.post(INFER_PATH, json=comfyui_api_workflow(), params={"media_type": "image"}).json()
    assert wrapped["media_type"] == "image"
    assert "frames" not in wrapped["bindings"]

    definition = inference_client.post(
        INFER_PATH, json=comfyui_endpoint_definition(), params={"media_type": "image"}
    ).json()
    assert definition["media_type"] == "video"


def test_a_ui_format_workflow_is_refused_with_the_export_hint(inference_client: TestClient):
    response = inference_client.post(INFER_PATH, json={"nodes": [], "links": []})
    assert response.status_code == 422
    assert codes(response) == {"comfyui_ui_format_workflow"}


def test_a_declarative_definition_is_refused_by_the_comfyui_schema(inference_client: TestClient):
    response = inference_client.post(INFER_PATH, json=custom_endpoint_definition())
    assert response.status_code == 422
    assert codes(response)


def test_a_payload_that_is_not_an_object_is_refused_with_a_located_diagnostic(inference_client: TestClient):
    response = inference_client.post(INFER_PATH, json=["not", "a", "definition"])
    assert response.status_code == 422
    assert response.json()["diagnostic"]["errors"][0]["path"] == "$"


def test_the_infer_path_is_registered_before_the_endpoint_id_route(inference_app: FastAPI):
    """字面路径必须排在 ``/{endpoint_id}`` 之前：撞上路径参数只会拿到解析失败的 422。

    断言注册序而不是某个响应码——今天 ``/{endpoint_id}`` 上没有 POST，颠倒注册序也还是能走到
    推断接口，等哪天它多出一个 POST，这条约束才会以最难查的方式失效。
    """
    paths = list(inference_app.openapi()["paths"])
    assert paths.index(INFER_PATH) < paths.index("/api/v1/custom-endpoints/{endpoint_id}")
