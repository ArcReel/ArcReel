"""script_plan→prompt_authoring content-confirmation service and pure-logic tests.

External behavior only: confirmation transitions (script_plan produced → pending → blocked → confirm → confirmed → admitted),
applicability, reconfirmation after edits, structural validation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import VersionManager
from lib.config.resolver import ConfigResolver
from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.i18n import _ as i18n_message
from lib.infra.json_io import atomic_write_json
from lib.project import project_trash
from lib.project.project_activity import ACTIVITY_FILENAME, record_project_activity, recorded_project_activity
from lib.project.project_manager import ProjectManager, find_episode
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.project.resource_paths import resource_relative_path
from lib.script import script_review
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    clear_quarantine,
    quarantine_path,
    write_quarantine,
)
from lib.script.reference_video.draft_validation import DraftViolation
from server.agent_toolset.script_authoring import CONFIRM_SCRIPT_REVIEW, GENERATE_EPISODE_SCRIPT
from server.draft_workflow import DraftContext, DraftWorkflow
from server.services.project.script_review import ScriptReviewError, ScriptReviewService
from server.tool_runtime import TextGenerationResult
from tests.factories import make_video_request_facts, register_project_sources
from tests.fakes import FakeConfigResolver, use_artifact_registration
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


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
                "source_text": "三年后，阿离立于屋檐下，轻声道：你终于回来了。",
            }
        ],
    }


def _admitted_drama_script_plan() -> dict:
    """Confirmation converts through speech admission: shots contain dialogue only, never interleaved voiceover."""
    plan = _drama_script_plan()
    plan["scenes"][0]["utterances"] = [{"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"}]
    return plan


def _narration_script_plan() -> dict:
    return {
        "episode": 1,
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "裴与出征后的第二年，送回一个襁褓中的婴儿。",
                "duration_seconds": 6,
                "segment_break": False,
                "characters_in_segment": ["裴与"],
                "scenes": [],
                "props": [],
            }
        ],
    }


def _rv_script_plan() -> dict:
    """Structured reference_video script_plan intermediate state (``script_plan_reference_units.json`` shape).

    Unit bodies are sole content truth; derive references from ``@[名称]`` on reads, never persist.
    """
    return {
        "units": [
            {
                "unit_id": "E1U01",
                "text": "@[阿离] 立于屋檐下，望向雨幕。\n@[裴与] 策马自远方而来。",
                "duration_seconds": 8,
                "source_text": "阿离立在檐下看雨，裴与骑马远远而来。",
            }
        ],
    }


def _narrowed_rv_facts(supported_durations):
    """Effective 4/8-second tiers cover configuration narrowing and full declared-set narrowing after model changes."""
    return make_video_request_facts(
        route="reference_video",
        generation_type="i2v",
        supported_durations=supported_durations,
        allowed_durations=(4, 8),
    )


def _off_tier_rv_script_plan() -> dict:
    """Second-unit confirmed 6-second durations fall outside narrowed tiers."""
    plan = _rv_script_plan()
    plan["units"][0]["duration_seconds"] = 4
    plan["units"].append({"unit_id": "E1U02", "text": "@[裴与] 策马而来。", "duration_seconds": 6})
    return plan


@pytest.fixture(autouse=True)
def unresolvable_video_request_facts(set_video_request_facts) -> None:
    """By default this module leaves video-request model facts unresolved: no DB access or system-default tier drift into assertions.

    Tests requiring tiers use ``video_request_facts`` or override through ``set_video_request_facts``.
    """
    set_video_request_facts(VideoRequestFactsFailure("video_capability_unavailable", (("capability", "i2v"),)))


def _make_project(
    tmp_path: Path,
    content_mode: str,
    *,
    generation_mode: str | None = None,
) -> ProjectManager:
    """Create test projects; tiers derive from test-supplied video-request facts through ``set_video_request_facts``."""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    pm.add_character("demo", "阿离", "少女")
    pm.add_character("demo", "裴与", "将军")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    if generation_mode is not None:

        def _set_mode(p: dict) -> None:
            p["generation_mode"] = generation_mode

        pm.update_project("demo", _set_mode)
    return pm


def _service(pm: ProjectManager) -> ScriptReviewService:
    """Review-panel and conversion-tier assertions both read test-supplied video-request facts; injected resolvers are placeholders only."""
    return ScriptReviewService(pm, config_resolver=cast(ConfigResolver, FakeConfigResolver()))


def _register_script_plan(pm: ProjectManager) -> None:
    """Conversion reads registered formal planning: add sources/activate manifests to prove and register artifacts."""
    project_path = pm.get_project_path("demo")
    source = project_path / "source" / "episode_1.txt"
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("三年后，阿离立于屋檐下：你终于回来了。", encoding="utf-8")
    activate_artifact_target_state(project_path, bump_schema=False)


def _write_script_plan(pm: ProjectManager, content_mode: str, content: dict) -> Path:
    filename = "script_plan_normalized_script.json" if content_mode == "drama" else "script_plan_segments.json"
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / filename
    atomic_write_json(path, content)
    _register_script_plan(pm)
    return path


def _write_rv_script_plan(pm: ProjectManager, content: dict) -> Path:
    """Write structured reference_video script_plan (``script_plan_reference_units.json``)."""
    drafts = pm.get_project_path("demo") / "drafts" / "episode_1"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / "script_plan_reference_units.json"
    atomic_write_json(path, content)
    _register_script_plan(pm)
    return path


def test_content_fingerprint_of_data_matches_path_based_fingerprint(tmp_path: Path):
    """Identical-content fingerprints from parsed objects must match file-path fingerprints, sharing
    normalization so callers safely replace rereading paths after loading into memory.
    """
    path = tmp_path / "content.json"
    data = {"b": 2, "a": 1, "nested": {"z": [3, 2, 1]}}
    atomic_write_json(path, data)

    assert script_review.content_fingerprint_of_data(data) == script_review.content_fingerprint(path)


def _write_prompt_authoring(pm: ProjectManager) -> Path:
    """Write prompt_authoring artifacts (generated script JSON), simulating produced prompt_authoring."""
    scripts = pm.get_project_path("demo") / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / "episode_1.json"
    atomic_write_json(path, {"title": "第一集", "scenes": []})
    return path


def _make_manual_split_project(tmp_path: Path, content_mode: str) -> ProjectManager:
    """Projects without whole sources/with empty ledgers: callers register episodes individually or construct files directly."""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)
    return pm


def _write_source_text(pm: ProjectManager, filename: str, text: str) -> Path:
    source_dir = pm.get_project_path("demo") / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# State transitions, drama
# ---------------------------------------------------------------------------


def _set_episode_target_duration(pm: ProjectManager, seconds: int) -> None:
    def _set(project: dict) -> None:
        project["episode_target_duration"] = seconds

    pm.update_project("demo", _set)


class TestEpisodeTargetDuration:
    """Review-panel episode-total/target comparisons come from state, with both values read together."""

    async def test_state_carries_the_project_target(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        _set_episode_target_duration(pm, 90)
        _write_script_plan(pm, "drama", _drama_script_plan())

        state = await _service(pm).get_state("demo", 1)

        assert state["episode_target_duration"] == 90

    async def test_state_reports_no_target_when_unset(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _drama_script_plan())

        state = await _service(pm).get_state("demo", 1)

        assert state["episode_target_duration"] is None

    async def test_out_of_range_value_degrades_to_no_target(self, tmp_path):
        """Dirty manually edited project.json values must not make panels compare out-of-range targets."""
        pm = _make_project(tmp_path, "drama")
        _set_episode_target_duration(pm, 5)
        _write_script_plan(pm, "drama", _drama_script_plan())

        state = await _service(pm).get_state("demo", 1)

        assert state["episode_target_duration"] is None


def _narration_script(*segments: dict, metadata: dict | None = None) -> dict:
    script: dict = {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": list(segments)}
    if metadata is not None:
        script["metadata"] = metadata
    return script


def _narration_script_segment(segment_id: str, **overrides: object) -> dict:
    segment: dict = {
        "segment_id": segment_id,
        "duration_seconds": 6,
        "novel_text": "原文",
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "image_prompt": "画面",
        "video_prompt": "动作",
        "generated_assets": {},
    }
    segment.update(overrides)
    return segment


def _write_script(pm: ProjectManager, script: dict) -> None:
    atomic_write_json(pm.get_project_path("demo") / "scripts" / "episode_1.json", script)


class TestDramaGateFlow:
    async def test_no_script_plan_then_pending_then_confirmed(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)

        # script_plan not produced
        assert (await svc.get_state("demo", 1))["status"] == "no_script_plan"

        # Produced script_plan → reviewable intermediate state, blocked
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["scenes"][0]["scene_id"] == "E1S01"
        assert state["content"]["scenes"][0]["utterances"][0]["speaker"] == "阿离"
        project_path = pm.get_project_path("demo")
        project = pm.load_project("demo")
        assert script_review.review_status(project_path, project, 1) == "pending_review"

        # Confirmation → admitted
        confirmed = await svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmed_at"]
        project = pm.load_project("demo")
        assert script_review.review_status(project_path, project, 1) == "confirmed"

    async def test_quarantined_script_plan_blocks_confirm_and_prompt_authoring(self, tmp_path, video_request_facts):
        """Drama drafts independently block: existing drafts reject confirmation/block prompt_authoring even with confirmed formal script_plan,
        unchanged on checkout; fingerprints alone would admit previous content users have not reviewed."""
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        project_path = pm.get_project_path("demo")
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        await svc.confirm("demo", 1)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
            content={"title": "第一集", "scenes": []},
            violations=[],
        )

        assert (await svc.get_state("demo", 1))["status"] == "pending_review"
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "quarantined"

        clear_quarantine(project_path, 1, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

    async def test_saving_confirmed_script_plan_is_rejected_without_writing(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        path = _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        await svc.confirm("demo", 1)
        before = path.read_bytes()

        edited = _admitted_drama_script_plan()
        edited["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, edited)

        assert exc.value.code == "script_plan_confirmed"
        assert path.read_bytes() == before
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

    async def test_confirmed_script_plan_stays_read_only_while_a_rerun_draft_is_pending(
        self, tmp_path, video_request_facts
    ):
        """Rerun planning with repair drafts retains confirmed formal planning but still forbids saves."""
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        path = _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        await svc.confirm("demo", 1)
        write_quarantine(
            pm.get_project_path("demo"),
            1,
            QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
            content=_admitted_drama_script_plan(),
            violations=[DraftViolation("台词不在原文里")],
        )
        before = path.read_bytes()

        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, _admitted_drama_script_plan())

        assert exc.value.code == "script_plan_confirmed"
        assert path.read_bytes() == before

    async def test_rerun_script_plan_after_confirm_repends_and_reopens_editing(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        await svc.confirm("demo", 1)

        rerun = _admitted_drama_script_plan()
        rerun["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
        _write_script_plan(pm, "drama", rerun)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

        edited = _admitted_drama_script_plan()
        edited["scenes"][0]["scene_description"] = "雨停了"
        state = await svc.save_content("demo", 1, edited)
        assert state["status"] == "pending_review"
        assert state["content"]["scenes"][0]["scene_description"] == "雨停了"

    async def test_legacy_mixed_scene_allows_metadata_edit_but_rejects_speech_edit_atomically(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        path = _write_script_plan(pm, "drama", _drama_script_plan())

        metadata_edit = _drama_script_plan()
        metadata_edit["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
        await svc.save_content("demo", 1, metadata_edit)

        speech_edit = json.loads(json.dumps(metadata_edit, ensure_ascii=False))
        speech_edit["scenes"][0]["utterances"][1]["text"] = "别过来。"
        with pytest.raises(ScriptReviewError) as exc_info:
            await svc.save_content("demo", 1, speech_edit)

        assert exc_info.value.code == "speech_admission"
        assert exc_info.value.admission is not None
        assert exc_info.value.admission.problems[0].code == "mixed_speech"
        assert json.loads(path.read_text(encoding="utf-8")) == metadata_edit

    async def test_repairing_marked_drama_candidate_clears_replan_marker(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        candidate = _drama_script_plan()
        candidate["scenes"][0]["needs_replan"] = True
        path = _write_script_plan(pm, "drama", candidate)

        repaired = json.loads(json.dumps(candidate, ensure_ascii=False))
        repaired["scenes"][0]["utterances"] = [{"kind": "dialogue", "speaker": "阿离", "text": "你终于回来了。"}]
        await svc.save_content("demo", 1, repaired)

        saved = json.loads(path.read_text(encoding="utf-8"))
        assert "needs_replan" not in saved["scenes"][0]

    async def test_metadata_edit_cannot_clear_drama_replan_marker(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        candidate = _drama_script_plan()
        candidate["scenes"][0]["needs_replan"] = True
        path = _write_script_plan(pm, "drama", candidate)

        metadata_edit = json.loads(json.dumps(candidate, ensure_ascii=False))
        metadata_edit["scenes"][0].pop("needs_replan")
        metadata_edit["scenes"][0]["scene_description"] = "雨势渐急，阿离仍站在屋檐下"
        await svc.save_content("demo", 1, metadata_edit)

        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["scenes"][0]["needs_replan"] is True
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "speech_admission"
        assert exc.value.admission is not None
        assert exc.value.admission.problems[0].code == "needs_replan"

    async def test_whitespace_reformat_keeps_confirmed(self, tmp_path, video_request_facts):
        """Key-order/whitespace-only changes preserve semantics/fingerprints and confirmed state."""
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        path = _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        await svc.confirm("demo", 1)

        # Identical contents rewritten with different indentation/key order
        path.write_text(json.dumps(_admitted_drama_script_plan(), ensure_ascii=False, indent=4), encoding="utf-8")
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"


# ---------------------------------------------------------------------------
# Confirmation converts whole episodes: create absent formal scripts; existing scripts require overwrite consent.
# ---------------------------------------------------------------------------


async def _confirm_over_existing_script(pm: ProjectManager) -> dict:
    """Existing formal scripts require versioned overwrite-list consent before confirmation."""
    svc = _service(pm)
    revision = (await svc.get_state("demo", 1))["script_overwrite"]["revision"]
    return await svc.confirm("demo", 1, overwrite_revision=revision)


def _formal_script(pm: ProjectManager) -> dict:
    return json.loads((pm.get_project_path("demo") / "scripts" / "episode_1.json").read_text(encoding="utf-8"))


def _entry_claims(entry_id: str) -> dict[ArtifactKey, ArtifactManifestEntry]:
    digest = f"sha256-v1:{'a' * 64}"
    return {
        ArtifactKey.episode_storyboard(1, entry_id): ArtifactManifestEntry(
            artifact_path=f"storyboards/scene_{entry_id}.png", basis_digest=digest
        ),
        ArtifactKey.episode_video(1, entry_id): ArtifactManifestEntry(
            artifact_path=f"videos/scene_{entry_id}.mp4", basis_digest=digest
        ),
    }


class TestConfirmMaterializesScript:
    async def test_drama_confirm_writes_every_plan_entry_pending_authoring(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama")
        plan = _admitted_drama_script_plan()
        second = json.loads(json.dumps(plan["scenes"][0], ensure_ascii=False))
        second["scene_id"] = "E1S02"
        plan["scenes"].append(second)
        _write_script_plan(pm, "drama", plan)
        svc = _service(pm)

        assert (await svc.get_state("demo", 1))["script_overwrite"] is None
        state = await svc.confirm("demo", 1)

        assert state["status"] == "confirmed"
        script = _formal_script(pm)
        assert [scene["scene_id"] for scene in script["scenes"]] == ["E1S01", "E1S02"]
        for scene in script["scenes"]:
            assert scene["pending_authoring"] is True
            assert scene["image_prompt"] is None
            assert scene["video_prompt"] is None
        assert [scene["scene_description"] for scene in script["scenes"]] == [
            entry["scene_description"] for entry in plan["scenes"]
        ]
        assert script["scenes"][0]["utterances"] == plan["scenes"][0]["utterances"]

    async def test_narration_confirm_writes_every_plan_entry_pending_authoring(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())

        await _service(pm).confirm("demo", 1)

        [segment] = _formal_script(pm)["segments"]
        assert segment["segment_id"] == "E1S01"
        assert segment["novel_text"] == _narration_script_plan()["segments"][0]["novel_text"]
        assert segment["pending_authoring"] is True
        assert segment["image_prompt"] is None
        assert segment["video_prompt"] is None

    async def test_reference_confirm_takes_unit_text_and_duration_from_plan(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        _write_rv_script_plan(pm, _rv_script_plan())

        await _service(pm).confirm("demo", 1)

        [unit] = _formal_script(pm)["video_units"]
        planned = _rv_script_plan()["units"][0]
        assert unit["unit_id"] == "E1U1"
        assert unit["text"] == planned["text"]
        assert unit["duration_seconds"] == planned["duration_seconds"]
        assert unit["source_text"] == planned["source_text"]
        assert unit["pending_authoring"] is True

    async def test_existing_script_without_acknowledgement_is_refused_untouched(self, tmp_path):
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(
            pm,
            _narration_script(
                _narration_script_segment("E1S01", generated_assets={"storyboard_image": "storyboards/a.png"}),
                _narration_script_segment("E1S09", generated_assets={"video_clip": "videos/b.mp4"}),
            ),
        )
        before = (pm.get_project_path("demo") / "scripts" / "episode_1.json").read_bytes()
        svc = _service(pm)
        expected_overwrite = {
            "revision": script_review.published_revision(script_review.content_fingerprint_of_data(json.loads(before))),
            "entries": [
                {
                    "id": "E1S01",
                    "has_storyboard": True,
                    "has_video": False,
                    "has_narration_audio": False,
                    "has_end_frame": False,
                    "grid_id": None,
                },
                {
                    "id": "E1S09",
                    "has_storyboard": False,
                    "has_video": True,
                    "has_narration_audio": False,
                    "has_end_frame": False,
                    "grid_id": None,
                },
            ],
            "storyboard_count": 1,
            "video_count": 1,
            "narration_audio_count": 0,
            "end_frame_count": 0,
            "grid_member_count": 0,
            "grid_count": 0,
        }

        assert (await svc.get_state("demo", 1))["script_overwrite"] == expected_overwrite
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)

        assert exc.value.code == "overwrite_required"
        assert exc.value.overwrite == expected_overwrite
        assert (pm.get_project_path("demo") / "scripts" / "episode_1.json").read_bytes() == before
        assert script_review.stored_review(pm.load_project("demo"), 1) == {}

    @pytest.mark.parametrize("spelling", ["prefixed", "bare"])
    async def test_acknowledged_overwrite_replaces_script_and_forgets_old_entry_claims(
        self, tmp_path, video_request_facts, spelling: str
    ):
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(pm, _narration_script(_narration_script_segment("E1S01"), _narration_script_segment("E1S09")))
        adapter = ProjectArtifactManifestAdapter(pm.get_project_path("demo"))
        old_claims = {**_entry_claims("E1S01"), **_entry_claims("E1S09")}
        for key, entry in old_claims.items():
            adapter.put_entry(key, entry)

        svc = _service(pm)
        revision = (await svc.get_state("demo", 1))["script_overwrite"]["revision"]

        assert revision.startswith("sha256-v1:")
        if spelling == "bare":
            revision = revision.removeprefix("sha256-v1:")

        state = await svc.confirm("demo", 1, overwrite_revision=revision)

        assert state["status"] == "confirmed"
        [segment] = _formal_script(pm)["segments"]
        # Planned E1S01 already used by old scripts; conversion issues IDs above this episode's previously used maximum.
        assert segment["segment_id"] == "E1S10"
        assert segment["pending_authoring"] is True
        assert segment["image_prompt"] is None
        snapshot = adapter.snapshot_entries()
        assert not old_claims.keys() & snapshot.keys()
        assert ArtifactKey.episode_script(1) in snapshot

    async def test_acknowledged_overwrite_moves_old_entries_to_the_project_trash(self, tmp_path, video_request_facts):
        """Overwrite moves old media/version histories into project trash; new entries get fresh IDs without old histories; removed entries move too."""
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(pm, _narration_script(_narration_script_segment("E1S01"), _narration_script_segment("E1S09")))
        project_path = pm.get_project_path("demo")
        versions = VersionManager(project_path)
        for entry_id in ("E1S01", "E1S09"):
            for resource_type in ("storyboards", "end_frames", "videos", "audio"):
                current = project_path / resource_relative_path(resource_type, entry_id)
                current.parent.mkdir(parents=True, exist_ok=True)
                current.write_bytes(b"old-" + resource_type.encode())
                versions.add_version(resource_type, entry_id, "旧提示词", source_file=current)

        await _confirm_over_existing_script(pm)

        for entry_id in ("E1S01", "E1S09"):
            for resource_type in ("storyboards", "end_frames", "videos", "audio"):
                assert not versions.has_versions(resource_type, entry_id)
                assert not (project_path / resource_relative_path(resource_type, entry_id)).exists()
        records = {record.object["id"]: record for record in project_trash.list_records(project_path)}
        assert set(records) == {"E1S01", "E1S09"}
        assert "storyboards/scene_E1S09.png" in records["E1S09"].files

    async def test_acknowledged_overwrite_keeps_old_entries_without_media_recoverable(
        self, tmp_path, video_request_facts
    ):
        """Overwrite structurally removes all old shots; text-only entries without generated media also become deletion records."""
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(pm, _narration_script(_narration_script_segment("E1S01"), _narration_script_segment("E1S09")))
        project_path = pm.get_project_path("demo")

        await _confirm_over_existing_script(pm)

        records = {record.object["id"]: record for record in project_trash.list_records(project_path)}
        assert set(records) == {"E1S01", "E1S09"}
        assert all(record.has_snapshot for record in records.values())

    async def test_overwrite_is_refused_untouched_when_version_history_is_unreadable(
        self, tmp_path, video_request_facts
    ):
        """Moving old entries to project trash is transactional overwrite: unreadable version records block all writes, preserving scripts/media."""
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(pm, _narration_script(_narration_script_segment("E1S01")))
        project_path = pm.get_project_path("demo")
        current = project_path / resource_relative_path("storyboards", "E1S01")
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old")
        (project_path / "versions").mkdir(exist_ok=True)
        (project_path / "versions" / "versions.json").write_text("{broken", encoding="utf-8")
        before = (project_path / "scripts" / "episode_1.json").read_bytes()

        with pytest.raises(ScriptReviewError):
            await _confirm_over_existing_script(pm)

        assert (project_path / "scripts" / "episode_1.json").read_bytes() == before
        assert current.read_bytes() == b"old"
        assert project_trash.list_records(project_path) == []

    async def test_acknowledgement_of_an_outdated_script_is_refused_with_the_current_listing(self, tmp_path):
        """Consent covers listed formal-script revisions only; changed scripts reject old-version confirmations with new lists."""
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(pm, _narration_script(_narration_script_segment("E1S01")))
        svc = _service(pm)
        listed_revision = (await svc.get_state("demo", 1))["script_overwrite"]["revision"]
        _write_script(
            pm,
            _narration_script(
                _narration_script_segment("E1S01", generated_assets={"video_clip": "videos/scene_E1S01.mp4"})
            ),
        )
        before = (pm.get_project_path("demo") / "scripts" / "episode_1.json").read_bytes()

        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1, overwrite_revision=listed_revision)

        assert exc.value.code == "overwrite_required"
        assert exc.value.overwrite is not None
        assert exc.value.overwrite["revision"] != listed_revision
        assert exc.value.overwrite["video_count"] == 1
        assert (pm.get_project_path("demo") / "scripts" / "episode_1.json").read_bytes() == before
        assert script_review.stored_review(pm.load_project("demo"), 1) == {}

    async def test_binding_lost_with_another_episode_at_the_canonical_path_is_refused(
        self, tmp_path, video_request_facts
    ):
        """Missing bound files with other-episode scripts at canonical paths reject confirmation before writes, preserving other scripts/current bindings.

        Migration-skipped episodes have this shape; canonical fallback would reconstruct another living script and bind this episode to it.
        """
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        project_path = pm.get_project_path("demo")
        foreign = {**_narration_script(_narration_script_segment("E2S01")), "episode": 2}
        atomic_write_json(project_path / "scripts" / "episode_1.json", foreign)

        def _rebind(project: dict) -> None:
            find_episode(project, 1)["script_file"] = "scripts/custom.json"

        pm.update_project("demo", _rebind)
        svc = _service(pm)

        assert (await svc.get_state("demo", 1))["script_overwrite"] is None
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)

        assert exc.value.code == "foreign_formal_script"
        assert exc.value.script_filename == "episode_1.json"
        assert json.loads((project_path / "scripts" / "episode_1.json").read_text(encoding="utf-8")) == foreign
        assert find_episode(pm.load_project("demo"), 1)["script_file"] == "scripts/custom.json"
        assert script_review.stored_review(pm.load_project("demo"), 1) == {}

    async def test_unresolvable_video_model_is_refused_with_a_configuration_hint(self, tmp_path):
        """Confirmation conversion needs duration tiers; unresolved video-request facts reject with their problem codes/parameters."""
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        svc = _service(pm)

        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)

        assert exc.value.code == "video_request_facts"
        assert exc.value.problem is not None
        assert exc.value.problem.code == "video_capability_unavailable"
        assert exc.value.problem.parameters() == {"capability": "i2v"}
        assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

    async def test_refused_conversion_records_no_confirmation(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _drama_script_plan())

        with pytest.raises(ScriptReviewError) as exc:
            await _service(pm).confirm("demo", 1)

        assert exc.value.code == "speech_admission"
        assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()
        assert (await _service(pm).get_state("demo", 1))["status"] == "pending_review"

    async def test_script_plan_rewritten_during_materialization_is_a_conflict(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        import lib.script.script_generator as script_generator_module

        pm = _make_project(tmp_path, "narration")
        plan = _narration_script_plan()
        plan_path = _write_script_plan(pm, "narration", plan)
        rewritten = json.loads(json.dumps(plan, ensure_ascii=False))
        rewritten["segments"][0]["novel_text"] = "确认途中被改写的正文。"
        read_overwrite = script_generator_module.formal_script_overwrite

        def rewrite_plan_then_read_overwrite(project_path: Path, project: dict, episode: int):
            # Materialization loaded/verified planning but not persisted; another entry writes new planning.
            atomic_write_json(plan_path, rewritten)
            return read_overwrite(project_path, project, episode)

        monkeypatch.setattr(script_generator_module, "formal_script_overwrite", rewrite_plan_then_read_overwrite)

        with pytest.raises(ScriptReviewError) as exc:
            await _service(pm).confirm("demo", 1)

        assert exc.value.code == "conversion_conflict"
        assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()
        assert script_review.stored_review(pm.load_project("demo"), 1) == {}


# ---------------------------------------------------------------------------
# State transitions, narration, sharing the same gate
# ---------------------------------------------------------------------------


class TestNarrationGateFlow:
    async def test_pending_then_confirm(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "narration")
        svc = _service(pm)
        _write_script_plan(pm, "narration", _narration_script_plan())

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["segments"][0]["novel_text"].startswith("裴与")

        assert (await svc.confirm("demo", 1))["status"] == "confirmed"

    async def test_saving_confirmed_novel_text_is_rejected_until_rerun(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "narration")
        svc = _service(pm)
        path = _write_script_plan(pm, "narration", _narration_script_plan())
        await svc.confirm("demo", 1)
        before = path.read_bytes()

        edited = _narration_script_plan()
        edited["segments"][0]["novel_text"] = "裴与出征后的第三年。"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, edited)
        assert exc.value.code == "script_plan_confirmed"
        assert path.read_bytes() == before

        rerun = _narration_script_plan()
        rerun["segments"][0]["duration_seconds"] = 8
        _write_script_plan(pm, "narration", rerun)
        await svc.save_content("demo", 1, edited)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

    async def test_quarantined_script_plan_blocks_confirm_and_prompt_authoring(self, tmp_path, video_request_facts):
        """Narration repair drafts independently block like other routes: drafts reject confirmation/block prompt_authoring even with confirmed
        formal script_plan, unchanged on checkout; fingerprints alone would admit previous content users have not
        reviewed."""
        pm = _make_project(tmp_path, "narration")
        svc = _service(pm)
        project_path = pm.get_project_path("demo")
        _write_script_plan(pm, "narration", _narration_script_plan())
        await svc.confirm("demo", 1)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            content={"segments": []},
            violations=[],
        )

        assert (await svc.get_state("demo", 1))["status"] == "pending_review"
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "quarantined"

        clear_quarantine(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"


# ---------------------------------------------------------------------------
# State transitions, reference_video, sharing the gate across content_mode
# ---------------------------------------------------------------------------


class TestReferenceVideoGateFlow:
    async def test_no_script_plan_then_pending_then_confirmed(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        project_path = pm.get_project_path("demo")

        # script_plan not produced
        assert (await svc.get_state("demo", 1))["status"] == "no_script_plan"

        # Produced script_plan → reviewable intermediate state, blocking prompt_authoring
        _write_rv_script_plan(pm, _rv_script_plan())
        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["units"][0]["unit_id"] == "E1U01"
        assert state["content"]["units"][0]["text"].startswith("@[阿离]")
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"

        # Confirmation → admitted
        confirmed = await svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmed_at"]
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "confirmed"

    async def test_saving_confirmed_units_is_rejected_without_writing(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        path = _write_rv_script_plan(pm, _rv_script_plan())
        await svc.confirm("demo", 1)
        before = path.read_bytes()

        edited = _rv_script_plan()
        edited["units"][0]["text"] = "@[阿离] 收伞。"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, edited)

        assert exc.value.code == "script_plan_confirmed"
        assert path.read_bytes() == before
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

    async def test_editing_unit_text_after_rerun_keeps_review_pending(self, tmp_path, video_request_facts):
        """Rerun-edited unit bodies await confirmation; bodies alone persist, without copied references."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        pm.add_scenes_batch("demo", {"屋檐": {"description": "雨夜屋檐"}})
        svc = _service(pm)
        _write_rv_script_plan(pm, _rv_script_plan())
        await svc.confirm("demo", 1)
        rerun = _rv_script_plan()
        rerun["units"][0]["text"] = "@[阿离] 立于屋檐下。"
        _write_rv_script_plan(pm, rerun)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

        edited = _rv_script_plan()
        edited["units"][0]["text"] = "@[阿离] 立于屋檐下。\n镜头扫过 @[屋檐]。"
        await svc.save_content("demo", 1, edited)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        unit = state["content"]["units"][0]
        assert unit["text"] == edited["units"][0]["text"]
        assert "references" not in unit

    async def test_quarantined_script_plan_blocks_confirm_and_prompt_authoring(self, tmp_path, video_request_facts):
        """Existing drafts reject confirmation/block prompt_authoring even if formal script_plan was confirmed.

        Pending drafts are independent of formal content fingerprints: violating resplits leave formal files unchanged; fingerprints
        alone would mark episodes confirmed/admitted while users see previous content.
        """
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        project_path = pm.get_project_path("demo")
        _write_rv_script_plan(pm, _rv_script_plan())
        await svc.confirm("demo", 1)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
        )

        assert (await svc.get_state("demo", 1))["status"] == "pending_review"
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "quarantined"

        # Cleared drafts restore existing fingerprint decisions; unchanged content retains confirmation.
        clear_quarantine(project_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

    def test_quarantine_path_follows_the_project_variant(self, tmp_path):
        """Parse drafts by current project variant; each route recognizes its own filename.

        Shared filenames or variant-independent checks misclassify other modes' legacy drafts as current pending work;
        no current writer cleans them, permanently blocking episodes.
        """
        drama_pm = _make_project(tmp_path / "drama", "drama")
        rv_pm = _make_project(tmp_path / "rv", "drama", generation_mode="reference_video")
        narration_pm = _make_project(tmp_path / "nr", "narration")

        drama_path = script_review.script_plan_quarantine_path(
            drama_pm.get_project_path("demo"), drama_pm.load_project("demo"), 1
        )
        rv_path = script_review.script_plan_quarantine_path(
            rv_pm.get_project_path("demo"), rv_pm.load_project("demo"), 1
        )
        narration_path = script_review.script_plan_quarantine_path(
            narration_pm.get_project_path("demo"), narration_pm.load_project("demo"), 1
        )

        assert drama_path is not None
        assert drama_path.name == "script_plan_normalized_script.invalid.json"
        assert rv_path is not None
        assert rv_path.name == "script_plan_reference_units.invalid.json"
        assert narration_path is not None
        assert narration_path.name == "script_plan_segments.invalid.json"

    def test_quarantine_of_another_variant_does_not_block(self, tmp_path):
        """Other generation modes' legacy drafts never affect current-mode blocking."""
        pm = _make_project(tmp_path, "drama")
        project_path = pm.get_project_path("demo")
        write_quarantine(
            project_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
        )

        assert script_review.script_plan_quarantined(project_path, pm.load_project("demo"), 1) is False

    async def test_confirm_rejects_unit_duration_out_of_range(self, tmp_path, video_request_facts):
        """Corrupt script_plan with out-of-range unit durations fails confirmation structure checks, blocking prompt_authoring."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        bad = _rv_script_plan()
        bad["units"][0]["duration_seconds"] = 9999  # Outside unit-duration structural plausibility intervals
        _write_rv_script_plan(pm, bad)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "invalid_content"

    async def test_confirm_rejects_text_with_unregistered_mention(self, tmp_path, video_request_facts):
        """Visual-body assets neither registered nor episode additions reject confirmation, naming units/assets."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        candidate = _rv_script_plan()
        candidate["units"][0]["text"] = "@[酒馆] 的木门被风吹开。"
        _write_rv_script_plan(pm, candidate)

        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)

        assert exc.value.code == "unregistered_references"
        assert exc.value.message == "E1U01: 酒馆"

    async def test_confirm_rejects_speech_problem_in_an_unmarked_unit(self, tmp_path, video_request_facts):
        """Speech admission applies to all units, not only needs_replan: character dialogue plus unowned voiceover in one unit
        cannot combine both audio tracks on one video, so confirmation must reject."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        candidate = _rv_script_plan()
        candidate["units"][0]["text"] = "@[阿离] 立于屋檐下。@[阿离]{雨要停了。}\n{雨声渐歇。}"
        _write_rv_script_plan(pm, candidate)

        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)

        assert exc.value.code == "speech_admission"

    @pytest.mark.parametrize("supported_durations", [(4, 6, 8), (4, 8)])
    async def test_confirm_names_the_off_tier_unit_by_position(
        self, tmp_path, set_video_request_facts, supported_durations
    ):
        """Confirmed durations excluded by narrowed tiers report current duration/options at planning unit positions, without internal IDs."""
        set_video_request_facts(_narrowed_rv_facts(supported_durations))
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        _write_rv_script_plan(pm, _off_tier_rv_script_plan())

        with pytest.raises(ScriptReviewError) as exc:
            await _service(pm).confirm("demo", 1)

        assert exc.value.code == "duration_off_tier"
        assert exc.value.params == {"position": 2, "duration_seconds": 6, "tiers": "4 / 8"}
        assert "视频单元 2" in exc.value.message
        assert "E1U02" not in exc.value.message
        assert not (pm.get_project_path("demo") / "scripts" / "episode_1.json").exists()
        assert (await _service(pm).get_state("demo", 1))["status"] == "pending_review"

    @pytest.mark.parametrize("supported_durations", [(4, 6, 8), (4, 8)])
    async def test_confirm_tool_reports_the_off_tier_unit_by_position(
        self, tmp_path, set_video_request_facts, supported_durations
    ):
        """Agent entry reports failure reasons by unit position in identical cases, excluding internal unit IDs."""
        set_video_request_facts(_narrowed_rv_facts(supported_durations))
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        _write_rv_script_plan(pm, _off_tier_rv_script_plan())
        ctx = ToolHarness(
            project_name="demo",
            data_root=tmp_path / "projects",
            pm=pm,
            config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        )

        refused = await run_declared_tool(CONFIRM_SCRIPT_REVIEW, ctx, {"episode_id": 1})

        assert refused.problem is not None
        assert "视频单元 2" in refused.problem.detail
        assert "6s" in refused.problem.detail
        assert "[4, 8]" in refused.problem.detail
        assert "E1U02" not in refused.problem.detail

    async def test_confirm_other_conversion_refusals_stay_generic(self, tmp_path, video_request_facts):
        """Other conversion rejections retain generic conversion_refused."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        plan = _rv_script_plan()
        plan["units"].append(dict(plan["units"][0]))
        _write_rv_script_plan(pm, plan)

        with pytest.raises(ScriptReviewError) as exc:
            await _service(pm).confirm("demo", 1)

        assert exc.value.code == "conversion_refused"
        assert "unit_id 重复" in exc.value.message

    async def test_reference_duration_tiers_take_narrowed_with_reference_durations(
        self, tmp_path, set_video_request_facts
    ):
        """Gate options use narrowed r2v facts, never full declarations, matching prompt_authoring presave checks.

        Veo 3.1 at 1080p accepts 8s only; exposing declarations lets users select 4/6s, passing save/confirm
        until prompt_authoring rejects, making confirmed content fail only after payment.
        """
        set_video_request_facts(
            {
                "r2v": make_video_request_facts(
                    route="reference_video",
                    generation_type="r2v",
                    resolution="1080p",
                    supported_durations=(4, 6, 8),
                    allowed_durations=(8,),
                    excluded_durations=(4, 6),
                ),
                "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
            }
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)

        tiers = await svc.get_reference_duration_tiers("demo", 1)
        assert tiers == {"with_references": [8], "units": {}}

    async def test_reference_duration_tiers_report_each_plan_unit_bucket(self, tmp_path, set_video_request_facts):
        """Per-unit panel tiers follow server buckets: registered characters lacking images use i2v and name unavailable references."""
        set_video_request_facts(
            {
                "i2v": make_video_request_facts(
                    route="reference_video",
                    generation_type="i2v",
                    supported_durations=(5, 10),
                    allowed_durations=(5, 10),
                ),
                "r2v": make_video_request_facts(
                    route="reference_video",
                    generation_type="r2v",
                    supported_durations=(4, 6, 8),
                    allowed_durations=(8,),
                ),
            }
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        plan = _rv_script_plan()
        plan["units"].append(
            {"unit_id": "E1U02", "text": "空镜：雨停了。", "duration_seconds": 5, "source_text": "雨停了。"}
        )

        tiers = await _service(pm).get_reference_duration_tiers("demo", 1, plan["units"])

        assert tiers is not None
        units = tiers["units"]
        assert set(units) == {"E1U01", "E1U02"}
        assert (units["E1U01"]["declared_capability"], units["E1U01"]["hydrated_capability"]) == ("r2v", "i2v")
        assert units["E1U01"]["unavailable_references"] == [
            {"type": "character", "name": "阿离"},
            {"type": "character", "name": "裴与"},
        ]
        assert units["E1U01"]["allowed_durations"] == [5, 10]
        assert [problem["code"] for problem in units["E1U01"]["problems"]] == [
            "reference_asset_missing",
            "reference_capability_changed",
        ]
        assert units["E1U02"]["hydrated_capability"] == "i2v"
        assert units["E1U02"]["allowed_durations"] == [5, 10]
        assert units["E1U02"]["problems"] == []

    async def test_no_image_i2v_tier_is_available_in_review_state(self, tmp_path, set_video_request_facts):
        """Unreferenced units use i2v facts, retaining i2v-only seconds in read-time migration/panel options."""
        set_video_request_facts(
            {
                "r2v": make_video_request_facts(route="reference_video", generation_type="r2v"),
                "i2v": make_video_request_facts(
                    route="reference_video",
                    generation_type="i2v",
                    provider_id="ark",
                    model_id="doubao-seedance-2-0-260128",
                    supported_durations=(5, 10),
                    allowed_durations=(5, 10),
                ),
            }
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        plan = _rv_script_plan()
        plan["units"][0]["duration_seconds"] = 5
        plan["units"][0]["text"] = "镜头1：空镜，晨光照亮街道。"
        _write_rv_script_plan(pm, plan)
        service = _service(pm)

        state = await service.get_state("demo", 1)
        tiers = await service.get_reference_duration_tiers("demo", 1, plan["units"])

        assert state["content"]["units"][0]["duration_seconds"] == 5
        assert 5 in state["supported_durations"]
        assert tiers["units"]["E1U01"]["allowed_durations"] == [5, 10]
        assert 5 not in tiers["with_references"]

    @pytest.mark.parametrize("operation", ["read", "confirm"])
    async def test_unknown_i2v_preserves_no_image_legacy_duration(self, tmp_path, set_video_request_facts, operation):
        """Unresolved i2v facts never borrow r2v tiers to rewrite unreferenced durations during migration; confirmation rejects with i2v codes."""
        set_video_request_facts(
            {
                "r2v": make_video_request_facts(route="reference_video", generation_type="r2v"),
                "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
            }
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        plan = {
            "units": [
                {
                    "unit_id": "E1U01",
                    "text": "镜头1：空镜，街道渐亮。",
                    "duration_seconds": 5,
                    "source_text": "原文",
                    "duration_override": False,
                }
            ]
        }
        path = _write_rv_script_plan(pm, plan)
        service = _service(pm)

        if operation == "confirm":
            with pytest.raises(ScriptReviewError) as exc:
                await service.confirm("demo", 1)
            assert exc.value.code == "video_request_facts"
            assert exc.value.problem is not None
            assert exc.value.problem.code == "reference_capability_unavailable"

        state = await service.get_state("demo", 1)
        tiers = await service.get_reference_duration_tiers("demo", 1, plan["units"])

        assert state["content"]["units"][0]["duration_seconds"] == 5
        assert json.loads(path.read_text())["units"][0]["duration_seconds"] == 5
        assert state["supported_durations"] is None
        assert tiers["units"]["E1U01"]["allowed_durations"] is None
        assert tiers["units"]["E1U01"]["problem"]["code"] == "reference_capability_unavailable"

    async def test_reference_duration_tiers_none_when_with_reference_facts_unresolved(self, tmp_path):
        """Unresolved r2v facts return None; presentation stays readonly, never borrowing declared sets for options."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)

        assert await svc.get_reference_duration_tiers("demo", 1) is None

    async def test_reference_duration_tiers_none_for_non_reference_video_episode(self, tmp_path, video_request_facts):
        """Non-reference_video variants return None without evaluating facts, using their own script_plan_kind,
        never relying on caller short-circuiting by get_state.supported_durations non-None."""
        pm = _make_project(tmp_path, "drama")  # Absent generation_mode, not reference_video
        svc = _service(pm)

        assert await svc.get_reference_duration_tiers("demo", 1) is None

    async def test_reference_duration_tiers_take_custom_provider_facts(self, tmp_path, set_video_request_facts):
        """Custom providers (``custom-``) lack ``PROVIDER_REGISTRY`` entries; video facts alone supply tiers,
        forwarding r2v facts unchanged to panels."""
        set_video_request_facts(
            {
                "r2v": make_video_request_facts(
                    route="reference_video",
                    generation_type="r2v",
                    provider_id="custom-acme",
                    model_id="acme-video",
                    supported_durations=(5, 10),
                    allowed_durations=(5, 10),
                ),
                "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
            }
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)

        tiers = await svc.get_reference_duration_tiers("demo", 1)
        assert tiers == {"with_references": [5, 10], "units": {}}


class TestReferenceVideoScriptPlanMigration:
    """One-time gate-side coercion migration of legacy per-shot-duration script_plan drafts."""

    @staticmethod
    def _legacy_script_plan(seconds: int = 8) -> dict:
        """Precoercion shapes: bodies already use v9 but retain retired ``duration_override`` markers."""
        legacy = _rv_script_plan()
        legacy["units"][0]["duration_seconds"] = seconds
        legacy["units"][0]["duration_override"] = False
        return legacy

    async def test_migration_takes_slot_so_prompt_authoring_never_sees_a_non_member_duration(
        self, tmp_path, set_video_request_facts
    ):
        """Confirmation migration must persist tier-member seconds, not merely values inside structural intervals.

        One-time idempotent migration makes first execution definitive; normal flows confirm before generation. Structural-only
        migration could persist nontier seconds later rejected by prompt_authoring enum schemas, invisible/uneditable in gates.
        Therefore confirmation and generation share tier tables.
        """
        set_video_request_facts(
            make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=(4, 8, 12),
                allowed_durations=(4, 8, 12),
            )
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        # 10s fits structural intervals but not tiers; structural clamp alone would persist it unchanged.
        legacy["units"][0]["duration_seconds"] = 10
        path = _write_rv_script_plan(pm, legacy)

        assert (await svc.get_state("demo", 1))["content"]["units"][0]["duration_seconds"] == 12
        assert json.loads(path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 12

    async def test_custom_provider_draft_migration_takes_slot_from_facts(self, tmp_path, set_video_request_facts):
        """Custom providers (``custom-``) lack ``PROVIDER_REGISTRY`` entries; video facts alone provide tiers.

        Read-time coercion uses full fact declarations, persisting tier-member seconds accepted by prompt_authoring enum schemas;
        ``supported_durations`` returns identical tiers, sharing panel-option/coercion origins.
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
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        # 7s fits structural intervals but not [5, 10].
        legacy["units"][0]["duration_seconds"] = 7
        path = _write_rv_script_plan(pm, legacy)

        state = await svc.get_state("demo", 1)
        assert state["content"]["units"][0]["duration_seconds"] == 10
        assert state["supported_durations"] == [5, 10]
        assert json.loads(path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 10

    async def test_custom_provider_direct_confirm_takes_slot_from_facts(self, tmp_path, set_video_request_facts):
        """Agent/API direct confirm bypassing get_state also coerces through fact tiers; divergent entries let first execution
        persist nontier seconds, because migration is one-time/idempotent."""
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
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        legacy["units"][0]["duration_seconds"] = 7
        _write_rv_script_plan(pm, legacy)

        state = await svc.confirm("demo", 1)
        assert state["status"] == "confirmed"
        assert state["content"]["units"][0]["duration_seconds"] == 10

    async def test_migration_falls_back_to_structural_clamp_without_video_backend(
        self, tmp_path, set_video_request_facts
    ):
        """Unresolved configured video models lack tiers; fall back to structural clamps rather than block draft loading."""
        set_video_request_facts(VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)))
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        legacy["units"][0]["duration_seconds"] = 10

        _write_rv_script_plan(pm, legacy)
        assert (await svc.get_state("demo", 1))["content"]["units"][0]["duration_seconds"] == 10

    async def test_legacy_draft_is_migrated_on_read_and_written_back(self, tmp_path, video_request_facts):
        """State reads coerce: strip retired ``duration_override``, retain unit durations, persist once and never rewrite on rereads."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        path = _write_rv_script_plan(pm, self._legacy_script_plan())

        unit = (await svc.get_state("demo", 1))["content"]["units"][0]
        assert unit["duration_seconds"] == 8
        assert "duration_override" not in unit

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["units"][0]["duration_seconds"] == 8
        assert "duration_override" not in on_disk["units"][0]

        # Idempotent: second reads never rewrite persisted content.
        before = path.read_bytes()
        await svc.get_state("demo", 1)
        assert path.read_bytes() == before

    async def test_legacy_draft_can_be_confirmed_and_saved(self, tmp_path, video_request_facts):
        """Coerced legacy drafts become confirmable/saveable; both failed structure checks before migration due to retired unit fields."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        _write_rv_script_plan(pm, self._legacy_script_plan())

        edited = (await svc.get_state("demo", 1))["content"]
        edited["units"][0]["text"] = "@[阿离] 收伞。"
        assert (await svc.save_content("demo", 1, edited))["status"] == "pending_review"

        confirmed = await svc.confirm("demo", 1)
        assert confirmed["status"] == "confirmed"

    async def test_confirm_survives_migration_without_reopening_review(self, tmp_path, video_request_facts):
        """Migration is mechanical coercion, not content editing; loading confirmed episodes never demands reconfirmation."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        path = _write_rv_script_plan(pm, legacy)

        # Record confirmation fingerprints before coercion, simulating preupgrade approved episodes.
        def _confirm_legacy(p: dict) -> None:
            script_review.apply_confirmation(p, 1, script_review.content_fingerprint(path), "2026-01-01T00:00:00Z")

        pm.update_project("demo", _confirm_legacy)
        assert (await svc.get_state("demo", 1))["status"] == "confirmed"

        state = await svc.get_state("demo", 1)
        assert state["status"] == "confirmed"
        assert state["confirmed_at"] == "2026-01-01T00:00:00Z"
        assert state["content"]["units"][0]["duration_seconds"] == 8

    async def test_confirm_reopens_review_when_migration_clamps_duration(self, tmp_path, set_video_request_facts):
        """Warning-producing clamps change content rather than format only; confirmed episodes must await reconfirmation,
        never transfer confirmation as pure structural coercion does, because users reviewed different seconds.
        """
        set_video_request_facts(
            make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=(4, 8, 12),
                allowed_durations=(4, 8, 12),
            )
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        # 90s exceeds maximum 12s tiers; migration selects tiers, rewrites and warns.
        legacy["units"][0]["duration_seconds"] = 90
        path = _write_rv_script_plan(pm, legacy)

        def _confirm_legacy(p: dict) -> None:
            script_review.apply_confirmation(p, 1, script_review.content_fingerprint(path), "2026-01-01T00:00:00Z")

        pm.update_project("demo", _confirm_legacy)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        assert state["content"]["units"][0]["duration_seconds"] == 12

    async def test_clamping_migration_reopens_review_for_grandfathered_episode(self, tmp_path, set_video_request_facts):
        """Legacy episodes without confirmation fingerprints admitted through grandfather prompt_authoring presence must await confirmation
        after duration clamps. Idempotent migration retries produce no warnings; without persisted mismatch markers,
        later generation silently adopts values users never reviewed.
        """
        set_video_request_facts(
            make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=(4, 8, 12),
                allowed_durations=(4, 8, 12),
            )
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        legacy["units"][0]["duration_seconds"] = 90
        _write_rv_script_plan(pm, legacy)
        _write_prompt_authoring(pm)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "pending_review"
        # Idempotent rereads never restore grandfather admission; mismatch markers persisted.
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

    async def test_clamping_migration_marker_survives_interrupted_project_write(
        self, tmp_path, monkeypatch, set_video_request_facts
    ):
        """Migration writes project mismatch markers and drafts separately; failed project writes must retry toward pending confirmation.

        Draft-first persistence yields changed=False on retries, permanently omitting markers and leaving grandfather episodes
        confirmed with clamped durations. Marker-first keeps old drafts, so retries rerun migration and self-heal.
        """
        set_video_request_facts(
            make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=(4, 8, 12),
                allowed_durations=(4, 8, 12),
            )
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        legacy["units"][0]["duration_seconds"] = 90
        _write_rv_script_plan(pm, legacy)
        _write_prompt_authoring(pm)

        original_update = pm.update_project
        failed: list[int] = []

        def _fail_first_project_write(*args, **kwargs):
            if not failed:
                failed.append(1)
                raise OSError("project write interrupted")
            return original_update(*args, **kwargs)

        monkeypatch.setattr(pm, "update_project", _fail_first_project_write)

        with pytest.raises(OSError, match=r"project write interrupted"):
            await svc.get_state("demo", 1)

        monkeypatch.setattr(pm, "update_project", original_update)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"

    async def test_confirm_direct_call_confirms_migrated_content(self, tmp_path, set_video_request_facts):
        """Agent/API can confirm directly without get_state; when internal migration clamps
        through tolerant outside-enum clamp/warning rules, confirm approves migrated persisted contents.
        """
        set_video_request_facts(
            make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=(4, 8, 12),
                allowed_durations=(4, 8, 12),
            )
        )
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        legacy["units"][0]["duration_seconds"] = 90
        _write_rv_script_plan(pm, legacy)

        state = await svc.confirm("demo", 1)
        assert state["status"] == "confirmed"
        assert state["content"]["units"][0]["duration_seconds"] == 12

    async def test_confirmation_carry_uses_written_content_not_post_write_reread(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """Transfer confirmation fingerprints directly from freshly written content, never reread disk: concurrent edits between writes
        and rereads would supply different fingerprints, incorrectly transferring confirmation onto unreviewed
        content.
        """
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        path = _write_rv_script_plan(pm, legacy)
        before = script_review.content_fingerprint(path)

        def _confirm_legacy(p: dict) -> None:
            script_review.apply_confirmation(p, 1, before, "2026-01-01T00:00:00Z")

        pm.update_project("demo", _confirm_legacy)

        concurrent_edit = self._legacy_script_plan()
        concurrent_edit["units"][0]["text"] = "并发编辑：紧随迁移写回落盘。"

        written: list[dict] = []
        original_write = script_review.atomic_write_json

        def _write_then_concurrent_edit(target_path, data):
            written.append(json.loads(json.dumps(data)))
            original_write(target_path, data)
            atomic_write_json(path, concurrent_edit)

        monkeypatch.setattr(script_review, "atomic_write_json", _write_then_concurrent_edit)

        await svc.get_state("demo", 1)

        migrated_fingerprint = script_review.content_fingerprint_of_data(written[0])
        concurrent_fingerprint = script_review.content_fingerprint_of_data(concurrent_edit)
        stored = script_review.stored_review(pm.load_project("demo"), 1)

        assert stored["confirmed_at"] == "2026-01-01T00:00:00Z"
        assert stored["fingerprint"] == migrated_fingerprint
        assert stored["fingerprint"] != concurrent_fingerprint

    async def test_migration_carries_confirmation_that_lands_after_project_snapshot_loaded(
        self, tmp_path, monkeypatch, video_request_facts
    ):
        """get_state never refreshes premigration project snapshots; confirmations between snapshot loading
        and migration completion must not rely on stale snapshots, misclassifying freshly confirmed
        episodes and skipping transfer permanently, unrecoverable through idempotent migration retries.
        """
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        legacy = self._legacy_script_plan()
        path = _write_rv_script_plan(pm, legacy)
        before = script_review.content_fingerprint(path)

        original_migrate = script_review.migrate_unit_durations

        def _migrate_with_concurrent_confirm(units, **kwargs):
            # Simulate another request confirm() after get_state project snapshot loading but before migration completion.
            def _confirm_legacy(p: dict) -> None:
                script_review.apply_confirmation(p, 1, before, "2026-01-01T00:00:00Z")

            pm.update_project("demo", _confirm_legacy)
            return original_migrate(units, **kwargs)

        monkeypatch.setattr(script_review, "migrate_unit_durations", _migrate_with_concurrent_confirm)

        state = await svc.get_state("demo", 1)
        assert state["status"] == "confirmed"

    async def test_migration_does_not_confirm_an_unconfirmed_episode(self, tmp_path, video_request_facts):
        """Already mismatched fingerprints from actual edits never transfer confirmation; retain pending state."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        _write_rv_script_plan(pm, self._legacy_script_plan())

        def _stale_confirm(p: dict) -> None:
            script_review.apply_confirmation(p, 1, "0" * 64, "2026-01-01T00:00:00Z")

        pm.update_project("demo", _stale_confirm)
        assert (await svc.get_state("demo", 1))["status"] == "pending_review"


class TestReferenceVideoPromptAuthoringEnforcement:
    async def test_confirm_tool_materializes_the_script_authoring_reads(self, tmp_path, video_request_facts):
        """Agent route: unconfirmed rv planning lacks formal scripts, so authoring points to confirmation; confirm_script_review
        materializes formal scripts on confirmation, admitting authoring."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        _write_rv_script_plan(pm, _rv_script_plan())
        project_path = pm.get_project_path("demo")

        ctx = ToolHarness(
            project_name="demo",
            data_root=tmp_path / "projects",
            pm=pm,
            config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        )
        refused = await run_declared_tool(GENERATE_EPISODE_SCRIPT, ctx, {"episode_id": 1})
        assert refused.problem is not None
        assert "尚无正式脚本" in refused.problem.detail
        assert "内容确认" in refused.problem.detail

        result = await run_declared_tool(CONFIRM_SCRIPT_REVIEW, ctx, {"episode_id": 1})
        assert result.problem is None, result
        assert (project_path / "scripts" / "episode_1.json").exists()

        dry_run = await run_declared_tool(GENERATE_EPISODE_SCRIPT, ctx, {"episode_id": 1, "dry_run": True})
        assert dry_run.problem is None, dry_run


# ---------------------------------------------------------------------------
# Scope: drama/narration/reference_video gated; ad excluded.
# ---------------------------------------------------------------------------


class TestApplicability:
    async def test_reference_video_applicable(self, tmp_path, video_request_facts):
        """reference_video across content_mode is gated, with reference_video planning variants."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        project = pm.load_project("demo")
        assert script_review.script_plan_kind(project) == "reference_video"
        assert script_review.is_applicable(project) is True
        # Absent script_plan → no_script_plan, distinct from ad not_applicable.
        assert (await _service(pm).get_state("demo", 1))["status"] == "no_script_plan"

    async def test_ad_not_applicable(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("addemo")
        pm.create_project_metadata("addemo", "Ad", "Anime", "ad")
        svc = _service(pm)
        assert script_review.script_plan_kind(svc.pm.load_project("addemo")) is None
        assert (await svc.get_state("addemo", 1))["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# Edit validation + confirmation prerequisites
# ---------------------------------------------------------------------------


class TestErrors:
    @pytest.mark.parametrize("kind", ["drama", "narration", "reference_video"])
    async def test_agent_draft_keeps_formal_plan_readonly_until_discarded(self, tmp_path, video_request_facts, kind):
        pm = _make_project(
            tmp_path,
            "narration" if kind == "narration" else "drama",
            generation_mode="reference_video" if kind == "reference_video" else None,
        )
        if kind == "reference_video":
            content = _rv_script_plan()
            path = _write_rv_script_plan(pm, content)
            doc_type = "reference_script_plan"
        else:
            content = _admitted_drama_script_plan() if kind == "drama" else _narration_script_plan()
            path = _write_script_plan(pm, kind, content)
            doc_type = f"{kind}_script_plan"
        service = _service(pm)
        state = await service.get_state("demo", 1)
        original = path.read_bytes()
        workflow = DraftWorkflow(
            DraftContext(project_name="demo", data_root=pm.data_root, pm=pm, config_resolver=service.config_resolver)
        )
        opened = await workflow.open(1, doc_type)
        quarantine_kind = script_review.script_plan_quarantine_kind(pm.load_project("demo"))
        assert quarantine_kind is not None
        draft_path = quarantine_path(pm.get_project_path("demo"), 1, quarantine_kind)
        original_draft = draft_path.read_bytes()
        edited = json.loads(json.dumps(state["content"]))
        if kind == "narration":
            edited["segments"][0]["novel_text"] += "后来。"
        elif kind == "drama":
            edited["title"] = "修改后的标题"
        else:
            edited["units"][0]["text"] += "\n雨停了。"

        with pytest.raises(ScriptReviewError) as exc:
            await service.save_content("demo", 1, edited, state["fingerprint"])

        assert exc.value.code == "draft_agent_owned"
        assert path.read_bytes() == original
        assert draft_path.read_bytes() == original_draft
        await workflow.discard(1, doc_type, opened["revision"])
        assert (await service.save_content("demo", 1, edited, state["fingerprint"]))["status"] == "pending_review"

    async def test_save_empty_dialogue_speaker_returns_structured_admission(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        path = _write_script_plan(pm, "drama", _drama_script_plan())

        bad = _drama_script_plan()
        bad["scenes"][0]["utterances"][1] = {"kind": "dialogue", "speaker": None, "text": "无人"}
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, bad)
        assert exc.value.code == "speech_admission"
        assert exc.value.admission is not None
        assert exc.value.admission.unit_id == "E1S01"
        assert exc.value.admission.problems[0].code == "empty_speaker"
        assert exc.value.admission.problems[0].locations[0].path == ("utterances", 1, "speaker")
        assert json.loads(path.read_text(encoding="utf-8")) == _drama_script_plan()

    async def test_save_invalid_non_speech_content_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        _write_script_plan(pm, "drama", _drama_script_plan())

        bad = _drama_script_plan()
        bad["scenes"][0]["duration_seconds"] = "invalid"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, bad)
        assert exc.value.code == "invalid_content"

    async def test_confirm_without_script_plan_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "no_script_plan"

    async def test_confirm_marked_drama_candidate_returns_structured_admission(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        candidate = _drama_script_plan()
        candidate["scenes"][0]["needs_replan"] = True
        _write_script_plan(pm, "drama", candidate)

        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)

        assert exc.value.code == "speech_admission"
        assert exc.value.admission is not None
        assert exc.value.admission.unit_id == "E1S01"
        assert exc.value.admission.problems[0].code == "needs_replan"
        project = pm.load_project("demo")
        assert script_review.review_status(pm.get_project_path("demo"), project, 1) == "pending_review"

    async def test_save_not_applicable_rejected(self, tmp_path):
        pm = _make_project(tmp_path, "ad")  # ad lacks structured script_plan; gate inapplicable.
        svc = _service(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, _drama_script_plan())
        assert exc.value.code == "not_applicable"

    async def test_get_state_unregistered_episode_rejected(self, tmp_path):
        """Applicable gates with unregistered episodes return episode_not_found, never no_script_plan."""
        pm = _make_project(tmp_path, "drama")  # Register episode 1 only.
        svc = _service(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.get_state("demo", 99)
        assert exc.value.code == "episode_not_found"

    async def test_save_unregistered_episode_writes_no_orphan(self, tmp_path):
        """Saving unregistered episodes returns episode_not_found without orphan drafts/episode_99 script_plan files."""
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 99, _drama_script_plan())
        assert exc.value.code == "episode_not_found"
        orphan = pm.get_project_path("demo") / "drafts" / "episode_99" / "script_plan_normalized_script.json"
        assert not orphan.exists()

    async def test_save_with_stale_fingerprint_conflicts_reference_video(self, tmp_path, video_request_facts):
        """rv concurrent edits with stale baseline fingerprints conflict without persistence/overwrite;
        retrying latest fingerprints, equivalent to refresh/merge, succeeds."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        path = _write_rv_script_plan(pm, _rv_script_plan())
        stale = (await svc.get_state("demo", 1))["fingerprint"]

        # Another editor saves first, changing content/fingerprints.
        other = _rv_script_plan()
        other["units"][0]["text"] = "@[阿离] 转身离开。"
        await svc.save_content("demo", 1, other)
        before = path.read_text(encoding="utf-8")

        mine = _rv_script_plan()
        mine["units"][0]["text"] = "@[裴与] 下马。"
        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, mine, stale)
        assert exc.value.code == "conflict"
        assert path.read_text(encoding="utf-8") == before

        fresh = (await svc.get_state("demo", 1))["fingerprint"]
        state = await svc.save_content("demo", 1, mine, fresh)
        assert state["status"] == "pending_review"
        assert json.loads(path.read_text(encoding="utf-8"))["units"][0]["text"] == "@[裴与] 下马。"

    async def test_save_with_stale_fingerprint_conflicts_drama(self, tmp_path):
        """Drama/narration Web saves share baseline protections and identical conflict codes."""
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        path = _write_script_plan(pm, "drama", _drama_script_plan())
        stale = (await svc.get_state("demo", 1))["fingerprint"]

        other = _drama_script_plan()
        other["title"] = "另一方改的标题"
        await svc.save_content("demo", 1, other)
        before = path.read_text(encoding="utf-8")

        with pytest.raises(ScriptReviewError) as exc:
            await svc.save_content("demo", 1, _drama_script_plan(), stale)
        assert exc.value.code == "conflict"
        assert path.read_text(encoding="utf-8") == before

    async def test_save_without_fingerprint_skips_baseline_check(self, tmp_path, video_request_facts):
        """Direct calls without baseline fingerprints preserve prior semantics: no comparison, immediate persistence."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        _write_rv_script_plan(pm, _rv_script_plan())
        other = _rv_script_plan()
        other["units"][0]["text"] = "@[阿离] 转身离开。"
        await svc.save_content("demo", 1, other)

        state = await svc.save_content("demo", 1, _rv_script_plan())
        assert state["status"] == "pending_review"

    async def test_rv_save_clears_stale_prompt_authoring_quarantine_on_change(self, tmp_path, video_request_facts):
        """Changed Web-saved script_plan invalidates existing prompt_authoring drafts whose structure-preserving diffs use old planning;
        unchanged saves retain drafts, sharing Agent persistence outlets/semantics."""
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        svc = _service(pm)
        project_path = pm.get_project_path("demo")
        _write_rv_script_plan(pm, _rv_script_plan())
        # First save normalized shapes, including model defaults, to establish common unchanged-content comparison bases.
        await svc.save_content("demo", 1, _rv_script_plan())
        prompt_authoring_q = quarantine_path(project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)

        write_quarantine(
            project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": [{"text": "旧基底"}]}, violations=[]
        )
        await svc.save_content("demo", 1, _rv_script_plan())  # Unchanged content: validated results match disk.
        assert prompt_authoring_q.exists()

        edited = _rv_script_plan()
        edited["units"][0]["text"] = "@[阿离] 转身离开。"
        await svc.save_content("demo", 1, edited)
        assert not prompt_authoring_q.exists()

    async def test_confirm_corrupt_script_plan_rejected(self, tmp_path):
        """Corrupt script_plan JSON, even with content_fingerprint hashes, fails confirmation structure validation."""
        pm = _make_project(tmp_path, "drama")
        svc = _service(pm)
        path = _write_script_plan(pm, "drama", _drama_script_plan())
        path.write_bytes(b"\x00\x01 not json at all {")
        with pytest.raises(ScriptReviewError) as exc:
            await svc.confirm("demo", 1)
        assert exc.value.code == "invalid_content"


# ---------------------------------------------------------------------------
# Sole persistence outlet: lib.script.script_review.write_script_plan_locked
# ---------------------------------------------------------------------------


class TestScriptPlanWriteStore:
    def _project_path(self, tmp_path: Path) -> Path:
        pm = _make_project(tmp_path, "drama", generation_mode="reference_video")
        return pm.get_project_path("demo")

    def test_conflict_on_stale_baseline_keeps_file(self, tmp_path: Path):
        project_path = self._project_path(tmp_path)
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})
        stale = "0" * 64

        with (
            pytest.raises(script_review.ScriptPlanWriteConflict) as exc,
            script_review.script_plan_write_lock(project_path, 1),
        ):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 2}]}, expected_fingerprint=stale)

        assert exc.value.expected == stale
        assert exc.value.actual == script_review.content_fingerprint_of_data({"units": [{"v": 1}]})
        assert exc.value.current_content == {"units": [{"v": 1}]}
        path = script_review.official_reference_script_plan_path(project_path, 1)
        assert json.loads(path.read_text(encoding="utf-8")) == {"units": [{"v": 1}]}

    def test_matching_baseline_and_none_baseline_write(self, tmp_path: Path):
        """Matching baselines pass; ``None`` means files absent when captured, also permitting first writes."""
        project_path = self._project_path(tmp_path)
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]}, expected_fingerprint=None)
        current = script_review.content_fingerprint(script_review.official_reference_script_plan_path(project_path, 1))
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 2}]}, expected_fingerprint=current)
        path = script_review.official_reference_script_plan_path(project_path, 1)
        assert json.loads(path.read_text(encoding="utf-8")) == {"units": [{"v": 2}]}

    def test_none_baseline_conflicts_when_file_appeared(self, tmp_path: Path):
        """None baselines with files created by others before writes conflict without overwrite."""
        project_path = self._project_path(tmp_path)
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})
        with (
            pytest.raises(script_review.ScriptPlanWriteConflict),
            script_review.script_plan_write_lock(project_path, 1),
        ):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 2}]}, expected_fingerprint=None)

    def test_prompt_authoring_quarantine_cleared_only_on_change(self, tmp_path: Path):
        project_path = self._project_path(tmp_path)
        prompt_authoring_q = quarantine_path(project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})

        # Unchanged content → retain
        write_quarantine(
            project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": [{"text": "基底"}]}, violations=[]
        )
        with script_review.script_plan_write_lock(project_path, 1):
            assert script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]}) is False
        assert prompt_authoring_q.exists()

        # Changed content → clear
        with script_review.script_plan_write_lock(project_path, 1):
            assert script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 2}]}) is True
        assert not prompt_authoring_q.exists()

        # Migration writes are mechanical coercion; retain even on content changes.
        write_quarantine(
            project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": [{"text": "基底"}]}, violations=[]
        )
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(
                project_path, 1, {"units": [{"v": 3}]}, clear_prompt_authoring_quarantine=False
            )
        assert prompt_authoring_q.exists()

    def test_successful_noop_refreshes_active_manifest_claim(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        project_path = self._project_path(tmp_path)
        project_file = project_path / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        project["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
        atomic_write_json(project_file, project)
        content = {"units": [{"v": 1}]}
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, content)

        registered: list[object] = []
        use_artifact_registration(monkeypatch, lambda _project_path, key: registered.append(key))

        with script_review.script_plan_write_lock(project_path, 1):
            assert script_review.write_script_plan_locked(project_path, 1, content) is False

        from lib.artifacts.artifact_manifest import ArtifactKey

        assert registered == [ArtifactKey.episode_script_plan(1)]

    def test_content_change_moves_project_activity_forward(self, tmp_path: Path):
        # Planning JSON mtimes never count as activity; write outlets record activity, excluding unchanged writes.
        project_path = self._project_path(tmp_path)
        stale = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        (project_path / ACTIVITY_FILENAME).write_text(stale.isoformat(), encoding="utf-8")

        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})
        recorded = recorded_project_activity(project_path)
        assert recorded is not None
        assert recorded > stale

        (project_path / ACTIVITY_FILENAME).write_text(stale.isoformat(), encoding="utf-8")
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})
        assert recorded_project_activity(project_path) == stale

    def test_failed_commit_leaves_project_activity_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Content changes invalidate downstream drafts; transactional cleanup records activity, rolled back with failed registration.
        project_path = self._project_path(tmp_path)
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})
        write_quarantine(
            project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": [{"text": "基底"}]}, violations=[]
        )
        stale = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        (project_path / ACTIVITY_FILENAME).write_text(stale.isoformat(), encoding="utf-8")

        def _fail(*_args: object) -> None:
            raise RuntimeError("registration failed")

        use_artifact_registration(monkeypatch, _fail)
        with (
            script_review.script_plan_write_lock(project_path, 1),
            pytest.raises(RuntimeError, match="registration failed"),
        ):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 2}]})

        assert quarantine_path(project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists()
        assert recorded_project_activity(project_path) == stale

    def test_failed_commit_keeps_activity_recorded_by_other_writers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Global project ledgers have draft/memory writers outside formal-file locks; rollback must retain concurrent accounting.
        project_path = self._project_path(tmp_path)
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})
        stale = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        (project_path / ACTIVITY_FILENAME).write_text(stale.isoformat(), encoding="utf-8")

        def _other_writer_then_fail(*_args: object) -> None:
            record_project_activity(project_path)
            raise RuntimeError("registration failed")

        use_artifact_registration(monkeypatch, _other_writer_then_fail)
        with (
            script_review.script_plan_write_lock(project_path, 1),
            pytest.raises(RuntimeError, match="registration failed"),
        ):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 2}]})

        recorded = recorded_project_activity(project_path)
        assert recorded is not None
        assert recorded > stale

    def test_deleting_the_script_plan_moves_project_activity_forward(self, tmp_path: Path):
        project_path = self._project_path(tmp_path)
        with script_review.script_plan_write_lock(project_path, 1):
            script_review.write_script_plan_locked(project_path, 1, {"units": [{"v": 1}]})
        stale = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        (project_path / ACTIVITY_FILENAME).write_text(stale.isoformat(), encoding="utf-8")

        path = script_review.official_reference_script_plan_path(project_path, 1)
        assert script_review.delete_script_plan_file(project_path, 1, path) is True

        recorded = recorded_project_activity(project_path)
        assert recorded is not None
        assert recorded > stale


# ---------------------------------------------------------------------------
# prompt_authoring and confirmation: authoring is ungated; confirmation tools share services.
# ---------------------------------------------------------------------------


class TestPromptAuthoringEnforcement:
    async def test_pending_review_does_not_block_authoring_the_formal_script(self, tmp_path, video_request_facts):
        """Authoring reads formal scripts only; unconfirmed rerun planning never blocks authoring entry."""
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(pm, _narration_script({**_narration_script_segment("E1S01"), "pending_authoring": True}))
        pm.save_script(
            "demo",
            _narration_script({**_narration_script_segment("E1S01"), "pending_authoring": True}),
            "episode_1.json",
        )
        pm.update_project(
            "demo", lambda p: script_review.apply_confirmation(p, 1, "sha256-v1:" + "0" * 64, "2026-01-01T00:00:00Z")
        )
        project_path = pm.get_project_path("demo")
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"

        ctx = ToolHarness(
            project_name="demo",
            data_root=tmp_path / "projects",
            pm=pm,
            config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        )
        result = await run_declared_tool(GENERATE_EPISODE_SCRIPT, ctx, {"episode_id": 1, "dry_run": True})

        assert isinstance(result.value, TextGenerationResult), result
        assert "DRY RUN" in result.value.message

    async def test_confirm_tool_unblocks_prompt_authoring(self, tmp_path, video_request_facts):
        """Agent confirm_script_review admits gates, preserving existing script_plan→prompt_authoring flows."""
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        project_path = pm.get_project_path("demo")
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"

        ctx = ToolHarness(
            project_name="demo",
            data_root=tmp_path / "projects",
            pm=pm,
            config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        )
        result = await run_declared_tool(CONFIRM_SCRIPT_REVIEW, ctx, {"episode_id": 1})

        assert result.problem is None, result
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "confirmed"

    async def test_confirm_tool_refusal_carries_the_same_loss_text_as_the_web_state(
        self, tmp_path, video_request_facts
    ):
        """Agent loss lists share server text with Web confirmations; tokens stay separately in params."""
        pm = _make_project(tmp_path, "narration")
        _write_script_plan(pm, "narration", _narration_script_plan())
        _write_script(
            pm,
            _narration_script(
                _narration_script_segment(
                    "E1S01",
                    end_frame_image="end_frames/scene_E1S01.png",
                    generated_assets={"narration_audio": "audio/segment_E1S01.wav", "grid_id": "grid_ab12"},
                )
            ),
        )
        web_overwrite = script_review.overwrite_with_text(
            (await _service(pm).get_state("demo", 1))["script_overwrite"], i18n_message
        )
        assert web_overwrite is not None
        ctx = ToolHarness(
            project_name="demo",
            data_root=tmp_path / "projects",
            pm=pm,
            config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        )

        refused = await run_declared_tool(CONFIRM_SCRIPT_REVIEW, ctx, {"episode_id": 1})

        assert refused.problem is not None
        assert refused.problem.code == "script_overwrite_required"
        assert refused.problem.params == {"script_overwrite": web_overwrite}
        assert web_overwrite["text"] in refused.problem.detail
        for lost in ("配音 1 段", "尾帧 1 张", "宫格归属 1 处"):
            assert lost in web_overwrite["text"]

    async def test_confirm_tool_reports_the_video_request_facts_problem(self, tmp_path, set_video_request_facts):
        """Agent unresolved video facts return problem codes/parameters in confirmation receipts, beyond internal error classes."""
        set_video_request_facts(
            VideoRequestFactsFailure(
                "video_supported_durations_incompatible",
                (("provider", "p"), ("model", "m"), ("resolution", "1080p"), ("capability", "i2v")),
            )
        )
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        project_path = pm.get_project_path("demo")
        ctx = ToolHarness(
            project_name="demo",
            data_root=tmp_path / "projects",
            pm=pm,
            config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        )

        result = await run_declared_tool(CONFIRM_SCRIPT_REVIEW, ctx, {"episode_id": 1})

        assert result.problem is not None
        text = result.problem.detail
        assert "video_supported_durations_incompatible" in text
        assert "resolution=1080p" in text
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"


# ---------------------------------------------------------------------------
# Exhaustive legacy cases: {script_plan present × prompt_authoring present × script_plan_review present} gate derivations
# ---------------------------------------------------------------------------


class TestLegacyEnumeration:
    async def test_no_script_plan(self, tmp_path):
        pm = _make_project(tmp_path, "drama")
        assert (await _service(pm).get_state("demo", 1))["status"] == "no_script_plan"

    async def test_script_plan_no_prompt_authoring_no_review_pending(self, tmp_path):
        """First postfeature script_plan without prompt_authoring/confirmation awaits confirmation and blocks."""
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _drama_script_plan())
        assert (await _service(pm).get_state("demo", 1))["status"] == "pending_review"

    async def test_script_plan_prompt_authoring_no_review_pending(self, tmp_path):
        """Existing script_plan + prompt_authoring without confirmation cannot prove scripts came from planning, so await confirmation."""
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _drama_script_plan())
        _write_prompt_authoring(pm)
        project_path = pm.get_project_path("demo")
        assert (await _service(pm).get_state("demo", 1))["status"] == "pending_review"
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "pending_review"

    async def test_script_plan_prompt_authoring_review_matching_confirmed(self, tmp_path, video_request_facts):
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        _write_prompt_authoring(pm)
        await _confirm_over_existing_script(pm)
        assert (await _service(pm).get_state("demo", 1))["status"] == "confirmed"

    async def test_script_plan_prompt_authoring_review_mismatch_pending(self, tmp_path, video_request_facts):
        """Confirmed script_plan reruns await reconfirmation even with prompt_authoring; fingerprints precede grandfather rules."""
        pm = _make_project(tmp_path, "drama")
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())
        _write_prompt_authoring(pm)
        await _confirm_over_existing_script(pm)
        rerun = _admitted_drama_script_plan()
        rerun["scenes"][0]["source_text"] = "改写后的原文锚"
        _write_script_plan(pm, "drama", rerun)
        assert (await _service(pm).get_state("demo", 1))["status"] == "pending_review"


# ---------------------------------------------------------------------------
# Manual presplit self-healing: empty episodes[] ledgers with existing source/episode_N.txt derived files
# make _require_episode register missing entries rather than declare deadlock.
# ---------------------------------------------------------------------------


class TestOwnSourceEpisodes:
    async def test_unregistered_episode_file_is_not_an_episode(self, tmp_path):
        """Unregistered source/episode_N.txt files never backfill episodes; state reads return episode_not_found without ledger changes."""
        pm = _make_manual_split_project(tmp_path, "narration")
        _write_source_text(pm, "episode_1.txt", "裴与出征后的第二年。")

        with pytest.raises(ScriptReviewError) as exc:
            await _service(pm).get_state("demo", 1)

        assert exc.value.code == "episode_not_found"
        assert pm.load_project("demo")["episodes"] == []

    async def test_confirm_unblocks_prompt_authoring_for_an_own_source_episode(self, tmp_path, video_request_facts):
        """Individually registered own-source episodes share content-confirmation outlets."""
        pm = _make_manual_split_project(tmp_path, "drama")
        pm.add_character("demo", "阿离", "少女")
        assert register_project_sources(pm, "demo", own_episodes=("任意原文内容",)) == [1]
        _write_script_plan(pm, "drama", _admitted_drama_script_plan())

        confirmed = await _service(pm).confirm("demo", 1)
        assert confirmed["status"] == "confirmed"

        project_path = pm.get_project_path("demo")
        assert script_review.review_status(project_path, pm.load_project("demo"), 1) == "confirmed"
        ep = script_review.find_episode(pm.load_project("demo"), 1)
        assert ep is not None
        assert ep["source_origin"] == "own"
        assert "source_range" not in ep
