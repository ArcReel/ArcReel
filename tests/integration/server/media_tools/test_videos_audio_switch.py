"""Audio-switch prechecks on Agent video enqueue paths.

Agent enqueue must reject Web-rejected always-audible-model + audio-disabled configuration. Otherwise orchestration would
strip voice constraints as silent, yielding audible video without controlled voices. Storyboard-video shares
``server.services.tasks.video_caps``; reference-video public projection carries identical criteria.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.generation.generation_queue_client import TaskSpec
from lib.generation.generation_result import GenerationSelectionMode
from lib.i18n import SUPPORTED_LOCALES, _, render_message
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.reference_video.request_projection import ReferenceRequestOptions
from lib.script.reference_video.text_parser import extract_mentions
from server.services.admission import video_batch_admission as admission_mod
from server.services.admission.video_batch_admission import admit_reference_video_batch
from server.services.tasks.video_caps import AudioSwitchConflict, assert_audio_switch_supported
from server.tool_runtime import ToolOutcome
from tests.factories import make_display_names, make_video_request_facts
from tests.fakes import bind_session_factory, use_active_tasks_lookup, use_reference_projection
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    read_generation_result,
    run_generate_videos,
)

_ALWAYS_AUDIBLE = "dashscope/wan2.7-i2v"
_CONTROLLABLE = "ark/doubao-seedance-2-0-260128"


_EPISODE_1 = {"scope": "episode", "episode_id": 1}


def _admission_codes(out: ToolOutcome[Any]) -> dict[str, list[str]]:
    assert isinstance(out.value, dict)
    return {
        unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
        for unit in out.value["batch_admission"]["units"]
    }


async def _seed_settings(factory: async_sessionmaker[AsyncSession], **settings: str) -> None:
    """Write system settings to the shared DB fixture."""
    async with factory() as session:
        svc = ConfigService(session)
        for key, value in settings.items():
            await svc.set_setting(key, value)
        await session.commit()


def _unit_spec(unit: dict[str, Any]) -> TaskSpec:
    """Enqueueable unit-spec substitute used only to signal bucketed deduplication that this item will enqueue."""
    return TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id=str(unit["unit_id"]),
        prompt="镜头",
        script_file="episode_1.json",
    )


class TestAssertAudioSwitchSupported:
    async def test_always_audible_model_with_audio_off_names_provider_and_model(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        with pytest.raises(ValueError, match=r"成片恒有声，无法关闭音频") as exc_info:
            await assert_audio_switch_supported({}, "i2v")

        assert "dashscope/wan2.7-i2v" in str(exc_info.value)

    async def test_controllable_model_keeps_the_off_setting(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_CONTROLLABLE, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        await assert_audio_switch_supported({}, "i2v")

        # Accepted audio-off remains unchanged, never rewritten as audible.
        assert await ConfigResolver(db_factory).video_generate_audio_for_project({}) is False


class TestStoryboardRouteGate:
    """Storyboard-video gates ignore content type but run only with actual tasks to enqueue."""

    async def test_gate_is_content_mode_agnostic(self, tmp_path, monkeypatch):
        seen: list[str] = []

        async def _reject(_project, generation_type, **_kwargs):
            seen.append(generation_type)
            raise AudioSwitchConflict("dashscope", "wan2.7-i2v")

        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        conflict = await admission_mod.audio_switch_conflict({"generation_mode": "storyboard"})
        assert conflict is not None
        assert conflict.code == "video_audio_switch_not_supported"
        assert seen == ["i2v"]

    async def test_voice_characters_resolve_independently_of_the_gate(self, tmp_path, monkeypatch):
        async def _not_silent(_project):
            return False

        monkeypatch.setattr(admission_mod, "resolve_project_is_silent", _not_silent)
        project = {"generation_mode": "storyboard", "characters": {"张三": {"description": "主角"}}}
        assert await admission_mod.resolve_voice_context(project, "drama") == project["characters"]


class TestReferenceRouteGate:
    """Reference video invokes public projection for units this batch will actually enqueue."""

    @staticmethod
    def _stub_current_state(monkeypatch) -> None:
        async def _no_active(**_kwargs):
            return []

        use_active_tasks_lookup(monkeypatch, _no_active)

    async def test_projects_each_pending_unit_and_skips_done_units(self, tmp_path, monkeypatch):
        seen: list[str] = []

        class _Projection:
            unit_id = "test"
            blocking_problems: tuple[object, ...] = ()
            cost = None
            planned_duration = 8
            request_duration = None

            def to_advisory_payload(self):
                return {"allowed": True, "unit_id": "test", "problems": []}

        async def _record(*, unit, **_kwargs):
            # Substitutes need predictable bucket signals; empty project fixtures would make production
            # unit_reference_declarations drop all unregistered names, so use direct body mentions here.
            seen.append("r2v" if extract_mentions(str(unit.get("text") or "")) else "i2v")
            return _Projection()

        self._stub_current_state(monkeypatch)
        use_reference_projection(monkeypatch, _record)
        units = [
            {"unit_id": "E1U1", "text": "@[张三] 推门"},
            {"unit_id": "E1U2", "text": "@[李四] 举杯"},
            {"unit_id": "E1U3", "text": "空镜：长街"},
        ]
        await admit_reference_video_batch(
            project_name="demo",
            project={},
            project_path=tmp_path,
            script={"video_units": units},
            script_file="episode_1.json",
            units=units,
            request_options=ReferenceRequestOptions(),
            operation="generate_video",
            selection=GenerationSelectionMode.MISSING_ONLY,
            spec_check=_unit_spec,
        )
        assert seen == ["r2v", "r2v", "i2v"]

    async def test_units_that_cannot_be_enqueued_do_not_trigger_projection(self, tmp_path, monkeypatch):
        """Unenqueueable units trigger no resolution; their earlier rejection already determines outcome."""
        called = False

        async def _record(**_kwargs):
            nonlocal called
            called = True

        def _reject(_unit):
            raise ValueError("正文为空")

        self._stub_current_state(monkeypatch)
        use_reference_projection(monkeypatch, _record)
        admission = await admit_reference_video_batch(
            project_name="demo",
            project={},
            project_path=tmp_path,
            script={"video_units": []},
            script_file="episode_1.json",
            units=[{"unit_id": "E1U1", "text": ""}],
            request_options=ReferenceRequestOptions(),
            operation="generate_video",
            selection=GenerationSelectionMode.MISSING_ONLY,
            spec_check=_reject,
        )
        assert called is False
        assert not admission.admitted


class _EpisodePM:
    """Minimal whole-episode pm substitute: one segment/episode, with optional storyboards chosen by callers.

    Match production current schemas and ledger bindings; activate manifests to register existing images during
    construction. Manifests alone determine generated presence. Later malformed in-memory items/prompts test
    per-item rejection without writing to disk, preserving this clean manifest baseline.
    """

    def __init__(self, project_dir: Path, *, with_storyboard: bool) -> None:
        self._project_dir = project_dir
        item: dict[str, Any] = {
            "segment_id": "E1S01",
            "novel_text": "镜头缓缓扫过原野。",
            "image_prompt": "原野远景",
            "video_prompt": "镜头平移",
        }
        if with_storyboard:
            item["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        self.script_payload: dict[str, Any] = {"content_mode": "narration", "episode": 1, "segments": [item]}
        self.project_payload: dict[str, Any] = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
        self._mirror()

    def get_project_path(self, _name: str) -> Path:
        return self._project_dir

    def _mirror(self) -> None:
        """Persist baseline projects and activate manifests, matching production migration backfill."""

        from lib.artifacts.artifact_activation import activate_artifact_target_state

        self._project_dir.mkdir(parents=True, exist_ok=True)
        (self._project_dir / "project.json").write_text(
            json.dumps(self.project_payload, ensure_ascii=False), encoding="utf-8"
        )
        scripts_dir = self._project_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(self.script_payload, ensure_ascii=False), encoding="utf-8"
        )
        activate_artifact_target_state(self._project_dir, bump_schema=False)

    def load_project(self, _name: str) -> dict[str, Any]:
        return self.project_payload

    def load_script(self, _name: str, _filename: str) -> dict[str, Any]:
        return self.script_payload


class TestStoryboardGateSkipsEmptyBatches:
    """No tasks means no audio gates; legacy audio-off settings must not turn no-op calls into errors."""

    async def test_all_items_filtered_out_still_fails_without_consulting_the_gate(self, tmp_path, monkeypatch):
        """All missing storyboards report per-ID unavailable inputs, not audio-switch conflicts."""
        project_dir = tmp_path / "demo"
        (project_dir / "storyboards").mkdir(parents=True)
        (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
        ctx = ToolHarness(project_name="demo", data_root=tmp_path, pm=_EpisodePM(project_dir, with_storyboard=False))
        rejected: list[str] = []

        async def _reject(_project, generation_type, **_kwargs):
            rejected.append(generation_type)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        enqueue = AsyncMock(return_value=([], []))

        out = await run_generate_videos(ctx, _EPISODE_1, batch_waiter=enqueue)

        assert rejected == []
        result = read_generation_result(out)
        assert result.blocked == ["E1S01"]
        assert [item.problem.code for item in result.items if item.problem is not None] == [
            "generation_unit_input_unusable"
        ]


class TestStoryboardGateEntersAdmission:
    """Report audio conflicts and other gaps together before task creation, not after confirmation."""

    @pytest.fixture(autouse=True)
    def _no_active_tasks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Admission checks active tasks; unit tests avoid real DBs by always returning none."""

        async def _none(**_kwargs):
            return []

        use_active_tasks_lookup(monkeypatch, _none)

    def _ctx(self, tmp_path: Path) -> ToolHarness:
        project_dir = tmp_path / "demo"
        (project_dir / "storyboards").mkdir(parents=True)
        (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
        return ToolHarness(
            project_name="demo",
            data_root=tmp_path,
            pm=_EpisodePM(project_dir, with_storyboard=True),
        )

    async def test_audio_switch_conflict_is_reported_as_a_blocked_admission(
        self, tmp_path, set_admission_video_request_facts
    ):
        enqueue = AsyncMock(return_value=([], []))
        set_admission_video_request_facts(make_video_request_facts(requested_generate_audio=False))

        out = await run_generate_videos(self._ctx(tmp_path), _EPISODE_1, batch_waiter=enqueue)

        assert out.value["batch_admission"]["decision"] == "blocked"
        enqueue.assert_not_awaited()
        assert _admission_codes(out) == {"E1S01": ["video_audio_switch_not_supported"]}

    async def test_audio_switch_conflict_renders_with_display_names(self, tmp_path, set_admission_video_request_facts):
        """Conflict problems include actual provider/model identities for catalog-rendered names, without placeholders/internal-ID display."""
        set_admission_video_request_facts(
            make_video_request_facts(provider_id="custom-7", model_id="shared-model", requested_generate_audio=False)
        )

        out = await run_generate_videos(self._ctx(tmp_path), _EPISODE_1, batch_waiter=AsyncMock(return_value=([], [])))

        assert isinstance(out.value, dict)
        (problem,) = [
            problem
            for unit in out.value["batch_admission"]["units"]
            for problem in unit["problems"]
            if problem["code"] == "video_audio_switch_not_supported"
        ]
        # Another provider names the same upstream model differently; provider identity is required for correct name lookup.
        models = {("custom-7", "shared-model"): "甲网关模型", ("custom-8", "shared-model"): "乙网关模型"}
        for locale in SUPPORTED_LOCALES:
            names = make_display_names(locale, providers={"custom-7": "我的网关"}, models=models)
            rendered = render_message(
                problem["code"],
                problem["params"],
                lambda key, locale=locale, **params: _(key, locale=locale, **params),
                names,
            )
            assert "{" not in rendered
            assert "custom-7" not in rendered
            assert "shared-model" not in rendered
            assert "我的网关" in rendered
            assert "甲网关模型" in rendered

    async def test_a_blank_prompt_is_refused_per_unit(self, tmp_path, monkeypatch):
        """Blank prompts cannot construct TaskSpec; report their specific codes rather than generic whole-batch errors."""

        async def _allow(_project, _generation_type, **_kwargs):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        ctx = self._ctx(tmp_path)
        ctx.pm.script_payload["segments"][0]["video_prompt"] = "   "

        out = await run_generate_videos(ctx, _EPISODE_1, batch_waiter=enqueue)

        enqueue.assert_not_awaited()
        assert out.value["batch_admission"]["decision"] == "blocked"
        assert _admission_codes(out) == {"E1S01": ["generation_unit_request_invalid"]}

    async def test_the_audio_conflict_joins_the_other_problems_of_the_same_unit(
        self, tmp_path, set_admission_video_request_facts
    ):
        """Audio conflicts and other same-unit gaps share one issue, letting users see all at once instead of fixing sequential surprises."""
        enqueue = AsyncMock(return_value=([], []))
        set_admission_video_request_facts(make_video_request_facts(requested_generate_audio=False))
        ctx = self._ctx(tmp_path)
        ctx.pm.script_payload["segments"][0]["characters_in_segment"] = ["无名氏"]

        out = await run_generate_videos(ctx, _EPISODE_1, batch_waiter=enqueue)

        enqueue.assert_not_awaited()
        assert _admission_codes(out) == {"E1S01": ["reference_asset_unregistered", "video_audio_switch_not_supported"]}
