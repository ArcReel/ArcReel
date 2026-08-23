"""`server.routers.tasks._localize_task` 的渲染行为：失败原因 + 生成警示。"""

from typing import Any

import pytest

from lib.asset_types import ASSET_SPECS
from lib.i18n import _ as translate_message
from lib.task_failure import encode_failure
from server.routers.tasks import _localize_task


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

        assert zh == ["脚本编排 7s 不在 sora 的时长档位内，已按 8s 生成，成片长于脚本编排"]
        assert en != zh
        assert "sora" in en[0]

    def test_warning_without_params_renders(self):
        task = _task(result={"warnings": [{"key": "ref_sora_single_ref", "params": {}}]})

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"]

        assert rendered == ["Sora 参考生视频暂不支持多图，已降级为单图"]

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

        assert "商品" in zh and "merchandise" not in zh
        assert "merchandise" in en and "product" not in en
        assert "hàng hóa" in vi

    @pytest.mark.parametrize("asset_type", sorted(ASSET_SPECS))
    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_every_asset_type_has_a_display_name(self, asset_type: str, locale: str):
        """新增资产类型时若漏加 ``asset_type_*``，i18n 会回落成 key 本身，比原样透传更糟。"""
        rendered = translate_message(f"asset_type_{asset_type}", locale=locale)

        assert rendered != f"asset_type_{asset_type}"

    def test_unregistered_asset_type_falls_through_unmapped(self):
        task = _task(
            result={"warnings": [{"key": "ref_ad_reference_skipped", "params": {"type": "widget", "name": "小美"}}]}
        )

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"][0]

        assert "widget" in rendered

    def test_malformed_asset_type_value_does_not_raise(self):
        """畸形持久化值（非字符串）不该让整个任务列表 500，与本模块其余容错口径一致。"""
        task = _task(
            result={"warnings": [{"key": "ref_ad_reference_skipped", "params": {"type": ["product"], "name": "小美"}}]}
        )

        rendered = _localize_task(task, _translator("zh"))["result"]["warnings"][0]

        assert "product" in rendered

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
        assert localized["error_code"] == "provider_unsupported_media"
        assert localized["error_params"] == {"provider_id": "grok", "media_type": "image"}
        assert localized["result"]["warnings"] == ["Sora 参考生视频暂不支持多图，已降级为单图"]

    def test_projection_failure_keeps_machine_details_while_message_tracks_locale(self):
        task = _task(
            status="failed",
            error_message=encode_failure(
                "reference_supported_durations_invalid",
                provider="fake",
                model="bad-model",
            ),
        )

        localized = {locale: _localize_task(task, _translator(locale)) for locale in ("zh", "en", "vi")}

        for result in localized.values():
            assert result["error_code"] == "reference_supported_durations_invalid"
            assert result["error_params"] == {"provider": "fake", "model": "bad-model"}
        assert len({result["error_message"] for result in localized.values()}) == 3


class TestWarningPassthroughAndTolerance:
    def test_execution_checkpoint_is_removed_without_mutating_internal_task(self):
        task = _task(execution_checkpoint_json='{"provider_id":"secret"}')

        localized = _localize_task(task, _translator("zh"))

        assert "execution_checkpoint_json" not in localized
        assert task["execution_checkpoint_json"] == '{"provider_id":"secret"}'

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

        assert rendered == ["Sora 参考生视频暂不支持多图，已降级为单图"]

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
