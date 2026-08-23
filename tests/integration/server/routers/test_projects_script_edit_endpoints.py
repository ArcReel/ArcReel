"""Tests for projects_script_edit_endpoints."""

import unicodedata
from contextlib import contextmanager
from copy import deepcopy

import pytest

from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
)


class TestProjectsRouter:
    def test_scene_segment_and_overview_endpoints(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [{"scene_id": "001", "duration_seconds": 8, "image_prompt": {}, "video_prompt": {}}],
        }
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }

        client = _client(monkeypatch, fake_pm)

        with client:
            patch_scene = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={"script_file": "episode_1.json", "updates": {"duration_seconds": 6, "segment_break": True}},
            )
            assert patch_scene.status_code == 200
            assert patch_scene.json()["scene"]["duration_seconds"] == 6

            patch_scene_missing = client.patch(
                "/api/v1/projects/ready/script-scenes/404",
                json={"script_file": "episode_1.json", "updates": {}},
            )
            assert patch_scene_missing.status_code == 404

            patch_segment = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "duration_seconds": 8, "segment_break": True},
            )
            assert patch_segment.status_code == 200

            not_narration = client.patch(
                "/api/v1/projects/ready/segments/001",
                json={"script_file": "episode_1.json", "duration_seconds": 8},
            )
            assert not_narration.status_code == 400

            segment_missing = client.patch(
                "/api/v1/projects/ready/segments/E9S99",
                json={"script_file": "narration.json", "duration_seconds": 8},
            )
            assert segment_missing.status_code == 404

            gen_overview_ok = client.post("/api/v1/projects/ready/generate-overview")
            assert gen_overview_ok.status_code == 200

    def test_update_segment_writes_character_and_clue_refs(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["Forest"],
                    "props": ["Sword"],
                }
            ],
        }

        client = _client(monkeypatch, fake_pm)
        nfd_cafe = unicodedata.normalize("NFD", "Café")

        with client:
            # 写入新引用列表
            patched = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={
                    "script_file": "narration.json",
                    "characters_in_segment": [" Bob ", f" {nfd_cafe} "],
                    "scenes": [" Castle "],
                    "props": [],
                },
            )
            assert patched.status_code == 200
            seg = patched.json()["segment"]
            assert seg["characters_in_segment"] == ["Bob", "Café"]
            assert seg["scenes"] == ["Castle"]
            assert seg["props"] == []

            # 不传字段时不应改动现有值
            untouched = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "duration_seconds": 7},
            )
            assert untouched.status_code == 200
            seg2 = untouched.json()["segment"]
            assert seg2["duration_seconds"] == 7
            assert seg2["characters_in_segment"] == ["Bob", "Café"]
            assert seg2["scenes"] == ["Castle"]
            assert seg2["props"] == []

    def test_update_segment_allows_unchanged_legacy_mixed_speech(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        prompt = {"dialogue": [{"speaker": "Alice", "line": "快走。"}]}
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "风吹过旷野。",
                    "video_prompt": prompt,
                    "needs_replan": True,
                }
            ],
        }
        client = _client(monkeypatch, fake_pm)

        with client:
            response = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "video_prompt": prompt, "note": "保留历史媒体"},
            )

        assert response.status_code == 200
        assert response.json()["segment"]["note"] == "保留历史媒体"
        assert response.json()["segment"]["needs_replan"] is True

    def test_update_segment_allows_visual_prompt_edit_on_legacy_mixed_speech(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "风吹过旷野。",
                    "video_prompt": {"action": "转身", "dialogue": [{"speaker": "Alice", "line": "快走。"}]},
                    "needs_replan": True,
                }
            ],
        }
        client = _client(monkeypatch, fake_pm)

        with client:
            response = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={
                    "script_file": "narration.json",
                    "video_prompt": {"action": "慢慢转身", "dialogue": [{"speaker": "Alice", "line": "快走。"}]},
                },
            )

        assert response.status_code == 200
        assert response.json()["segment"]["video_prompt"]["action"] == "慢慢转身"
        assert response.json()["segment"]["needs_replan"] is True

    def test_update_segment_rejects_drama_script_with_residual_segments(self, tmp_path, monkeypatch):
        # drama 脚本残留 segments 键不应被当 narration 改写：须返回 400 而非放行
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "drama.json")] = {
            "content_mode": "drama",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
            "scenes": [{"scene_id": "E1S01"}],
        }

        client = _client(monkeypatch, fake_pm)

        with client:
            resp = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "drama.json", "duration_seconds": 7},
            )
            assert resp.status_code == 400

    def test_update_segment_write_value_error_returns_422(self, tmp_path, monkeypatch):
        # 写盘统一入口对客户端错误（结构非法 / 集号错配 / 非法文件名）抛 ValueError，
        # router 须统一转 422 而非落到 500 兜底。
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }

        @contextmanager
        def _raising_locked_script(name, script_file):
            script = fake_pm.load_script(name, script_file)
            yield script
            raise ValueError("脚本内 episode=1 与文件名 episode_10 不一致")

        monkeypatch.setattr(fake_pm, "locked_script", _raising_locked_script)
        client = _client(monkeypatch, fake_pm)

        with client:
            resp = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "duration_seconds": 7},
            )
            assert resp.status_code == 422
            body = resp.json()
            # 摘要走产品语言，异常原文只进独立诊断字段
            assert body["detail"] == "脚本结构校验失败，请检查后重试"
            assert "不一致" in body["diagnostic"]

    def test_update_scene_supports_character_and_clue_refs(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "001",
                    "duration_seconds": 8,
                    "characters_in_scene": ["Alice"],
                    "scenes": [],
                    "props": [],
                }
            ],
        }

        client = _client(monkeypatch, fake_pm)
        nfd_cafe = unicodedata.normalize("NFD", "Café")

        with client:
            patched = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={
                    "script_file": "episode_1.json",
                    "updates": {
                        "characters_in_scene": [f" {nfd_cafe} "],
                        "scenes": [" Castle "],
                        "props": [" Map "],
                    },
                },
            )
            assert patched.status_code == 200
            scene = patched.json()["scene"]
            assert scene["characters_in_scene"] == ["Café"]
            assert scene["scenes"] == ["Castle"]
            assert scene["props"] == ["Map"]

            gen_overview_bad = client.post("/api/v1/projects/bad/generate-overview")
            assert gen_overview_bad.status_code == 400
            assert "源目录为空" in gen_overview_bad.json()["detail"]

            # 供应商未配置属另一类原因，与「源目录为空」区分（各自映射独立 i18n key）
            gen_overview_no_provider = client.post("/api/v1/projects/no-provider/generate-overview")
            assert gen_overview_no_provider.status_code == 400
            assert "配置文本供应商" in gen_overview_no_provider.json()["detail"]

            update_overview = client.patch(
                "/api/v1/projects/ready/overview",
                json={"synopsis": "new synopsis", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            )
            assert update_overview.status_code == 200
            assert update_overview.json()["overview"]["synopsis"] == "new synopsis"

    def test_update_scene_atomically_rejects_mixed_utterances(self, tmp_path, monkeypatch):
        # 人工编辑不能把一次视频请求写成角色发声 + 叙述旁白混合单元。
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "001",
                    "duration_seconds": 8,
                    "characters_in_scene": ["Alice"],
                    "scenes": [],
                    "props": [],
                    "utterances": [],
                }
            ],
        }

        client = _client(monkeypatch, fake_pm)
        utterances = [
            {"kind": "dialogue", "speaker": "Alice", "text": "你来了。"},
            {"kind": "voiceover", "speaker": None, "text": "命运就此转向。"},
        ]

        with client:
            rejected = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={"script_file": "episode_1.json", "updates": {"utterances": utterances}},
            )
            assert rejected.status_code == 409
            detail = rejected.json()["detail"]
            assert detail["problems"][0]["code"] == "mixed_speech"
            assert detail["problems"][0]["operation_index"] == 0
            assert detail["problems"][0]["next_action"] == "replan_unit"
            assert fake_pm.scripts[("ready", "episode_1.json")]["scenes"][0]["utterances"] == []

    def test_update_scene_allows_unchanged_legacy_mixed_utterances(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        utterances = [
            {"kind": "dialogue", "speaker": "Alice", "text": "你来了。"},
            {"kind": "voiceover", "speaker": None, "text": "命运就此转向。"},
        ]
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [{"scene_id": "001", "duration_seconds": 8, "utterances": utterances}],
        }
        client = _client(monkeypatch, fake_pm)

        with client:
            response = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={
                    "script_file": "episode_1.json",
                    "updates": {"utterances": utterances, "note": "保留历史媒体"},
                },
            )

        assert response.status_code == 200
        assert response.json()["scene"]["note"] == "保留历史媒体"

    def test_update_scene_rejects_legacy_prompt_edit_that_introduces_mixed_speech(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        original_prompt = {"action": "转身"}
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "001",
                    "duration_seconds": 8,
                    "video_prompt": original_prompt,
                    "voiceover": ["命运就此转向。"],
                }
            ],
        }
        client = _client(monkeypatch, fake_pm)

        with client:
            response = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={
                    "script_file": "episode_1.json",
                    "updates": {
                        "video_prompt": {
                            "action": "转身",
                            "dialogue": [{"speaker": "Alice", "line": "跟紧我。"}],
                        }
                    },
                },
            )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["problems"][0]["code"] == "mixed_speech"
        assert detail["problems"][0]["locations"] == [
            {"path": ["video_prompt", "dialogue", 0, "line"], "line": None},
            {"path": ["voiceover", 0], "line": None},
        ]
        saved = fake_pm.scripts[("ready", "episode_1.json")]["scenes"][0]
        assert saved["video_prompt"] == original_prompt

    @staticmethod
    def _ad_script(shot_ids: list[str]) -> dict:
        return {
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": sid,
                    "section": "hook",
                    "duration_seconds": 4,
                    "voiceover_text": f"口播 {sid}",
                    "video_prompt": {},
                    "products_in_shot": [],
                }
                for sid in shot_ids
            ],
        }

    def test_update_shot_edits_voiceover_section_duration(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01", "E1S02"])
        client = _client(monkeypatch, fake_pm)

        with client:
            patched = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "updates": {
                        "voiceover_text": "新口播",
                        "section": "demo",
                        "duration_seconds": 6,
                        "products_in_shot": ["速干杯"],
                    },
                },
            )
            assert patched.status_code == 200
            shot = patched.json()["shot"]
            assert shot["voiceover_text"] == "新口播"
            assert shot["section"] == "demo"
            assert shot["duration_seconds"] == 6
            assert shot["products_in_shot"] == ["速干杯"]
            # 持久化落到脚本存储
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"][0]
            assert saved["voiceover_text"] == "新口播"

    def test_update_shot_allows_unchanged_legacy_mixed_speech(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        script = self._ad_script(["E1S01"])
        script["shots"][0]["video_prompt"] = {"dialogue": [{"speaker": "Alice", "line": "快走。"}]}
        fake_pm.scripts[("ad-ready", "episode_1.json")] = script
        client = _client(monkeypatch, fake_pm)

        with client:
            response = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "updates": {"voiceover_text": "口播 E1S01", "note": "保留历史媒体"},
                },
            )

        assert response.status_code == 200
        assert response.json()["shot"]["note"] == "保留历史媒体"

    def test_update_shot_allows_visual_prompt_edit_on_legacy_mixed_speech(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        script = self._ad_script(["E1S01"])
        script["shots"][0]["video_prompt"] = {
            "action": "转身",
            "dialogue": [{"speaker": "Alice", "line": "快走。"}],
        }
        script["shots"][0]["needs_replan"] = True
        fake_pm.scripts[("ad-ready", "episode_1.json")] = script
        client = _client(monkeypatch, fake_pm)

        with client:
            response = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "updates": {
                        "video_prompt": {
                            "action": "慢慢转身",
                            "dialogue": [{"speaker": "Alice", "line": "快走。"}],
                        }
                    },
                },
            )

        assert response.status_code == 200
        assert response.json()["shot"]["video_prompt"]["action"] == "慢慢转身"
        assert response.json()["shot"]["needs_replan"] is True

    def test_update_shot_ignores_non_whitelisted_fields(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01"])
        client = _client(monkeypatch, fake_pm)

        with client:
            patched = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "updates": {"shot_id": "E1S99", "generated_assets": {"status": "completed"}, "note": "备注"},
                },
            )
            assert patched.status_code == 200
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"][0]
            assert saved["shot_id"] == "E1S01"
            assert "generated_assets" not in saved
            assert saved["note"] == "备注"

    def test_update_shot_rejects_non_ad_script(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

        with client:
            rejected = client.patch(
                "/api/v1/projects/ready/script-shots/001",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert rejected.status_code == 400

    def test_update_shot_unknown_id_404(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01"])
        client = _client(monkeypatch, fake_pm)

        with client:
            missing = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S99",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert missing.status_code == 404

    def test_reorder_shots_full_permutation(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01", "E1S02", "E1S03"])
        client = _client(monkeypatch, fake_pm)

        with client:
            reordered = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S03", "E1S01", "E1S02"]},
            )
            assert reordered.status_code == 200
            assert [s["shot_id"] for s in reordered.json()["shots"]] == ["E1S03", "E1S01", "E1S02"]
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"]
            assert [s["shot_id"] for s in saved] == ["E1S03", "E1S01", "E1S02"]

    def test_reorder_shots_rejects_mismatched_ids(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01", "E1S02"])
        client = _client(monkeypatch, fake_pm)

        with client:
            # 数量不一致
            short = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S01"]},
            )
            assert short.status_code == 400
            # 重复 ID
            dup = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S01", "E1S01"]},
            )
            assert dup.status_code == 400
            # 集合不匹配
            mismatch = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S01", "E1S99"]},
            )
            assert mismatch.status_code == 400
            # 原顺序未被破坏
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"]
            assert [s["shot_id"] for s in saved] == ["E1S01", "E1S02"]

    def test_reorder_shots_rejects_non_ad_script(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

        with client:
            rejected = client.post(
                "/api/v1/projects/ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["001"]},
            )
            assert rejected.status_code == 400

    def test_corrupted_shots_shape_fails_loud_not_silently_wiped(self, tmp_path, monkeypatch):
        """shots 非列表 / 含非对象元素时返回 422，且不被 reorder 空排列覆盖成 []。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = {"content_mode": "ad", "shots": "oops"}
        client = _client(monkeypatch, fake_pm)

        with client:
            # 非列表 shots：reorder 传空排列也必须 422，不得把损坏数据覆盖成空列表
            wiped = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": []},
            )
            assert wiped.status_code == 422
            assert fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"] == "oops"

            # PATCH 路径同样 422，而非误导性的 404
            patched = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert patched.status_code == 422

            # 列表含非对象元素：同样 fail loud
            fake_pm.scripts[("ad-ready", "episode_1.json")] = {"content_mode": "ad", "shots": [{"shot_id": "a"}, 42]}
            mixed = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["a"]},
            )
            assert mixed.status_code == 422

            # shot_id 缺失或非字符串：拦下避免 PATCH 误报 404 / reorder KeyError 变 500
            fake_pm.scripts[("ad-ready", "episode_1.json")] = {
                "content_mode": "ad",
                "shots": [{"shot_id": "a"}, {"section": "hook"}],
            }
            missing_id = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["a"]},
            )
            assert missing_id.status_code == 422

            fake_pm.scripts[("ad-ready", "episode_1.json")] = {
                "content_mode": "ad",
                "shots": [{"shot_id": 7}],
            }
            dirty_id = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert dirty_id.status_code == 422

            # 重复 shot_id：身份键不唯一，PATCH 会静默更新首个命中项，必须拦下
            fake_pm.scripts[("ad-ready", "episode_1.json")] = {
                "content_mode": "ad",
                "shots": [{"shot_id": "a"}, {"shot_id": "a"}],
            }
            dup_id = client.patch(
                "/api/v1/projects/ad-ready/script-shots/a",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert dup_id.status_code == 422

    @pytest.mark.parametrize(
        ("method", "project_name", "endpoint", "body"),
        [
            (
                "patch",
                "ready",
                "/api/v1/projects/ready/script-scenes/001",
                {"script_file": "episode_1.json", "updates": {}},
            ),
            (
                "patch",
                "ad-ready",
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                {"script_file": "episode_1.json", "updates": {}},
            ),
            (
                "post",
                "ad-ready",
                "/api/v1/projects/ad-ready/script-shots/reorder",
                {"script_file": "episode_1.json", "shot_ids": ["E1S01"]},
            ),
            (
                "patch",
                "ready",
                "/api/v1/projects/ready/segments/E1S01",
                {"script_file": "narration.json"},
            ),
            (
                "patch",
                "ready",
                "/api/v1/projects/ready/episodes/1",
                {"title": "新集名"},
            ),
        ],
    )
    def test_script_edit_routes_refuse_on_a_migration_blocked_project(
        self, tmp_path, monkeypatch, method: str, project_name: str, endpoint: str, body: dict
    ):
        """写剧本的路由一律在入口层被拒：五条手动编辑路由与同文件其它写入路由同守卫。

        阻断由入口声明，不指望内层兜底：其中四条经 ScriptBatchEditor 另有一道内部裁决，
        改分集标题那条走 locked_episode_script，全程不读裁决，入口守卫是它唯一的一道。

        409 的 detail 要同时带项目名与迁移失败原因——阻断回执得让人知道该修哪个项目的什么。
        """

        import lib.project_migration_guard as guard
        from lib.project_migration_failure import record_migration_failure

        fake_pm = _FakePM(tmp_path)
        record_migration_failure(fake_pm.base / project_name, ValueError("坏数据"), schema_version=7)
        monkeypatch.setattr(guard, "get_project_manager", lambda: fake_pm)
        client = _client(monkeypatch, fake_pm)
        before_project_data = deepcopy(fake_pm.project_data)
        before_scripts = deepcopy(fake_pm.scripts)

        with client:
            response = getattr(client, method)(endpoint, json=body)

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert project_name in detail
        assert "坏数据" in detail
        # 阻断必须发生在惰性迁移读写之前：project.json 与 scripts/*.json 都不能被动过
        assert fake_pm.project_data == before_project_data
        assert fake_pm.scripts == before_scripts
