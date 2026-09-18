"""ComfyUI 端点的预览请求与测试连接目标。"""

from __future__ import annotations

from random import Random
from typing import Any

from lib.custom_provider.comfyui_backend import ComfyuiVideoBackend
from lib.custom_provider.endpoint_definition import AssetData, validate_definition
from lib.custom_provider.endpoint_test import (
    ComfyuiConversions,
    EndpointTestAssets,
    EndpointTestCredentials,
    EndpointTestParameters,
    comfyui_target,
    preview_comfyui_request,
    support_for_kind,
)
from lib.custom_provider.endpoint_test.comfyui import comfyui_credential_needs
from tests.factories import comfyui_endpoint_definition

CREDENTIALS = EndpointTestCredentials(base_url="https://comfy.test", api_key="sk-secret-key-1234")
PARAMETERS = EndpointTestParameters(model="wan-t2v", prompt="一只猫走过屋顶\nAvoid: 文字字幕", resolution="720p")


def _definition(**overrides: Any) -> dict[str, Any]:
    definition = comfyui_endpoint_definition(**overrides)
    assert validate_definition(definition).valid
    return definition


def _with_image_bindings() -> dict[str, Any]:
    """在最小定义上补首帧与两个参考图格子，把素材那一段带进来。"""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["10"] = {"class_type": "LoadImage", "inputs": {"image": "draft.png"}}
    definition["workflow"]["11"] = {"class_type": "LoadImage", "inputs": {"image": "ref-a.png"}}
    definition["workflow"]["12"] = {"class_type": "LoadImage", "inputs": {"image": "ref-b.png"}}
    definition["bindings"]["start_image"] = [{"node": "10", "input": "image", "class_type": "LoadImage"}]
    definition["bindings"]["reference_images"] = [
        {"node": "11", "input": "image", "class_type": "LoadImage"},
        {"node": "12", "input": "image", "class_type": "LoadImage"},
    ]
    assert validate_definition(definition).valid
    return definition


def _with_reference_slots() -> dict[str, Any]:
    """两个参考图格子经 ``ImageBatch`` 汇进视频节点的可选入口——张数变少时改得动图。"""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["20"] = {"class_type": "LoadImage", "inputs": {"image": "ref_a.png"}}
    definition["workflow"]["21"] = {"class_type": "LoadImage", "inputs": {"image": "ref_b.png"}}
    definition["workflow"]["22"] = {"class_type": "ImageBatch", "inputs": {"image1": ["20", 0], "image2": ["21", 0]}}
    definition["workflow"]["30"] = {
        "class_type": "WanVaceToVideo",
        "inputs": {"positive": ["6", 0], "reference_image": ["22", 0]},
    }
    definition["workflow"]["3"]["inputs"]["latent_image"] = ["30", 0]
    definition["bindings"]["reference_images"] = [
        {
            "node": "20",
            "input": "image",
            "class_type": "LoadImage",
            "consumer": {"node": "22", "input": "image1", "class_type": "ImageBatch"},
        },
        {
            "node": "21",
            "input": "image",
            "class_type": "LoadImage",
            "consumer": {"node": "22", "input": "image2", "class_type": "ImageBatch"},
        },
    ]
    assert validate_definition(definition).valid
    return definition


def _preview(definition: dict[str, Any] | None = None, **kwargs: Any):
    return preview_comfyui_request(
        definition if definition is not None else _definition(),
        kwargs.pop("parameters", PARAMETERS),
        rng=kwargs.pop("rng", Random(7)),
        **kwargs,
    )


class TestPreviewRequest:
    def test_the_submit_section_is_the_prompt_post_with_the_filled_workflow(self):
        preview = _preview(credentials=CREDENTIALS)

        assert preview.submit.method == "POST"
        assert preview.submit.url == "https://comfy.test/prompt"
        body = preview.submit.body
        assert isinstance(body, dict)
        # 提示词按绑定填进正向节点，Avoid 行拆去负向节点——与真发同一个构造。
        assert body["prompt"]["6"]["inputs"]["text"] == "一只猫走过屋顶"
        assert "文字字幕" in body["prompt"]["7"]["inputs"]["text"]
        assert body["client_id"] == "arcreel-{{ prompt_id }}"

    def test_the_poll_section_carries_the_same_credentials(self):
        """ComfyUI 多半套在反向代理后面：要核的正是这组头会不会发到每一条路由上。"""
        definition = _definition(auth={"headers": {"Authorization": "Bearer {{api_key}}"}})

        preview = _preview(definition, credentials=CREDENTIALS)

        assert preview.poll.method == "GET"
        assert preview.poll.url == "https://comfy.test/history/{{ prompt_id }}"
        assert preview.poll.headers == preview.submit.headers
        assert preview.result is None

    def test_credentials_are_masked_in_the_headers(self):
        definition = _definition(auth={"headers": {"Authorization": "Bearer {{api_key}}"}})

        preview = _preview(definition, credentials=CREDENTIALS)

        assert preview.submit.headers == {"Authorization": "Bearer ****1234"}

    def test_a_masked_query_credential_reads_as_asterisks_in_the_url(self):
        """查询参数会被百分号编码，打码记号在预览 URL 上还原成惯用形。"""
        definition = _definition(auth={"query": {"token": "{{api_key}}"}})

        preview = _preview(definition, credentials=CREDENTIALS)

        assert preview.submit.url == "https://comfy.test/prompt?token=****1234"

    def test_an_empty_api_key_renders_no_credential_at_all(self):
        """ComfyUI 原生无鉴权：空凭证发出去只会让代理以 401 拒掉一个本该放行的请求。"""
        definition = _definition(auth={"headers": {"Authorization": "Bearer {{api_key}}"}})

        preview = _preview(definition, credentials=EndpointTestCredentials(base_url="https://comfy.test", api_key=""))

        assert preview.submit.headers == {}
        assert preview.submit.url == "https://comfy.test/prompt"

    def test_without_credentials_the_host_stays_a_placeholder(self):
        preview = _preview()

        assert preview.submit.url == "{{ base_url }}/prompt"

    def test_the_conversions_explain_the_size_frames_and_seed(self):
        preview = _preview(credentials=CREDENTIALS)

        conversions = preview.conversions
        assert conversions is not None
        # 9:16 + 720p 的落地像素、以及实际生效的随机种子，都只在换算说明里看得到。
        assert (conversions["width"], conversions["height"]) == (720, 1280)
        assert conversions["aspect_ratio"] == "9:16"
        assert conversions["resolution"] == "720p"
        assert isinstance(conversions["seed"], int)
        assert conversions["negative_prompt"] == "文字字幕"
        assert conversions["dropped_nodes"] == []

    def test_an_unbound_dimension_reads_as_null(self):
        """未绑定即「以 workflow 字面值为准」：用户在项目页选的时长对这个端点根本不起作用。"""
        preview = _preview(credentials=CREDENTIALS)

        assert preview.conversions is not None
        assert preview.conversions["frames"] is None

    def test_the_workflow_fingerprint_matches_the_one_committed_with_a_version(self):
        preview = _preview(credentials=CREDENTIALS)

        assert preview.conversions is not None
        assert len(str(preview.conversions["workflow_sha256"])) == 64

    def test_missing_assets_are_summarised_so_the_shape_does_not_collapse(self):
        """真发时用户是会带上素材的：留空会让改图把读图节点连同下游删掉。"""
        preview = _preview(_with_image_bindings(), credentials=CREDENTIALS)

        body = preview.submit.body
        assert isinstance(body, dict)
        assert body["prompt"]["10"]["inputs"]["image"] == "<start_image not uploaded>"
        assert body["prompt"]["11"]["inputs"]["image"] == "<reference_images not uploaded>"
        assert preview.conversions is not None
        assert preview.conversions["dropped_nodes"] == []

    def test_an_uploaded_asset_becomes_a_size_summary(self):
        assets = EndpointTestAssets(by_source={"start_image": AssetData("image/png", b"png-bytes")})

        preview = _preview(_with_image_bindings(), credentials=CREDENTIALS, assets=assets)

        body = preview.submit.body
        assert isinstance(body, dict)
        assert body["prompt"]["10"]["inputs"]["image"] == "<start_image, 9 bytes>"

    def test_fewer_reference_images_than_slots_reports_the_dropped_nodes(self):
        """张数少于格子数正是会触发改图的输入，删了哪些节点是预览最该说清的事。"""
        assets = EndpointTestAssets(by_source={"reference_images": [AssetData("image/png", b"one")]})

        preview = _preview(_with_reference_slots(), credentials=CREDENTIALS, assets=assets)

        body = preview.submit.body
        assert isinstance(body, dict)
        assert "21" not in body["prompt"]
        assert preview.conversions is not None
        # 空出来的格子连同被它旁路掉的合并节点一起进说明：用户看到的就是这次图上少了哪几个节点。
        assert preview.conversions["dropped_nodes"] == ["21", "22"]

    def test_not_placeholding_missing_assets_gives_the_shape_a_real_submit_would_have(self):
        """关掉占位摘要后走的是真发那条路：绑定了却没给值的读图节点连同下游一并删掉。"""
        preview = _preview(_with_image_bindings(), credentials=CREDENTIALS, placeholder_missing_assets=False)

        body = preview.submit.body
        assert isinstance(body, dict)
        assert "10" not in body["prompt"]
        assert preview.conversions is not None
        assert "10" in preview.conversions["dropped_nodes"]

    def test_the_draft_workflow_is_never_touched(self):
        definition = _definition()

        _preview(definition, credentials=CREDENTIALS)

        assert definition["workflow"]["6"]["inputs"]["text"] == "一只猫"

    def test_the_conversions_payload_keys_match_the_dataclass(self):
        """换算说明原样进响应体：字段名就是接口，加字段要同时改两处。"""
        preview = _preview(credentials=CREDENTIALS)

        assert preview.conversions is not None
        assert set(preview.conversions) == set(ComfyuiConversions.__dataclass_fields__)


class TestTrialRunTarget:
    def test_it_builds_the_production_video_backend(self):
        target = comfyui_target(_definition(), CREDENTIALS, PARAMETERS)

        assert target.provider == "comfy.test"
        assert target.model == "wan-t2v"

    async def test_the_backend_is_the_one_generation_uses(self):
        target = comfyui_target(_definition(), CREDENTIALS, PARAMETERS)

        assert isinstance(await target.build_backend(), ComfyuiVideoBackend)

    def test_it_skips_the_capability_gate(self):
        """费用固定 0，而能力只从节点绑定推导——内联定义这条入口拿不到推导结果。"""
        assert comfyui_target(_definition(), CREDENTIALS, PARAMETERS).gate_capabilities is False

    def test_it_leaves_the_result_body_without_a_rendered_request(self):
        """实发 workflow 的种子与素材引用名要到提交那一刻才定下来。"""
        assert support_for_kind("comfyui").trial_run_request_preview is False


class TestCredentialNeeds:
    def test_base_url_is_always_required(self):
        """ComfyUI 的路由全在服务地址根下，定义里一个绝对地址都不写。"""
        assert comfyui_credential_needs(_definition())[0] is True

    def test_an_auth_section_that_sends_something_asks_for_an_api_key(self):
        definition = _definition()
        definition["auth"] = {"headers": {"X-API-Key": "{{ api_key }}"}}

        assert comfyui_credential_needs(definition)[1] is True

    def test_two_empty_tables_ask_for_nothing(self):
        """``{"headers": {}}`` 是合法定义、渲染出来一个头都没有：为它索要 api_key 会把一台
        不设防的 ComfyUI 挡在预览与测试连接之外。校验器与渲染都按这个口径。"""
        definition = _definition()
        definition["auth"] = {"headers": {}}

        assert comfyui_credential_needs(definition) == (True, False)

    def test_no_auth_section_asks_for_nothing(self):
        assert comfyui_credential_needs(_definition()) == (True, False)
