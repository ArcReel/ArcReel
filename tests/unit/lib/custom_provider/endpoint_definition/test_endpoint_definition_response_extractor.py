"""声明式端点响应提取与状态映射。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoint_definition import JsonPathEvaluationError, extract_value, map_status
from lib.video_backends.base import ProviderJobStatus

COMFYUI_STATUS = {"paths": ["$.code"], "accept": "scalar"}
COMFYUI_VIDEO_URL = ["$.data[?@.fileType == 'mp4'].fileUrl"]
COMFYUI_ERROR = ["$.data.failedReason.exception_message", "$.msg"]
COMFYUI_STATUS_MAP = {"0": "succeeded", "813": "queued", "804": "running", "805": "failed"}

NEWAPI_STATUS = ["$.data.status", "$.status"]
NEWAPI_VIDEO_URL = ["$.data.url", "$.data.result_url", "$.metadata.url", "$.url"]
NEWAPI_ERROR = ["$.data.fail_reason", "$.data.error.message", "$.error.message"]
NEWAPI_STATUS_MAP = {
    "not_start": "queued",
    "in_progress": "running",
    "processing": "running",
    "success": "succeeded",
    "succeeded": "succeeded",
    "completed": "succeeded",
    "failure": "failed",
    "failed": "failed",
}


def test_priority_paths_return_the_first_acceptable_hit():
    response = {"code": 813, "data": {"resultUrl": "https://cdn.example.test/video.mp4"}}

    assert extract_value(["$.missing", "$.data.absent", "$.data.resultUrl"], response) == (
        "https://cdn.example.test/video.mp4"
    )
    assert extract_value({"paths": ["$.code"], "accept": "scalar"}, response) == 813


def test_json_decode_path_item_reopens_an_embedded_document():
    response = {
        "data": {
            "bad": "not json",
            "resultJson": '{"resultUrls":["https://cdn.example.test/video.mp4"]}',
        }
    }

    assert (
        extract_value(
            [
                {"path": "$.data.bad", "json_decode": True, "then": ["$.resultUrls[0]"]},
                {"path": "$.data.resultJson", "json_decode": True, "then": ["$.missing", "$.resultUrls[0]"]},
                "$.fallback",
            ],
            response,
        )
        == "https://cdn.example.test/video.mp4"
    )


def test_default_accept_skips_non_string_hits_and_scalar_admits_numbers():
    response = {"code": 0, "meta": None, "data": {"items": []}, "url": "   ", "final": "ok"}

    assert extract_value(["$.code", "$.meta", "$.data", "$.url", "$.final"], response) == "ok"
    assert extract_value({"paths": ["$.data", "$.meta", "$.code"], "accept": "scalar"}, response) == 0


def test_all_paths_missing_yields_none():
    assert extract_value(["$.a", "$.b.c"], {"other": "x"}) is None
    assert extract_value({"paths": ["$.a"], "accept": "scalar"}, {"other": "x"}) is None
    assert extract_value([{"path": "$.blob", "json_decode": True, "then": ["$.url"]}], {"blob": "{}"}) is None


def test_jsonpath_evaluation_errors_are_normalized():
    with pytest.raises(JsonPathEvaluationError) as caught:
        extract_value(["$[?@.a == 1e400]"], {"a": 1})

    assert isinstance(caught.value.__cause__, OverflowError)


def test_unknown_and_expired_statuses_do_not_escape_declarative_four_tiers():
    assert map_status("WAITING_GPU") is ProviderJobStatus.RUNNING
    assert map_status("expired") is ProviderJobStatus.FAILED


def test_comfyui_multi_output_workflow_picks_the_video_and_its_coins():
    completed = {
        "code": 0,
        "msg": "success",
        "data": [
            {"fileUrl": "https://cdn.example.test/out/preview.png", "fileType": "png", "consumeCoins": "0"},
            {"fileUrl": "https://cdn.example.test/out/final.mp4", "fileType": "mp4", "consumeCoins": "18"},
        ],
    }

    assert map_status(extract_value(COMFYUI_STATUS, completed), COMFYUI_STATUS_MAP) is ProviderJobStatus.SUCCEEDED
    assert extract_value(COMFYUI_VIDEO_URL, completed) == "https://cdn.example.test/out/final.mp4"
    assert extract_value(["$.data[?@.fileType == 'mp4'].consumeCoins"], completed) == "18"


def test_comfyui_queued_code_maps_without_any_output_payload():
    queued = {"code": 813, "msg": "TASK_QUEUE", "data": None}

    assert map_status(extract_value(COMFYUI_STATUS, queued), COMFYUI_STATUS_MAP) is ProviderJobStatus.QUEUED
    assert extract_value(COMFYUI_VIDEO_URL, queued) is None


def test_comfyui_workflow_emitting_only_images_yields_no_video_url():
    png_only = {
        "code": 0,
        "msg": "success",
        "data": [{"fileUrl": "https://cdn.example.test/out/preview.png", "fileType": "png", "consumeCoins": "2"}],
    }

    assert map_status(extract_value(COMFYUI_STATUS, png_only), COMFYUI_STATUS_MAP) is ProviderJobStatus.SUCCEEDED
    assert extract_value(COMFYUI_VIDEO_URL, png_only) is None


def test_comfyui_node_failure_reports_the_exception_message_over_the_generic_code():
    failed = {
        "code": 805,
        "msg": "APIKEY_TASK_STATUS_ERROR",
        "data": {"failedReason": {"node_name": "KSampler", "exception_message": "CUDA out of memory"}},
    }

    assert map_status(extract_value(COMFYUI_STATUS, failed), COMFYUI_STATUS_MAP) is ProviderJobStatus.FAILED
    assert extract_value(COMFYUI_ERROR, failed) == "CUDA out of memory"


def test_newapi_submit_falls_back_from_task_id_to_id():
    assert extract_value(["$.task_id", "$.id"], {"id": "task_abc", "object": "video", "status": "queued"}) == "task_abc"


def test_newapi_gemini_channel_shape_reads_data_url():
    succeeded = {
        "code": "success",
        "data": {"task_id": "task_abc", "status": "succeeded", "url": "https://host/proxy/a.mp4", "format": "mp4"},
    }

    assert map_status(extract_value(NEWAPI_STATUS, succeeded), NEWAPI_STATUS_MAP) is ProviderJobStatus.SUCCEEDED
    assert extract_value(NEWAPI_VIDEO_URL, succeeded) == "https://host/proxy/a.mp4"


def test_newapi_task_dto_shape_reads_result_url_and_fail_reason():
    not_start = {"code": "success", "data": {"task_id": "task_abc", "status": "NOT_START", "progress": "0%"}}
    success = {
        "code": "success",
        "data": {
            "task_id": "task_abc",
            "status": "SUCCESS",
            "progress": "100%",
            "result_url": "https://host/v1/videos/task_abc/content",
        },
    }
    failure = {
        "code": "success",
        "data": {"task_id": "task_abc", "status": "FAILURE", "fail_reason": "upstream returned 500"},
    }

    assert map_status(extract_value(NEWAPI_STATUS, not_start), NEWAPI_STATUS_MAP) is ProviderJobStatus.QUEUED
    assert map_status(extract_value(NEWAPI_STATUS, success), NEWAPI_STATUS_MAP) is ProviderJobStatus.SUCCEEDED
    assert extract_value(NEWAPI_VIDEO_URL, success) == "https://host/v1/videos/task_abc/content"
    assert map_status(extract_value(NEWAPI_STATUS, failure), NEWAPI_STATUS_MAP) is ProviderJobStatus.FAILED
    assert extract_value(NEWAPI_ERROR, failure) == "upstream returned 500"


def test_newapi_openai_style_shape_reads_metadata_url_and_nested_error_message():
    in_progress = {"id": "task_abc", "status": "in_progress", "progress": 30}
    completed = {
        "id": "task_abc",
        "status": "completed",
        "metadata": {"url": "https://host/proxy/c.mp4", "duration": 5},
    }
    failed = {
        "id": "task_abc",
        "status": "failed",
        "error": {"code": "moderation", "message": "prompt blocked by moderation"},
    }

    assert map_status(extract_value(NEWAPI_STATUS, in_progress), NEWAPI_STATUS_MAP) is ProviderJobStatus.RUNNING
    assert map_status(extract_value(NEWAPI_STATUS, completed), NEWAPI_STATUS_MAP) is ProviderJobStatus.SUCCEEDED
    assert extract_value(NEWAPI_VIDEO_URL, completed) == "https://host/proxy/c.mp4"
    assert map_status(extract_value(NEWAPI_STATUS, failed), NEWAPI_STATUS_MAP) is ProviderJobStatus.FAILED
    assert extract_value(NEWAPI_ERROR, failed) == "prompt blocked by moderation"
