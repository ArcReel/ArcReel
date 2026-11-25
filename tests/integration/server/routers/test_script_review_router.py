"""script_plan→prompt_authoring 内容确认路由测试：审阅读取、内容编辑、确认动作的可测状态流转。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.resolver import ConfigResolver
from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.i18n import _ as i18n_message
from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from lib.script import script_review
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import script_review as router_mod
from server.services.project.script_review import ScriptReviewService
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import make_video_request_facts
from tests.fakes import FakeConfigResolver


def _drama_script_plan() -> dict:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["阿离"],
                "scenes": [],
                "props": [],
                "scene_description": "雨夜，阿离立于屋檐下",
                "utterances": [
                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                    {"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"},
                ],
                "source_text": "三年后，阿离立于屋檐下：你终于回来了。",
            }
        ],
    }


def _rv_script_plan() -> dict:
    return {
        "units": [
            {
                "unit_id": "E1U01",
                "text": "@[阿离] 立于屋檐下。",
                "duration_seconds": 4,
            }
        ],
    }


def _client(
    monkeypatch,
    tmp_path: Path,
    *,
    generation_mode: str | None = None,
    content_mode: str = "drama",
) -> tuple[TestClient, ProjectManager]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    pm.add_character("demo", "阿离", "少女")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    if generation_mode is not None:
        pm.update_project("demo", lambda p: p.__setitem__("generation_mode", generation_mode))

    monkeypatch.setattr(router_mod, "get_project_manager", lambda: pm)
    resolver = cast(ConfigResolver, FakeConfigResolver())
    monkeypatch.setattr(
        router_mod,
        "ScriptReviewService",
        lambda project_manager: ScriptReviewService(project_manager, config_resolver=resolver),
    )

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(router_mod.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm


def _register_script_plan(pm: ProjectManager) -> None:
    """确认转换读的是已登记的正式脚本规划：补上源文并激活产物清单。"""
    from lib.artifacts.artifact_activation import activate_artifact_target_state

    project_path = pm.get_project_path("demo")
    source = project_path / "source" / "episode_1.txt"
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("三年后，阿离立于屋檐下：你终于回来了。", encoding="utf-8")
    activate_artifact_target_state(project_path, bump_schema=False)


def _write_script_plan(pm: ProjectManager, content: dict) -> None:
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / "script_plan_normalized_script.json", content)
    _register_script_plan(pm)


def _write_rv_script_plan(pm: ProjectManager, content: dict) -> None:
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    atomic_write_json(drafts / "script_plan_reference_units.json", content)
    _register_script_plan(pm)


def _admitted_drama_script_plan() -> dict:
    """确认即转换，转换过发声准入：分镜只留台词，不与画外音混排。"""
    plan = _drama_script_plan()
    plan["scenes"][0]["utterances"] = [{"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"}]
    return plan


@pytest.mark.usefixtures("video_request_facts")
class TestScriptReviewRouter:
    def test_full_gate_flow(self, tmp_path, monkeypatch, video_request_facts):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"

            got = client.get(base)
            assert got.status_code == 200
            assert got.json()["status"] == "no_script_plan"

            _write_script_plan(pm, _admitted_drama_script_plan())
            got = client.get(base)
            body = got.json()
            assert body["status"] == "pending_review"
            assert body["content"]["scenes"][0]["utterances"][0]["speaker"] == "阿离"

            from lib.script import script_review

            assert (
                script_review.review_status(pm.get_project_path("demo"), pm.load_project("demo"), 1) == "pending_review"
            )

            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed"
            assert "script_entry_currency" not in client.get(base).json()
            assert client.get(f"{base}/conversion-preview").status_code == 404
            assert client.post(f"{base}/convert").status_code == 404
            assert script_review.review_status(pm.get_project_path("demo"), pm.load_project("demo"), 1) == "confirmed"

    def test_saving_confirmed_script_plan_is_rejected_with_recognizable_code(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_script_plan(pm, _admitted_drama_script_plan())
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 200, confirmed.text
            plan_path = pm.get_project_path("demo") / "drafts" / "episode_1" / "script_plan_normalized_script.json"
            before = plan_path.read_bytes()

            edited = _admitted_drama_script_plan()
            edited["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
            refused = client.put(
                f"{base}/content", params={"base_fingerprint": confirmed.json()["fingerprint"]}, json=edited
            )

            assert refused.status_code == 409
            assert refused.json()["detail"] == i18n_message("script_review_script_plan_confirmed")
            assert refused.json()["diagnostic"] == {"code": "script_plan_confirmed"}
            assert plan_path.read_bytes() == before
            assert client.get(base).json()["status"] == "confirmed"

    def test_confirm_over_existing_script_requires_acknowledgement(self, tmp_path, monkeypatch, video_request_facts):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_script_plan(pm, _admitted_drama_script_plan())
            assert client.post(f"{base}/confirm").status_code == 200
            script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
            produced = json.loads(script_path.read_text(encoding="utf-8"))
            produced["scenes"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"
            produced["scenes"][0].setdefault("generated_assets", {}).update(
                {
                    "storyboard_image": "storyboards/scene_E1S01.png",
                    "video_clip": "videos/scene_E1S01.mp4",
                    "narration_audio": "audio/segment_E1S01.wav",
                    "grid_id": "grid_ab12",
                    "grid_cell_index": 0,
                }
            )
            script_path.write_text(json.dumps(produced, ensure_ascii=False), encoding="utf-8")
            before = script_path.read_bytes()
            expected_overwrite = {
                "revision": script_review.published_revision(
                    script_review.content_fingerprint_of_data(json.loads(before))
                ),
                "entries": [
                    {
                        "id": "E1S01",
                        "has_storyboard": True,
                        "has_video": True,
                        "has_narration_audio": True,
                        "has_end_frame": True,
                        "grid_id": "grid_ab12",
                    }
                ],
                "storyboard_count": 1,
                "video_count": 1,
                "narration_audio_count": 1,
                "end_frame_count": 1,
                "grid_member_count": 1,
                "grid_count": 1,
            }
            expected_overwrite["text"] = script_review.render_overwrite_loss_text(expected_overwrite, i18n_message)
            for lost in ("分镜图 1 张", "视频 1 段", "配音 1 段", "尾帧 1 张", "宫格归属 1 处"):
                assert lost in expected_overwrite["text"]

            rerun = _admitted_drama_script_plan()
            rerun["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
            _write_script_plan(pm, rerun)
            state = client.get(base).json()
            assert state["status"] == "pending_review"
            assert state["script_overwrite"] == expected_overwrite

            refused = client.post(f"{base}/confirm")
            assert refused.status_code == 409
            assert refused.json()["detail"] == i18n_message("script_review_overwrite_required")
            displayed_overwrite = refused.json()["diagnostic"]["script_overwrite"]
            displayed_text = displayed_overwrite.pop("text")
            assert displayed_overwrite == {key: value for key, value in expected_overwrite.items() if key != "text"}
            assert "E1S01" not in displayed_text
            assert "S01" in displayed_text
            for lost in ("分镜图 1 张", "视频 1 段", "配音 1 段", "尾帧 1 张", "宫格归属 1 处"):
                assert lost in displayed_text
            assert script_path.read_bytes() == before
            assert client.get(base).json()["status"] == "pending_review"

            assert client.post(f"{base}/confirm", json={"unexpected": True}).status_code == 422
            confirmed = client.post(f"{base}/confirm", json={"overwrite_revision": expected_overwrite["revision"]})
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "confirmed"
            assert pm.load_script("demo", "episode_1.json")["scenes"][0]["pending_authoring"] is True

    def test_confirm_without_resolvable_video_model_names_the_missing_configuration(
        self, tmp_path, monkeypatch, set_video_request_facts
    ):
        """确认转换要确定分镜时长档位：视频请求事实解析不出时按事实的问题码回 422，与预检、执行同码。"""
        set_video_request_facts(VideoRequestFactsFailure("video_capability_unavailable", (("capability", "i2v"),)))
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_script_plan(pm, _admitted_drama_script_plan())

            refused = client.post(f"{base}/confirm")

            assert refused.status_code == 422
            assert refused.json()["detail"] == i18n_message("video_capability_unavailable", capability="i2v")
            assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()
            assert client.get(base).json()["status"] == "pending_review"

    def test_edit_content_repends(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_script_plan(pm, _drama_script_plan())
            client.post(f"{base}/confirm")

            edited = _drama_script_plan()
            edited["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
            put = client.put(f"{base}/content", json=edited)
            assert put.status_code == 200
            assert put.json()["status"] == "pending_review"

            got = client.get(base)
            assert got.json()["content"]["scenes"][0]["scene_description"] == "雨势渐急，阿离仍站在屋檐下"

    def test_editing_legacy_mixed_speech_returns_structured_atomic_rejection(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            original = _drama_script_plan()
            _write_script_plan(pm, original)
            edited = _drama_script_plan()
            edited["scenes"][0]["utterances"][1]["text"] = "你怎么才回来。"

            put = client.put(f"{base}/content", json=edited)

            assert put.status_code == 409
            detail = put.json()["detail"]
            assert detail["unit_id"] == "E1S01"
            assert detail["problems"][0]["code"] == "mixed_speech"
            assert client.get(base).json()["content"] == original

    def test_put_empty_dialogue_speaker_returns_structured_409(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_script_plan(pm, _drama_script_plan())
            bad = _drama_script_plan()
            bad["scenes"][0]["utterances"][1] = {"kind": "dialogue", "speaker": None, "text": "无人"}
            put = client.put(f"{base}/content", json=bad)
            assert put.status_code == 409
            detail = put.json()["detail"]
            assert detail["unit_id"] == "E1S01"
            assert detail["problems"][0]["code"] == "empty_speaker"
            assert detail["problems"][0]["locations"] == [{"path": ["utterances", 1, "speaker"], "line": None}]

    def test_put_invalid_non_speech_content_422(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            _write_script_plan(pm, _drama_script_plan())
            bad = _drama_script_plan()
            bad["scenes"][0]["duration_seconds"] = "invalid"
            put = client.put(f"{base}/content", json=bad)
            assert put.status_code == 422

    def test_confirm_without_script_plan_409(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 409

    def test_get_unregistered_episode_404(self, tmp_path, monkeypatch):
        """未在 project.json 登记的分集 → GET 返回 404，而非误报 no_script_plan 的 200。"""
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            got = client.get("/api/v1/projects/demo/episodes/99/script-review")
            assert got.status_code == 404


class TestReferenceVideoRouter:
    def test_full_gate_flow(self, tmp_path, monkeypatch, video_request_facts):
        """rv 走同一 HTTP gate：结构化 units 可读、可编辑、web 确认放行 prompt_authoring（与 web 确认等价）。"""
        from lib.script import script_review

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"

            no_script_plan_body = client.get(base).json()
            assert no_script_plan_body["status"] == "no_script_plan"
            assert "quarantine" not in no_script_plan_body

            _write_rv_script_plan(pm, _rv_script_plan())
            body = client.get(base).json()
            assert body["status"] == "pending_review"
            assert body["content"]["units"][0]["unit_id"] == "E1U01"
            assert "quarantine" not in body
            assert (
                script_review.review_status(pm.get_project_path("demo"), pm.load_project("demo"), 1) == "pending_review"
            )

            edited = _rv_script_plan()
            edited["units"][0]["text"] = "@[阿离] 转身离去。"
            put = client.put(f"{base}/content", json=edited)
            assert put.status_code == 200
            assert put.json()["status"] == "pending_review"

            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed"
            assert script_review.review_status(pm.get_project_path("demo"), pm.load_project("demo"), 1) == "confirmed"

    @pytest.mark.parametrize("supported_durations", [(4, 6, 8), (4, 8)])
    def test_confirm_with_off_tier_unit_names_its_position_and_tiers(
        self, tmp_path, monkeypatch, set_video_request_facts, supported_durations
    ):
        """已确认时长被收窄出局：422 专用文案按单元位置给出现时长与可选档位，不回落通用的转换拒绝。"""
        set_video_request_facts(
            make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=supported_durations,
                allowed_durations=(4, 8),
            )
        )
        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            plan = _rv_script_plan()
            plan["units"].append({"unit_id": "E1U02", "text": "@[阿离] 转身。", "duration_seconds": 6})
            _write_rv_script_plan(pm, plan)

            refused = client.post(f"{base}/confirm")

            assert refused.status_code == 422
            assert refused.json()["detail"] == i18n_message(
                "script_review_duration_off_tier", position=2, duration_seconds=6, tiers="4 / 8"
            )
            assert "E1U02" not in refused.text
            assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()

    def test_web_save_and_confirm_tag_project_changes_as_webui(self, tmp_path, monkeypatch, video_request_facts):
        """内容确认页的保存与确认是用户在 Web 上的操作：写入产生的项目变更须标为 webui。

        未标注时变更按默认的 filesystem 来源广播，前端把它当作外部改动，自动跳到确认时登记的资产窗格。
        """
        from lib.project.project_change_hints import (
            register_project_change_batch_listener,
            register_project_change_listener,
        )

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        sources: list[str] = []
        unregister_hint = register_project_change_listener(lambda _name, source, _paths: sources.append(source))
        unregister_batch = register_project_change_batch_listener(
            lambda _name, source, _changes: sources.append(source)
        )
        try:
            with client:
                base = "/api/v1/projects/demo/episodes/1/script-review"
                _write_rv_script_plan(pm, _rv_script_plan())
                sources.clear()

                edited = _rv_script_plan()
                edited["units"][0]["text"] = "@[阿离] 转身离去。"
                assert client.put(f"{base}/content", json=edited).status_code == 200
                assert client.post(f"{base}/confirm").status_code == 200
        finally:
            unregister_hint()
            unregister_batch()

        assert sources
        assert set(sources) == {"webui"}

    def test_supported_durations_exposed_for_reference_video_only(self, tmp_path, monkeypatch, video_request_facts):
        """rv 变体的 GET 带出档位表供 web 渲染时长选择；drama 变体下为 None。"""
        rv_client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with rv_client:
            _write_rv_script_plan(pm, _rv_script_plan())
            body = rv_client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            durations = body["supported_durations"]
            assert durations is None or (isinstance(durations, list) and all(isinstance(d, int) for d in durations))
            assert "duration_tiers" in body

        drama_client, drama_pm = _client(monkeypatch, tmp_path / "drama")
        with drama_client:
            _write_script_plan(drama_pm, _drama_script_plan())
            body = drama_client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            assert body["supported_durations"] is None
            assert body["duration_tiers"] is None

    def test_duration_tiers_survive_save_and_confirm_responses(self, tmp_path, monkeypatch, video_request_facts):
        """PUT / confirm 的响应同样带 ``duration_tiers``——它们各自独立调用 ``get_state``，
        不经过 GET 那次合并；不带的话前端 ``adopt()`` 用保存后的响应覆盖 GET 读到的收窄结果，
        退回未收窄的 ``supported_durations``，与刚加载时的呈现不一致。"""
        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with client:
            _write_rv_script_plan(pm, _rv_script_plan())
            base = "/api/v1/projects/demo/episodes/1/script-review"
            get_body = client.get(base).json()
            assert "duration_tiers" in get_body

            put_body = client.put(f"{base}/content", json=_rv_script_plan()).json()
            assert "duration_tiers" in put_body

            confirm_body = client.post(f"{base}/confirm").json()
            assert "duration_tiers" in confirm_body

    def test_duration_tiers_and_supported_durations_resolved_for_custom_provider(
        self, tmp_path, monkeypatch, set_video_request_facts
    ):
        """自定义供应商（``custom-`` 前缀）不在 ``PROVIDER_REGISTRY``：视频请求事实是它唯一的档位来源。

        ``supported_durations``（声明全集，供存量草稿的读时收编 clamp）与 ``duration_tiers``
        （收窄后的逐 unit 可选项）都按事实给出真实档位，读时迁移的收编与面板可选项同源。
        """
        set_video_request_facts(
            make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                provider_id="custom-acme",
                model_id="acme-video",
                supported_durations=(5, 10),
                allowed_durations=(5, 10),
            )
        )
        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")

        with client:
            _write_rv_script_plan(pm, _rv_script_plan())
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            assert body["supported_durations"] == [5, 10]
            assert body["duration_tiers"] == {
                "with_references": [5, 10],
                "units": {"E1U01": body["duration_tiers"]["units"]["E1U01"]},
            }
            assert body["duration_tiers"]["units"]["E1U01"]["allowed_durations"] == [5, 10]

    def test_put_with_stale_base_fingerprint_conflicts_409(self, tmp_path, monkeypatch, video_request_facts):
        """PUT 携带的 ``base_fingerprint`` 与盘上现值不一致（编辑期间另一方已保存）→ 409、
        不落盘；拿最新指纹重试放行。缺省不带指纹的调用维持原语义（不比对）。"""
        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        _write_rv_script_plan(pm, _rv_script_plan())

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            stale = client.get(base).json()["fingerprint"]

            other = _rv_script_plan()
            other["units"][0]["text"] = "@[阿离] 转身离开。"
            assert client.put(f"{base}/content", json=other).status_code == 200

            mine = _rv_script_plan()
            resp = client.put(f"{base}/content", params={"base_fingerprint": stale}, json=mine)
            assert resp.status_code == 409
            assert client.get(base).json()["content"]["units"][0]["text"] == "@[阿离] 转身离开。"

            fresh = client.get(base).json()["fingerprint"]
            resp = client.put(f"{base}/content", params={"base_fingerprint": fresh}, json=mine)
            assert resp.status_code == 200
            assert resp.json()["content"]["units"][0]["text"] == "@[阿离] 立于屋檐下。"

    def test_retired_draft_routes_not_mounted(self):
        from server.app import app

        assert all("drafts" not in path.split("/") for path in app.openapi()["paths"])


async def test_blocked_first_generation_and_regeneration_transaction(tmp_path, monkeypatch, video_request_facts):
    from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from server.text_generation import TextGenerationError, TextGenerationRequest, generate_reference_script_plan
    from tests.fakes import use_text_generator
    from tests.integration.server.agent_tool_support import (
        ToolHarness,
        run_declared_tool,
        rv_generator_returning,
        rv_unit,
    )

    client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
    project_path = pm.get_project_path("demo")
    pm.update_project("demo", lambda project: project["episodes"][0].update(source_origin="own"))
    source = project_path / "source/episode_1.txt"
    source.parent.mkdir(exist_ok=True)
    source.write_text("山雨初歇。", encoding="utf-8")
    url = "/api/v1/projects/demo/episodes/1/script-review"
    ctx = ToolHarness("demo", pm.data_root, pm, config_resolver=cast(ConfigResolver, FakeConfigResolver()))
    path = script_review.official_reference_script_plan_path(project_path, 1)
    manifest = ProjectArtifactManifestAdapter(project_path)
    key = ArtifactKey.episode_script_plan(1)

    async def generate(text):
        use_text_generator(monkeypatch, rv_generator_returning([rv_unit(text, source_text="山雨初歇。")]))
        return await generate_reference_script_plan(
            TextGenerationRequest(episode=1),
            project_name="demo",
            projects=pm,
            config_resolver=ctx.services.capabilities,
        )

    def snapshot():
        return path.read_bytes(), manifest.get_entry(key), script_review.stored_review(pm.load_project("demo"), 1)

    def fail_registration(*args, **kwargs):
        raise OSError("injected registration failure")

    bad = "@[未登记] 出场"
    good = "天光微亮"
    before_first = (project_path / "project.json").read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr("lib.artifacts.artifact_activation.register_current_artifact", fail_registration)
        with pytest.raises(TextGenerationError, match="injected registration failure"):
            await generate(bad)
    assert not path.exists()
    assert manifest.get_entry(key) is None
    assert (project_path / "project.json").read_bytes() == before_first

    first = await generate(bad)
    assert first.blocking_issues[0]["code"] == "unregistered_asset"
    assert manifest.get_entry(key) is not None
    assert not list(path.parent.glob("*.invalid.json"))
    state = client.get(url).json()
    agent = await run_declared_tool("get_script_plan_content", ctx, {"episode_id": 1})
    assert agent.problem is None
    assert agent.value.revision == state["revision"]
    assert [i["code"] for i in state["blocking_issues"]] == [i["code"] for i in agent.value.blocking_issues]
    assert client.post(f"{url}/confirm").status_code == 422
    pending = snapshot()
    pending_high_water = pm.load_project("demo")["episodes"][0].get("item_id_high_water")
    with pytest.raises(TextGenerationError, match="原规划"):
        await generate(bad)
    assert snapshot() == pending
    assert pm.load_project("demo")["episodes"][0].get("item_id_high_water") == pending_high_water

    await generate(good)
    assert client.post(f"{url}/confirm").json()["status"] == "confirmed"
    confirmed = snapshot()
    confirmed_high_water = pm.load_project("demo")["episodes"][0].get("item_id_high_water")
    with pytest.raises(TextGenerationError, match="原规划"):
        await generate(bad)
    assert snapshot() == confirmed
    assert pm.load_project("demo")["episodes"][0].get("item_id_high_water") == confirmed_high_water
    pm.update_project(
        "demo", lambda project: script_review.mark_ledger_stale(project_path, project, project["episodes"][0], 1)
    )
    with monkeypatch.context() as fault:
        fault.setattr("lib.artifacts.artifact_activation.register_current_artifact", fail_registration)
        with pytest.raises(TextGenerationError, match="injected registration failure"):
            await generate("雨停了")
    assert snapshot() == confirmed
    assert script_review.STALE_SCRIPT_PLAN_REBUILT_REVISION_FIELD not in pm.load_project("demo")["episodes"][0]
    assert pm.load_project("demo")["episodes"][0].get("item_id_high_water") == confirmed_high_water
    await generate(good)
    assert path.read_bytes() == confirmed[0]
    assert client.get(url).json()["status"] == "confirmed"
    assert (
        pm.load_project("demo")["episodes"][0][script_review.STALE_SCRIPT_PLAN_REBUILT_REVISION_FIELD]
        == confirmed[2]["fingerprint"]
    )


def test_plan_capability_inputs_tracks_character_voice_binding() -> None:
    from server.text_generation import plan_capability_inputs

    base = {"generation_mode": "reference_video", "character_voice_binding": "prompt"}
    changed = {**base, "character_voice_binding": "reference_audio"}
    assert plan_capability_inputs(base) != plan_capability_inputs(changed)
