"""Clip storyboard references to image-backend limits before numbering; actual counts match declarations while bases retain full assembled sets."""

import base64
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.visual_artifact_provenance import VisualReference, build_storyboard_image_visual_basis
from lib.backends.image_backends.grok import GrokImageBackend
from lib.backends.image_backends.openrouter import OpenRouterImageBackend
from lib.backends.image_backends.vidu import ViduImageBackend
from server.services.tasks import generation_context, generation_tasks
from tests.fakes import (
    bind_session_factory,
    hook_claim_recheck,
    use_backend_assembler,
    use_generation_context,
    use_project_manager,
)
from tests.http_capture import capture_http
from tests.integration.server.services.tasks.generation_tasks_support import (
    _EIGHT_REFERENCE_CHARACTERS,
    FakeGenerator,
    _FakePM,
    fake_resolve_ctx,
    persist_active_fake_project,
    pm_with_eight_references,
    prepare_files,
)

ITEM_ID = "E1S02"
STORYBOARD_PAYLOAD = {"script_file": "episode_1.json", "prompt": "queued prompt"}
#: Default fake_resolve_ctx image-lane backend_model; warnings name it rather than registry provider display names.
FAKE_BACKEND_MODEL = "gpt-image-2"
CLAMPED_PROMPT = (
    "Style: Anime\n"
    "Visual style: cinematic\n"
    "Reference_Images: '图1, 图2, 图3, 图4, 图5, 图6: character reference images; 图7: scene reference images.'\n"
    "Scene: 图1握着玉佩立在图7门口\n"
    "Composition:\n  shot_type: Medium Shot\n  lighting: 暖光\n  ambiance: 薄雾\n"
    "Avoid: watermarks, extra text, logos"
)


def _expected_basis(project_path: Path, pm: _FakePM):
    """Full assembly basis: six character sheets + scene + prop, identical to target-state reconstruction from script entries."""
    references = [
        VisualReference(
            path=project_path / "characters" / f"{name}.png",
            role="asset_sheet",
            logical_type="character",
            logical_id=name,
            kind="sheet",
        )
        for name in ["Alice", *_EIGHT_REFERENCE_CHARACTERS]
    ]
    references.append(
        VisualReference(
            path=project_path / "scenes" / "祠堂.png",
            role="asset_sheet",
            logical_type="scene",
            logical_id="祠堂",
            kind="sheet",
        )
    )
    references.append(
        VisualReference(
            path=project_path / "props" / "玉佩.png",
            role="asset_sheet",
            logical_type="prop",
            logical_id="玉佩",
            kind="sheet",
        )
    )
    return build_storyboard_image_visual_basis(
        resource_id=ITEM_ID,
        image_prompt=pm.script["segments"][1]["image_prompt"],
        style="Anime",
        style_description="cinematic",
        aspect_ratio="9:16",
        references=references,
    )


def _patch_execution(monkeypatch, pm: _FakePM, generator: FakeGenerator, *, limit: int | None) -> None:
    use_project_manager(monkeypatch, pm)
    use_generation_context(monkeypatch, fake_resolve_ctx(generator, image_max_reference_images=limit))


async def _run_storyboard(generator: FakeGenerator) -> tuple[dict, dict]:
    """Return ``(任务结果, 供应商收到的调用)``."""
    result = await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)
    return result, generator.image_calls[0]


class TestStoryboardReferenceClamping:
    async def test_over_limit_references_are_dropped_before_numbering(self, tmp_path, monkeypatch):
        """Vidu limit seven: send seven, declarations end at 图7, eighth reference reverts to its bare name."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)

        result, call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 7
        # Registered basis still includes all eight images: provider limits are outside bases; the clipped prop remains an artifact dependency.
        entry = ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_storyboard(1, ITEM_ID))
        assert entry is not None
        assert entry.basis_digest == _expected_basis(project_path, pm).digest
        assert call["prompt"] == CLAMPED_PROMPT
        # Users and Agent must know discarded references: task results carry warnings shaped like reference-to-video warnings.
        assert result["warnings"] == [
            {"key": "ref_too_many_images", "params": {"count": 8, "model": FAKE_BACKEND_MODEL, "max_count": 7}}
        ]

    async def test_clamped_storyboard_is_current_against_the_target_state(self, tmp_path, monkeypatch):
        """Clipping affects outgoing images/numbering only: registered storyboard remains current against reconstructed target bases, never stale due to clipping."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)

        await _run_storyboard(generator)

        # Fake PM does not persist storyboard pointers; persist formal post-commit shape so planners can reconstruct entries.
        pm.script["segments"][1]["generated_assets"] = {"storyboard_image": f"storyboards/scene_{ITEM_ID}.png"}
        persist_active_fake_project(pm, register_script=False)
        resolver = active_artifact_currency_resolver(project_path, pm.load_project("demo"))
        comparison = resolver.compare(
            ArtifactKey.episode_storyboard(1, ITEM_ID), artifact_path=f"storyboards/scene_{ITEM_ID}.png"
        )
        assert comparison.status is ArtifactStatus.CURRENT

    async def test_limit_above_the_assembled_count_changes_nothing(self, tmp_path, monkeypatch):
        """OpenAI limit sixteen: send all eight, numbering through 图8."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=16)

        result, call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 8
        assert "图7: scene reference images; 图8: prop reference images." in call["prompt"]
        assert "图1握着图8立在图7门口" in call["prompt"]
        # Without clipping, omit warning keys entirely so users see no empty notice.
        assert "warnings" not in result

    async def test_backend_without_a_declared_limit_never_clamps(self, tmp_path, monkeypatch):
        """Without declared entry/backend limits (unlisted), do not clip; text equals the sufficient-limit case."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=None)

        _result, call = await _run_storyboard(generator)

        assert len(call["reference_images"]) == 8
        assert "图8: prop reference images." in call["prompt"]

    async def test_a_dropped_reference_changing_before_submit_still_aborts(self, tmp_path, monkeypatch):
        """Clipped prop sheets remain basis dependencies: revoking their registration before submit still blocks; provider receives nothing."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)
        hook_claim_recheck(
            monkeypatch,
            before=lambda: ProjectArtifactManifestAdapter(project_path).delete_entry(
                ArtifactKey.asset_sheet("prop", "玉佩")
            ),
        )

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        assert generator.image_calls == []

    async def test_a_kept_reference_changing_before_submit_still_aborts(self, tmp_path, monkeypatch):
        """Revoking the still-outgoing scene image 图7 before submission also blocks recheck; provider receives nothing."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        generator = FakeGenerator(project_path)
        _patch_execution(monkeypatch, pm, generator, limit=7)
        hook_claim_recheck(
            monkeypatch,
            before=lambda: ProjectArtifactManifestAdapter(project_path).delete_entry(
                ArtifactKey.asset_sheet("scene", "祠堂")
            ),
        )

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        assert generator.image_calls == []


class TestLimitComesFromTheResolvedBackend:
    """Keep image lane real: actual resolve_generation_context constructs Vidu backend, which declares limits."""

    @pytest.fixture
    async def vidu_lane(self, db_factory, monkeypatch):
        bind_session_factory(monkeypatch, db_factory)
        generation_context.reset_backend_cache_for_tests()

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            assert (provider_id, media_type) == ("vidu", "image")
            return ViduImageBackend(api_key="k", model=model_id, base_url="https://api.vidu.com/ent/v2")

        use_backend_assembler(monkeypatch, _assemble)
        yield
        generation_context.reset_backend_cache_for_tests()

    async def test_vidu_receives_seven_images_and_a_prompt_numbered_to_match(
        self, tmp_path, monkeypatch, vidu_lane, caplog
    ):
        """Short-circuit creation with 400 (backend maps ProviderRejectedError); inspect only actual outgoing request bodies."""
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        pm.project["image_provider_i2i"] = "vidu/viduq2"
        (project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")
        use_project_manager(monkeypatch, pm)

        with capture_http() as router:
            route = router.post("https://api.vidu.com/ent/v2/reference2image").mock(
                return_value=httpx.Response(400, json={"code": 1013, "message": "nope"})
            )
            with caplog.at_level(logging.INFO), pytest.raises(httpx.HTTPStatusError):
                await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        body = json.loads(route.calls.last.request.content)
        assert len(body["images"]) == 7
        assert body["prompt"] == CLAMPED_PROMPT
        # Orchestration clips; Vidu's own fallback truncation no longer triggers on normal paths.
        assert "exceeds model=viduq2 limit 7" in caplog.text
        assert "Vidu 参考图数量" not in caplog.text


class TestGrokLaneClampsToFive:
    """Real Grok backend declares five references; send first five and return a clipping notice."""

    @pytest.fixture
    async def grok_lane(self, db_factory, monkeypatch):
        bind_session_factory(monkeypatch, db_factory)
        generation_context.reset_backend_cache_for_tests()
        client = MagicMock()
        client.image.sample = AsyncMock(
            return_value=SimpleNamespace(respect_moderation=True, url="https://grok.test/out.png")
        )

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            assert (provider_id, media_type) == ("grok", "image")
            with patch("lib.backends.image_backends.grok.create_grok_client", return_value=client):
                return GrokImageBackend(api_key="k", model=model_id)

        use_backend_assembler(monkeypatch, _assemble)
        yield client
        generation_context.reset_backend_cache_for_tests()

    async def test_grok_receives_five_images_and_the_result_warns(self, tmp_path, monkeypatch, grok_lane):
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        pm.project["image_provider_i2i"] = "grok/grok-imagine-image"
        (project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")
        use_project_manager(monkeypatch, pm)
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")

        with capture_http() as router:
            router.get("https://grok.test/out.png").mock(return_value=httpx.Response(200, content=buffer.getvalue()))
            result = await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        sent = grok_lane.image.sample.call_args.kwargs
        assert len(sent["image_urls"]) == 5
        assert "图1, 图2, 图3, 图4, 图5: character reference images" in sent["prompt"]
        assert "图6" not in sent["prompt"]
        assert result["warnings"] == [
            {"key": "ref_too_many_images", "params": {"count": 8, "model": "grok-imagine-image", "max_count": 5}}
        ]


class TestOpenRouterLaneClampsToTheCatalogLimit:
    """Real OpenRouter backend derives limits from catalog input_references; orchestration clips and numbers accordingly."""

    MODEL = "vendor/three-refs"
    BASE_URL = "https://openrouter.ai/api/v1"

    @pytest.fixture
    async def openrouter_lane(self, db_factory, monkeypatch):
        bind_session_factory(monkeypatch, db_factory)
        generation_context.reset_backend_cache_for_tests()

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            assert media_type == "image"
            return OpenRouterImageBackend(api_key="k", model=self.MODEL, base_url=self.BASE_URL)

        use_backend_assembler(monkeypatch, _assemble)
        yield
        generation_context.reset_backend_cache_for_tests()

    async def test_three_reference_model_receives_three_images_numbered_to_match(
        self, tmp_path, monkeypatch, openrouter_lane
    ):
        project_path = prepare_files(tmp_path)
        pm = pm_with_eight_references(project_path)
        pm.project["image_provider_i2i"] = "vidu/viduq2"
        (project_path / "project.json").write_text(json.dumps(pm.project, ensure_ascii=False), encoding="utf-8")
        use_project_manager(monkeypatch, pm)
        catalog = {
            "data": [
                {
                    "id": self.MODEL,
                    "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 3}},
                }
            ]
        }
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
        image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

        with capture_http() as router:
            models = router.get(f"{self.BASE_URL}/images/models").mock(return_value=httpx.Response(200, json=catalog))
            route = router.post(f"{self.BASE_URL}/images").mock(
                return_value=httpx.Response(200, json={"data": [{"b64_json": image_b64}]})
            )
            result = await generation_tasks.execute_storyboard_task("demo", ITEM_ID, STORYBOARD_PAYLOAD)

        body = json.loads(route.calls.last.request.content)
        assert len(body["input_references"]) == 3
        assert "图1, 图2, 图3: character reference images" in body["prompt"]
        assert "图4" not in body["prompt"]
        assert result["warnings"] == [
            {"key": "ref_too_many_images", "params": {"count": 8, "model": self.MODEL, "max_count": 3}}
        ]
        # Resolution/generation share one instance and one catalog lookup, keeping limits consistent and avoiding backend rejection.
        assert models.call_count == 1
