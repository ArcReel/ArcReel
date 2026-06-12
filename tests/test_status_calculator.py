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
    def test_select_content_mode_and_items(self):
        mode, items = StatusCalculator._select_content_mode_and_items(
            {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]}
        )
        assert mode == "narration"
        assert len(items) == 1

        mode2, items2 = StatusCalculator._select_content_mode_and_items({"scenes": [{"scene_id": "E1S01"}]})
        assert mode2 == "drama"
        assert len(items2) == 1

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
        (draft_dir / "step1_segments.md").write_text("ok")
        calc2 = StatusCalculator(_FakePM(project_root, {}, {}))
        status2, script2 = calc2._load_episode_script("demo", 2, "scripts/episode_2.json")
        assert status2 == "segmented"
        assert script2 is None

        # Case 3: 两者都不存在 → ("none", None)
        calc3 = StatusCalculator(_FakePM(project_root, {}, {}))
        status3, script3 = calc3._load_episode_script("demo", 3, "scripts/episode_3.json")
        assert status3 == "none"
        assert script3 is None

        # Case 4: drama 模式 — step1_normalized_script.md 存在 → ("segmented", None)
        draft_dir_drama = project_path / "drafts" / "episode_4"
        draft_dir_drama.mkdir(parents=True)
        (draft_dir_drama / "step1_normalized_script.md").write_text("drama draft")
        calc4 = StatusCalculator(_FakePM(project_root, {}, {}))
        status4, script4 = calc4._load_episode_script("demo", 4, "scripts/episode_4.json", content_mode="drama")
        assert status4 == "segmented"
        assert script4 is None

        # Case 5: drama 模式 — 无 step1_normalized_script.md → ("none", None)
        calc5 = StatusCalculator(_FakePM(project_root, {}, {}))
        status5, script5 = calc5._load_episode_script("demo", 5, "scripts/episode_5.json", content_mode="drama")
        assert status5 == "none"
        assert script5 is None

    def test_calculate_current_phase_setup(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project_no_overview = {}
        assert calc.calculate_current_phase(project_no_overview, []) == "setup"
        # 即使有空集列表，但无 overview 且无资产 → 仍是 setup
        assert calc.calculate_current_phase(project_no_overview, [], assets_completed=0) == "setup"

    def test_calculate_current_phase_worldbuilding(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project = {"overview": {"synopsis": "test"}}
        # 无任何 generated 脚本 → worldbuilding
        episodes_stats = [{"script_status": "none"}, {"script_status": "segmented"}]
        assert calc.calculate_current_phase(project, episodes_stats) == "worldbuilding"
        # 无集 → worldbuilding
        assert calc.calculate_current_phase(project, []) == "worldbuilding"
        # 没有 overview，但已有资产产出 → 仍判定为 worldbuilding（不卡在 setup）
        assert calc.calculate_current_phase({}, [], assets_completed=1) == "worldbuilding"
        # 没有 overview / 资产，但已有分段草稿 → 仍判定为 worldbuilding
        assert calc.calculate_current_phase({}, [{"script_status": "segmented"}], assets_completed=0) == "worldbuilding"

    def test_calculate_current_phase_scripting(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project = {"overview": {"synopsis": "test"}}
        # 有至少一集 generated，但未全部 → scripting
        episodes_stats = [
            {"script_status": "generated", "status": "draft"},
            {"script_status": "none"},
        ]
        assert calc.calculate_current_phase(project, episodes_stats) == "scripting"
        # 没有 overview 也一样：脚本产物本身就是更强信号
        assert calc.calculate_current_phase({}, episodes_stats) == "scripting"

    def test_calculate_current_phase_production_and_completed(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project = {"overview": {"synopsis": "test"}}
        # 全部 generated，有未完成视频 → production
        episodes_stats = [
            {"script_status": "generated", "status": "in_production"},
            {"script_status": "generated", "status": "draft"},
        ]
        assert calc.calculate_current_phase(project, episodes_stats) == "production"
        # 全部 completed → completed
        episodes_stats_done = [
            {"script_status": "generated", "status": "completed"},
        ]
        assert calc.calculate_current_phase(project, episodes_stats_done) == "completed"
        # 没有 overview 也一样：production / completed 由脚本与视频状态决定
        assert calc.calculate_current_phase({}, episodes_stats) == "production"
        assert calc.calculate_current_phase({}, episodes_stats_done) == "completed"

    @pytest.mark.unit
    def test_calculate_current_phase_regression_no_overview_with_artifacts(self, tmp_path):
        """回归：项目缺 overview 但已生成资产+脚本+部分视频，阶段必须前进。

        历史 bug：``calculate_current_phase`` 早退判定 ``if not overview: return "setup"``
        导致用户跳过 overview 直接做后续步骤时，阶段进度条永远卡在「筹备」。
        """
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project = {"title": "无 overview 的项目"}

        # 仅有资产 → worldbuilding（之前会被错判为 setup）
        assert calc.calculate_current_phase(project, [], assets_completed=2) == "worldbuilding"

        # 资产 + 部分脚本 → scripting
        partial_scripts = [
            {"script_status": "generated", "status": "draft"},
            {"script_status": "none"},
        ]
        assert calc.calculate_current_phase(project, partial_scripts, assets_completed=3) == "scripting"

        # 资产 + 全部脚本 + 部分视频 → production
        all_scripts_in_prod = [
            {"script_status": "generated", "status": "in_production"},
        ]
        assert calc.calculate_current_phase(project, all_scripts_in_prod, assets_completed=3) == "production"

    def test_calculate_project_status(self, tmp_path):
        project_root = tmp_path / "projects"
        project_path = project_root / "demo"
        (project_path / "characters").mkdir(parents=True)
        (project_path / "scenes").mkdir(parents=True)
        (project_path / "props").mkdir(parents=True)
        (project_path / "characters" / "A.png").write_bytes(b"ok")
        (project_path / "scenes" / "S1.png").write_bytes(b"ok")
        (project_path / "props" / "P1.png").write_bytes(b"ok")

        project = {
            "overview": {"synopsis": "test"},
            "characters": {"A": {"character_sheet": "characters/A.png"}, "B": {"character_sheet": ""}},
            "scenes": {
                "S1": {"scene_sheet": "scenes/S1.png"},
                "S2": {"scene_sheet": ""},
            },
            "props": {
                "P1": {"prop_sheet": "props/P1.png"},
            },
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
            ],
        }
        scripts = {
            "episode_1.json": {
                "content_mode": "narration",
                "segments": [
                    {"duration_seconds": 4, "generated_assets": {"storyboard_image": "a.png", "video_clip": "b.mp4"}},
                ],
            }
        }
        calc = StatusCalculator(_FakePM(project_root, project, scripts))
        status = calc.calculate_project_status("demo", project)

        assert status["current_phase"] == "completed"
        assert status["phase_progress"] == 1.0
        assert status["characters"] == {"total": 2, "completed": 1}
        assert status["scenes"] == {"total": 2, "completed": 1}
        assert status["props"] == {"total": 1, "completed": 1}
        assert status["episodes_summary"] == {"total": 1, "scripted": 1, "in_production": 0, "completed": 1}

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

        assert "status" in enriched
        assert enriched["status"]["current_phase"] == "scripting"
        ep1 = enriched["episodes"][0]
        assert ep1["script_status"] == "generated"
        assert ep1["status"] == "scripted"
        assert ep1["scenes_count"] == 1
        assert ep1["storyboards"] == {"total": 1, "completed": 0}
        ep2 = enriched["episodes"][1]
        assert ep2["script_status"] == "none"
        assert ep2["status"] == "draft"

    def test_stale_ledger_episode_regresses_to_pending_preprocess(self, tmp_path):
        """账本标 stale 的集：读时状态回退为待预处理（script_status=none），已有产物不删除。

        重排使该集原文范围失效，剧本/媒体虽存在但已过期；读时回退驱动前端
        与 agent 走重做流程，旧产物沿覆盖/版本机制保留可回滚。
        """
        project_root = tmp_path / "projects"
        (project_root / "demo" / "drafts" / "episode_1").mkdir(parents=True)
        (project_root / "demo" / "drafts" / "episode_1" / "step1_segments.md").write_text("ok", encoding="utf-8")
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
        # 项目级汇总同步回退：仅 1 集计为已生成剧本
        assert enriched["status"]["episodes_summary"]["scripted"] == 1

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

    def test_calculate_project_status_preloaded_scripts_skips_pm_load(self, tmp_path):
        """preloaded_scripts 覆盖所有集时，不应再调用 pm.load_script。

        list_projects 的 hot-path 合同：与 resolve_project_cover 共用一份加载结果，
        避免 cover + status 两次 JSON 解析。"""
        project_root = tmp_path / "projects"
        project_path = project_root / "demo"
        project_path.mkdir(parents=True)

        project = {
            "overview": {"synopsis": "test"},
            "characters": {},
            "scenes": {},
            "props": {},
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
            ],
        }

        class _TrackingPM(_FakePM):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.load_calls: list[str] = []

            def load_script(self, project_name, filename):
                self.load_calls.append(filename)
                return super().load_script(project_name, filename)

        pm = _TrackingPM(project_root, project, {})  # 空 scripts：若走 pm.load_script 必抛
        calc = StatusCalculator(pm)

        preloaded = {
            "scripts/episode_1.json": {
                "content_mode": "narration",
                "segments": [{"duration_seconds": 4, "generated_assets": {}}],
            }
        }
        status = calc.calculate_project_status("demo", project, preloaded_scripts=preloaded)

        assert pm.load_calls == []  # 预加载命中：未触发任何一次 pm.load_script
        # 合理性断言：预加载的剧本被识别为 generated 且纳入统计
        assert status["episodes_summary"]["total"] == 1
        assert status["episodes_summary"]["scripted"] == 1

    def test_calculate_project_status_preloaded_scripts_falls_back_for_missing(self, tmp_path):
        """preloaded_scripts 未覆盖的集：回退 pm.load_script，保持"尽力而为"合同。"""
        project_root = tmp_path / "projects"
        project_path = project_root / "demo"
        project_path.mkdir(parents=True)

        project = {
            "overview": {"synopsis": "test"},
            "characters": {},
            "scenes": {},
            "props": {},
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
                {"episode": 2, "script_file": "scripts/episode_2.json"},
            ],
        }

        class _TrackingPM(_FakePM):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.load_calls: list[str] = []

            def load_script(self, project_name, filename):
                self.load_calls.append(filename)
                return super().load_script(project_name, filename)

        # pm 仅能加载 episode_2；preload 覆盖 episode_1。
        pm = _TrackingPM(
            project_root,
            project,
            {"episode_2.json": {"content_mode": "narration", "segments": [{"duration_seconds": 4}]}},
        )
        calc = StatusCalculator(pm)

        preloaded = {
            "scripts/episode_1.json": {"content_mode": "narration", "segments": [{"duration_seconds": 4}]},
        }
        status = calc.calculate_project_status("demo", project, preloaded_scripts=preloaded)

        # 预加载命中 episode_1 (no load_script 调用)；episode_2 未预加载 → pm.load_script 一次。
        assert pm.load_calls == ["scripts/episode_2.json"]
        assert status["episodes_summary"]["total"] == 2
        assert status["episodes_summary"]["scripted"] == 2


class TestAdStatusCalculation:
    """广告/短片模式（平铺 shots[]）的状态与统计计算。"""

    def test_select_ad_mode_and_items(self):
        mode, items = StatusCalculator._select_content_mode_and_items(
            {"content_mode": "ad", "shots": [{"shot_id": "E1S01"}]}
        )
        assert mode == "ad"
        assert len(items) == 1

    def test_select_ad_by_duck_typing_when_content_mode_absent(self):
        mode, items = StatusCalculator._select_content_mode_and_items({"shots": [{"shot_id": "E1S01"}]})
        assert mode == "ad"
        assert len(items) == 1

    def test_ad_with_reference_generation_mode_still_dispatches_shots(self):
        """ad 剧本骨架唯一：残留 generation_mode 戳也按 shots 分派，不找 video_units。"""
        mode, items = StatusCalculator._select_content_mode_and_items(
            {
                "content_mode": "ad",
                "generation_mode": "reference_video",
                "shots": [{"shot_id": "E1S01"}, {"shot_id": "E1S02"}],
            }
        )
        assert mode == "ad"
        assert len(items) == 2

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

    def test_ad_reference_path_scores_videos_by_units(self, tmp_path):
        """ad + reference_video：视频进度按派生 unit 计，分镜仍按 shots 计（该路径恒 0）。"""
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        script = {
            "content_mode": "ad",
            "shots": [
                {"shot_id": "E1S01", "duration_seconds": 3},
                {"shot_id": "E1S02", "duration_seconds": 2},
            ],
            "reference_units": [
                {
                    "unit_id": "E1U1",
                    "shot_ids": ["E1S01", "E1S02"],
                    "generated_assets": {"video_clip": "reference_videos/E1U1.mp4"},
                },
            ],
        }

        stats = calc.calculate_episode_stats("demo", script, generation_mode="reference_video")

        assert stats["videos"] == {"total": 1, "completed": 1}
        assert stats["status"] == "completed"
        # 时长口径仍以 shots（内容唯一真相）求和
        assert stats["duration_seconds"] == 5
        assert stats["scenes_count"] == 2

    def test_ad_reference_path_without_index_stays_draft(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        script = {"content_mode": "ad", "shots": [{"shot_id": "E1S01", "duration_seconds": 3}]}

        stats = calc.calculate_episode_stats("demo", script, generation_mode="reference_video")

        assert stats["videos"] == {"total": 0, "completed": 0}
        assert stats["status"] == "draft"

    def test_ad_reference_path_malformed_index_scores_as_not_derived(self, tmp_path):
        """索引形状损坏（非数组 / 夹非 dict 条目）按未派生计分，不部分计数、不抛错。"""
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        valid_unit = {
            "unit_id": "E1U1",
            "shot_ids": ["E1S01"],
            "generated_assets": {"video_clip": "reference_videos/E1U1.mp4"},
        }
        for malformed in (
            "garbage",
            {"unit_id": "E1U1"},
            [valid_unit, "junk"],
            [{**valid_unit, "generated_assets": "done"}],
            [valid_unit, {"unit_id": "E1U2", "shot_ids": [], "generated_assets": ["x"]}],
        ):
            script = {
                "content_mode": "ad",
                "shots": [{"shot_id": "E1S01", "duration_seconds": 3}],
                "reference_units": malformed,
            }

            stats = calc.calculate_episode_stats("demo", script, generation_mode="reference_video")

            assert stats["videos"] == {"total": 0, "completed": 0}
            assert stats["status"] == "draft"

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

    def test_ad_missing_duration_counts_zero(self, tmp_path):
        # ad 无单镜头默认时长偏好：缺 duration_seconds 的镜头按 0 计入，
        # 不挪用 narration(4)/drama(8) 的默认值污染 target_duration 对照
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        stats = calc.calculate_episode_stats(
            "demo",
            {"content_mode": "ad", "shots": [{"duration_seconds": 3}, {}]},
        )
        assert stats["duration_seconds"] == 3

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

    def test_duck_typing_precedence_segments_over_scenes_over_shots(self):
        """缺 content_mode 的老脚本同时残留多种键时，鸭子类型优先级固定为
        segments > scenes > shots（依赖 SCRIPT_SHAPES 注册顺序，本测试钉住该顺序）。"""
        mode, _ = StatusCalculator._select_content_mode_and_items({"segments": [{}], "scenes": [{}], "shots": [{}]})
        assert mode == "narration"
        mode, _ = StatusCalculator._select_content_mode_and_items({"scenes": [{}], "shots": [{}]})
        assert mode == "drama"
