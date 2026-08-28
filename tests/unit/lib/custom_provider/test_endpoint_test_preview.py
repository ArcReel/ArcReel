"""预览请求与验证响应：不外发一个字节的两种端点测试。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoint_definition import AssetData
from lib.custom_provider.endpoint_test import (
    EndpointTestAssets,
    EndpointTestCredentials,
    EndpointTestDefinitionError,
    EndpointTestParameters,
    check_response,
    parse_response_body,
    preview_request,
)
from tests.factories import custom_endpoint_definition
from tests.http_capture import capture_http

PARAMETERS = EndpointTestParameters(model="demo-v1", prompt="一只猫", duration_seconds=5, aspect_ratio="9:16")
CREDENTIALS = EndpointTestCredentials(base_url="https://api.example.com", api_key="sk-secret-key-1234")


class TestCostsNothing:
    """两种模式的卖点就是不外发：出站被 respx 全量拦截，任何一次请求都会当场抛错。"""

    def test_preview_sends_no_request(self):
        with capture_http() as router:
            preview_request(custom_endpoint_definition(), PARAMETERS, credentials=CREDENTIALS)

        assert router.calls.call_count == 0

    def test_checking_a_response_sends_no_request(self):
        with capture_http() as router:
            check_response(custom_endpoint_definition(), "poll", {"status": "completed"})

        assert router.calls.call_count == 0


class TestPreviewRequest:
    def test_renders_submit_and_poll_from_the_definition(self):
        preview = preview_request(custom_endpoint_definition(), PARAMETERS, credentials=CREDENTIALS)

        assert preview.submit.method == "POST"
        assert preview.submit.url == "https://api.example.com/v1/video/create"
        assert isinstance(preview.submit.body, dict)
        assert preview.submit.body["prompt"] == "一只猫"
        assert preview.submit.body["duration"] == 5
        assert preview.result is None

    def test_a_versioned_base_url_previews_the_same_url_the_runtime_would_send(self):
        """定义带显式版本段时，配置末尾的版本段被剥掉——与运行时 backend 同一份归一化。"""
        credentials = EndpointTestCredentials(base_url="https://api.example.com/v1", api_key="sk-secret-key-1234")

        preview = preview_request(custom_endpoint_definition(), PARAMETERS, credentials=credentials)

        assert preview.submit.url == "https://api.example.com/v1/video/create"

    def test_a_versioned_poll_url_strips_the_configured_version_segment_too(self):
        """提交写死绝对地址、只有轮询引用带版本段的 base_url：剥版本段的判定看整份定义。"""
        definition = custom_endpoint_definition()
        definition["submit"] = {**definition["submit"], "url": "https://fixed.example.com/video/create"}
        credentials = EndpointTestCredentials(base_url="https://api.example.com/v1", api_key="sk-secret-key-1234")

        preview = preview_request(definition, PARAMETERS, credentials=credentials)

        assert preview.poll.url == "https://api.example.com/v1/video/fetch/{{ task_id }}"

    def test_masks_the_api_key_everywhere_it_was_rendered(self):
        preview = preview_request(custom_endpoint_definition(), PARAMETERS, credentials=CREDENTIALS)

        assert preview.submit.headers["Authorization"] == "Bearer ****1234"
        assert "sk-secret-key-1234" not in str(preview.submit.headers) + preview.submit.url

    def test_masks_credentials_carried_in_the_query_string(self):
        definition = custom_endpoint_definition(auth={"query": {"key": "{{ api_key }}"}})

        preview = preview_request(definition, PARAMETERS, credentials=CREDENTIALS)

        assert preview.submit.url.endswith("key=****1234")

    def test_a_short_key_does_not_rewrite_matching_substrings_elsewhere(self):
        """打码值渲染前就落在凭证注入点上：host / model / prompt 里恰好相同的子串不得被改写。"""
        credentials = EndpointTestCredentials(base_url="https://test.example.com", api_key="test")
        parameters = EndpointTestParameters(
            model="test-model", prompt="test prompt", duration_seconds=5, aspect_ratio="9:16"
        )

        preview = preview_request(custom_endpoint_definition(), parameters, credentials=credentials)

        assert preview.submit.headers["Authorization"] == "Bearer ****"
        assert preview.submit.url == "https://test.example.com/v1/video/create"
        assert isinstance(preview.submit.body, dict)
        assert preview.submit.body["model"] == "test-model"
        assert preview.submit.body["prompt"] == "test prompt"

    def test_keeps_task_id_unrendered_in_the_polling_request(self):
        preview = preview_request(custom_endpoint_definition(), PARAMETERS, credentials=CREDENTIALS)

        assert preview.poll.url == "https://api.example.com/v1/video/fetch/{{ task_id }}"

    def test_without_credentials_falls_back_to_hints_and_keeps_the_key_placeholder(self):
        definition = custom_endpoint_definition()
        definition["meta"] = {**definition["meta"], "hints": {"base_url": "https://hinted.example.com"}}

        preview = preview_request(definition, PARAMETERS)

        assert preview.submit.url == "https://hinted.example.com/v1/video/create"
        assert preview.submit.headers["Authorization"] == "Bearer {{ api_key }}"

    def test_replaces_uploaded_assets_with_a_size_summary(self):
        assets = EndpointTestAssets(by_source={"start_image": AssetData("image/png", b"x" * 2048)})

        preview = preview_request(custom_endpoint_definition(), PARAMETERS, credentials=CREDENTIALS, assets=assets)

        assert isinstance(preview.submit.body, dict)
        assert preview.submit.body["image"] == "<data:image/png;base64, 2048 bytes>"

    def test_missing_assets_can_render_like_the_runtime(self):
        """测试连接的结果体记录真发形状：缺席的可选素材按运行时口径整字段省略，不放占位摘要。"""
        preview = preview_request(
            custom_endpoint_definition(), PARAMETERS, credentials=CREDENTIALS, placeholder_missing_assets=False
        )

        assert isinstance(preview.submit.body, dict)
        assert "image" not in preview.submit.body

    def test_missing_assets_still_show_the_field(self):
        """占位摘要不能省：留空会让整串占位符把字段删掉，预览出的形状就与真发时不同。"""
        preview = preview_request(custom_endpoint_definition(), PARAMETERS, credentials=CREDENTIALS)

        assert isinstance(preview.submit.body, dict)
        assert "start_image" in preview.submit.body["image"]

    def test_render_failure_reports_a_diagnostic_on_the_failing_section(self):
        definition = custom_endpoint_definition()
        definition["enum_maps"] = {"duration": {"10": 10}}

        with pytest.raises(EndpointTestDefinitionError) as exc_info:
            preview_request(definition, PARAMETERS, credentials=CREDENTIALS)

        issue = exc_info.value.diagnostics.errors[0]
        assert issue.path == "submit"
        assert issue.code.value == "template_render_failed"


class TestCheckResponse:
    def test_reports_each_path_of_the_priority_array(self):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["video_url"] = ["$.data.url", "$.video_url"]

        report = check_response(definition, "poll", {"status": "completed", "video_url": "https://cdn/v.mp4"})

        video = next(field for field in report.fields if field.key == "video_url")
        assert [attempt.matched for attempt in video.attempts] == [False, True]
        assert video.value == "https://cdn/v.mp4"

    def test_maps_the_provider_status_through_the_definition_dictionary(self):
        report = check_response(custom_endpoint_definition(), "poll", {"status": "processing"})

        assert report.raw_status == "processing"
        assert report.status == "running"

    def test_reads_the_submit_stage_by_its_own_rule(self):
        """提交节没有状态可读，判据是取不取得到 task_id。"""
        report = check_response(custom_endpoint_definition(), "submit", {"task_id": "job-1"})

        assert report.task_id == "job-1"
        assert report.status is None

    def test_a_missing_task_id_surfaces_the_provider_error(self):
        report = check_response(custom_endpoint_definition(), "submit", {"error": {"message": "quota exceeded"}})

        assert report.task_id is None
        assert report.error == "quota exceeded"

    def test_failure_path_overrides_a_healthy_looking_status(self):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["failure"] = ["$.fail_reason"]

        report = check_response(definition, "poll", {"status": "processing", "fail_reason": "nsfw"})

        assert report.status == "failed"

    def test_rejects_a_stage_the_definition_does_not_declare(self):
        with pytest.raises(EndpointTestDefinitionError):
            check_response(custom_endpoint_definition(), "result", {"video_url": "https://cdn/v.mp4"})

    def test_a_raw_string_body_is_parsed_as_json_when_it_can_be(self):
        report = check_response(custom_endpoint_definition(), "poll", parse_response_body('{"status": "completed"}'))

        assert report.status == "succeeded"
