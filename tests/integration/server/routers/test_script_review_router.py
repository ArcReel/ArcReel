"""Content-confirmation routes script_plan→prompt_authoring: review reads/edits/confirmation state transitions."""

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
    # Panel/conversion tier assertions read test facts via ``set_video_request_facts`` /
    # ``video_request_facts``; construction-only placeholder resolvers never touch configuration DBs.
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
    """Conversions read registered formal planning; add source/activate manifests."""
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
    """Confirmation converts through speech admission: dialogue-only shots, never interleaved voiceover."""
    plan = _drama_script_plan()
    plan["scenes"][0]["utterances"] = [{"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"}]
    return plan


class TestScriptReviewRouter:
    def test_full_gate_flow(self, tmp_path, monkeypatch, video_request_facts):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"

            # script_plan not produced
            got = client.get(base)
            assert got.status_code == 200
            assert got.json()["status"] == "no_script_plan"

            # Produced script_plan → pending_review with visible structured contents
            _write_script_plan(pm, _admitted_drama_script_plan())
            got = client.get(base)
            body = got.json()
            assert body["status"] == "pending_review"
            assert body["content"]["scenes"][0]["utterances"][0]["speaker"] == "阿离"

            # Await content review before confirmation.
            from lib.script import script_review

            assert (
                script_review.review_status(pm.get_project_path("demo"), pm.load_project("demo"), 1) == "pending_review"
            )

            # Confirmation → confirmed, admitting prompt_authoring
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed"
            # Confirmation directly materializes scripts; status describes review only, without separate rehearsal/conversion entries.
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
            # Loss lists include dubbing/ending frames/grid memberships; Web diagnostics identify entries by titles.
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
        """Unresolved conversion video facts return 422 with actual problem codes, matching preflight/execution."""
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
        """Unregistered episodes return GET 404, never false no_script_plan 200."""
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            got = client.get("/api/v1/projects/demo/episodes/99/script-review")
            assert got.status_code == 404


class TestReferenceVideoRouter:
    def test_full_gate_flow(self, tmp_path, monkeypatch, video_request_facts):
        """rv shares HTTP gates: readable/editable structured units, Web confirmation admits prompt_authoring equivalently."""
        from lib.script import script_review

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"

            no_script_plan_body = client.get(base).json()
            assert no_script_plan_body["status"] == "no_script_plan"
            assert no_script_plan_body["quarantine"] is None

            _write_rv_script_plan(pm, _rv_script_plan())
            body = client.get(base).json()
            assert body["status"] == "pending_review"
            assert body["content"]["units"][0]["unit_id"] == "E1U01"
            assert body["quarantine"] is None
            assert (
                script_review.review_status(pm.get_project_path("demo"), pm.load_project("demo"), 1) == "pending_review"
            )

            # Edited unit bodies → await reconfirmation
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
        """Narrowed-out confirmed durations return dedicated positional 422 with current/options, never generic refusal."""
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
        """Web confirmation-page save/confirm operations must mark emitted project changes webui.

        Unmarked defaults broadcast filesystem, making frontend treat own edits as external and navigate to registered-asset panes.
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

    def test_quarantine_surfaced_with_recomputed_line_anchored_violations(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """Existing drafts add GET ``quarantine``; production validators reassess on read,
        never trust old draft snapshots, deliberately labeled stale here."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine
        from lib.script.reference_video.draft_validation import DraftViolation

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        novel = "阿离站在屋檐下。"
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text(novel, encoding="utf-8")

        flat_units = [{"duration_seconds": 4, "source_text": novel, "text": "镜头1：门开了\n@[阿离]：｛我来了。｝"}]
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": flat_units},
            violations=[DraftViolation("stale", code="fullwidth_braces", label="unit E1U01", line=1)],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base).json()
            assert body["status"] == "pending_review"
            assert body["quarantine"] is not None
            violations = body["quarantine"]["violations"]
            assert len(violations) == 1
            assert violations[0]["code"] == "fullwidth_braces"
            assert violations[0]["line"] == 1
            assert violations[0]["message"] != "stale"

            # Existing drafts reject confirmation; formal planning has no admissible contents yet.
            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 409

    def test_quarantine_schema_invalid_keeps_raw_content(self, tmp_path, monkeypatch, video_request_facts):
        """Nonarray draft units report schema_invalid, returning ``content`` unchanged without coercion,
        enabling raw-text fallback rather than unit-list traversal."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine
        from lib.script.reference_video.draft_validation import DraftViolation

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text("阿离站在屋檐下。", encoding="utf-8")
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": "被改坏了"},
            violations=[DraftViolation("stale", code="schema_invalid")],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            quarantine = body["quarantine"]
            assert quarantine["content"] == {"units": "被改坏了"}
            assert [v["code"] for v in quarantine["violations"]] == ["schema_invalid"]
            assert quarantine["violations"][0]["message"] != "stale"

    def test_quarantine_meta_broken_reports_recompute_failure_not_snapshot(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """Missing ``meta.source`` prevents reassessment; report inability itself, never reuse old
        draft snapshots, keeping reports accountable to current values."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine
        from lib.script.reference_video.draft_validation import DraftViolation

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        write_quarantine(
            pm.get_project_path("demo"),
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"duration_seconds": 4, "source_text": "原文", "text": "镜头1：门开了"}]},
            violations=[DraftViolation("stale", code="fullwidth_braces", label="unit E1U01", line=1)],
            meta={},
        )

        with client:
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            violations = body["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["quarantine_unreadable"]
            assert "stale" not in violations[0]["message"]
            assert violations[0]["label"] == ""

    def test_quarantine_surfaced_for_narration_variant(self, tmp_path, monkeypatch, video_request_facts):
        """Narration repair drafts also enter GET, reassessed through narration validators.

        Dispatch presentation by kind, never hardcode reference video; otherwise other drafts appear clean
        despite blocked confirmation, hiding reasons.
        """
        from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, write_quarantine
        from lib.script.draft_violation import DraftViolation

        client, pm = _client(monkeypatch, tmp_path, content_mode="narration")
        project_path = pm.get_project_path("demo")
        novel = "阿离站在屋檐下。"
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text(novel, encoding="utf-8")

        segment = {
            "segment_id": "E1S01",
            "novel_text": novel,
            "duration_seconds": 5,
            "segment_break": False,
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
        }
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            content={"segments": [segment]},
            violations=[DraftViolation("stale", code="blank_novel_text", label="segment E1S01")],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base).json()
            assert body["quarantine"] is not None
            violations = body["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["duration_off_tier"], "读时重算，不回放上一轮快照"
            assert violations[0]["label"] == "segment E1S01"
            assert body["quarantine"]["content"]["segments"][0]["segment_id"] == "E1S01"

            assert client.post(f"{base}/confirm").status_code == 409

    def test_quarantine_surfaced_for_drama_variant(self, tmp_path, monkeypatch, video_request_facts):
        """Valid drama editing drafts also enter GET with empty violations/current coerced contents,
        explaining awaiting promotion rather than blank panels."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_DRAMA_SCRIPT_PLAN, write_quarantine

        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        novel = "阿离站在屋檐下。"
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text(novel, encoding="utf-8")

        scene = {
            "scene_id": "E1S01",
            "duration_seconds": 4,
            "segment_break": False,
            "characters_in_scene": ["阿离"],
            "scenes": [],
            "props": [],
            "scene_description": "阿离站在屋檐下。",
            "utterances": [{"kind": "dialogue", "speaker": "阿离", "text": "我来了。"}],
            "source_text": novel,
        }
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
            content={"title": "第一集", "scenes": [scene]},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base).json()
            assert body["quarantine"] is not None
            assert body["quarantine"]["violations"] == []
            assert body["quarantine"]["content"]["scenes"][0]["scene_id"] == "E1S01"

            assert client.post(f"{base}/confirm").status_code == 409

    def test_supported_durations_exposed_for_reference_video_only(self, tmp_path, monkeypatch, video_request_facts):
        """rv GET exposes Web duration tiers; drama returns None."""
        rv_client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        with rv_client:
            _write_rv_script_plan(pm, _rv_script_plan())
            body = rv_client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            durations = body["supported_durations"]
            assert durations is None or (isinstance(durations, list) and all(isinstance(d, int) for d in durations))
            # duration_tiers always exists, None when unnarrowed/unresolved models, distinguishing full
            # declarations from narrowed per-unit tiers without KeyError fallback.
            assert "duration_tiers" in body

        drama_client, drama_pm = _client(monkeypatch, tmp_path / "drama")
        with drama_client:
            _write_script_plan(drama_pm, _drama_script_plan())
            body = drama_client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            assert body["supported_durations"] is None
            assert body["duration_tiers"] is None

    def test_quarantine_corrupted_envelope_reported_not_treated_as_clean(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """Existing corrupt-JSON envelopes make ``read_quarantine`` return None by its own rules,
        but GET must distinguish drafts from absence; otherwise panels appear clean/admit confirmation
        while confirm() rejects existence with 409, producing unexplained repeated failure.
        """
        from lib.script import script_review as lib_script_review

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        _write_rv_script_plan(pm, _rv_script_plan())

        quarantine_path = lib_script_review.script_plan_quarantine_path(project_path, pm.load_project("demo"), 1)
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.write_text("{ 这不是合法 JSON", encoding="utf-8")

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base).json()
            quarantine = body["quarantine"]
            assert quarantine is not None
            assert quarantine["content"] is None
            assert [v["code"] for v in quarantine["violations"]] == ["quarantine_unreadable"]

            confirmed = client.post(f"{base}/confirm")
            assert confirmed.status_code == 409

    def test_quarantine_cleared_between_existence_check_and_read_is_not_reported_as_corrupted(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """Promotion removes drafts after existence checks/before ``read_quarantine`` reads,
        having persisted formal contents. Reads spanning cleanup mean no draft, not corruption,
        avoiding falsely pending newly promoted episodes."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine
        from server.services.project import script_review as mod

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        _write_rv_script_plan(pm, _rv_script_plan())
        quarantine_path = write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[],
        )

        real_read_quarantine = mod.read_quarantine

        def _read_after_concurrent_clear(*args, **kwargs):
            # Simulate passed existence checks before actual reads while concurrent promotion
            # removes files.
            quarantine_path.unlink()
            return real_read_quarantine(*args, **kwargs)

        monkeypatch.setattr(mod, "read_quarantine", _read_after_concurrent_clear)

        with client:
            body = client.get("/api/v1/projects/demo/episodes/1/script-review").json()
            assert body["quarantine"] is None

    def test_quarantine_unreadable_message_localized_by_accept_language(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """``quarantine_unreadable`` messages localize via ``_t``/``Accept-Language``.

        Other codes use production-rendered/interpolated Chinese templates, not localized; ``quarantine_unreadable``
        alone has two noninterpolated fixed strings for corrupt envelopes/missing damaged meta, limiting localization
        changes to these two cases."""
        from lib.script import script_review as lib_script_review

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        _write_rv_script_plan(pm, _rv_script_plan())

        quarantine_path = lib_script_review.script_plan_quarantine_path(project_path, pm.load_project("demo"), 1)
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.write_text("{ not valid json", encoding="utf-8")

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            body = client.get(base, headers={"Accept-Language": "en"}).json()
            message = body["quarantine"]["violations"][0]["message"]
            assert message == i18n_message("script_review_quarantine_unreadable", locale="en")

    def test_quarantine_recheck_reports_video_request_facts_failure_by_problem_code(
        self, tmp_path, monkeypatch, set_video_request_facts
    ):
        """Unresolved video facts during read reassessment report localized fact-code violations,
        never misclassified draft corruption."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine

        set_video_request_facts(VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),)))
        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text("阿离站在屋檐下。", encoding="utf-8")
        _write_rv_script_plan(pm, _rv_script_plan())
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            body = client.get(
                "/api/v1/projects/demo/episodes/1/script-review", headers={"Accept-Language": "en"}
            ).json()
            violations = body["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["reference_capability_unavailable"]
            assert violations[0]["message"] == i18n_message(
                "reference_capability_unavailable", locale="en", capability="r2v"
            )

    def test_duration_tiers_survive_save_and_confirm_responses(self, tmp_path, monkeypatch, video_request_facts):
        """PUT/confirm also return ``duration_tiers``; independent ``get_state`` calls
        bypass GET merging. Otherwise frontend ``adopt()`` overwrites loaded narrowing with save responses,
        reverting to unnarrowed ``supported_durations`` and inconsistent presentation."""
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
        """Custom providers (``custom-``) lack ``PROVIDER_REGISTRY``; facts alone supply tiers.

        ``supported_durations`` full declarations for legacy clamps and ``duration_tiers``
        narrowed per-unit options both use real facts, sharing migration/panel truth.
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

    def test_quarantine_with_non_string_meta_source_degrades_gracefully(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """Valid envelopes with nonstring ``meta.source``, such as numbers, degrade to unreassessable
        rather than letting ``safe_join`` ``TypeError`` escape as 500, which would show blank errors
        precisely when users most need panel recovery guidance."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[],
            meta={"source": 12345},
        )

        with client:
            resp = client.get("/api/v1/projects/demo/episodes/1/script-review")
            assert resp.status_code == 200
            violations = resp.json()["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["quarantine_unreadable"]

    def test_quarantine_with_directory_valued_meta_source_degrades_gracefully(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """String ``meta.source`` pointing to directories passes ``Path.exists()`` True;
        ``read_text()`` raises ``IsADirectoryError``, requiring quarantine_unreadable degradation,
        never escaped 500 from OSError subclasses outside ValueError/type-error handling."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[],
            meta={"source": "source"},
        )

        with client:
            resp = client.get("/api/v1/projects/demo/episodes/1/script-review")
            assert resp.status_code == 200
            violations = resp.json()["quarantine"]["violations"]
            assert [v["code"] for v in violations] == ["quarantine_unreadable"]

    def test_put_response_includes_quarantine_created_during_the_request(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """Saves affect formal drafts while quarantine is separate; PUT missing ``quarantine`` makes
        panel ``adopt()`` infer absence/admit confirmation despite drafts existing throughout
        saves. Preexisting-draft fixtures equal drafts produced during pending saves."""
        from lib.script.draft_quarantine import QUARANTINE_KIND_SCRIPT_PLAN, write_quarantine
        from lib.script.reference_video.draft_validation import DraftViolation

        client, pm = _client(monkeypatch, tmp_path, generation_mode="reference_video")
        project_path = pm.get_project_path("demo")
        (project_path / "source").mkdir(parents=True, exist_ok=True)
        (project_path / "source" / "episode_1.txt").write_text("阿离站在屋檐下。", encoding="utf-8")
        _write_rv_script_plan(pm, _rv_script_plan())
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"duration_seconds": 4, "source_text": "x", "text": "镜头1：门开了"}]},
            violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
            meta={"source": "source/episode_1.txt"},
        )

        with client:
            base = "/api/v1/projects/demo/episodes/1/script-review"
            put_body = client.put(f"{base}/content", json=_rv_script_plan()).json()
            assert put_body["quarantine"] is not None
            # Complete meta.source permits reassessment; assert actual current violations rather than
            # missing-meta quarantine_unreadable fallback entries.
            codes = [v["code"] for v in put_body["quarantine"]["violations"]]
            assert codes
            assert "quarantine_unreadable" not in codes

    def test_put_with_stale_base_fingerprint_conflicts_409(self, tmp_path, monkeypatch, video_request_facts):
        """Stale PUT ``base_fingerprint`` returns 409 without writes;
        newest fingerprints retry successfully. Omission preserves no-comparison behavior."""
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
            # Conflicts retain other writers' disk contents without overwrite.
            assert client.get(base).json()["content"]["units"][0]["text"] == "@[阿离] 转身离开。"

            fresh = client.get(base).json()["fingerprint"]
            resp = client.put(f"{base}/content", params={"base_fingerprint": fresh}, json=mine)
            assert resp.status_code == 200
            assert resp.json()["content"]["units"][0]["text"] == "@[阿离] 立于屋檐下。"
