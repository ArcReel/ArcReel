from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lib.minimax_h3_prompt import (
    H3_SYSTEM_PROMPT_SHA256,
    H3PromptArtifact,
    H3PromptReference,
    H3PromptSections,
    confirm_h3_prompt_artifact,
    h3_prompt_artifact_path,
    load_h3_prompt_artifact,
    load_h3_system_prompt,
    parse_h3_prompt,
    save_h3_prompt_artifact,
)
from lib.text_backends.base import TextGenerationResult
from server.services.h3_prompt_optimization import H3PromptContext, H3PromptOptimizationService

pytestmark = pytest.mark.unit


def _prompt(*, timestamp: str = "00:03.000") -> str:
    return f"""subject_definitions:
<Picture 1> is the blue bowl.

summary:
The liquid settles into the bowl.

retention_analysis:
Retain the bowl silhouette and cobalt color.

detailed_description:
At {timestamp}, the liquid rotates around <Picture 1> while the voice timbre follows <Audio 1>.

overall_soundscape:
Quiet workshop ambience.

non_diegetic_music:
No music."""


def _artifact() -> H3PromptArtifact:
    sections = H3PromptSections.model_validate(
        parse_h3_prompt(_prompt(), duration_seconds=8, picture_count=1, audio_count=1).model_dump()
    )
    return H3PromptArtifact(
        episode=1,
        unit_id="E1U01",
        sections=sections,
        rendered_prompt=sections.render(),
        basis_digest="basis-v1",
        model_id="MiniMax-H3",
        optimizer_provider="test",
        optimizer_model="test-model",
        request_duration_seconds=8,
        aspect_ratio="16:9",
        narration_delivery="post_production",
        optimized_at=datetime.now(UTC).isoformat(),
    )


def test_pinned_ref_en_is_loaded_byte_exactly() -> None:
    raw = load_h3_system_prompt().encode()
    assert hashlib.sha256(raw).hexdigest() == H3_SYSTEM_PROMPT_SHA256


def test_parser_requires_six_ordered_sections_and_valid_request_facts() -> None:
    sections = parse_h3_prompt(_prompt(), duration_seconds=8, picture_count=1, audio_count=1)
    assert list(sections.model_dump()) == [
        "subject_definitions",
        "summary",
        "retention_analysis",
        "detailed_description",
        "overall_soundscape",
        "non_diegetic_music",
    ]
    with pytest.raises(ValueError, match="must be earlier"):
        parse_h3_prompt(_prompt(timestamp="00:08.000"), duration_seconds=8, picture_count=1, audio_count=1)
    with pytest.raises(ValueError, match="only 0 audio"):
        parse_h3_prompt(_prompt(), duration_seconds=8, picture_count=1, audio_count=0)


def test_artifact_supports_zero_padded_unit_ids_and_confirmation_is_basis_guarded(tmp_path: Path) -> None:
    artifact = _artifact()
    save_h3_prompt_artifact(tmp_path, artifact)
    assert h3_prompt_artifact_path(tmp_path, 1, "E1U01").is_file()
    assert load_h3_prompt_artifact(tmp_path, 1, "E1U01") == artifact

    with pytest.raises(ValueError, match="stale"):
        confirm_h3_prompt_artifact(tmp_path, 1, "E1U01", expected_basis_digest="basis-v2")
    confirmed = confirm_h3_prompt_artifact(tmp_path, 1, "E1U01", expected_basis_digest="basis-v1")
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_at is not None


async def test_optimizer_keeps_pinned_system_prompt_separate_and_saves_pending_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Any] = []

    class _Generator:
        async def generate(self, request: Any, *, project_name: str) -> TextGenerationResult:
            captured.append((request, project_name))
            return TextGenerationResult(text=_prompt(), provider="test", model="optimizer")

    async def _factory(_project_name: str) -> Any:
        return _Generator()

    projection = SimpleNamespace(
        request_duration=SimpleNamespace(seconds=8),
        provider_candidate=SimpleNamespace(model_id="MiniMax-H3", resolution="720p"),
    )
    context = H3PromptContext(
        episode=1,
        unit={"unit_id": "E1U01", "text": "runtime facts"},
        projection=projection,
        narration_delivery="post_production",
        aspect_ratio="16:9",
        image_references=(H3PromptReference(label="Picture 1", kind="prop", name="Bowl"),),
        image_paths=(tmp_path / "bowl.png",),
        audio_references=(H3PromptReference(label="Audio 1", kind="speaker", name="Dad"),),
        audio_paths=(tmp_path / "dad.mp3",),
        basis_digest="basis-v1",
        user_prompt="runtime facts only",
    )
    service = H3PromptOptimizationService(generator_factory=_factory)

    async def _contexts(*_args: Any, **_kwargs: Any) -> tuple[Path, list[H3PromptContext]]:
        return tmp_path, [context]

    monkeypatch.setattr(service, "_contexts", _contexts)
    artifacts = await service.optimize("demo", 1)

    request, project_name = captured[0]
    assert request.system_prompt == load_h3_system_prompt()
    assert request.prompt == "runtime facts only"
    assert project_name == "demo"
    assert artifacts[0].status == "pending_review"
    assert load_h3_prompt_artifact(tmp_path, 1, "E1U01") == artifacts[0]
