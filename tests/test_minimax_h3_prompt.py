from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lib.minimax_h3_prompt import (
    H3_MAX_PROMPT_CHARS,
    H3_SYSTEM_PROMPT_SHA256,
    H3PromptArtifact,
    H3PromptReference,
    H3PromptSections,
    H3PromptTooLongError,
    confirm_h3_prompt_artifact,
    h3_prompt_artifact_path,
    load_h3_prompt_artifact,
    load_h3_system_prompt,
    parse_h3_prompt,
    save_h3_prompt_artifact,
)
from lib.script_editor import split_segment
from lib.text_backends.base import TextGenerationResult
from lib.video_style import UnifiedVideoStyle
from server.services.h3_prompt_optimization import (
    H3PromptContext,
    H3PromptOptimizationService,
    _optimizer_user_prompt,
)

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


def _oversized_prompt() -> str:
    return _prompt().replace(
        "At 00:03.000,",
        f"{'x' * H3_MAX_PROMPT_CHARS}\nAt 00:03.000,",
    )


def _context(
    tmp_path: Path,
    *,
    basis_digest: str = "basis-v1",
    unit_id: str = "E1U01",
) -> H3PromptContext:
    return H3PromptContext(
        episode=1,
        unit={"unit_id": unit_id, "text": "runtime facts"},
        projection=SimpleNamespace(
            request_duration=SimpleNamespace(seconds=8),
            provider_candidate=SimpleNamespace(model_id="MiniMax-H3", resolution="720p"),
        ),
        narration_delivery="post_production",
        aspect_ratio="16:9",
        image_references=(H3PromptReference(label="Picture 1", kind="prop", name="Bowl"),),
        image_paths=(tmp_path / "bowl.png",),
        audio_references=(H3PromptReference(label="Audio 1", kind="speaker", name="Dad"),),
        audio_paths=(tmp_path / "dad.mp3",),
        basis_digest=basis_digest,
        user_prompt=_optimizer_user_prompt({"unit": {"unit_id": unit_id, "text": "runtime facts"}}),
    )


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


def test_optimizer_user_prompt_includes_the_complete_provider_character_limit() -> None:
    prompt = _optimizer_user_prompt({"unit": {"unit_id": "E1U01"}})

    assert f"must not exceed {H3_MAX_PROMPT_CHARS} characters" in prompt
    assert "including all section headers" in prompt


def test_optimizer_user_prompt_includes_unified_video_style_rules() -> None:
    prompt = _optimizer_user_prompt({"project": {"video_style": {"prompt": "No background music."}}})

    assert "project.video_style.prompt" in prompt
    assert "non_diegetic_music as N/A" in prompt


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

    with pytest.raises(H3PromptTooLongError) as exc_info:
        parse_h3_prompt(_oversized_prompt(), duration_seconds=8, picture_count=1, audio_count=1)
    assert exc_info.value.actual_chars > H3_MAX_PROMPT_CHARS
    assert exc_info.value.max_chars == H3_MAX_PROMPT_CHARS


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


@pytest.mark.parametrize(
    "unit_id",
    ("E0U1", "E1U0", "E1U01__1", "E1U01_1/../escape"),
)
def test_artifact_rejects_noncanonical_or_unsafe_unit_ids(tmp_path: Path, unit_id: str) -> None:
    with pytest.raises(ValueError, match="invalid reference video unit id"):
        h3_prompt_artifact_path(tmp_path, 1, unit_id)


async def test_worker_prompt_step_accepts_the_stable_child_id_created_by_split(tmp_path: Path) -> None:
    script = {
        "video_units": [
            {"unit_id": "E1U01", "generated_assets": {}},
            {"unit_id": "E1U02", "generated_assets": {}},
        ]
    }
    split_segment(
        script,
        "E1U01",
        [
            {"text": "first half", "duration_seconds": 8},
            {"text": "second half", "duration_seconds": 8},
        ],
    )
    split_unit_id = script["video_units"][1]["unit_id"]
    assert [unit["unit_id"] for unit in script["video_units"]] == ["E1U01", "E1U01_1", "E1U02"]

    class _Generator:
        async def generate(self, request: Any, *, project_name: str) -> TextGenerationResult:
            return TextGenerationResult(text=_prompt(), provider="test", model="optimizer")

    async def _factory(_project_name: str) -> Any:
        return _Generator()

    service = H3PromptOptimizationService(generator_factory=_factory)
    artifacts = await service._optimize_contexts(
        "demo",
        tmp_path,
        [_context(tmp_path, unit_id=split_unit_id)],
    )

    assert artifacts[0].unit_id == "E1U01_1"
    assert h3_prompt_artifact_path(tmp_path, 1, "E1U01_1").is_file()
    assert load_h3_prompt_artifact(tmp_path, 1, "E1U01_1") == artifacts[0]


async def test_video_style_prompt_does_not_mechanically_rewrite_optimizer_sections(tmp_path: Path) -> None:
    class _Generator:
        async def generate(self, request: Any, *, project_name: str) -> TextGenerationResult:
            return TextGenerationResult(
                text=_prompt().replace("No music.", "A warm orchestral score."),
                provider="test",
                model="optimizer",
            )

    async def _factory(_project_name: str) -> Any:
        return _Generator()

    style = UnifiedVideoStyle(
        prompt="Use close-miked ASMR wire and enamel sounds with no background music.",
        source="user",
        updated_at=datetime.now(UTC),
    )
    context = replace(_context(tmp_path), video_style=style)
    artifacts = await H3PromptOptimizationService(generator_factory=_factory)._optimize_contexts(
        "demo",
        tmp_path,
        [context],
    )

    assert artifacts[0].sections.non_diegetic_music == "A warm orchestral score."


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

    captured.clear()
    rendered = await service.optimized_prompt_for_context("demo", tmp_path, context)
    assert rendered == artifacts[0].rendered_prompt
    assert captured == []


async def test_worker_prompt_step_reoptimizes_a_stale_artifact(
    tmp_path: Path,
) -> None:
    captured: list[Any] = []

    class _Generator:
        async def generate(self, request: Any, *, project_name: str) -> TextGenerationResult:
            captured.append((request, project_name))
            return TextGenerationResult(text=_prompt(), provider="test", model="optimizer")

    async def _factory(_project_name: str) -> Any:
        return _Generator()

    stale = _artifact()
    save_h3_prompt_artifact(tmp_path, stale)
    projection = SimpleNamespace(
        request_duration=SimpleNamespace(seconds=8),
        provider_candidate=SimpleNamespace(model_id="MiniMax-H3", resolution="720p"),
    )
    context = H3PromptContext(
        episode=1,
        unit={"unit_id": "E1U01", "text": "updated runtime facts"},
        projection=projection,
        narration_delivery="post_production",
        aspect_ratio="16:9",
        image_references=(H3PromptReference(label="Picture 1", kind="prop", name="Bowl"),),
        image_paths=(tmp_path / "bowl.png",),
        audio_references=(H3PromptReference(label="Audio 1", kind="speaker", name="Dad"),),
        audio_paths=(tmp_path / "dad.mp3",),
        basis_digest="basis-v2",
        user_prompt="updated runtime facts only",
    )
    service = H3PromptOptimizationService(generator_factory=_factory)

    rendered = await service.optimized_prompt_for_context("demo", tmp_path, context)

    assert len(captured) == 1
    assert captured[0][0].system_prompt == load_h3_system_prompt()
    assert captured[0][1] == "demo"
    refreshed = load_h3_prompt_artifact(tmp_path, 1, "E1U01")
    assert refreshed is not None
    assert rendered == refreshed.rendered_prompt
    assert refreshed.basis_digest == "basis-v2"


async def test_optimizer_retries_an_over_limit_response_with_length_feedback(tmp_path: Path) -> None:
    requests: list[Any] = []
    responses = iter((_oversized_prompt(), _prompt()))

    class _Generator:
        async def generate(self, request: Any, *, project_name: str) -> TextGenerationResult:
            requests.append(request)
            return TextGenerationResult(text=next(responses), provider="test", model="optimizer")

    async def _factory(_project_name: str) -> Any:
        return _Generator()

    context = _context(tmp_path)
    service = H3PromptOptimizationService(generator_factory=_factory)

    artifacts = await service._optimize_contexts("demo", tmp_path, [context])

    assert len(requests) == 2
    assert requests[0].prompt == context.user_prompt
    assert requests[1].prompt.startswith(context.user_prompt)
    assert "Your previous response rendered to" in requests[1].prompt
    assert f"{H3_MAX_PROMPT_CHARS}-character provider limit" in requests[1].prompt
    assert (
        artifacts[0].rendered_prompt
        == parse_h3_prompt(_prompt(), duration_seconds=8, picture_count=1, audio_count=1).render()
    )
    assert load_h3_prompt_artifact(tmp_path, 1, "E1U01") == artifacts[0]


async def test_optimizer_stops_after_three_over_limit_responses_without_saving(tmp_path: Path) -> None:
    requests: list[Any] = []

    class _Generator:
        async def generate(self, request: Any, *, project_name: str) -> TextGenerationResult:
            requests.append(request)
            return TextGenerationResult(text=_oversized_prompt(), provider="test", model="optimizer")

    async def _factory(_project_name: str) -> Any:
        return _Generator()

    service = H3PromptOptimizationService(generator_factory=_factory)

    with pytest.raises(H3PromptTooLongError):
        await service._optimize_contexts("demo", tmp_path, [_context(tmp_path)])

    assert len(requests) == 3
    assert load_h3_prompt_artifact(tmp_path, 1, "E1U01") is None
