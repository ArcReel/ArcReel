from pathlib import Path

import pytest

from lib.status_calculator import StatusCalculator


class _FakePM:
    def __init__(self, project_root: Path, project: dict, scripts: dict[str, dict]):
        self._project_root = project_root
        self._project = project
        self._scripts = scripts

    def load_project(self, project_name: str):
        return self._project

    def get_project_path(self, project_name: str):
        return self._project_root / project_name

    def load_script(self, project_name: str, filename: str):
        if filename.startswith("scripts/"):
            filename = filename[len("scripts/") :]
        if filename not in self._scripts:
            raise FileNotFoundError(filename)
        return self._scripts[filename]


class TestStatusCalculator:
    @pytest.mark.unit
    def test_select_kind_and_items(self):
        kind, items = StatusCalculator._select_kind_and_items(
            {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]}, "storyboard"
        )
        assert kind == "segments"
        assert len(items) == 1

        kind2, items2 = StatusCalculator._select_kind_and_items({"scenes": [{"scene_id": "E1S01"}]}, "storyboard")
        assert kind2 == "scenes"
        assert len(items2) == 1

    @pytest.mark.unit
    def test_calculate_episode_stats_statuses(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))

        # draft：无任何资源
        draft = calc.calculate_episode_stats(
            "demo",
            {"content_mode": "narration", "segments": [{"duration_seconds": 4}]},
        )
        assert draft["status"] == "draft"
        assert draft["storyboards"] == {"total": 1, "completed": 0}
        assert draft["videos"] == {"total": 1, "completed": 0}
        assert draft["scenes_count"] == 1
        assert draft["duration_seconds"] == 4

        # in_production：有分镜图
        in_prod = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "narration",
                "segments": [
                    {"generated_assets": {"storyboard_image": "a.png"}, "duration_seconds": 6},
                    {"duration_seconds": 4},
                ],
            },
        )
        assert in_prod["status"] == "in_production"
        assert in_prod["storyboards"] == {"total": 2, "completed": 1}
        assert in_prod["videos"] == {"total": 2, "completed": 0}

        # completed：所有场景有视频
        completed = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "drama",
                "scenes": [
                    {"generated_assets": {"video_clip": "a.mp4"}, "duration_seconds": 8},
                ],
            },
        )
        assert completed["status"] == "completed"
        assert completed["storyboards"] == {"total": 1, "completed": 0}
        assert completed["videos"] == {"total": 1, "completed": 1}

    @pytest.mark.unit
    def test_calculate_episode_stats_tolerates_corrupt_generated_assets(self, tmp_path):
        """generated_assets 为非 dict 脏数据（如字符串）时按缺失处理，不抛 AttributeError。"""
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))

        stats = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "narration",
                "segments": [
                    {"generated_assets": "corrupt", "duration_seconds": 4},
                    {"generated_assets": {"storyboard_image": "a.png"}, "duration_seconds": 4},
                ],
            },
        )
        assert stats["storyboards"] == {"total": 2, "completed": 1}
        assert stats["videos"] == {"total": 2, "completed": 0}

    @pytest.mark.unit
    def test_load_episode_script(self, tmp_path):
        project_root = tmp_path / "projects"
        project_path = project_root / "demo"

        # Case 1: 脚本 JSON 存在 → ("generated", script)
        script_data = {"content_mode": "narration", "segments": []}
        scripts = {"episode_1.json": script_data}
        calc = StatusCalculator(_FakePM(project_root, {}, scripts))
        status, script = calc._load_episode_script("demo", 1, "scripts/episode_1.json")
        assert status == "generated"
        assert script == script_data

        # Case 2: 脚本不存在，draft 文件存在 → ("segmented", None)
        draft_dir = project_path / "drafts" / "episode_2"
        draft_dir.mkdir(parents=True)
        (draft_dir / "step1_segments.json").write_text("ok")
        calc2 = StatusCalculator(_FakePM(project_root, {}, {}))
        status2, script2 = calc2._load_episode_script("demo", 2, "scripts/episode_2.json")
        assert status2 == "segmented"
        assert script2 is None

        # Case 2b: narration 仅有旧 step1_segments.md（存量）→ 仍认作 ("segmented", None)
        draft_dir_legacy = project_path / "drafts" / "episode_6"
        draft_dir_legacy.mkdir(parents=True)
        (draft_dir_legacy / "step1_segments.md").write_text("legacy md")
        calc_legacy = StatusCalculator(_FakePM(project_root, {}, {}))
        status_legacy, script_legacy = calc_legacy._load_episode_script("demo", 6, "scripts/episode_6.json")
        assert status_legacy == "segmented"
        assert script_legacy is None

        # Case 3: 两者都不存在 → ("none", None)
        calc3 = StatusCalculator(_FakePM(project_root, {}, {}))
        status3, script3 = calc3._load_episode_script("demo", 3, "scripts/episode_3.json")
        assert status3 == "none"
        assert script3 is None

        # Case 4: drama 模式 — step1_normalized_script.json（结构化内容）存在 → ("segmented", None)
        draft_dir_drama = project_path / "drafts" / "episode_4"
        draft_dir_drama.mkdir(parents=True)
        (draft_dir_drama / "step1_normalized_script.json").write_text('{"title":"t","scenes":[]}')
        calc4 = StatusCalculator(_FakePM(project_root, {}, {}))
        status4, script4 = calc4._load_episode_script("demo", 4, "scripts/episode_4.json", content_mode="drama")
        assert status4 == "segmented"
        assert script4 is None

        # Case 5: drama 模式 — 无 step1_normalized_script.json → ("none", None)
        calc5 = StatusCalculator(_FakePM(project_root, {}, {}))
        status5, script5 = calc5._load_episode_script("demo", 5, "scripts/episode_5.json", content_mode="drama")
        assert status5 == "none"
        assert script5 is None

        # Case 6（AC8）：drama 仅残留旧 step1_normalized_script.md（无 .json、无剧本 JSON）→ ("none", None)
        # 旧 .md 是结构化前的自由文本残留，不视为有效 step1；在制品会被路由回重跑 step1。
        draft_dir_drama_legacy = project_path / "drafts" / "episode_7"
        draft_dir_drama_legacy.mkdir(parents=True)
        (draft_dir_drama_legacy / "step1_normalized_script.md").write_text("legacy free-text draft")
        calc6 = StatusCalculator(_FakePM(project_root, {}, {}))
        status6, script6 = calc6._load_episode_script("demo", 7, "scripts/episode_7.json", content_mode="drama")
        assert status6 == "none"
        assert script6 is None

        # Case 7：reference_video 首次拆分未过校验，只产出隔离草稿、正式 step1_reference_units.json
        # 从未写过 → 仍要判 ("segmented", None)，否则 web 路由会把这一集送进 EpisodeSourceReview
        # 而不是挂着隔离态预览面板的 ScriptReviewGate，用户看不到违约详情与修复入口——这恰是隔离
        # 草稿最常见的产出路径（首轮拆分失败），不能因为「正式文件还没写过」就当没有草稿。
        draft_dir_rv_quarantine = project_path / "drafts" / "episode_8"
        draft_dir_rv_quarantine.mkdir(parents=True)
        (draft_dir_rv_quarantine / "step1_reference_units.invalid.json").write_text('{"units":[]}')
        calc7 = StatusCalculator(_FakePM(project_root, {}, {}))
        status7, script7 = calc7._load_episode_script(
            "demo", 8, "scripts/episode_8.json", generation_mode="reference_video"
        )
        assert status7 == "segmented"
        assert script7 is None

    @pytest.mark.unit
    def test_enrich_project(self, tmp_path):
        project_root = tmp_path / "projects"
        project_root.mkdir(parents=True)
        project = {
            "overview": {"synopsis": "test"},
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
                {"episode": 2, "script_file": "scripts/missing.json"},
            ],
            "characters": {},
            "scenes": {},
            "props": {},
        }
        script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 6,
                    "characters_in_segment": ["A", "B"],
                    "scenes": ["S1"],
                    "props": ["P1"],
                    "generated_assets": {},
                }
            ],
        }
        calc = StatusCalculator(_FakePM(project_root, project, {"episode_1.json": script}))

        enriched = calc.enrich_project(
            "demo",
            {
                **project,
                "episodes": [
                    {"episode": 1, "script_file": "scripts/episode_1.json"},
                    {"episode": 2, "script_file": "scripts/missing.json"},
                ],
            },
        )

        ep1 = enriched["episodes"][0]
        assert ep1["script_status"] == "generated"
        assert ep1["status"] == "scripted"
        assert ep1["scenes_count"] == 1
        assert ep1["storyboards"] == {"total": 1, "completed": 0}
        ep2 = enriched["episodes"][1]
        assert ep2["script_status"] == "none"
        assert ep2["status"] == "draft"

    @pytest.mark.unit
    def test_stale_ledger_episode_regresses_to_pending_preprocess(self, tmp_path):
        """账本标 stale 的集：读时状态回退为待预处理（script_status=none），已有产物不删除。

        重新规划使该集原文范围失效，剧本/媒体虽存在但已过期；读时回退驱动前端
        与 agent 走重做流程，旧产物沿覆盖/版本机制保留可回滚。
        """
        project_root = tmp_path / "projects"
        (project_root / "demo" / "drafts" / "episode_1").mkdir(parents=True)
        (project_root / "demo" / "drafts" / "episode_1" / "step1_segments.json").write_text("ok", encoding="utf-8")
        project = {
            "overview": {"synopsis": "test"},
            "characters": {},
            "scenes": {},
            "props": {},
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "stale"},
                {"episode": 2, "script_file": "scripts/episode_2.json", "ledger_status": "consumed"},
            ],
        }
        scripts = {
            "episode_1.json": {
                "content_mode": "narration",
                "segments": [
                    {"duration_seconds": 4, "generated_assets": {"storyboard_image": "a.png", "video_clip": "b.mp4"}}
                ],
            },
            "episode_2.json": {"content_mode": "narration", "segments": [{"duration_seconds": 4}]},
        }
        calc = StatusCalculator(_FakePM(project_root, project, scripts))

        enriched = calc.enrich_project("demo", project)

        ep1 = enriched["episodes"][0]
        # stale 集即使剧本与分段草稿都在，也回退为待预处理
        assert ep1["script_status"] == "none"
        assert ep1["status"] == "draft"
        assert ep1["videos"] == {"total": 0, "completed": 0}
        # 不删除任何产物：条目仍保留剧本引用与账本状态
        assert ep1["script_file"] == "scripts/episode_1.json"
        assert ep1["ledger_status"] == "stale"
        # 非 stale 集不受影响
        ep2 = enriched["episodes"][1]
        assert ep2["script_status"] == "generated"

    @pytest.mark.unit
    def test_enrich_script(self, tmp_path):
        script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 6,
                    "characters_in_segment": ["A", "B"],
                    "scenes": ["S1"],
                    "props": ["P1"],
                    "generated_assets": {},
                }
            ],
        }
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        enriched_script = calc.enrich_script({**script})
        assert enriched_script["metadata"]["total_scenes"] == 1
        assert enriched_script["metadata"]["estimated_duration_seconds"] == 6
        assert enriched_script["characters_in_episode"] == ["A", "B"]
        assert enriched_script["scenes_in_episode"] == ["S1"]
        assert enriched_script["props_in_episode"] == ["P1"]

    @pytest.mark.unit
    def test_load_episode_script_corrupted_json(self, tmp_path):
        """JSON 损坏时应降级返回 ('generated', None)，而不是上抛异常。"""
        import json

        class _CorruptPM(_FakePM):
            def load_script(self, project_name, filename):
                raise json.JSONDecodeError("Expecting value", "doc", 0)

        calc = StatusCalculator(_CorruptPM(tmp_path / "projects", {}, {}))
        status, script = calc._load_episode_script("demo", 1, "scripts/episode_1.json")
        assert status == "generated"
        assert script is None

    @pytest.mark.unit
    def test_select_ad_by_duck_typing_when_content_mode_absent(self):
        # 本地 legacy 容忍：缺 content_mode 的存量 ad 剧本按 shots 键鸭子推断（矩阵不覆盖本地阶梯）。
        kind, items = StatusCalculator._select_kind_and_items({"shots": [{"shot_id": "E1S01"}]}, "storyboard")
        assert kind == "shots"
        assert len(items) == 1

    @pytest.mark.unit
    def test_calculate_episode_stats_for_ad(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))

        stats = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "ad",
                "shots": [
                    {"duration_seconds": 3, "generated_assets": {"storyboard_image": "a.png"}},
                    {"duration_seconds": 5},
                ],
            },
        )
        assert stats["status"] == "in_production"
        assert stats["scenes_count"] == 2
        assert stats["duration_seconds"] == 8
        assert stats["storyboards"] == {"total": 2, "completed": 1}
        assert stats["videos"] == {"total": 2, "completed": 0}

    @pytest.mark.unit
    def test_ad_reference_path_scores_videos_by_units(self, tmp_path):
        """ad + reference_video 与其他内容模式一样按自包含 video_units 计分。"""
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        script = {
            "content_mode": "ad",
            "video_units": [
                {
                    "unit_id": "E1U1",
                    "shots": [{"text": "镜头1：产品特写"}],
                    "references": [],
                    "duration_seconds": 5,
                    "generated_assets": {"video_clip": "reference_videos/E1U1.mp4"},
                },
            ],
        }

        stats = calc.calculate_episode_stats("demo", script, generation_mode="reference_video")

        assert stats["videos"] == {"total": 1, "completed": 1}
        assert stats["status"] == "completed"
        assert stats["duration_seconds"] == 5
        assert stats["scenes_count"] == 1

    @pytest.mark.unit
    def test_ad_storyboard_path_ignores_leftover_index(self, tmp_path):
        """切回 storyboard 路径后按 shots 计分，残留索引不污染状态。"""
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01", "duration_seconds": 3}],
            "reference_units": [
                {
                    "unit_id": "E1U1",
                    "shot_ids": ["E1S01"],
                    "generated_assets": {"video_clip": "reference_videos/E1U1.mp4"},
                }
            ],
        }

        stats = calc.calculate_episode_stats("demo", script, generation_mode="storyboard")

        assert stats["videos"] == {"total": 1, "completed": 0}

    @pytest.mark.unit
    def test_ad_missing_duration_counts_zero(self, tmp_path):
        # ad 无单镜头默认时长偏好：缺 duration_seconds 的镜头按 0 计入，
        # 不挪用 narration(4)/drama(8) 的默认值污染 target_duration 对照
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        stats = calc.calculate_episode_stats(
            "demo",
            {"content_mode": "ad", "shots": [{"duration_seconds": 3}, {}]},
        )
        assert stats["duration_seconds"] == 3

    @pytest.mark.unit
    def test_enrich_script_aggregates_ad_references(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        script = {
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "duration_seconds": 3,
                    "characters_in_shot": ["主播"],
                    "scenes": ["客厅"],
                    "props": ["速干杯"],
                },
                {
                    "shot_id": "E1S02",
                    "duration_seconds": 5,
                    "characters_in_shot": [],
                    "scenes": ["客厅"],
                    "props": [],
                },
            ],
        }
        enriched = calc.enrich_script(script)
        assert enriched["metadata"]["total_scenes"] == 2
        assert enriched["duration_seconds"] == 8
        assert enriched["characters_in_episode"] == ["主播"]
        assert enriched["scenes_in_episode"] == ["客厅"]
        assert enriched["props_in_episode"] == ["速干杯"]

    @pytest.mark.unit
    def test_legacy_ad_script_on_reference_route_keeps_shots(self):
        """缺 content_mode 的遗留 shots 剧本仍按可见主骨架计分，不虚构 video_units。"""
        script = {"shots": [{"shot_id": "E1S01"}, {"shot_id": "E1S02"}]}
        kind, items = StatusCalculator._select_kind_and_items(script, "reference_video")
        assert kind == "shots"
        assert len(items) == 2

    @pytest.mark.unit
    def test_malformed_unit_container_scores_as_empty(self):
        """``video_units`` 非数组（外部编辑 / 归档导入的脏数据）归一为空而不是原样下传——
        否则下游按 dict 键迭代、对 str 调 get，项目详情读取变成 500 全不可查看。"""
        for malformed in ({"unit_a": {}}, "E1U1", 3):
            kind, items = StatusCalculator._select_kind_and_items(
                {"content_mode": "narration", "video_units": malformed}, "reference_video"
            )
            assert kind == "video_units"
            assert items == []

    @pytest.mark.unit
    def test_non_object_units_are_dropped_before_scoring(self):
        """``video_units`` 夹非对象条目：剔除而不是原样下传——下游对 str 调 get 会让
        项目详情读取变成 500，整个项目不可查看。"""
        kind, items = StatusCalculator._select_kind_and_items(
            {"content_mode": "narration", "video_units": ["bad", {"unit_id": "E1U1"}, 7, None]},
            "reference_video",
        )
        assert kind == "video_units"
        assert items == [{"unit_id": "E1U1"}]

    @pytest.mark.unit
    def test_enrich_script_tolerates_malformed_unit_references(self, tmp_path):
        """unit 本身合法但 references 容器/条目脏：聚合跳过而非抛 AttributeError。"""
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        script = {
            "content_mode": "narration",
            "metadata": {},
            "video_units": [
                {"unit_id": "E1U1", "references": "bad"},
                {"unit_id": "E1U2", "references": ["bad", {"type": "character", "name": "张三"}]},
                {"unit_id": "E1U3"},
            ],
        }
        enriched = calc.enrich_script(script, generation_mode="reference_video")
        assert enriched["characters_in_episode"] == ["张三"]

    @pytest.mark.unit
    def test_enrich_script_tolerates_non_string_reference_names(self, tmp_path):
        """``name`` 为 list / dict 会在集合 add 处抛 unhashable，为数字会让 sorted 抛
        混类型比较错误——两者都让项目详情读取失败，须一并跳过。"""
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        script = {
            "content_mode": "narration",
            "metadata": {},
            "video_units": [
                {
                    "unit_id": "E1U1",
                    "references": [
                        {"type": "character", "name": ["坏"]},
                        {"type": "character", "name": {"k": "v"}},
                        {"type": "scene", "name": 7},
                        {"type": "prop", "name": ""},
                        {"type": "character", "name": "张三"},
                    ],
                }
            ],
        }
        enriched = calc.enrich_script(script, generation_mode="reference_video")
        assert enriched["characters_in_episode"] == ["张三"]
        assert enriched["scenes_in_episode"] == []
        assert enriched["props_in_episode"] == []

    @pytest.mark.unit
    def test_duck_typing_precedence_segments_over_scenes_over_shots(self):
        """缺 content_mode 的老脚本同时残留多种键时，鸭子类型优先级固定为
        segments > scenes > shots（依赖 _LEGACY_DUCK_TYPE_KINDS 顺序，本测试锁定该顺序）。"""
        kind, _ = StatusCalculator._select_kind_and_items(
            {"segments": [{}], "scenes": [{}], "shots": [{}]}, "storyboard"
        )
        assert kind == "segments"
        kind, _ = StatusCalculator._select_kind_and_items({"scenes": [{}], "shots": [{}]}, "storyboard")
        assert kind == "scenes"


# 骨架种类 → 触发该骨架的 (content_mode, generation_mode)，即 resolve_declared_kind 的逆。
_KIND_TO_MODES = {
    "segments": ("narration", None),
    "scenes": ("drama", None),
    "shots": ("ad", None),
    "video_units": ("narration", "reference_video"),
}


class TestStatusCalculatorSkeletonExhaustiveness:
    """穷尽性断言：calculate_episode_stats 的按 kind 分派覆盖 SKELETONS 全部键。

    第五种骨架加入 SKELETONS（+ 规范解析映射）时，script_duration_total 查 _ITEM_FALLBACK_DURATIONS
    KeyError，逐个报红。
    """

    @pytest.mark.unit
    @pytest.mark.parametrize("kind", list(_KIND_TO_MODES))
    def test_calculate_episode_stats_covers_every_skeleton_kind(self, kind, tmp_path):
        from lib.script_skeleton import SKELETONS

        # 遍历 SKELETONS 全键：新增第五种骨架而 _KIND_TO_MODES 未登记即 KeyError 报红。
        assert set(_KIND_TO_MODES) == set(SKELETONS)

        content_mode, gen_mode = _KIND_TO_MODES[kind]
        id_field = SKELETONS[kind].id_field
        script = {"content_mode": content_mode, kind: [{id_field: "E1S01"}]}

        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        stats = calc.calculate_episode_stats("demo", script, generation_mode=gen_mode)

        assert isinstance(stats, dict)
        assert "status" in stats
        assert stats["scenes_count"] == 1
