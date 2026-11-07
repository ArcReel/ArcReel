"""Tests for CostEstimationService."""

import json
from pathlib import Path

import pytest
from sqlalchemy import update

from arcreel_market_core.video_backend_contract import VideoGenerationRequest, VideoGenerationResult
from lib.backends.backend_assembly.specs import builtin_video_metered_usage
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.video_backends.ark import Seedance2VideoBackend
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.db.models.api_call import ApiCall
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.generation.video_request_facts import (
    ExecutionVideoIdentity,
    VideoRequestCostFacts,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from lib.script.reference_video.request_projection import (
    configured_reference_request_facts,
    project_reference_unit_request,
)
from server.services.admission import cost_estimation
from server.services.admission.cost_estimation import CostEstimationService, quote_video_request
from tests.catalog_support import install_catalog, with_entry_changes
from tests.factories import activate_reference_project, make_video_request_facts
from tests.fakes import fake_reference_request_facts


async def _seed_call(
    db_factory,
    project_name: str,
    call_type: str,
    model: str,
    *,
    provider: str = PROVIDER_GEMINI,
    resolution: str | None = None,
    segment_id: str | None = None,
    output_path: str | None = None,
    usage_tokens: int | None = None,
    cost_amount: float | None = None,
    currency: str | None = None,
    unpriced: bool = False,
) -> None:
    """Seed a completed call directly through UsageRepository, equivalent to old UsageTracker seeding.

    Nonempty ``cost_amount`` bypasses automatic price-table calculation and settles directly, isolating allocation/aggregation cases such as unit-to-shot costs from provider pricing configuration. Seed rows have no reported usage; absent ``cost_amount`` settles as unpriced."""
    async with db_factory() as session:
        repo = UsageRepository(session)
        cid = await repo.start_call(
            project_name=project_name,
            call_type=call_type,
            model=model,
            provider=provider,
            resolution=resolution,
            segment_id=segment_id,
        )
        await repo.finish_call(
            cid,
            status="success",
            settlement=SettlementInput(usage_tokens=usage_tokens, cost_amount=cost_amount, currency=currency),
            output_path=output_path,
        )
        if unpriced:
            # Settlement writes numeric costs only; set unpriced rows directly to NULL, matching built-in missing-unit-price settlement.
            await session.execute(
                update(ApiCall).where(ApiCall.id == cid).values(cost_amount=None, unpriced_reason="missing_rate")
            )
            await session.commit()


def _make_script(
    episode: int,
    segment_ids: list[str],
    durations: list[int],
    generated_assets_overrides: list[dict] | None = None,
) -> dict:
    """Helper to create a narration episode script dict."""
    default_assets = {"storyboard_image": None, "video_clip": None, "status": "pending"}
    segments = []
    for i, (sid, dur) in enumerate(zip(segment_ids, durations, strict=True)):
        assets = {**default_assets}
        if generated_assets_overrides and i < len(generated_assets_overrides):
            assets.update(generated_assets_overrides[i])
        segments.append(
            {
                "segment_id": sid,
                "episode": episode,
                "duration_seconds": dur,
                "segment_break": False,
                "novel_text": "text",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "medium", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "aa"},
                "generated_assets": assets,
            }
        )
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": "narration",
        "duration_seconds": sum(durations),
        "summary": "test",
        "novel": {"title": "t", "chapter": "c"},
        "segments": segments,
    }


def _make_ad_script(shot_ids: list[str], durations: list[int]) -> dict:
    """Helper to create an ad episode script dict with flat shots[]."""
    shots = []
    for sid, dur in zip(shot_ids, durations, strict=True):
        shots.append(
            {
                "shot_id": sid,
                "section": "hook",
                "duration_seconds": dur,
                "voiceover_text": "口播文案" * 10,
                "products_in_shot": [],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "medium", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "aa"},
                "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
            }
        )
    return {
        "episode": 1,
        "title": "Ad",
        "content_mode": "ad",
        "duration_seconds": sum(durations),
        "novel": {"title": "t", "chapter": "c"},
        "shots": shots,
    }


def _make_reference_video_script(episode: int, content_mode: str, unit_specs: list[tuple[str, int]]) -> dict:
    """Helper to create a reference_video episode script dict (video_units[])."""
    units = []
    for unit_id, duration in unit_specs:
        units.append(
            {
                "unit_id": unit_id,
                "text": "t",
                "duration_seconds": duration,
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
        )
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": content_mode,
        "generation_mode": "reference_video",
        "duration_seconds": sum(d for _, d in unit_specs),
        "novel": {"title": "t", "chapter": "c"},
        "video_units": units,
    }


@pytest.fixture(autouse=True)
def active_video_pricing_models(monkeypatch):
    """Cost cases verify requests/metering; lifecycle cases cover shutdown behavior."""
    data = catalog.factory_snapshot()
    for model in ("veo-3.1-generate-preview", "veo-3.1-fast-generate-preview", "veo-3.1-lite-generate-preview"):
        data = with_entry_changes(data, "gemini-aistudio", model, discontinued=False, shutdown_date=None)
    install_catalog(monkeypatch, data)


class TestCostEstimationService:
    async def test_storyboard_facts_failure_reports_each_segment_without_video_quote(
        self, db_factory, set_video_request_facts
    ):
        failure = VideoRequestFactsFailure(
            "video_supported_durations_incompatible",
            (("provider", "gemini-aistudio"), ("model", "veo-3.1-generate-preview"), ("resolution", "1080p")),
        )
        set_video_request_facts(failure)
        project = {
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
            project, {"ep1.json": _make_script(1, ["E1S001", "E1S002"], [8, 8])}, project_name="failed-facts"
        )
        for segment in result["episodes"][0]["segments"]:
            assert segment["estimate"]["video"] == {}
            assert segment["request_projection"]["allowed"] is False
            problem = segment["request_projection"]["problems"][0]
            assert (problem["code"], problem["action"], problem["params"]) == (
                failure.code,
                failure.action,
                failure.parameters(),
            )

    @pytest.mark.parametrize("generation_mode", ["storyboard", "reference_video"])
    @pytest.mark.parametrize("explicit_resolution", [False, True])
    @pytest.mark.parametrize(
        ("provider", "model", "selected_resolution", "duration"),
        [
            ("gemini-aistudio", "veo-3.1-generate-preview", "1080p", 8),
            ("grok", "grok-imagine-video-1.5", "1080p", 8),
            ("dashscope", "wan3.0-video", "1080p", 8),
            ("grok", "grok-imagine-video", "720p", 8),
            ("minimax", "MiniMax-H3", "2k", 6),
            ("minimax", "MiniMax-Hailuo-2.3", "1080p", 6),
            ("kling", "kling-v3", "4k", 8),
            ("vidu", "viduq3-turbo", "1080p", 8),
            # Video-token estimates use official conversion; reported tokens equal to that formula yield equal charges.
            ("ark", "doubao-seedance-2-0-260128", "1080p", 8),
        ],
    )
    async def test_video_estimate_uses_request_resolution_and_settlement_pricing(
        self,
        db_factory,
        generation_mode,
        explicit_resolution,
        provider,
        model,
        selected_resolution,
        duration,
    ):
        resolution = selected_resolution if explicit_resolution else None
        project = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": generation_mode,
            "video_provider_i2v": f"{provider}/{model}",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        if resolution is not None:
            project["model_settings"] = {f"{provider}/{model}": {"resolution": resolution}}
        script = (
            _make_script(1, ["E1S001"], [duration])
            if generation_mode == "storyboard"
            else _make_reference_video_script(1, "narration", [("E1U1", duration)])
        )

        result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
            project, {"ep1.json": script}, project_name="test-pricing-source"
        )

        estimate = result["episodes"][0]["segments"][0]["estimate"]["video"]
        assert estimate
        assert all(amount > 0 for amount in estimate.values())

        # Settlement receives actual outgoing requests (storyboard first frames, project video aspect ratio) and provider responses;
        # backend reports metering by usage contracts, then shares estimation's catalog lookup/calculator.
        executed = await evaluate_video_request_facts(
            project,
            route=generation_mode,
            generation_type="i2v",
            identity=ExecutionVideoIdentity(provider, model),
            resolver=ConfigResolver(db_factory),
        )
        assert isinstance(executed, VideoRequestFacts)
        request = VideoGenerationRequest(
            prompt="p",
            output_path=Path("out.mp4"),
            aspect_ratio="9:16",
            duration_seconds=duration,
            resolution=executed.resolution,
            start_image=Path("storyboard.png") if generation_mode == "storyboard" else None,
            generate_audio=executed.requested_generate_audio,
        )
        tokens = (
            Seedance2VideoBackend.estimate_video_tokens(resolution=executed.resolution, ratio="9:16", seconds=duration)
            if provider == "ark"
            else None
        )
        result_reported = VideoGenerationResult(
            video_path=Path("out.mp4"),
            provider=provider,
            model=model,
            duration_seconds=duration,
            usage_tokens=int(tokens) if tokens is not None else None,
        )
        reported = builtin_video_metered_usage(provider, model, request, result_reported)
        async with db_factory() as session:
            usage = UsageRepository(session)
            call_id = await usage.start_call(
                project_name="test-pricing-source",
                call_type="video",
                provider=provider,
                model=model,
                resolution=executed.resolution,
                duration_seconds=duration,
                generate_audio=executed.generate_audio,
                segment_id="billed",
            )
            await usage.finish_call(
                call_id,
                status="success",
                settlement=SettlementInput(usage_tokens=result_reported.usage_tokens, usage=(reported,)),
            )
            actual = await usage.get_actual_costs_by_segment("test-pricing-source")
        assert estimate == actual["billed"]["video"]

    async def test_reference_bucket_quotes_match_execution_facts_and_settlement(self, db_factory):
        i2v = "gemini-aistudio/veo-3.1-generate-preview"
        r2v = "gemini-aistudio/veo-3.1-fast-generate-preview"
        project = {
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": i2v,
            "video_provider_r2v": r2v,
            "model_settings": {i2v: {"resolution": "720p"}, r2v: {"resolution": "1080p"}},
            "characters": {"A": {"name": "A"}},
            "episodes": [{"episode": 1, "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("i2v", 8), ("r2v", 8)])
        script["video_units"][1]["text"] = "@[A] 走进房间"
        resolver = ConfigResolver(db_factory)
        result = await CostEstimationService(resolver, db_factory).compute(
            project, {"ep1.json": script}, project_name="bucket-resolutions"
        )
        segments = {segment["segment_id"]: segment for segment in result["episodes"][0]["segments"]}
        for bucket, pair, resolution, allowed in (("i2v", i2v, "720p", (4, 6, 8)), ("r2v", r2v, "1080p", (8,))):
            provider, model = pair.split("/")
            executed = await evaluate_video_request_facts(
                project,
                route="reference_video",
                generation_type=bucket,
                identity=ExecutionVideoIdentity(provider, model),
                resolver=resolver,
            )
            assert isinstance(executed, VideoRequestFacts)
            assert executed.resolution == resolution
            assert executed.allowed_durations == allowed
            assert segments[bucket]["request_projection"]["capability"] == bucket
            async with db_factory() as session:
                usage = UsageRepository(session)
                call_id = await usage.start_call(
                    project_name="bucket-resolutions",
                    call_type="video",
                    provider=executed.provider_id,
                    model=executed.model_id,
                    resolution=executed.resolution,
                    duration_seconds=8,
                    generate_audio=executed.generate_audio,
                    segment_id=bucket,
                )
                reported = builtin_video_metered_usage(
                    executed.provider_id,
                    executed.model_id,
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=Path("out.mp4"),
                        aspect_ratio="9:16",
                        duration_seconds=8,
                        resolution=executed.resolution,
                        reference_images=[Path("characters/A.png")] if bucket == "r2v" else None,
                        generate_audio=executed.requested_generate_audio,
                    ),
                    VideoGenerationResult(
                        video_path=Path("out.mp4"),
                        provider=executed.provider_id,
                        model=executed.model_id,
                        duration_seconds=8,
                    ),
                )
                await usage.finish_call(call_id, status="success", settlement=SettlementInput(usage=(reported,)))
                actual = await usage.get_actual_costs_by_segment("bucket-resolutions")
            assert segments[bucket]["estimate"]["video"], segments[bucket]["request_projection"]
            assert segments[bucket]["estimate"]["video"] == actual[bucket]["video"]

    async def test_reference_estimate_buckets_an_unclaimed_sheet_like_admission_and_execution(
        self, db_factory, tmp_path: Path, set_video_request_facts
    ):
        """Quotes share admission/execution criteria: unclaimed on-disk images select i2v but receive no quote because references/capabilities disagree; claimed units receive r2v quotes."""
        set_video_request_facts(
            {
                "i2v": make_video_request_facts(
                    route="reference_video", generation_type="i2v", model_id="veo-3.1-generate-preview"
                ),
                "r2v": make_video_request_facts(
                    route="reference_video",
                    generation_type="r2v",
                    model_id="veo-3.1-fast-generate-preview",
                    supported_durations=(8,),
                    allowed_durations=(8,),
                ),
            }
        )
        (tmp_path / "characters").mkdir()
        (tmp_path / "characters" / "张三.png").write_bytes(b"image")
        project = activate_reference_project(
            tmp_path, {"characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}}}
        )
        project["characters"]["李四"] = {"description": "y", "character_sheet": "characters/李四.png"}
        (tmp_path / "characters" / "李四.png").write_bytes(b"image")
        (tmp_path / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        script = _make_reference_video_script(1, "narration", [("E1U1", 8), ("E1U2", 8)])
        script["video_units"][0]["text"] = "@[张三] 推门"
        script["video_units"][1]["text"] = "@[李四] 回头"
        resolver = ConfigResolver(db_factory)

        result = await CostEstimationService(resolver, db_factory, project_path=tmp_path).compute(
            project, {"scripts/episode_1.json": script}, project_name="unclaimed-sheet"
        )
        lookup = configured_reference_request_facts(project, resolver)
        projections = {
            unit["unit_id"]: await project_reference_unit_request(
                project=project, script=script, unit=unit, project_path=tmp_path, request_facts_lookup=lookup
            )
            for unit in script["video_units"]
        }

        segments = {segment["segment_id"]: segment for segment in result["episodes"][0]["segments"]}
        claimed, unclaimed = segments["E1U1"]["request_projection"], segments["E1U2"]["request_projection"]
        assert claimed["capability"] == projections["E1U1"].hydrated_generation_type == "r2v"
        assert unclaimed["capability"] == projections["E1U2"].hydrated_generation_type == "i2v"
        assert (
            [problem["code"] for problem in unclaimed["problems"]]
            == [problem.code for problem in projections["E1U2"].blocking_problems]
            == ["reference_asset_missing", "reference_capability_changed"]
        )
        assert segments["E1U2"]["estimate"]["video"] == {}
        assert projections["E1U1"].cost is not None
        quote = await quote_video_request(projections["E1U1"].cost, db_factory)
        assert quote is not None
        assert quote.amount is not None
        assert quote.amount > 0
        assert segments["E1U1"]["estimate"]["video"] == {quote.currency: quote.amount}

    async def test_shared_video_quote_exposes_exact_amount_currency_and_request_coordinates(self, db_factory):
        quote = await quote_video_request(
            VideoRequestCostFacts(
                request_facts=make_video_request_facts(
                    provider_id="grok", model_id="grok-imagine-video", resolution="720p", generate_audio=True
                ),
                duration_seconds=8,
                aspect_ratio="16:9",
            ),
            db_factory,
        )

        assert quote is not None
        assert quote.to_payload() == {
            "amount": pytest.approx(0.56),
            "currency": "USD",
            "provider_id": "grok",
            "model_id": "grok-imagine-video",
            "request_duration_seconds": 8,
            "unpriced_reason": None,
        }

    @pytest.mark.parametrize(
        ("has_audio_track", "controllable", "requested", "expected"),
        [
            (
                True,
                False,
                False,
                True,
            ),  # Always audible (AI Studio catalog model): upstream produces audible output normally.
            (
                True,
                True,
                False,
                False,
            ),  # Switchable (Vertex, including user-added models with relaxed audio constraints): follow switch.
            (False, False, True, True),  # Always silent: forward switch unchanged; backend meters by model.
            (True, True, True, True),
        ],
    )
    def test_provider_request_audio_is_what_execution_sends(self, has_audio_track, controllable, requested, expected):
        """Estimated reconstructed requests carry execution's actual audio switch, which backends use for metering."""
        facts = make_video_request_facts(
            provider_id="gemini-vertex",
            requested_generate_audio=requested,
            has_audio_track=has_audio_track,
            audio_switch_controllable=controllable,
        )

        request = VideoRequestCostFacts(facts, 8, aspect_ratio="16:9").provider_request()

        assert request.generate_audio is expected

    async def test_ark_video_tokens_are_estimated_by_the_official_formula(self, db_factory):
        """Seedance 2.0 estimates video tokens as (input-video seconds + output seconds) × width × height × 24 / 1024, then applies price tables."""
        quote = await quote_video_request(
            VideoRequestCostFacts(
                request_facts=make_video_request_facts(
                    provider_id="ark", model_id="doubao-seedance-2-0-260128", resolution="720p"
                ),
                duration_seconds=5,
                aspect_ratio="16:9",
                has_first_frame=True,
            ),
            db_factory,
        )

        assert quote is not None
        # 5 × 1280 × 720 × 24 / 1024 = 108000 tokens; 720p without input video costs 46 CNY / million tokens.
        assert (quote.amount, quote.currency, quote.unpriced_reason) == (pytest.approx(4.968), "CNY", None)

    async def test_video_tokens_without_an_estimate_are_unpriced_not_guessed(self, db_factory):
        """Upstream-auto aspect ratios leave pixel counts unknown, so video-token estimates are unpriced; never guess fixed tokens per second."""
        quote = await quote_video_request(
            VideoRequestCostFacts(
                request_facts=make_video_request_facts(
                    provider_id="ark", model_id="doubao-seedance-2-0-260128", resolution="720p"
                ),
                duration_seconds=5,
                aspect_ratio="adaptive",
            ),
            db_factory,
        )

        assert quote is not None
        assert (quote.amount, quote.currency, quote.unpriced_reason) == (None, None, "no_usage_estimate")

    async def test_estimate_single_episode(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S002"], [6, 8])}

        result = await service.compute(project_data, scripts, project_name="test")

        assert len(result["episodes"]) == 1
        ep = result["episodes"][0]
        assert len(ep["segments"]) == 2
        for seg in ep["segments"]:
            assert "image" in seg["estimate"]
            assert "video" in seg["estimate"]
            for cost in seg["estimate"].values():
                assert isinstance(cost, dict)
                assert all(isinstance(v, (int, float)) for v in cost.values())

    async def test_actual_costs_included(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image",
            resolution="1K",
            cost_amount=0.067,
            currency="USD",
            segment_id="E1S001",
            output_path="a.png",
        )

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="proj")

        seg = result["episodes"][0]["segments"][0]
        assert seg["actual"]["image"]["USD"] == pytest.approx(0.067)
        assert "unassigned" not in result["episodes"][0]["totals"]["actual"]
        assert "unassigned" not in result["project_totals"]["actual"]

    async def test_duplicate_unit_id_does_not_double_count_actual_cost(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "duplicate-unit",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=1.25,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {
            "ep1.json": _make_reference_video_script(
                1,
                "narration",
                [("E1U1", 5), ("E1U1", 5)],
            )
        }

        result = await service.compute(project_data, scripts, project_name="duplicate-unit")

        assert result["episodes"][0]["totals"]["actual"]["video"]["USD"] == pytest.approx(1.25)
        assert result["project_totals"]["actual"]["video"]["USD"] == pytest.approx(1.25)

    async def test_deleted_unit_actual_cost_is_reconciled_as_unassigned(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "deleted-unit",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=1.25,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U2", 5)])}

        result = await service.compute(project_data, scripts, project_name="deleted-unit")

        episode = result["episodes"][0]
        assert episode["totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.25)
        assert result["project_totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.25)

    async def test_replaced_script_reconciles_all_historical_actual_costs(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        for unit_id, amount, call_type in (
            ("E1U1", 0.4, "image"),
            ("E1U2", 1.6, "video"),
            ("E1U3", 0.2, "audio"),
        ):
            await _seed_call(
                db_factory,
                "replaced-script",
                call_type,
                "historical-model",
                segment_id=unit_id,
                cost_amount=amount,
                currency="USD",
            )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U9", 5)])}

        result = await service.compute(project_data, scripts, project_name="replaced-script")

        episode = result["episodes"][0]
        assert episode["totals"]["actual"]["unassigned"]["USD"] == pytest.approx(2.2)
        assert result["project_totals"]["actual"]["unassigned"]["USD"] == pytest.approx(2.2)

    async def test_missing_script_episode_still_gets_history_row(self, db_factory):
        """Missing-script episodes contribute no estimates, but retain historical spending on their episode rows."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "missing-script",
            "video",
            "historical-model",
            segment_id="E2U1",
            cost_amount=1.5,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [
                {"episode": 1, "title": "Ep1", "script_file": "ep1.json"},
                {"episode": 2, "title": "Ep2", "script_file": "ep2.json"},
            ],
        }
        # Missing scripts/ep2.json simulates lost script files with episode metadata retained in project.json.
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        result = await service.compute(project_data, scripts, project_name="missing-script")

        episodes = result["episodes"]
        assert [ep["episode"] for ep in episodes] == [1, 2]
        assert episodes[1]["title"] == "Ep2"
        assert episodes[1]["segments"] == []
        assert episodes[1]["totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.5)
        assert result["project_totals"]["actual"]["unassigned"]["USD"] == pytest.approx(1.5)

    async def test_null_segment_history_counts_as_unassigned(self, db_factory):
        """Historical video/audio with empty segment_id and unattributable images still represent real spending."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "null-segment",
            "video",
            "historical-model",
            segment_id=None,
            cost_amount=2.0,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "null-segment",
            "audio",
            "historical-model",
            segment_id=None,
            cost_amount=0.5,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "null-segment",
            "image",
            "historical-model",
            segment_id=None,
            output_path="misc/legacy.png",
            cost_amount=0.3,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "null-segment",
            "image",
            "historical-model",
            segment_id=None,
            output_path="characters/hero.png",
            cost_amount=0.7,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        result = await service.compute(project_data, scripts, project_name="null-segment")

        project_actual = result["project_totals"]["actual"]
        # Asset images group by type; other images + video + audio are unattributed.
        assert project_actual["characters"]["USD"] == pytest.approx(0.7)
        assert project_actual["unassigned"]["USD"] == pytest.approx(2.8)
        assert "unassigned" not in result["episodes"][0]["totals"]["actual"]

    async def test_unit_id_named_like_project_sentinel_is_not_double_counted(self, db_factory):
        """If script unit_id equals a sentinel-key literal, count its spending once; sentinel keys never share value space with real IDs."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        await _seed_call(
            db_factory,
            "sentinel-unit",
            "video",
            "historical-model",
            segment_id="__project__",
            cost_amount=3.0,
            currency="USD",
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("__project__", 5)])}

        result = await service.compute(project_data, scripts, project_name="sentinel-unit")

        project_actual = result["project_totals"]["actual"]
        assert project_actual["video"]["USD"] == pytest.approx(3.0)
        assert "unassigned" not in project_actual

    async def test_grid_actual_costs_apportioned_to_scenes(self, db_factory):
        """Grid actual cost should be split evenly among scenes sharing the grid_id."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_abc123"
        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]  # 9 scenes

        # Record grid image API call
        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image",
            resolution="2K",
            cost_amount=0.101,
            currency="USD",
            segment_id=grid_id,
            output_path="g.png",
        )

        # All 9 scenes reference the same grid_id
        overrides = [{"grid_id": grid_id, "grid_cell_index": i} for i in range(9)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj")

        # Each scene should get 1/9 of the grid cost
        expected_per_scene = round(0.101 / 9, 6)
        for seg in result["episodes"][0]["segments"]:
            assert seg["actual"]["image"]["USD"] == pytest.approx(expected_per_scene, abs=1e-5)

        # Episode total should equal the full grid cost
        ep_total_image = result["episodes"][0]["totals"]["actual"].get("image", {})
        assert ep_total_image.get("USD", 0) == pytest.approx(0.101, abs=1e-4)

        # Project totals should NOT have a separate "grid" bucket
        assert "grid" not in result["project_totals"]["actual"]
        # But should have the cost under "image"
        assert result["project_totals"]["actual"]["image"]["USD"] == pytest.approx(0.101, abs=1e-4)

    async def test_grid_duplicate_ids_each_claim_own_share(self, db_factory):
        """A grid covering two same-ID entries (supported by ADR 0053) allocates each its own equal share rather than duplicating paid grid totals."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_dup"
        await _seed_call(
            db_factory,
            "proj-dup",
            "image",
            "historical-model",
            segment_id=grid_id,
            cost_amount=1.0,
            currency="USD",
        )

        overrides = [
            {"grid_id": grid_id, "grid_cell_index": 0},
            {"grid_id": grid_id, "grid_cell_index": 1},
        ]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S001"], [6, 6], generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj-dup")

        segments = result["episodes"][0]["segments"]
        assert len(segments) == 2
        for seg in segments:
            assert seg["segment_id"] == "E1S001"
            assert seg["actual"]["image"]["USD"] == pytest.approx(0.5)

        ep_total_image = result["episodes"][0]["totals"]["actual"].get("image", {})
        assert ep_total_image.get("USD", 0) == pytest.approx(1.0)
        assert result["project_totals"]["actual"]["image"]["USD"] == pytest.approx(1.0)

    async def test_grid_duplicate_ids_across_multiple_grids(self, db_factory):
        """If one ID occurs three times in a grid and also in another grid, each entry receives only its own grid's share."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        for grid_id, amount in (("grid_a", 0.9), ("grid_b", 1.0)):
            await _seed_call(
                db_factory,
                "proj-multi",
                "image",
                "historical-model",
                segment_id=grid_id,
                cost_amount=amount,
                currency="USD",
            )

        overrides = [
            {"grid_id": "grid_a", "grid_cell_index": 0},
            {"grid_id": "grid_a", "grid_cell_index": 1},
            {"grid_id": "grid_a", "grid_cell_index": 2},
            {"grid_id": "grid_b", "grid_cell_index": 0},
            {"grid_id": "grid_b", "grid_cell_index": 1},
        ]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        seg_ids = ["E1S001", "E1S001", "E1S001", "E1S001", "E1S002"]
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 5, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj-multi")

        per_scene_costs = [seg["actual"]["image"]["USD"] for seg in result["episodes"][0]["segments"]]
        assert per_scene_costs == pytest.approx([0.3, 0.3, 0.3, 0.5, 0.5])
        assert result["project_totals"]["actual"]["image"]["USD"] == pytest.approx(1.9)

    async def test_grid_actual_split_remainder_sums_exactly(self, db_factory):
        """After dividing an indivisible paid grid cost (USD 0.101 across nine shares), shares sum exactly to the frozen paid amount."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_remainder"
        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]  # 9 scenes

        await _seed_call(
            db_factory,
            "proj-rem",
            "image",
            "historical-model",
            segment_id=grid_id,
            cost_amount=0.101,
            currency="USD",
        )

        overrides = [{"grid_id": grid_id, "grid_cell_index": i} for i in range(9)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj-rem")

        per_scene_costs = [seg["actual"]["image"]["USD"] for seg in result["episodes"][0]["segments"]]
        assert sum(per_scene_costs) == pytest.approx(0.101, abs=1e-9)

    async def test_claimed_key_keeps_unconsumed_cost_types_as_unassigned(self, db_factory):
        """Claims are granular by (accounting key, type): grid keys consume image only; same-key video remains unattributed."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_mixed"
        seg_ids = ["E1S001", "E1S002"]
        await _seed_call(
            db_factory,
            "mixed-key",
            "image",
            "historical-model",
            segment_id=grid_id,
            cost_amount=0.6,
            currency="USD",
        )
        await _seed_call(
            db_factory,
            "mixed-key",
            "video",
            "historical-model",
            segment_id=grid_id,
            cost_amount=1.4,
            currency="USD",
        )

        overrides = [{"grid_id": grid_id, "grid_cell_index": i} for i in range(2)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6, 6], generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="mixed-key")

        project_actual = result["project_totals"]["actual"]
        assert project_actual["image"]["USD"] == pytest.approx(0.6)
        assert project_actual["unassigned"]["USD"] == pytest.approx(1.4)

    async def test_grid_partial_generation_some_without_grid_id(self, db_factory):
        """Scenes without grid_id should have empty actual image cost."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        grid_id = "grid_partial"
        seg_ids = [f"E1S{i:03d}" for i in range(1, 6)]  # 5 scenes

        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image",
            resolution="2K",
            cost_amount=0.101,
            currency="USD",
            segment_id=grid_id,
            output_path="g.png",
        )

        # Only first 3 scenes have grid_id
        overrides = [
            {"grid_id": grid_id, "grid_cell_index": 0},
            {"grid_id": grid_id, "grid_cell_index": 1},
            {"grid_id": grid_id, "grid_cell_index": 2},
            {},  # no grid_id
            {},  # no grid_id
        ]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 5, generated_assets_overrides=overrides)}

        result = await service.compute(project_data, scripts, project_name="proj")

        segments = result["episodes"][0]["segments"]
        expected = round(0.101 / 3, 6)
        for seg in segments[:3]:
            assert seg["actual"]["image"]["USD"] == pytest.approx(expected, abs=1e-5)
        for seg in segments[3:]:
            assert seg["actual"]["image"] == {}

    async def test_single_mode_unaffected_by_grid_logic(self, db_factory):
        """Single generation mode should be completely unaffected by grid apportionment."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "proj",
            "image",
            "gemini-3.1-flash-image",
            resolution="1K",
            cost_amount=0.067,
            currency="USD",
            segment_id="E1S001",
            output_path="a.png",
        )

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "single",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S002"], [6, 8])}

        result = await service.compute(project_data, scripts, project_name="proj")

        seg1 = result["episodes"][0]["segments"][0]
        assert seg1["actual"]["image"]["USD"] == pytest.approx(0.067)
        seg2 = result["episodes"][0]["segments"][1]
        assert seg2["actual"]["image"] == {}

    async def test_grid_estimate_count_follows_4k_gate(self, db_factory, monkeypatch):
        """Estimated grid counts share execution's 4K gate: twelve scenes fit one 4×4 at 4K but require two capped 3×3 grids otherwise, doubling estimated totals.

        Use resolution-independent per-image pricing to separate count changes from tier-price changes."""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        from server.services.admission import cost_estimation as ce

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "img",
                        "display_name": "Img",
                        "endpoint": "openai-images",
                        # Per-image pricing independent of resolution: Grok Imagine 0.02 USD/image.
                        "entry_binding": "xai/grok-imagine-image",
                    },
                ],
            )
            await session.commit()

        seg_ids = [f"E1S{i:03d}" for i in range(1, 13)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "image_provider_t2i": "custom-1/img",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 12)}

        async def _estimated_image_total(resolution: str | None) -> float:
            async def _resolution(_r, _project):
                return resolution

            monkeypatch.setattr(ce, "resolve_image_resolution", _resolution)
            service = CostEstimationService(ConfigResolver(db_factory), db_factory)
            result = await service.compute(project_data, scripts, project_name="proj")
            return sum(seg["estimate"]["image"]["USD"] for seg in result["episodes"][0]["segments"])

        total_4k = await _estimated_image_total("4K")
        assert total_4k > 0
        # Twelve shares each round(…, 6); aggregate rounding error for twice the total is at most 18e-6.
        assert await _estimated_image_total("2K") == pytest.approx(total_4k * 2, abs=2e-5)
        assert await _estimated_image_total(None) == pytest.approx(total_4k * 2, abs=2e-5)

    async def test_grid_estimate_prices_at_resolved_resolution(self, db_factory, monkeypatch):
        """Price grids using execution-effective resolution: 4K projects use 4K prices, unconfigured projects use fallback tiers.

        Nine scenes always produce one grid_9, isolating unit-price differences from counts."""
        from lib.script.grid.layout import GRID_FALLBACK_RESOLUTION
        from server.services.admission import cost_estimation as ce

        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            # Explicitly pin resolution-tier-priced models: test pricing follows resolved tiers,
            # never depend on global image defaults coincidentally using tiered pricing.
            "image_provider_t2i": "gemini-aistudio/gemini-3.1-flash-image",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9)}

        async def _estimated_image_total(resolution: str | None) -> float:
            async def _resolution(_r, _project):
                return resolution

            monkeypatch.setattr(ce, "resolve_image_resolution", _resolution)
            service = CostEstimationService(ConfigResolver(db_factory), db_factory)
            result = await service.compute(project_data, scripts, project_name="proj")
            return sum(seg["estimate"]["image"]["USD"] for seg in result["episodes"][0]["segments"])

        total_2k = await _estimated_image_total("2K")
        total_4k = await _estimated_image_total("4K")
        assert total_2k > 0
        # This model's 4K exceeds 2K price; estimates increase rather than always pricing 2K.
        assert total_4k > total_2k
        # Unset resolution uses fallback pricing tiers from the same source as execution.
        assert await _estimated_image_total(None) == pytest.approx(
            await _estimated_image_total(GRID_FALLBACK_RESOLUTION)
        )

    async def test_grid_estimate_without_fallback_tier_in_catalog_is_priced(self, db_factory, monkeypatch):
        """For models lacking fallback tiers (viduq1 declares only 1080p), unset grid resolution is omitted and price-table defaults apply."""
        from server.services.admission import cost_estimation as ce

        async def _unset(_r, _project):
            return None

        monkeypatch.setattr(ce, "resolve_image_resolution", _unset)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "image_provider_t2i": "vidu/viduq1",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, [f"E1S{i:03d}" for i in range(1, 10)], [6] * 9)}

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        result = await service.compute(project_data, scripts, project_name="proj")

        segments = result["episodes"][0]["segments"]
        assert sum(seg["estimate"]["image"].get("CNY", 0) for seg in segments) == pytest.approx(0.625, rel=1e-4)

    async def test_plain_storyboard_estimate_prices_at_resolved_resolution(self, db_factory, monkeypatch):
        """Ordinary nongrid storyboard images likewise use execution-effective resolution; unset resolution is omitted and price-table defaults apply."""
        from server.services.admission import cost_estimation as ce

        seg_ids = [f"E1S{i:03d}" for i in range(1, 4)]
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            # Resolution-tier-priced models make tier differences observable.
            "image_provider_t2i": "gemini-aistudio/gemini-3.1-flash-image",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 3)}

        async def _estimated_image_total(resolution: str | None) -> float:
            async def _resolution(_r, _project):
                return resolution

            monkeypatch.setattr(ce, "resolve_image_resolution", _resolution)
            service = CostEstimationService(ConfigResolver(db_factory), db_factory)
            result = await service.compute(project_data, scripts, project_name="proj")
            return sum(seg["estimate"]["image"]["USD"] for seg in result["episodes"][0]["segments"])

        total_1k = await _estimated_image_total("1K")
        assert total_1k > 0
        # This model's 4K exceeds 1K price; estimates increase.
        assert await _estimated_image_total("4K") > total_1k
        # Unset resolution is omitted by execution; price-table default is 1K.
        assert await _estimated_image_total(None) == pytest.approx(total_1k)

    async def test_grid_estimate_duplicate_ids_across_groups_each_correct(self, db_factory):
        """Same-ID entries in different groups display their own group's equal-share estimates without later-group overwrites."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        # First nine entries share grid_9 (1/9 image each); entry 10 segment_break starts grid_4 alone.
        seg_ids = [f"E1S{i:03d}" for i in range(1, 9)] + ["DUP", "DUP"]
        script = _make_script(1, seg_ids, [6] * 10)
        script["segments"][9]["segment_break"] = True

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "image_provider_t2i": "gemini-aistudio/gemini-3.1-flash-image",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }

        result = await service.compute(project_data, {"ep1.json": script}, project_name="proj")
        segments = result["episodes"][0]["segments"]

        unit = segments[9]["estimate"]["image"]["USD"]  # Sole occupant of a grid pays full-image price.
        assert unit > 0
        # Include ninth entry sharing tenth entry's name: ID keys would otherwise overwrite its share with second-group full price.
        for seg in segments[:9]:
            assert seg["estimate"]["image"]["USD"] == pytest.approx(round(unit / 9, 6))

    async def test_project_level_actual_split_by_asset_type(self, db_factory):
        """Split project-level image costs by output_path prefix into characters/scenes/props."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        # Three project-level image calls under characters / scenes / props.
        for sub in ("characters", "scenes", "props"):
            await _seed_call(
                db_factory,
                "proj",
                "image",
                "gemini-3.1-flash-image",
                resolution="1K",
                cost_amount=0.067,
                currency="USD",
                output_path=f"projects/proj/{sub}/a.png",
            )

        result = await service.compute(
            {"title": "T", "content_mode": "narration", "episodes": []},
            {},
            project_name="proj",
        )
        actual = result["project_totals"]["actual"]

        assert "characters" in actual
        assert actual["characters"].get("USD", 0) > 0
        assert "scenes" in actual
        assert actual["scenes"].get("USD", 0) > 0
        assert "props" in actual
        assert actual["props"].get("USD", 0) > 0
        # Old keys must be absent.
        assert "character_and_clue" not in actual

    async def test_dirty_script_skipped_with_warning(self, db_factory, caplog):
        """One malformed episode segments=null must not cause project estimate 5xx: retain that episode with zero segments + warning, while estimating normal episodes."""
        import logging

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [
                {"episode": 1, "title": "Ep1", "script_file": "ep1.json"},
                {"episode": 2, "title": "Ep2-dirty", "script_file": "ep2.json"},
                {"episode": 3, "title": "Ep3", "script_file": "ep3.json"},
            ],
        }
        # ep2 segments=null (malformed) makes get_storyboard_items raise ScriptEditError.
        dirty_script = {
            "episode": 2,
            "title": "Dirty",
            "content_mode": "narration",
            "summary": "t",
            "novel": {"title": "t", "chapter": "c"},
            "segments": None,  # Malformed data.
        }
        scripts = {
            "ep1.json": _make_script(1, ["E1S001"], [6]),
            "ep2.json": dirty_script,
            "ep3.json": _make_script(3, ["E3S001"], [8]),
        }

        with caplog.at_level(logging.WARNING, logger="server.services.admission.cost_estimation"):
            result = await service.compute(project_data, scripts, project_name="test")

        # Normal ep1/ep3 contribute estimates; malformed ep2 remains present with empty segments.
        assert len(result["episodes"]) == 3
        eps_by_episode = {ep["episode"]: ep for ep in result["episodes"]}
        assert len(eps_by_episode[1]["segments"]) == 1
        assert len(eps_by_episode[2]["segments"]) == 0
        assert len(eps_by_episode[3]["segments"]) == 1

        # Warnings explicitly identify the skipped episode.
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ep2.json" in m for m in warnings), warnings

    async def test_audio_estimate_per_segment_by_characters(self, db_factory):
        """Narration estimates and settlement share calculators: count novel_text by entry's double-Chinese-character rule; models includes audio entries."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "narration_delivery": "use_tts",
            "audio_backend": "dashscope/qwen3-tts-flash",
            "narration_voice": "Cherry",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        script = _make_script(1, ["E1S001", "E1S002"], [6, 8])
        script["segments"][0]["novel_text"] = "字" * 100
        script["segments"][1]["novel_text"] = ""
        scripts = {"ep1.json": script}

        result = await service.compute(project_data, scripts, project_name="test")

        assert result["models"]["audio"] == {"provider": "dashscope", "model": "qwen3-tts-flash"}
        segments = result["episodes"][0]["segments"]
        # qwen3-tts-flash costs ¥0.8/10,000 characters, Chinese counted twice: 100 Chinese characters → 200 billable characters → 0.016 CNY.
        assert segments[0]["estimate"]["audio"]["CNY"] == pytest.approx(0.016)
        # Sourceless segments contribute no narration estimates.
        assert segments[1]["estimate"]["audio"] == {}
        # Both episode/project totals include audio.
        assert result["episodes"][0]["totals"]["estimate"]["audio"]["CNY"] == pytest.approx(0.016)
        assert result["project_totals"]["estimate"]["audio"]["CNY"] == pytest.approx(0.016)

    async def test_post_production_project_has_no_audio_estimate(self, db_factory):
        """Post-production narration projects produce no TTS narration estimates even with retained TTS snapshots; global defaults do not participate either."""
        from lib.config.service import ConfigService

        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "narration_delivery": "post_production",
            "audio_backend": "dashscope/qwen3-tts-flash",
            "narration_voice": "Cherry",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        script = _make_script(1, ["E1S001"], [6])
        script["segments"][0]["novel_text"] = "字" * 100

        result = await service.compute(project_data, {"ep1.json": script}, project_name="test")

        assert result["episodes"][0]["segments"][0]["estimate"]["audio"] == {}

    async def test_audio_actual_costs_included(self, db_factory):
        """Aggregate actual narration costs by segment into actual.audio."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "proj",
            "audio",
            "qwen3-tts-flash",
            provider="dashscope",
            segment_id="E1S001",
            output_path="a.wav",
            usage_tokens=100,
            cost_amount=0.008,
            currency="CNY",
        )

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="proj")

        seg = result["episodes"][0]["segments"][0]
        assert seg["actual"]["audio"]["CNY"] == pytest.approx(0.008)
        assert result["project_totals"]["actual"]["audio"]["CNY"] == pytest.approx(0.008)

    async def test_ad_storyboard_estimates_per_shot(self, db_factory):
        """Ad storyboard-image-to-video returns per-storyboard image/video estimates and aggregates episode/project totals."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "storyboard",
            "image_provider_t2i": "gemini-aistudio/gemini-3.1-flash-image",
            "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1", "E1S2"], [4, 6])}

        result = await service.compute(project_data, scripts, project_name="ad-proj")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1S1", "E1S2"]
        for seg in segments:
            assert seg["estimate"]["image"], seg
            assert seg["estimate"]["video"], seg
        # Video estimates vary by storyboard duration, never evenly split episode totals.
        assert segments[0]["estimate"]["video"] != segments[1]["estimate"]["video"]
        assert result["episodes"][0]["totals"]["estimate"]["image"]
        assert result["project_totals"]["estimate"]["video"]

    async def test_ad_voiceover_does_not_produce_audio_estimate(self, db_factory):
        """Ad spoken copy produces no narration estimates in this release, where dubbing occurs in Jianying after draft export."""
        from lib.config.service import ConfigService

        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_ad_script(["E1S1"], [4])}

        result = await service.compute(project_data, scripts, project_name="ad-proj")

        assert result["episodes"][0]["segments"][0]["estimate"]["audio"] == {}

    async def test_ad_reference_video_estimates_self_contained_units(self, db_factory):
        """Ad/short-film reference-to-video displays costs by video_units, omitting storyboard-image and separate audio estimates."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {
            "ep1.json": _make_reference_video_script(
                1,
                "ad",
                [("E1U1", 4), ("E1U2", 6)],
            )
        }

        result = await service.compute(project_data, scripts, project_name="ad-ref")

        segments = result["episodes"][0]["segments"]
        assert [segment["segment_id"] for segment in segments] == ["E1U1", "E1U2"]
        assert [segment["duration_seconds"] for segment in segments] == [4, 6]
        for segment in segments:
            assert segment["estimate"]["image"] == {}
            assert segment["estimate"]["audio"] == {}
            assert segment["estimate"]["video"]
        assert result["project_totals"]["estimate"].get("image", {}) == {}
        assert result["project_totals"]["estimate"]["video"]

    async def test_narration_reference_video_produces_nonzero_video_estimate(self, db_factory):
        """Narration + reference_video estimates must not always be zero. `get_storyboard_items` always returns empty for this generation_mode; the former loop therefore never estimated video costs.

        Units are display granularity (``Shot`` lacks independent IDs), so segment_id equals unit_id directly rather than allocating back to member shots as in ads."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])}

        result = await service.compute(project_data, scripts, project_name="narration-ref")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2"]
        assert [seg["duration_seconds"] for seg in segments] == [6, 8]
        for seg in segments:
            assert seg["estimate"]["image"] == {}
            assert seg["estimate"]["audio"] == {}
            assert seg["estimate"]["video"]
        assert result["project_totals"]["estimate"].get("image", {}) == {}
        assert result["project_totals"]["estimate"]["video"]

    async def test_drama_reference_video_actual_cost_matches_unit_id(self, db_factory):
        """Actual costs match unit_id directly without allocation: reference_videos accounting resource_id equals output segment_id."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        await _seed_call(
            db_factory,
            "drama-ref-actual",
            "video",
            "veo-3.1",
            provider="veo",
            segment_id="E1U1",
            cost_amount=0.8,
            currency="USD",
        )

        project_data = {
            "title": "Drama",
            "content_mode": "drama",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "drama", [("E1U1", 8)])}

        result = await service.compute(project_data, scripts, project_name="drama-ref-actual")

        segments = result["episodes"][0]["segments"]
        assert segments[0]["segment_id"] == "E1U1"
        assert segments[0]["actual"]["video"]["USD"] == pytest.approx(0.8)
        assert result["episodes"][0]["totals"]["actual"]["video"]["USD"] == pytest.approx(0.8)
        assert result["project_totals"]["actual"]["video"]["USD"] == pytest.approx(0.8)

    async def test_narration_reference_video_estimate_uses_rounded_up_unit_duration(self, db_factory, monkeypatch):
        """Upward tier selection estimates requested six seconds, not original five-second script duration.

        With unset resolution, Veo 3.1 omits resolution; imageless units select from full [4, 6, 8] tiers."""
        priced_durations: list[int | None] = []
        original = cost_estimation.price_builtin_call

        def _spy(*, model, media_type, requests, **kwargs):
            if media_type == "video":
                priced_durations.append(requests[0].dimensions["duration"])
            return original(model=model, media_type=media_type, requests=requests, **kwargs)

        monkeypatch.setattr(cost_estimation, "price_builtin_call", _spy)

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "gemini-aistudio/veo-3.1-generate-preview",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        rounded = await service.compute(project_data, scripts, project_name="narration-ref-round")

        seg = rounded["episodes"][0]["segments"][0]
        assert seg["segment_id"] == "E1U1"
        assert seg["duration_seconds"] == 5
        assert seg["estimate"]["video"]
        assert seg["request_projection"]["request_duration"] == 6
        assert priced_durations == [6]
        assert seg["estimate"]["video"] == rounded["episodes"][0]["totals"]["estimate"]["video"]

    async def test_reference_video_quote_is_identical_for_tts_and_post_production_projects(
        self, db_factory, monkeypatch
    ):
        """Narration delivery never changes video requests: identical units in use_tts/post_production projects select and quote identically."""
        request_facts = fake_reference_request_facts(
            durations=(4, 8, 12),
            provider_id="grok",
            model_id="grok-imagine-video",
            max_reference_images=4,
            resolution="720p",
        )
        monkeypatch.setattr(
            "server.services.admission.cost_estimation.configured_reference_request_facts",
            lambda _project, _resolver: request_facts,
        )
        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        async def _segment(narration_delivery: str) -> dict:
            project_data = {
                "title": "Narration",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "video_provider_i2v": "grok/grok-imagine-video",
                "narration_delivery": narration_delivery,
                "audio_backend": "dashscope/qwen3-tts-flash",
                "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
            }
            result = await service.compute(project_data, scripts, project_name=f"delivery-{narration_delivery}")
            return result["episodes"][0]["segments"][0]

        use_tts = await _segment("use_tts")
        post_production = await _segment("post_production")

        assert use_tts["request_projection"] == post_production["request_projection"]
        assert use_tts["request_projection"]["request_duration"] == 8
        assert "request_cost" not in use_tts["request_projection"]
        assert use_tts["estimate"]["video"] == post_production["estimate"]["video"] == {"USD": pytest.approx(0.56)}

    async def test_reference_video_estimate_blocks_when_duration_metadata_is_empty(self, db_factory, monkeypatch):
        request_facts = fake_reference_request_facts(
            durations=(),
            provider_id="kling",
            model_id="kling-v3",
            max_reference_images=4,
            resolution="1080p",
        )
        monkeypatch.setattr(
            "server.services.admission.cost_estimation.configured_reference_request_facts",
            lambda _project, _resolver: request_facts,
        )
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        result = await service.compute(project_data, scripts, project_name="missing-duration-metadata")

        segment = result["episodes"][0]["segments"][0]
        assert segment["estimate"]["video"] == {}
        assert segment["request_projection"]["request_duration"] is None
        assert segment["request_projection"]["problems"] == [
            {
                "code": "reference_supported_durations_missing",
                "blocking": True,
                "unit_id": "E1U1",
                "locations": [{"path": ["duration_seconds"], "line": None}],
                "params": {"provider": "kling", "model": "kling-v3"},
                "action": "configure_video_model",
            }
        ]

    async def test_reference_route_gives_no_estimate_for_mismatched_storyboard_script(self, db_factory):
        """Mismatched storyboard skeletons in reference-to-video projects produce no estimates because current generation mode cannot generate them.

        Execution refuses and requires replanning; estimate accurately as zero rather than inventing a storyboard route."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {
            "ep1.json": {
                "episode": 1,
                "title": "Episode 1",
                "content_mode": "narration",
                "duration_seconds": 10,
                "novel": {"title": "t", "chapter": "c"},
                "segments": [
                    {"segment_id": "E1S1", "duration": 5, "narration": "n1", "visual_prompt": "v1"},
                    {"segment_id": "E1S2", "duration": 5, "narration": "n2", "visual_prompt": "v2"},
                ],
            }
        }

        result = await service.compute(project_data, scripts, project_name="narration-mismatched")

        assert result["episodes"][0]["segments"] == []
        assert not result["project_totals"]["estimate"]["video"]

    async def test_reference_route_estimates_units_ignoring_residual_segments(self, db_factory):
        """Reference-to-video estimates units only; leftover segments do not participate. Project mode determines routing, never shape voting."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6)])
        script["segments"] = [
            {"segment_id": "E1S1", "duration_seconds": 5, "narration": "n1", "visual_prompt": "v1"},
        ]
        scripts = {"ep1.json": script}

        result = await service.compute(project_data, scripts, project_name="narration-residual-segments")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1"]
        assert result["project_totals"]["estimate"]["video"]

    async def test_storyboard_route_gives_no_estimate_for_mismatched_unit_script(self, db_factory):
        """Mismatched video_units skeletons in storyboard-image-to-video projects produce no estimates; only project generation mode determines estimates."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 6)])}

        result = await service.compute(project_data, scripts, project_name="narration-mismatched-units")

        assert result["episodes"][0]["segments"] == []
        assert not result["project_totals"]["estimate"]["video"]

    async def test_narration_reference_video_estimate_skips_unenqueueable_units(self, db_factory):
        """Blank/whitespace unit bodies cannot enqueue (``video_batch_admission.reference_unit_task_spec`` rejects blank bodies; ``TaskSpec.from_request`` also rejects empty prompts), so generate no new estimates. Retain whole units and aggregate their history: enqueueability affects future estimates, never whether they generated successfully before. Editing paid units to blank bodies must not erase historical spending from totals."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6)])
        script["video_units"].append(
            {
                "unit_id": "E1U2",
                "text": "",
                "needs_replan": True,
                "duration_seconds": 5,
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
        )
        script["video_units"].append(
            {
                "unit_id": "E1U3",
                "text": "   ",
                "duration_seconds": 5,
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
        )
        scripts = {"ep1.json": script}
        await _seed_call(
            db_factory,
            "narration-unenqueueable-units",
            "video",
            "veo-3.1-lite-generate-preview",
            segment_id="E1U2",
            cost_amount=1.23,
            currency="USD",
        )

        result = await service.compute(project_data, scripts, project_name="narration-unenqueueable-units")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2", "E1U3"]
        by_id = {seg["segment_id"]: seg for seg in segments}
        assert by_id["E1U2"]["estimate"]["video"] == {}
        assert by_id["E1U2"]["actual"]["video"] == {"USD": 1.23}
        assert by_id["E1U3"]["estimate"]["video"] == {}
        assert result["project_totals"]["actual"]["video"] == {"USD": 1.23}

    async def test_reference_video_estimate_skips_replan_unit_but_keeps_actual(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)
        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "ad", [("E1U1", 6)])
        script["video_units"][0]["needs_replan"] = True
        await _seed_call(
            db_factory,
            "ad-replan-cost",
            "video",
            "veo-3.1-lite-generate-preview",
            segment_id="E1U1",
            cost_amount=1.23,
            currency="USD",
        )

        result = await service.compute(project_data, {"ep1.json": script}, project_name="ad-replan-cost")

        segment = result["episodes"][0]["segments"][0]
        assert segment["estimate"]["video"] == {}
        assert segment["actual"]["video"] == {"USD": 1.23}
        assert result["project_totals"]["actual"]["video"] == {"USD": 1.23}

    async def test_narration_reference_video_estimate_skips_unit_with_malformed_duration(self, db_factory):
        """Agent/external edits may make ``duration_seconds`` nonnumeric (strings, lists, dicts). Video preflight (``server/media_tools/videos.py``) catches ``ValueError`` per unit and skips; estimation must match. One dirty duration must not cause project-wide 500 or suppress normal episodes/units."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8), ("E1U3", 8)])
        script["video_units"][0]["duration_seconds"] = "bad"
        script["video_units"][1]["duration_seconds"] = ["not", "a", "number"]

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-bad-duration")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2", "E1U3"]
        assert segments[0]["estimate"]["video"] == {}
        assert segments[1]["estimate"]["video"] == {}
        assert segments[2]["estimate"]["video"]

    async def test_narration_reference_video_estimate_skips_unit_with_non_string_text(self, db_factory):
        """Agent/external edits may make ``text`` a truthy nonstring such as ``true``/``1``. Type-check before prompt concatenation can raise ``TypeError`` and turn one dirty row into project-wide 500."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])
        script["video_units"][0]["text"] = True

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-non-string-text")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U1", "E1U2"]
        assert segments[0]["estimate"]["video"] == {}
        assert segments[1]["estimate"]["video"]

    async def test_narration_reference_video_estimate_rejects_non_string_unit_id(self, db_factory):
        """Agent/external edits may make ``unit_id`` truthy nonstrings (numbers/booleans). Execution (``execute_reference_video_task``) locates raw uncoerced script IDs by string resource_id; type mismatch means "unit not found". Estimation must require an original string, never str()-coerce and quote an unexecutable request."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "kling/kling-v3",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 8)])
        script["video_units"][0]["unit_id"] = 1

        result = await service.compute(project_data, {"ep1.json": script}, project_name="narration-non-str-unit-id")

        segments = result["episodes"][0]["segments"]
        assert [seg["segment_id"] for seg in segments] == ["E1U2"]
        assert segments[0]["estimate"]["video"]

    async def test_narration_reference_video_estimate_handles_token_priced_video_model(self, db_factory):
        """Token-priced video models (Ark/Seedance) need nonzero estimates too.

        Reference-video estimates supply only ``duration_seconds``; without backend-formula video-token estimation, token price tables lack usage and cannot price."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_backend": "ark",
            "target_duration": 30,
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 6)])}

        result = await service.compute(project_data, scripts, project_name="narration-ark-token")

        segments = result["episodes"][0]["segments"]
        assert segments[0]["segment_id"] == "E1U1"
        assert segments[0]["estimate"]["video"]
        assert result["project_totals"]["estimate"]["video"]

    async def test_empty_episodes(self, db_factory):
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        result = await service.compute(
            {"title": "T", "content_mode": "narration", "episodes": []}, {}, project_name="p"
        )

        assert result["episodes"] == []
        assert result["project_totals"]["estimate"] == {}

    async def test_cost_estimation_uses_t2i_default_when_split_fields_present(self, db_factory):
        """If project has only image_provider_t2i, use it for estimates; T2I is the estimation anchor."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "openai/gpt-image-1",
            "image_provider_i2i": "openai/gpt-image-1-edit",
            "episodes": [],
        }

        result = await service.compute(project_data, {}, project_name="test_split")

        # T2I field should be the canonical image cost estimation anchor
        assert result["models"]["image"]["provider"] == "openai"
        assert result["models"]["image"]["model"] == "gpt-image-1"

    async def test_cost_estimation_no_image_provider_falls_back_to_resolver(self, db_factory):
        """Without image_provider_t2i, cost_estimation never falls back itself to I2I/legacy. ProjectManager.load_project lazy-upgrades legacy fields; I2I/T2I are orthogonal slots and substitution misprices. Use resolver defaults when T2I is absent."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            # Only i2i and legacy fields exist; cost_estimation ignores them and uses resolver defaults.
            "image_provider_i2i": "openai/gpt-image-1-edit",
            "image_backend": "gemini/gemini-2.0-flash-preview-image-generation",
            "episodes": [],
        }

        result = await service.compute(project_data, {}, project_name="test_no_t2i")

        # Positive contract: missing project T2I uses resolver; empty DB has no image providers,
        # so cost_estimation catches failure and returns ("unknown", "unknown").
        # This also excludes i2i gpt-image-1-edit and legacy gemini-2.0-... slots.
        assert result["models"]["image"]["provider"] == "unknown"
        assert result["models"]["image"]["model"] == "unknown"

    async def test_cost_estimation_does_not_hide_unexpected_resolution_error(self, db_factory, monkeypatch):
        """Expose unexpected programming errors instead of treating them as unavailable capabilities and producing false quotes."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        async def _raise(self, project, provider_id, model_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(ConfigResolver, "resolve_resolution", _raise)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "episodes": [],
        }

        with pytest.raises(RuntimeError, match="boom"):
            await service.compute(project_data, {}, project_name="test_resolution_exc")

    @pytest.mark.parametrize(
        ("video_backend", "configured_generate_audio", "expected_usd"),
        [
            # Unset Veo resolution produces 720p. AI Studio has no audio-off tier and always bills audio-inclusive prices:
            # either switch setting estimates veo-3.1-lite-generate-preview 720p at 0.05 USD/s including audio.
            ("gemini-aistudio/veo-3.1-lite-generate-preview", True, 0.05),
            ("gemini-aistudio/veo-3.1-lite-generate-preview", False, 0.05),
            # Vertex has separate audio-off pricing, unaffected by AI Studio adjustment; follow switch
            # (veo-3.1-fast-generate-001 720p: audio 0.10 / silent 0.08 USD/s).
            ("gemini-vertex/veo-3.1-fast-generate-001", True, 0.10),
            ("gemini-vertex/veo-3.1-fast-generate-001", False, 0.08),
        ],
    )
    async def test_video_estimate_generate_audio_by_provider(
        self, db_factory, video_backend, configured_generate_audio, expected_usd
    ):
        """Across all (provider, audio-switch) combinations, estimates match actual provider metering. Supply switches directly in loaded project dicts to prove capability resolution does not reread project files."""

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "video_backend": video_backend,
            "video_generate_audio": configured_generate_audio,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [1])}

        result = await service.compute(project_data, scripts, project_name="test")

        seg = result["episodes"][0]["segments"][0]
        assert seg["estimate"]["video"]["USD"] == pytest.approx(expected_usd)

    async def test_kling_reference_model_estimate_uses_effective_silent_tier(self, db_factory):
        """Reference-capability models produce no human voice; even with project audio enabled, estimate backend settlement's silent tier."""
        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "video_backend": "kling/kling-video-o1",
            "video_generate_audio": True,
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }

        result = await service.compute(
            project_data, {"ep1.json": _make_script(1, ["E1S001"], [5])}, project_name="test"
        )

        # kling-video-o1 default std is silent ¥0.6/s; wrongly following project audio switch would price ¥0.8/s.
        assert result["episodes"][0]["segments"][0]["estimate"]["video"]["CNY"] == pytest.approx(3.0)

    async def test_disabled_custom_video_model_estimate_reports_unknown(self, db_factory):
        """Estimate identity matches execution: disabled project models are dangling references under task-bucket gates, never replaced by enabled provider defaults the user did not select and execution will not use (``docs/adr/0054``)."""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "disabled",
                        "display_name": "Disabled",
                        "endpoint": "openai-video",
                        "is_enabled": False,
                        "entry_binding": "kuaishou/kling-v2-6",
                        "multiplier": 30.0,
                    },
                    {
                        "model_id": "runtime",
                        "display_name": "Runtime",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                        "is_default": True,
                        "entry_binding": "kuaishou/kling-v2-6",
                    },
                ],
            )
            await session.commit()

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "video_backend": "custom-1/disabled",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }

        result = await service.compute(
            project_data, {"ep1.json": _make_script(1, ["E1S001"], [6])}, project_name="test-custom-fallback"
        )

        assert result["models"]["video"] == {"provider": "unknown", "model": "unknown"}
        # Unresolved identity falls back to generic catalog prices, neither disabled-model pricing (Kling 2.6 720p 0.3/s × 6s × 30 → 54.0)
        # nor enabled provider-default model entry prices (0.3/s × 6s → 1.8).
        video_estimate = result["episodes"][0]["segments"][0]["estimate"]["video"]
        assert video_estimate.get("CNY") != pytest.approx(1.8)
        assert video_estimate.get("CNY") != pytest.approx(54.0)

    @pytest.mark.parametrize(
        ("generation_mode", "expected_model"),
        [("storyboard", "kling-v3"), ("reference_video", "kling-v3-omni")],
    )
    async def test_estimate_resolves_video_model_by_generation_mode_bucket(
        self, db_factory, generation_mode, expected_model
    ):
        """Price by generation_mode model bucket, matching actual charges; changing modes switches price tables."""
        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": generation_mode,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "kling/kling-v3-omni",
            "episodes": [],
        }

        result = await service.compute(project_data, {}, project_name="test-video-bucket")

        assert result["models"]["video"] == {"provider": "kling", "model": expected_model}

    async def test_unit_duration_slots_come_from_the_r2v_bucket_model(self, db_factory, monkeypatch):
        """Image-backed units select tiers and prices from the same r2v model. Wrongly selecting i2v Kling [5,10] would keep five seconds and price with r2v Veo, while execution requests Veo's image-constrained eight seconds, mismatching estimated/billed quantities."""
        priced: list[tuple[str | None, int | None]] = []
        original = cost_estimation.price_builtin_call

        def _spy(*, model, media_type, requests, **kwargs):
            if media_type == "video":
                priced.append((model, requests[0].dimensions["duration"]))
            return original(model=model, media_type=media_type, requests=requests, **kwargs)

        monkeypatch.setattr(cost_estimation, "price_builtin_call", _spy)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "gemini-aistudio/veo-3.1-generate-preview",
            "characters": {"A": {"name": "A"}},
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}
        scripts["ep1.json"]["video_units"][0]["text"] = "@[A] 走进房间"

        await service.compute(project_data, scripts, project_name="r2v-duration-slots")

        assert priced == [("veo-3.1-generate-preview", 8)]

    async def test_degenerate_unit_prices_by_i2v_bucket_model(self, db_factory, monkeypatch):
        """Imageless degraded units use i2v for both tier selection and pricing. Execution avoids image-required r2v models; pricing r2v Veo eight-second tiers would disagree with actual Kling five-second charges."""
        priced: list[tuple[str | None, int | None]] = []
        original = cost_estimation.price_builtin_call

        def _spy(*, model, media_type, requests, **kwargs):
            if media_type == "video":
                priced.append((model, requests[0].dimensions["duration"]))
            return original(model=model, media_type=media_type, requests=requests, **kwargs)

        monkeypatch.setattr(cost_estimation, "price_builtin_call", _spy)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "gemini-aistudio/veo-3.1-generate-preview",
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_reference_video_script(1, "narration", [("E1U1", 5)])}

        await service.compute(project_data, scripts, project_name="i2v-degenerate-unit")

        assert priced == [("kling-v3", 5)]

    async def test_all_episodes_priced_by_the_project_route_bucket(self, db_factory, monkeypatch):
        """Project generation mode and bucket are consistent across episodes, unaffected by script shapes. In storyboard projects, ep2 video_units mismatches produce no estimate (execution refuses/replans), never drag pricing into r2v."""
        priced_models: list[str | None] = []
        original = cost_estimation.price_builtin_call

        def _spy(*, model, media_type, requests, **kwargs):
            if media_type == "video":
                priced_models.append(model)
            return original(model=model, media_type=media_type, requests=requests, **kwargs)

        monkeypatch.setattr(cost_estimation, "price_builtin_call", _spy)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Narration",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "kling/kling-v3-omni",
            "episodes": [
                {"episode": 1, "title": "", "script_file": "ep1.json"},
                {"episode": 2, "title": "", "script_file": "ep2.json"},
            ],
        }
        scripts = {
            "ep1.json": _make_script(1, ["E1S001"], [6]),
            "ep2.json": _make_reference_video_script(2, "narration", [("E2U1", 6)]),
        }

        result = await service.compute(project_data, scripts, project_name="per-episode-bucket")

        # Only ep1 with a skeleton matching generation mode contributes estimates, priced by project-mode i2v bucket.
        assert priced_models == ["kling-v3"]
        assert result["episodes"][1]["segments"] == []
        assert result["models"]["video"] == {"provider": "kling", "model": "kling-v3"}

    @pytest.mark.parametrize(
        ("text", "expected_model"),
        [("@[A] 走进房间", "kling-v3-omni"), ("空镜头", "kling-v3")],
    )
    async def test_ad_reference_route_prices_by_unit_reference_bucket(
        self, db_factory, monkeypatch, text, expected_model
    ):
        """Ad reference-to-video prices units by actually usable references: images → r2v, no images → i2v. Its episodes enqueue reference-video tasks; pricing shares execution's imageless downgrade."""
        priced_models: list[str | None] = []
        original = cost_estimation.price_builtin_call

        def _spy(*, model, media_type, requests, **kwargs):
            if media_type == "video":
                priced_models.append(model)
            return original(model=model, media_type=media_type, requests=requests, **kwargs)

        monkeypatch.setattr(cost_estimation, "price_builtin_call", _spy)

        service = CostEstimationService(ConfigResolver(db_factory), db_factory)
        project_data = {
            "title": "Ad",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "target_duration": 30,
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "kling/kling-v3-omni",
            "characters": {"A": {"name": "A"}},
            "episodes": [{"episode": 1, "title": "", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "ad", [("E1U1", 6)])
        script["video_units"][0]["text"] = text
        scripts = {"ep1.json": script}

        await service.compute(project_data, scripts, project_name="ad-reference-route-bucket")

        assert priced_models == [expected_model]

    async def test_custom_provider_estimates_use_the_matched_entry_prices(self, db_factory):
        """Custom-provider image/video/audio estimates use matched entry price tables × multipliers, sharing actual accounting."""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "img",
                        "display_name": "Img",
                        "endpoint": "openai-images",
                        "entry_binding": "xai/grok-imagine-image",
                    },
                    {
                        "model_id": "relay/kling-v2-6",
                        "display_name": "Vid",
                        "endpoint": "openai-video",
                        "capability_overrides": {"durations": [6]},
                        "multiplier": 0.5,
                    },
                    {
                        "model_id": "aud",
                        "display_name": "Aud",
                        "endpoint": "openai-tts",
                        "entry_binding": "minimax/speech-2.8-turbo",
                    },
                ],
            )
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "custom-1/img",
            "video_backend": "custom-1/relay/kling-v2-6",
            "narration_delivery": "use_tts",
            "audio_backend": "custom-1/aud",
            "narration_voice": "alloy",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        script = _make_script(1, ["E1S001"], [6])
        script["segments"][0]["novel_text"] = "字" * 10000  # 10,000 characters.
        scripts = {"ep1.json": script}

        result = await service.compute(project_data, scripts, project_name="test-custom")

        assert result["models"]["image"] == {"provider": "custom-1", "model": "img"}
        seg = result["episodes"][0]["segments"][0]
        # image: bound entry charges 0.02 USD/image, independent of 1K/2K.
        assert seg["estimate"]["image"]["USD"] == pytest.approx(0.02)
        # video: name matches Kling 2.6, 720p silent 0.3 CNY/s × 6s × model multiplier 0.5 = 0.9 CNY.
        assert seg["estimate"]["video"]["CNY"] == pytest.approx(0.9)
        # audio: 10,000 Chinese characters count twice → 20,000 / 10,000 × 2.0 = 4.0 CNY.
        assert seg["estimate"]["audio"]["CNY"] == pytest.approx(4.0)
        # Include synchronously in episode/project totals.
        assert result["project_totals"]["estimate"]["video"]["CNY"] == pytest.approx(0.9)

    async def test_custom_provider_grid_estimate_uses_the_entry_price(self, db_factory):
        """Grid custom-provider image pricing also propagates: 2K grid unit price equals the entry's per-image price."""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "img",
                        "display_name": "Img",
                        "endpoint": "openai-images",
                        # Per-image pricing independent of resolution: Grok Imagine 0.02 USD/image.
                        "entry_binding": "xai/grok-imagine-image",
                    },
                ],
            )
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        seg_ids = [f"E1S{i:03d}" for i in range(1, 10)]  # Nine scenes → one grid_9.
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "image_provider_t2i": "custom-1/img",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 9)}

        result = await service.compute(project_data, scripts, project_name="test-grid-custom")

        # Nine cells form one grid; spread 0.02 USD/image across nine cells.
        segments = result["episodes"][0]["segments"]
        for seg in segments:
            assert seg["estimate"]["image"]["USD"] == pytest.approx(round(0.02 / 9, 6))
        # Episode total equals full-image price 0.02 USD.
        assert result["episodes"][0]["totals"]["estimate"]["image"]["USD"] == pytest.approx(0.02, abs=1e-4)

    async def test_grid_estimate_counts_every_chunk_of_oversized_group(self, db_factory):
        """Over-capacity groups price the post-split grid count, matching actual enqueue outputs."""
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        from lib.script.grid.layout import plan_grid_chunks

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Custom",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "img",
                        "display_name": "Img",
                        "endpoint": "openai-images",
                        # Per-image pricing independent of resolution: Grok Imagine 0.02 USD/image.
                        "entry_binding": "xai/grok-imagine-image",
                    },
                ],
            )
            await session.commit()

        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        seg_ids = [f"E1S{i:03d}" for i in range(1, 13)]  # Twelve scenes at non-4K limit nine produce two grids.
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "aspect_ratio": "9:16",
            "image_provider_t2i": "custom-1/img",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, seg_ids, [6] * 12)}

        result = await service.compute(project_data, scripts, project_name="test-grid-oversized")

        expected_grids = len(plan_grid_chunks(seg_ids, "9:16", allow_large_grid=False))
        assert expected_grids == 2
        assert result["episodes"][0]["totals"]["estimate"]["image"]["USD"] == pytest.approx(
            0.02 * expected_grids, abs=1e-4
        )

    async def test_custom_provider_without_price_degrades_to_zero(self, db_factory):
        """Missing custom-provider price models degrade estimates to zero with debug logs, without raising, matching existing behavior."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "custom-99/ghost",  # Provider/model absent from DB.
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="test-noprice")

        # Assert actual resolved custom provider/model to exclude same-result false positives from unknown resolver fallback.
        assert result["models"]["image"] == {"provider": "custom-99", "model": "ghost"}
        # Missing prices make calculate_cost return zero; _add_cost filters it, leaving empty image estimates without raising.
        seg = result["episodes"][0]["segments"][0]
        assert seg["estimate"]["image"] == {}

    async def test_custom_provider_malformed_id_degrades_to_zero(self, db_factory):
        """Malformed custom- provider IDs with nonnumeric suffixes degrade parse_provider_id ValueError to zero without raising."""
        resolver = ConfigResolver(db_factory)
        service = CostEstimationService(resolver, db_factory)

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "custom-abc/ghost",  # Write-side validation checks prefixes only, so nonnumeric suffixes can persist.
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001"], [6])}

        result = await service.compute(project_data, scripts, project_name="test-malformed-id")

        # Assert actual malformed provider/model to exclude same-result false positives from unknown resolver fallback.
        assert result["models"]["image"] == {"provider": "custom-abc", "model": "ghost"}
        seg = result["episodes"][0]["segments"][0]
        assert seg["estimate"]["image"] == {}


class TestUnpricedCosts:
    """Mark unpriced portions on both estimated and actual sides so UI distinguishes genuine zero from unknown."""

    async def test_custom_model_without_price_is_reported_on_both_sides(self, db_factory):
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {"model_id": "img", "display_name": "Img", "endpoint": "openai-images"},
                    {
                        "model_id": "vid",
                        "display_name": "Vid",
                        "endpoint": "openai-video",
                        "capability_overrides": {"durations": [6]},
                        "multiplier": 0.0,
                    },
                ],
            )
            await session.commit()
        await _seed_call(
            db_factory, "test-unpriced", "image", "img", provider="custom-1", segment_id="E1S001", unpriced=True
        )
        await _seed_call(db_factory, "test-unpriced", "video", "vid", provider="custom-1", segment_id="E1S001")

        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "image_provider_t2i": "custom-1/img",
            "video_provider_i2v": "custom-1/vid",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        scripts = {"ep1.json": _make_script(1, ["E1S001", "E1S002"], [6, 6])}

        result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
            project_data, scripts, project_name="test-unpriced"
        )

        unpriced_img = {"call_type": "image", "provider": "custom-1", "provider_name": "Relay", "model": "img"}
        # Unmatched image models are unpriced; zero-multiplier video models are genuinely zero-cost, never unpriced.
        assert result["unpriced"]["estimate"] == [{**unpriced_img, "count": 2}]
        assert result["unpriced"]["actual"] == [{**unpriced_img, "count": 1}]
        assert result["missing_local_calls"] is False

    async def test_reference_unit_on_custom_model_without_price_is_reported(self, db_factory):
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        async with db_factory() as session:
            await CustomProviderRepository(session).create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="k",
                models=[
                    {
                        "model_id": "vid",
                        "display_name": "Vid",
                        "endpoint": "openai-video",
                        "capability_overrides": {"durations": [6]},
                    }
                ],
            )
            await session.commit()
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_provider_i2v": "custom-1/vid",
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }
        script = _make_reference_video_script(1, "narration", [("E1U1", 6), ("E1U2", 6)])

        result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
            project_data, {"ep1.json": script}, project_name="test-unpriced-reference"
        )

        assert result["unpriced"]["estimate"] == [
            {"call_type": "video", "provider": "custom-1", "provider_name": "Relay", "model": "vid", "count": 2}
        ]

    async def test_zero_cost_call_is_not_reported_as_unpriced(self, db_factory):
        """Unpriced means NULL cost: recorded zero is genuine zero regardless of whether the model has a price."""
        await _seed_call(
            db_factory,
            "test-zero",
            "image",
            "img",
            provider="custom-1",
            segment_id="E1S001",
            cost_amount=0.0,
            currency="USD",
        )
        await _seed_call(
            db_factory, "test-zero", "video", "vid", provider="custom-1", segment_id="E1S001", unpriced=True
        )
        await _seed_call(
            db_factory, "test-zero", "video", "vid", provider="custom-1", segment_id="E1S002", unpriced=True
        )

        result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
            {"title": "Test", "content_mode": "narration", "episodes": []}, {}, project_name="test-zero"
        )

        assert result["unpriced"]["actual"] == [
            {"call_type": "video", "provider": "custom-1", "provider_name": "custom-1", "model": "vid", "count": 2}
        ]

    @pytest.mark.parametrize(
        ("generated", "call", "expected"),
        [
            ("asset", None, True),
            ("storyboard", None, True),
            ("asset", ("image", "success"), False),
            # Text calls and failed media calls cannot establish artifact costs; never turn unknown historical spending into genuine zero.
            ("asset", ("text", "success"), True),
            ("asset", ("image", "failed"), True),
            (None, None, False),
        ],
    )
    async def test_missing_local_calls_flags_generated_media_without_call_records(
        self, db_factory, generated, call, expected
    ):
        """Without local media call records, as in imported projects, actual costs are unknowable; brand-new projects with no generation genuinely cost zero."""
        project_name = "test-imported"
        if call is not None:
            call_type, status = call
            async with db_factory() as session:
                repo = UsageRepository(session)
                call_id = await repo.start_call(
                    project_name=project_name, call_type=call_type, model="m", provider=PROVIDER_GEMINI
                )
                await repo.finish_call(call_id, status=status, settlement=SettlementInput())
        script = _make_script(
            1,
            ["E1S001"],
            [6],
            [{"storyboard_image": "storyboards/E1S001.png"}] if generated == "storyboard" else None,
        )
        project_data = {
            "title": "Test",
            "content_mode": "narration",
            "characters": {"A": {"character_sheet": "characters/A.png" if generated == "asset" else ""}},
            "episodes": [{"episode": 1, "title": "Ep1", "script_file": "ep1.json"}],
        }

        result = await CostEstimationService(ConfigResolver(db_factory), db_factory).compute(
            project_data, {"ep1.json": script}, project_name=project_name
        )

        assert result["missing_local_calls"] is expected


class TestImageBatchEstimate:
    """Batch-image estimates and settlement share catalog lookup/calculators; backend derives metering from request facts."""

    async def test_openai_output_tokens_estimated_from_size_and_quality(self, db_factory, monkeypatch):
        from lib.catalog import query
        from server.services.admission.cost_estimation import estimate_image_batch_cost
        from tests.catalog_support import install_catalog, with_entry_facts

        # Estimate output tokens from entry quality-tier bases supplied by this case, independent of factory-snapshot coverage.
        bases = {"low": 16, "medium": 48, "high": 96}
        install_catalog(
            monkeypatch, with_entry_facts(query.factory_snapshot(), "openai", "gpt-image-2", output_token_bases=bases)
        )

        project = {
            "title": "T",
            "content_mode": "narration",
            "image_provider_t2i": "openai/gpt-image-2",
            "model_settings": {"openai/gpt-image-2": {"resolution": "1K"}},
        }

        estimated = await estimate_image_batch_cost(
            project, ["t2i", "t2i"], aspect_ratio="1:1", resolver=ConfigResolver(db_factory), session_factory=db_factory
        )

        # 1K → medium quality, 1:1 → 1024x1024: official calculator 1756 output tokens × $30/million; prompt text tokens count zero.
        assert estimated.total == {"USD": pytest.approx(2 * 1756 * 30 / 1_000_000)}
        assert estimated.unpriced_count == 0

    async def test_unknown_reference_count_is_unpriced_when_references_are_billed(self, db_factory):
        from server.services.admission.cost_estimation import estimate_image_batch_cost

        project = {
            "title": "T",
            "content_mode": "narration",
            "image_provider_t2i": "agnes/agnes-image-2.5-flash",
            "image_provider_i2i": "agnes/agnes-image-2.5-flash",
        }
        resolver = ConfigResolver(db_factory)

        t2i = await estimate_image_batch_cost(
            project, ["t2i"], aspect_ratio="9:16", resolver=resolver, session_factory=db_factory
        )
        assert (t2i.total, t2i.unpriced_count) == ({"USD": pytest.approx(0.01)}, 0)
        # i2i reveals references but not their count; Agnes surcharges from the fourth image, so pricing is unknown.
        i2i = await estimate_image_batch_cost(
            project, ["i2i"], aspect_ratio="9:16", resolver=resolver, session_factory=db_factory
        )
        assert (i2i.total, i2i.unpriced_count) == ({}, 1)
        assert i2i.to_payload() == {"estimated_cost": None, "unpriced_count": 1}

    async def test_batch_totals_the_priced_images_and_counts_the_unpriced_ones(self, db_factory):
        """If t2i can price but i2i cannot, totals include t2i only and mark i2i image counts unpriced."""
        from server.services.admission.cost_estimation import estimate_image_batch_cost

        project = {
            "title": "T",
            "content_mode": "narration",
            "image_provider_t2i": "agnes/agnes-image-2.5-flash",
            "image_provider_i2i": "agnes/agnes-image-2.5-flash",
        }

        estimated = await estimate_image_batch_cost(
            project,
            ["t2i", "i2i", "t2i", "i2i", "i2i"],
            aspect_ratio="9:16",
            resolver=ConfigResolver(db_factory),
            session_factory=db_factory,
        )

        assert estimated.total == {"USD": pytest.approx(0.02)}
        assert estimated.unpriced_count == 3
        assert estimated.to_payload() == {"estimated_cost": {"USD": pytest.approx(0.02)}, "unpriced_count": 3}

    async def test_lane_whose_provider_cannot_be_resolved_counts_all_its_images_as_unpriced(
        self, db_factory, monkeypatch
    ):
        from lib.config.resolver import ConfigResolver as Resolver
        from server.services.admission.cost_estimation import estimate_image_batch_cost

        real = Resolver.resolve_image_backend

        async def resolve(self, project, payload, *, generation_type=None, **kwargs):
            if generation_type == "i2i":
                raise ValueError("没有配置图片供应商")
            return await real(self, project, payload, generation_type=generation_type, **kwargs)

        monkeypatch.setattr(Resolver, "resolve_image_backend", resolve)
        project = {
            "title": "T",
            "content_mode": "narration",
            "image_provider_t2i": "agnes/agnes-image-2.5-flash",
        }

        estimated = await estimate_image_batch_cost(
            project,
            ["t2i", "i2i", "i2i"],
            aspect_ratio="9:16",
            resolver=ConfigResolver(db_factory),
            session_factory=db_factory,
        )

        assert estimated.total == {"USD": pytest.approx(0.01)}
        assert estimated.unpriced_count == 2

    async def test_empty_batch_has_nothing_to_price(self, db_factory):
        from server.services.admission.cost_estimation import estimate_image_batch_cost

        estimated = await estimate_image_batch_cost(
            {"title": "T"}, [], aspect_ratio="9:16", resolver=ConfigResolver(db_factory), session_factory=db_factory
        )

        assert (estimated.total, estimated.unpriced_count) == ({}, 0)
