from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, InMemoryArtifactManifestAdapter
from lib.artifacts.generation_input import ManifestInputObservation, storyboard_image_input
from lib.backends.image_backends.vidu import ViduImageBackend
from lib.backends.image_errors import ImageCapabilityError
from lib.billing.ledger import Ledger
from lib.generation.media_generator import MediaGenerator
from server.services.admission.image_references import check_storyboard_image_limit
from tests.factories import image_bytes
from tests.http_capture import capture_http, only_request, request_json


@pytest.mark.invariant
@given(count=st.integers(0, 5), limit=st.integers(0, 6), pending=st.booleans(), segment_break=st.booleans())
@example(count=0, limit=0, pending=True, segment_break=False)
@example(count=1, limit=0, pending=True, segment_break=False)
async def test_storyboard_admission_reserves_every_image_in_the_later_submit(
    example_database, count, limit, pending, segment_break
):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        previous_path = "storyboards/scene_E1S01.png"
        images = [f"ref-{i}.png" for i in range(count)]
        project = {"products": {"P": {"reference_images": images}}}
        previous = {"shot_id": "E1S01", "generated_assets": {}}
        script = {
            "content_mode": "ad",
            "shots": [
                previous,
                {
                    "shot_id": "E1S02",
                    "image_prompt": "P",
                    "products_in_shot": ["P"],
                    "segment_break": segment_break,
                },
            ],
        }
        adapter = InMemoryArtifactManifestAdapter(artifacts=set(images))
        for index, path in enumerate(images):
            (root / path).write_bytes(image_bytes(index))
        planned = {"E1S01"} if pending else set()
        failure = None
        try:
            check_storyboard_image_limit(
                project,
                script,
                root,
                episode=1,
                resource_id="E1S02",
                limit=(limit, "vidu"),
                planned_ids=planned,
                observation=ManifestInputObservation(root, adapter),
            )
        except ImageCapabilityError as exc:
            failure = exc

        if pending:
            (root / previous_path).parent.mkdir()
            (root / previous_path).write_bytes(image_bytes(count))
            previous["generated_assets"] = {"storyboard_image": previous_path}
            adapter = InMemoryArtifactManifestAdapter(artifacts={previous_path, *images})
            adapter.put_entry(ArtifactKey.episode_storyboard(1, "E1S01"), ArtifactManifestEntry(previous_path, "basis"))
        assembled = storyboard_image_input(
            project, script, episode=1, resource_id="E1S02", observation=ManifestInputObservation(root, adapter)
        )
        assert not assembled.gaps
        backend = ViduImageBackend(api_key="test", base_url="https://api.vidu.com")
        generator = MediaGenerator(root, image_backend=backend, image_provider_id="vidu")
        async with example_database(root / "ledger.sqlite3") as session_factory:
            generator.ledger = Ledger(session_factory=session_factory)
            with (
                assembled.freeze(
                    max_reference_images=None, model=backend.model, bind_claims=lambda claims, _: claims
                ) as frozen,
                capture_http() as router,
            ):
                submit = router.post("https://api.vidu.com/reference2image").mock(
                    return_value=httpx.Response(200, json={})
                )
                with pytest.raises(RuntimeError, match="task_id"):
                    await generator.generate_image_async(
                        frozen.prompt,
                        "storyboards",
                        "E1S02",
                        reference_images=frozen.references.reference_images,
                        reference_sources=frozen.references.source_paths,
                    )
                sent = len(request_json(only_request(submit)).get("images", []))
        assert sent == count + int(pending and not segment_break)
        assert (failure is not None) == (sent > limit)
        if failure is not None:
            assert failure.code == "image_reference_images_exceeded"
            assert failure.params["count"] == sent
