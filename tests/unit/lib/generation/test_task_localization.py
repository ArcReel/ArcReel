"""`server.routers.tasks._localize_task` 的渲染行为：失败原因 + 生成警示。"""

from typing import Any

from lib.generation.generation_result import GenerationAction, GenerationProblem, encode_generation_problem
from lib.generation.task_failure import encode_failure
from lib.i18n import _ as translate_message
from server.routers.tasks import _localize_task
from tests.factories import make_display_names


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

        zh = _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"]
        en = _localize_task(task, _translator("en"), make_display_names("en"))["result"]["warnings"]

        assert zh == ["脚本编排 7s 不在 sora 的时长档位内，已按 8s 生成，成片长于脚本编排"]
        assert en != zh
        assert "sora" in en[0]

    def test_warning_without_params_renders(self):
        task = _task(result={"warnings": [{"key": "ref_sora_single_ref", "params": {}}]})

        rendered = _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"]

        assert rendered == ["Sora 参考生视频暂不支持多图，已降级为单图"]

    def test_multiple_warnings_keep_order(self):
        task = _task(
            result={
                "warnings": [
                    {"key": "ref_sora_single_ref", "params": {}},
                    {"key": "ref_too_many_images", "params": {"count": 8, "model": "viduq2", "max_count": 7}},
                ]
            }
        )

        rendered = _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"]

        assert len(rendered) == 2
        assert rendered[0].startswith("Sora")
        assert rendered[1].startswith("参考图数量 8")

    def test_input_task_is_not_mutated(self):
        warnings = [{"key": "ref_sora_single_ref", "params": {}}]
        task = _task(result={"warnings": warnings})

        _localize_task(task, _translator("en"), make_display_names("en"))

        assert task["result"]["warnings"] is warnings
        assert warnings[0] == {"key": "ref_sora_single_ref", "params": {}}

    def test_error_message_and_warnings_render_together(self):
        task = _task(
            status="failed",
            error_message=encode_failure("provider_unsupported_media", provider_id="grok", media_type="image"),
            result={"warnings": [{"key": "ref_sora_single_ref", "params": {}}]},
        )

        localized = _localize_task(task, _translator("zh"), make_display_names("zh"))

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

        localized = {
            locale: _localize_task(task, _translator(locale), make_display_names(locale))
            for locale in ("zh", "en", "vi")
        }

        for result in localized.values():
            assert result["error_code"] == "reference_supported_durations_invalid"
            assert result["error_params"] == {"provider": "fake", "model": "bad-model"}
        assert len({result["error_message"] for result in localized.values()}) == 3

    def test_prompt_authoring_unit_failure_names_the_unit_by_position(self):
        """提示词编写预判失败：摘要按界面位置称呼视频单元，不带内部前缀，面向 Agent 的原文收进详情。"""
        detail = "正式脚本 的 unit E1U04 的台词念完约需 4.2 秒"
        task = _task(
            status="failed",
            error_message=encode_generation_problem(
                GenerationProblem(
                    code="prompt_authoring_dialogue_overload",
                    detail=detail,
                    action=GenerationAction.FIX_INPUT,
                    params={"unit_id": "E1U04", "position": 2, "spoken_seconds": "4.2", "duration_seconds": 1},
                )
            ),
        )

        localized = {
            locale: _localize_task(task, _translator(locale), make_display_names(locale))
            for locale in ("zh", "en", "vi")
        }

        assert localized["zh"]["error_message"].startswith("视频单元 2 的台词念完约需 4.2 秒，超过单元时长 1 秒")
        assert localized["en"]["error_message"].startswith("Video unit 2 ")
        assert localized["vi"]["error_message"].startswith("Đơn vị video 2 ")
        for result in localized.values():
            assert "E1U04" not in result["error_message"]
            assert result["error_code"] == "prompt_authoring_dialogue_overload"
            assert result["error_detail"] == detail

    def test_prompt_authoring_duration_off_tier_lists_duration_and_tiers(self):
        detail = "unit E1U04 时长 8s 不在当前生效档位 [4, 6] 内"
        task = _task(
            status="failed",
            error_message=encode_generation_problem(
                GenerationProblem(
                    code="prompt_authoring_duration_off_tier",
                    detail=detail,
                    action=GenerationAction.FIX_INPUT,
                    params={"unit_id": "E1U04", "position": 2, "duration_seconds": 8, "tiers": "4 / 6"},
                )
            ),
        )

        localized = {
            locale: _localize_task(task, _translator(locale), make_display_names(locale))
            for locale in ("zh", "en", "vi")
        }

        assert localized["zh"]["error_message"].startswith("视频单元 2 的时长 8 秒")
        assert localized["en"]["error_message"].startswith("Video unit 2 ")
        assert localized["vi"]["error_message"].startswith("Đơn vị video 2 ")
        for result in localized.values():
            assert "8" in result["error_message"]
            assert "4 / 6" in result["error_message"]
            assert "E1U04" not in result["error_message"]
            assert "generate_episode_script" not in result["error_message"]
            assert result["error_detail"] == detail

    def test_prompt_authoring_unit_invalid_renders_in_every_locale(self):
        task = _task(
            status="failed",
            error_message=encode_generation_problem(
                GenerationProblem(
                    code="prompt_authoring_unit_invalid",
                    detail="正式脚本 的 unit E1U01 ...",
                    action=GenerationAction.FIX_INPUT,
                    params={"unit_id": "E1U01", "position": 1},
                )
            ),
        )

        messages = {
            locale: _localize_task(task, _translator(locale), make_display_names(locale))["error_message"]
            for locale in ("zh", "en", "vi")
        }

        assert messages["zh"].startswith("视频单元 1 的正文")
        assert len(set(messages.values())) == 3
        assert all("{" not in message for message in messages.values())


class TestWarningPassthroughAndTolerance:
    def test_execution_checkpoint_is_removed_without_mutating_internal_task(self):
        task = _task(execution_checkpoint_json='{"provider_id":"secret"}')

        localized = _localize_task(task, _translator("zh"), make_display_names("zh"))

        assert "execution_checkpoint_json" not in localized
        assert task["execution_checkpoint_json"] == '{"provider_id":"secret"}'

    def test_task_without_result_is_returned_unchanged(self):
        task = _task()

        assert _localize_task(task, _translator("zh"), make_display_names("zh")) is task

    def test_empty_warning_list_is_left_alone(self):
        task = _task(result={"warnings": []})

        assert _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"] == []

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

        rendered = _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"]

        assert rendered == ["Sora 参考生视频暂不支持多图，已降级为单图"]

    def test_non_list_warnings_become_empty_list(self):
        task = _task(result={"warnings": "boom"})

        assert _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"] == []

    def test_unknown_key_falls_back_to_the_key_itself(self):
        task = _task(result={"warnings": [{"key": "some_future_warning", "params": {}}]})

        assert _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"] == [
            "some_future_warning"
        ]

    def test_missing_params_leave_the_template_intact(self):
        task = _task(result={"warnings": [{"key": "ref_duration_rounded_up", "params": {"total": 7}}]})

        rendered = _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]["warnings"]

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

        result = _localize_task(task, _translator("zh"), make_display_names("zh"))["result"]

        assert result["version"] == 3
        assert result["file_path"] == "reference_videos/E1U1.mp4"
