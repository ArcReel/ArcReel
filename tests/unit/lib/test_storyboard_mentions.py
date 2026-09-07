"""分镜画面描述里 ``@[登记名]`` 的绑定检查。"""

from __future__ import annotations

from lib.storyboard_mentions import (
    WARN_STORYBOARD_MENTION_UNBOUND,
    render_storyboard_mention_warnings,
    storyboard_mention_warnings,
)

PROJECT = {
    "characters": {"张三": {"description": "主角", "derivatives": {"黑化": {"description": "黑化形态"}}}},
    "scenes": {"酒馆": {"description": "旧木酒馆"}},
    "props": {},
    "products": {"保温杯": {"description": "主推商品"}},
}


def _segment(segment_id: str, scene: object, **fields: object) -> dict:
    return {
        "segment_id": segment_id,
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "image_prompt": {"scene": scene, "composition": {}} if isinstance(scene, str) else scene,
        **fields,
    }


def _warning(unit_id: str, name: str) -> dict:
    return {"key": WARN_STORYBOARD_MENTION_UNBOUND, "params": {"unit_id": unit_id, "name": name}}


class TestStoryboardMentionWarnings:
    def test_mentions_bound_to_declared_registered_assets_raise_nothing(self):
        script = {
            "segments": [
                _segment("E1S01", "@[张三]推门走进@[酒馆]", characters_in_segment=["张三"], scenes=["酒馆"]),
                _segment("E1S02", "@[张三/黑化]立在门口", characters_in_segment=["张三/黑化"]),
            ]
        }
        assert storyboard_mention_warnings(PROJECT, script) == []

    def test_unregistered_and_undeclared_mentions_are_reported_per_entry(self):
        script = {
            "segments": [
                _segment("E1S01", "@[张三]与@[无名路人]对视", characters_in_segment=[]),
                _segment("E1S02", "@[酒馆]里空无一人", scenes=["酒馆"]),
            ]
        }
        assert storyboard_mention_warnings(PROJECT, script) == [
            _warning("E1S01", "张三"),
            _warning("E1S01", "无名路人"),
        ]

    def test_unit_ids_limit_the_entries_that_are_checked(self):
        script = {
            "segments": [
                _segment("E1S01", "@[无名路人]", characters_in_segment=[]),
                _segment("E1S02", "@[另一个路人]", characters_in_segment=[]),
            ]
        }
        assert storyboard_mention_warnings(PROJECT, script, unit_ids=["E1S02"]) == [_warning("E1S02", "另一个路人")]

    def test_text_form_prompts_and_ad_product_fields_are_checked_too(self):
        script = {
            "content_mode": "ad",
            "shots": [
                {"shot_id": "E1S01", "products_in_shot": ["保温杯"], "image_prompt": "@[保温杯]立在桌上"},
                {"shot_id": "E1S02", "products_in_shot": [], "image_prompt": "@[保温杯]立在桌上"},
            ],
        }
        assert storyboard_mention_warnings(PROJECT, script) == [_warning("E1S02", "保温杯")]

    def test_reference_video_scripts_have_no_storyboard_entries(self):
        script = {"generation_mode": "reference_video", "video_units": [{"unit_id": "E1U1", "text": "@[路人]"}]}
        assert storyboard_mention_warnings(PROJECT, script) == []

    def test_entries_without_a_scene_text_are_skipped(self):
        script = {"segments": [_segment("E1S01", {"composition": {}}), _segment("E1S02", None)]}
        assert storyboard_mention_warnings(PROJECT, script) == []


class TestRenderStoryboardMentionWarnings:
    def test_each_warning_is_rendered_through_the_i18n_key_table(self):
        from lib.i18n import _ as translate

        lines = render_storyboard_mention_warnings(
            [_warning("E1S02", "无名路人"), _warning("E1S03", "保温杯")], translate
        )
        assert lines == [
            translate(WARN_STORYBOARD_MENTION_UNBOUND, unit_id="E1S02", name="无名路人"),
            translate(WARN_STORYBOARD_MENTION_UNBOUND, unit_id="E1S03", name="保温杯"),
        ]
        assert all("@[" in line and "图N" in line for line in lines)

    def test_no_warnings_render_to_no_lines(self):
        assert render_storyboard_mention_warnings([], lambda key, **params: key) == []
