from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from arcreel_market_core.video_backend_contract import VideoCapabilityError, VideoGenerationRequest
from lib.backends.video_backends.vidu import VIDU_BACKENDS
from lib.backends.video_capabilities import projected_video_capabilities
from lib.backends.video_frame_slots import gate_video_request
from lib.capabilities.kernel import resolve
from lib.generation.video_request_facts import ResolutionOverride, snapshot_video_request_facts
from lib.script.reference_video.reference_assets import FilesystemReferenceAssets
from lib.script.reference_video.request_projection import ReferenceUnitRequestProjector
from lib.script.script_models import ReferenceResource, ResolvedReferenceAsset
from server.services.admission.cost_estimation import VideoPriceSource, quote_video_request_from_price
from tests.factories import image_bytes
from tests.http_capture import capture_http, only_request, request_json

pytestmark = pytest.mark.invariant


@given(
    channel=st.sampled_from(("q3-turbo", "q3-mix", "q3-ad")),
    model_audio=st.booleans(),
    reference_audio=st.one_of(st.none(), st.booleans()),
    requested=st.booleans(),
    model_limit=st.integers(1, 9),
    override=st.one_of(st.none(), st.integers(1, 7)),
    count=st.integers(1, 8),
    durations=st.sets(st.integers(3, 8)),
    planned=st.integers(3, 8),
    resolution=st.sampled_from(("720p", "1080p")),
)
@example(
    channel="q3-turbo",
    model_audio=False,
    reference_audio=None,
    requested=False,
    model_limit=1,
    override=None,
    count=1,
    durations={3},
    planned=3,
    resolution="720p",
)
@example(
    channel="q3-mix",
    model_audio=False,
    reference_audio=None,
    requested=False,
    model_limit=1,
    override=None,
    count=1,
    durations={3},
    planned=3,
    resolution="720p",
)
async def test_reference_video_quote_and_admission_match_submit(
    channel, model_audio, reference_audio, requested, model_limit, override, count, durations, planned, resolution
):
    model = f"vidu{channel}"
    backend_type = VIDU_BACKENDS[f"vidu-video-{channel}"]
    snapshot = resolve(
        backend_type.describe_video_endpoint(model),
        facts={
            "has_audio_track": model_audio,
            "reference_route_has_audio_track": reference_audio,
            "max_reference_images": model_limit,
            "durations": sorted(durations),
            "duration_constraints": {"by_resolution": {"1080p": [4, 6, 8]}},
        },
        overrides={"max_reference_images": override} if override is not None else {},
        provider_id="vidu",
        model_id=model,
    )

    async def lookup(bucket):
        return snapshot_video_request_facts(
            snapshot,
            {},
            route="reference_video",
            generation_type=bucket,
            requested_audio=requested,
            resolution_override=ResolutionOverride(resolution),
        )

    with TemporaryDirectory() as directory:
        root = Path(directory)
        assets = []
        for index in range(count):
            path = root / f"{index}.png"
            path.write_bytes(image_bytes(index))
            assets.append(ResolvedReferenceAsset(path, ReferenceResource(type="character", name=f"C{index}")))
        project = {"characters": {asset.reference.name: {"character_sheet": asset.path.name} for asset in assets}}
        unit = {
            "unit_id": "E1U1",
            "duration_seconds": planned,
            "text": " ".join(f"@[{a.reference.name}]" for a in assets),
        }
        projection = await ReferenceUnitRequestProjector(lookup, FilesystemReferenceAssets(root)).project_current(
            project=project,
            script={},
            unit=unit,
            resolved_assets=assets,
        )
        tiers = durations if resolution == "720p" else durations.intersection({4, 6, 8})
        if not tiers:
            assert [problem.code for problem in projection.blocking_problems] == [
                "reference_supported_durations_incompatible" if durations else "reference_supported_durations_missing"
            ]
            assert projection.request_duration is None
            assert projection.cost is None
            return
        assert projection.cost is not None
        assert projection.request_duration is not None
        facts = projection.request_facts
        assert facts is not None
        assert facts.request_snapshot is not None
        codes = {problem.code for problem in projection.blocking_problems}
        expected_codes = set()
        limit = override if override is not None else min(model_limit, 7)
        if count > limit:
            expected_codes.add("video_reference_images_exceeded")
        track_declared = model_audio if reference_audio is None else reference_audio
        audio_conflict = channel == "q3-mix" and track_declared and not requested
        if audio_conflict:
            expected_codes.add("video_audio_switch_not_supported")
        if planned > max(tiers):
            expected_codes.add("needs_replan")
        elif planned not in tiers:
            expected_codes.add("reference_duration_confirmation_required")
        assert codes == expected_codes

        backend = backend_type(api_key="test", model=model, base_url="https://api.vidu.com")
        execution = VideoGenerationRequest(
            prompt="scene",
            output_path=root / "result.mp4",
            reference_images=[asset.path for asset in projection.request_assets],
            duration_seconds=projection.request_duration.seconds,
            resolution=resolution,
            generate_audio=requested,
            video_capabilities=projected_video_capabilities(facts.request_snapshot),
        )

        def gate():
            gate_video_request(
                caps=execution.video_capabilities,
                provider="vidu",
                model=model,
                has_image=True,
                reference_images=execution.reference_images,
            )

        with capture_http() as router:
            submit = router.post("https://api.vidu.com/reference2video").mock(return_value=httpx.Response(200, json={}))
            if count > limit:
                with pytest.raises(VideoCapabilityError) as error:
                    gate()
                assert error.value.code == "video_reference_images_exceeded"
                assert error.value.params["limit"] == limit
                return
            gate()
            if audio_conflict:
                with pytest.raises(VideoCapabilityError) as error:
                    await backend.generate(execution)
                assert error.value.code == "video_audio_switch_not_supported"
                assert not submit.called
                return
            with pytest.raises(RuntimeError, match="task_id"):
                await backend.generate(execution)
            body = request_json(only_request(submit))
        usage = backend.metered_usage(model, projection.cost.provider_request())
        assert usage.quantities["input_images"] == len(body["images"]) == count
        assert len(body["images"]) <= limit
        assert usage.dimensions["resolution"] == body["resolution"] == resolution
        fitting = [tier for tier in tiers if tier >= planned]
        assert usage.dimensions["duration"] == body["duration"] == (min(fitting) if fitting else max(tiers))
        if channel == "q3-turbo":
            assert body["audio"] is requested
        else:
            assert "audio" not in body
        audible = track_declared and body.get("audio", channel == "q3-mix")
        assert facts.has_audio_track is (track_declared and channel != "q3-ad")
        assert usage.dimensions["audio"] is audible
        if channel == "q3-turbo":
            quote = quote_video_request_from_price(projection.cost, VideoPriceSource(None, 1))
            rate = {"720p": 0.3125, "1080p": 0.40625}[body["resolution"]]
            assert quote.request_duration_seconds == body["duration"]
            assert (quote.amount, quote.currency) == (rate * body["duration"], "CNY")
