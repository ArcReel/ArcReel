"""ad 模式参考直出派生分组器（lib/reference_video/ad_units.py）单测。

分组器是纯函数：输入 ad 剧本的平铺 shots[]，输出 video_unit 轻量索引
（unit → shot_ids + 参考集），不复制镜头内容。
"""

import pytest

from lib.reference_video.ad_units import (
    derive_ad_reference_units,
    render_ad_unit_prompt,
    resolve_ad_unit_shots,
    sync_ad_reference_units,
)


def _shot(shot_id: str, duration: int = 3, **overrides) -> dict:
    base = {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播",
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        "image_prompt": {
            "scene": f"{shot_id} 画面",
            "composition": {"shot_type": "Close-up", "lighting": "自然光", "ambiance": "明亮"},
        },
        "video_prompt": {
            "action": f"{shot_id} 动作",
            "camera_motion": "Static",
            "ambiance_audio": "环境音",
            "dialogue": [],
        },
    }
    base.update(overrides)
    return base


class TestDeriveGrouping:
    def test_consecutive_shots_grouped_into_single_unit(self):
        shots = [_shot("E1S1"), _shot("E1S2"), _shot("E1S3")]

        units = derive_ad_reference_units(shots, episode=1)

        assert len(units) == 1
        assert units[0]["unit_id"] == "E1U1"
        assert units[0]["shot_ids"] == ["E1S1", "E1S2", "E1S3"]

    def test_unit_holds_at_most_four_shots(self):
        shots = [_shot(f"E1S{n}") for n in range(1, 7)]

        units = derive_ad_reference_units(shots, episode=1)

        assert [u["shot_ids"] for u in units] == [
            ["E1S1", "E1S2", "E1S3", "E1S4"],
            ["E1S5", "E1S6"],
        ]
        assert [u["unit_id"] for u in units] == ["E1U1", "E1U2"]

    def test_unit_total_duration_respects_provider_cap(self):
        shots = [
            _shot("E1S1", duration=5),
            _shot("E1S2", duration=5),
            _shot("E1S3", duration=5),
            _shot("E1S4", duration=2),
        ]

        units = derive_ad_reference_units(shots, episode=1, max_unit_duration=12)

        assert [u["shot_ids"] for u in units] == [["E1S1", "E1S2"], ["E1S3", "E1S4"]]

    def test_single_shot_exceeding_cap_forms_its_own_unit(self):
        # 单镜头无法再拆，超上限时独立成 unit，留给执行层 clamp + warning 软处理
        shots = [_shot("E1S1", duration=15), _shot("E1S2", duration=3)]

        units = derive_ad_reference_units(shots, episode=1, max_unit_duration=10)

        assert [u["shot_ids"] for u in units] == [["E1S1"], ["E1S2"]]

    def test_no_cap_groups_by_shot_count_only(self):
        shots = [_shot(f"E1S{n}", duration=15) for n in range(1, 5)]

        units = derive_ad_reference_units(shots, episode=1)

        assert [u["shot_ids"] for u in units] == [["E1S1", "E1S2", "E1S3", "E1S4"]]

    def test_short_two_to_three_second_shots_are_legal(self):
        # 2-3 秒短切镜头是该路径的合法常态（快节奏剪辑感）
        shots = [_shot("E1S1", duration=2), _shot("E1S2", duration=3), _shot("E1S3", duration=2)]

        units = derive_ad_reference_units(shots, episode=1, max_unit_duration=10)

        assert [u["shot_ids"] for u in units] == [["E1S1", "E1S2", "E1S3"]]


class TestReferenceInheritance:
    def test_unit_inherits_member_shot_references_products_first(self):
        # 产品镜头沿用注入二元规则：产品参考全量进入参考集且排序绝对优先
        shots = [
            _shot("E1S1", characters_in_shot=["小美"], scenes=["客厅"]),
            _shot("E1S2", products_in_shot=["按摩仪"], characters_in_shot=["小美"], props=["毛巾"]),
        ]

        units = derive_ad_reference_units(shots, episode=1)

        assert units[0]["references"] == [
            {"type": "product", "name": "按摩仪"},
            {"type": "character", "name": "小美"},
            {"type": "scene", "name": "客厅"},
            {"type": "prop", "name": "毛巾"},
        ]

    def test_references_deduplicated_preserving_first_appearance(self):
        shots = [
            _shot("E1S1", products_in_shot=["按摩仪"], characters_in_shot=["小美", "小明"]),
            _shot("E1S2", products_in_shot=["精华液", "按摩仪"], characters_in_shot=["小美"]),
        ]

        units = derive_ad_reference_units(shots, episode=1)

        assert units[0]["references"] == [
            {"type": "product", "name": "按摩仪"},
            {"type": "product", "name": "精华液"},
            {"type": "character", "name": "小美"},
            {"type": "character", "name": "小明"},
        ]

    def test_references_deduplicated_across_encoding_forms(self):
        # 同一资产在两个镜头里以 NFC / NFD 两种等价编码写入：派生参考集须按归一名判同，
        # 否则画布上重复显示同一资产、并各占一个参考图槽位
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd

        shots = [
            _shot("E1S1", characters_in_shot=[name_nfc], products_in_shot=[name_nfc]),
            _shot("E1S2", characters_in_shot=[name_nfd], products_in_shot=[name_nfd]),
        ]

        units = derive_ad_reference_units(shots, episode=1)

        assert units[0]["references"] == [
            {"type": "product", "name": name_nfc},
            {"type": "character", "name": name_nfc},
        ]

    def test_atmosphere_only_unit_has_zero_product_references(self):
        shots = [_shot("E1S1", scenes=["海边"]), _shot("E1S2", scenes=["海边"])]

        units = derive_ad_reference_units(shots, episode=1)

        assert units[0]["references"] == [{"type": "scene", "name": "海边"}]


class TestReproducibility:
    def test_same_shots_and_cap_always_produce_identical_grouping(self):
        shots = [
            _shot("E1S1", duration=2, products_in_shot=["按摩仪"]),
            _shot("E1S2", duration=5, characters_in_shot=["小美"]),
            _shot("E1S3", duration=8, scenes=["客厅"]),
            _shot("E1S4", duration=3),
            _shot("E1S5", duration=12),
        ]

        first = derive_ad_reference_units(shots, episode=1, max_unit_duration=15)
        second = derive_ad_reference_units(shots, episode=1, max_unit_duration=15)

        assert first == second

    def test_index_only_references_shot_ids_without_copying_content(self):
        shots = [_shot("E1S1", products_in_shot=["按摩仪"])]

        units = derive_ad_reference_units(shots, episode=1)

        assert set(units[0].keys()) == {"unit_id", "shot_ids", "references"}

    def test_dirty_shots_skipped_deterministically(self):
        shots = [
            "not-a-dict",
            _shot("E1S1"),
            {"section": "hook"},  # 缺 shot_id
            _shot("E1S2", duration="bad"),  # 脏时长按 0 计
            {"shot_id": ""},  # 空 shot_id
        ]

        units = derive_ad_reference_units(shots, episode=1)

        assert [u["shot_ids"] for u in units] == [["E1S1", "E1S2"]]


class TestSyncPersistence:
    def test_sync_writes_index_into_script(self):
        script = {"episode": 1, "shots": [_shot("E1S1"), _shot("E1S2")]}

        units = sync_ad_reference_units(script, episode=1)

        assert script["reference_units"] == units
        assert units[0]["shot_ids"] == ["E1S1", "E1S2"]
        assert units[0]["generated_assets"]["status"] == "pending"

    def test_resync_with_unchanged_shots_preserves_generated_assets(self):
        script = {"episode": 1, "shots": [_shot("E1S1"), _shot("E1S2")]}
        sync_ad_reference_units(script, episode=1)
        script["reference_units"][0]["generated_assets"]["video_clip"] = "reference_videos/E1U1.mp4"
        script["reference_units"][0]["generated_assets"]["status"] = "completed"

        units = sync_ad_reference_units(script, episode=1)

        assert units[0]["generated_assets"]["video_clip"] == "reference_videos/E1U1.mp4"
        assert units[0]["generated_assets"]["status"] == "completed"

    def test_resync_after_shot_change_resets_changed_unit_assets(self):
        script = {"episode": 1, "shots": [_shot("E1S1"), _shot("E1S2")]}
        sync_ad_reference_units(script, episode=1)
        script["reference_units"][0]["generated_assets"]["video_clip"] = "reference_videos/E1U1.mp4"
        # 新增镜头改变了 E1U1 的成员集合 → 该 unit 的旧产物指针不再可信
        script["shots"].append(_shot("E1S3"))

        units = sync_ad_reference_units(script, episode=1)

        assert units[0]["shot_ids"] == ["E1S1", "E1S2", "E1S3"]
        assert units[0]["generated_assets"].get("video_clip") is None

    def test_resync_after_reference_change_resets_unit_assets(self):
        script = {"episode": 1, "shots": [_shot("E1S1")]}
        sync_ad_reference_units(script, episode=1)
        script["reference_units"][0]["generated_assets"]["video_clip"] = "reference_videos/E1U1.mp4"
        script["shots"][0]["products_in_shot"] = ["按摩仪"]

        units = sync_ad_reference_units(script, episode=1)

        assert units[0]["references"] == [{"type": "product", "name": "按摩仪"}]
        assert units[0]["generated_assets"].get("video_clip") is None


class TestResolveUnitShots:
    def test_hydrates_member_shots_from_script_in_index_order(self):
        script = {"shots": [_shot("E1S1"), _shot("E1S2"), _shot("E1S3")]}
        unit = {"unit_id": "E1U1", "shot_ids": ["E1S2", "E1S3"]}

        shots = resolve_ad_unit_shots(script, unit)

        assert [s["shot_id"] for s in shots] == ["E1S2", "E1S3"]

    def test_dangling_shot_id_raises_stale_index_error(self):
        script = {"shots": [_shot("E1S1")]}
        unit = {"unit_id": "E1U1", "shot_ids": ["E1S1", "E1S9"]}

        with pytest.raises(ValueError, match="E1S9"):
            resolve_ad_unit_shots(script, unit)


class TestRenderUnitPrompt:
    def test_renders_shot_headers_with_durations_and_visual_content(self):
        shots = [
            _shot("E1S1", duration=3),
            _shot("E1S2", duration=2),
        ]

        prompt = render_ad_unit_prompt(shots, style="水彩插画")

        assert "Style: 水彩插画" in prompt
        assert "Shot 1 (3s):" in prompt
        assert "Shot 2 (2s):" in prompt
        assert "E1S1 画面" in prompt
        assert "E1S1 动作" in prompt

    def test_voiceover_text_excluded_from_video_prompt(self):
        # 口播是后期配音的输入，不进画面生成 prompt
        shots = [_shot("E1S1", voiceover_text="买它买它")]

        prompt = render_ad_unit_prompt(shots)

        assert "买它买它" not in prompt

    def test_dialogue_and_camera_motion_included(self):
        shots = [
            _shot(
                "E1S1",
                video_prompt={
                    "action": "举起产品",
                    "camera_motion": "Zoom In",
                    "ambiance_audio": "",
                    "dialogue": [{"speaker": "小美", "line": "太好用了"}],
                },
            )
        ]

        prompt = render_ad_unit_prompt(shots)

        assert "Zoom In" in prompt
        assert "太好用了" in prompt

    @pytest.mark.unit
    def test_dialogue_speaker_normalized_to_nfc(self):
        # derive_voice_bindings（script_preview 复用于 ad 路径）把说话人名归一到 NFC 再产出
        # 音色绑定声明；画面 prompt 的台词句式须用同一坐标系，否则两处 `<X>` 字节不同，
        # 供应商侧无法把参考音色与这句台词对上。
        import unicodedata

        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        assert name_nfd != name_nfc
        shots = [
            _shot(
                "E1S1",
                video_prompt={
                    "action": "",
                    "camera_motion": "",
                    "ambiance_audio": "",
                    "dialogue": [{"speaker": name_nfd, "line": "太好用了"}],
                },
            )
        ]

        prompt = render_ad_unit_prompt(shots)

        assert f"<{name_nfc}>说 {{太好用了}}" in prompt
        assert name_nfd not in prompt

    def test_all_blank_shots_render_empty_for_enqueue_guard(self):
        # 空提示词必须渲染为空串，让 TaskSpec 入队守卫当场拒绝
        shots = [_shot("E1S1", image_prompt={"scene": "", "composition": {}}, video_prompt={"action": ""})]

        assert render_ad_unit_prompt(shots, style="水彩插画") == ""

    @pytest.mark.unit
    def test_dialogue_without_speaker_renders_as_voiceover(self):
        shots = [
            _shot(
                "E1S1",
                video_prompt={
                    "action": "",
                    "camera_motion": "",
                    "ambiance_audio": "",
                    "dialogue": [{"line": "颈椎终于舒服了"}],
                },
            )
        ]

        prompt = render_ad_unit_prompt(shots)

        assert "画外音说 {颈椎终于舒服了}" in prompt
