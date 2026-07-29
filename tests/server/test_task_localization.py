"""`server.routers.tasks._localize_task` 的渲染行为：失败原因 + 生成警示。"""

from typing import Any

import pytest

from lib.i18n import _ as translate_message
from lib.task_failure import encode_failure
from server.routers.tasks import _localize_task

pytestmark = pytest.mark.unit


def _translator(locale: str):
    def translate(key: str, **kwargs: Any) -> str:
        return translate_message(key, locale=locale, **kwargs)

    return translate


def _task(**overrides: Any) -> dict[str, Any]:
    task = {
        "task_id": "t1",
        "status": "succeeded",
        "error_message": None,
        "result": None,
    }
    task.update(overrides)
    return task


class TestWarningRendering:
    def test_structured_warning_renders_to_locale_text(self):
        task = _task(
            result={
                "version": 1,
                "warnings": [
                    {
                        "key": "ref_duration_rounded_up",
                        "params": {"total": 7, "model": "sora", "duration": 8},
                    }
                ],
            }
        )

        zh = _localize_task(task, _translator("zh"))["result"]["warnings"]
        en = _localize_task(task, _translator("en"))["result"]["warnings"]

        assert zh == ["剧本编排 7s 不在 sora 的时长档位内，已按 8s 生成，成片长于剧本编排"]
        assert en != zh
        assert "sora" in en[0]

    def test_warning_without_params_renders(self):
        task = _task(result={"warnings": [{"key": "ref_sora_single_ref", "params": {}}]})

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"]

        assert rendered == ["Sora 参考模式暂不支持多图，已降级为单图"]

    def test_multiple_warnings_keep_order(self):
        task = _task(
            result={
                "warnings": [
                    {"key": "ref_sora_single_ref", "params": {}},
                    {"key": "ref_ad_reference_skipped", "params": {"type": "product", "name": "小美"}},
                ]
            }
        )

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"]

        assert len(rendered) == 2
        assert rendered[0].startswith("Sora")
        assert "小美" in rendered[1]

    def test_asset_type_in_skipped_reference_warning_is_localized(self):
        task = _task(
            result={"warnings": [{"key": "ref_ad_reference_skipped", "params": {"type": "product", "name": "小美"}}]}
        )

        zh = _localize_task(task, _translator("zh"))["result"]["warnings"][0]
        en = _localize_task(task, _translator("en"))["result"]["warnings"][0]
        vi = _localize_task(task, _translator("vi"))["result"]["warnings"][0]

        assert "产品" in zh and "product" not in zh
        assert "product" in en
        assert "sản phẩm" in vi

    def test_unregistered_asset_type_falls_through_unmapped(self):
        task = _task(
            result={"warnings": [{"key": "ref_ad_reference_skipped", "params": {"type": "widget", "name": "小美"}}]}
        )

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"][0]

        assert "widget" in rendered

    def test_input_task_is_not_mutated(self):
        warnings = [{"key": "ref_sora_single_ref", "params": {}}]
        task = _task(result={"warnings": warnings})

        _localize_task(task, _translator("en"))

        assert task["result"]["warnings"] is warnings
        assert warnings[0] == {"key": "ref_sora_single_ref", "params": {}}

    def test_error_message_and_warnings_render_together(self):
        task = _task(
            status="failed",
            error_message=encode_failure("provider_unsupported_media", provider_id="grok", media_type="image"),
            result={"warnings": [{"key": "ref_sora_single_ref", "params": {}}]},
        )

        localized = _localize_task(task, _translator("zh"))

        assert not localized["error_message"].startswith("[")
        assert localized["result"]["warnings"] == ["Sora 参考模式暂不支持多图，已降级为单图"]


class TestWarningPassthroughAndTolerance:
    def test_task_without_result_is_returned_unchanged(self):
        task = _task()

        assert _localize_task(task, _translator("zh")) is task

    def test_empty_warning_list_is_left_alone(self):
        task = _task(result={"warnings": []})

        assert _localize_task(task, _translator("zh"))["result"]["warnings"] == []

    def test_malformed_entries_are_skipped_not_raised(self):
        task = _task(
            result={
                "warnings": [
                    "already a string",
                    {"params": {"x": 1}},
                    {"key": 42},
                    {"key": "ref_sora_single_ref", "params": "not a dict"},
                    {"key": "ref_sora_single_ref", "params": {"locale": "en"}},
                ]
            }
        )

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"]

        assert rendered == ["Sora 参考模式暂不支持多图，已降级为单图"]

    def test_non_list_warnings_become_empty_list(self):
        task = _task(result={"warnings": "boom"})

        assert _localize_task(task, _translator("zh"))["result"]["warnings"] == []

    def test_unknown_key_falls_back_to_the_key_itself(self):
        task = _task(result={"warnings": [{"key": "some_future_warning", "params": {}}]})

        assert _localize_task(task, _translator("zh"))["result"]["warnings"] == ["some_future_warning"]

    def test_missing_params_leave_the_template_intact(self):
        task = _task(result={"warnings": [{"key": "ref_duration_rounded_up", "params": {"total": 7}}]})

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"]

        assert len(rendered) == 1
        assert "{model}" in rendered[0]

    def test_other_result_fields_survive(self):
        task = _task(
            result={
                "version": 3,
                "file_path": "reference_videos/E1U1.mp4",
                "warnings": [{"key": "ref_sora_single_ref", "params": {}}],
            }
        )

        result = _localize_task(task, _translator("zh"))["result"]

        assert result["version"] == 3
        assert result["file_path"] == "reference_videos/E1U1.mp4"
