"""Correction memory through declared tools and real versioned video inspections."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.artifacts.version_manager import VersionManager
from lib.final_cut.basis import version_snapshot
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from server.agent_toolset.envelope import encode_outcome
from server.agent_toolset.video_review import INSPECT_VIDEO_UNITS, VIDEO_CORRECTION_MEMORY
from tests.factories import install_current_video, run_bundled_ffmpeg
from tests.integration.server.agent_tool_support import ToolHarness, draft_of, problem_of, run_declared_tool


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    directory = tmp_path_factory.mktemp("correction-clips")
    result = {}
    for color in ("red", "blue", "green"):
        path = directory / f"{color}.mp4"
        run_bundled_ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"color={color}:size=160x90:rate=25:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        )
        result[color] = path
    return result


@pytest.fixture
def ctx(tmp_path: Path, clips: dict[str, Path]) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [{"segment_id": unit, "novel_text": "旁白"} for unit in ("E1S01", "E1S02")],
        },
        "episode_1.json",
        validate=False,
    )
    harness = ToolHarness("demo", tmp_path, pm)
    install_current_video(harness.project_path, "videos", "E1S01", clips["red"])
    references = harness.project_path / "references"
    references.mkdir(exist_ok=True)
    Image.new("RGB", (8, 8), "red").save(references / "subject.png")
    return harness


def _context(**updates: Any) -> dict[str, Any]:
    return {
        "provider": "provider",
        "model": "model",
        "workflow": "palette consistency",
        "reference_paths": ["references/subject.png"],
        **updates,
    }


def _install(ctx: ToolHarness, source: Path, *, unit: str = "E1S01", **metadata: Any) -> int:
    provenance = {
        "execution_task_id": "candidate-generation-task",
        "execution_request_digest": "d" * 64,
        "execution_provider_id": "provider",
        "execution_provider_model_id": "model",
        **metadata,
    }
    version = VersionManager(ctx.project_path).add_version(
        "videos", unit, "A blue background, locked camera", source_file=source, **provenance
    )
    current = ctx.project_path / resource_relative_path("videos", unit)
    current.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, current)
    return version


async def _memory(ctx: ToolHarness, command: dict[str, Any]) -> dict[str, Any]:
    return draft_of(await run_declared_tool(VIDEO_CORRECTION_MEMORY, ctx, {"command": command}))


async def _lookup(ctx: ToolHarness, **context_updates: Any) -> dict[str, Any]:
    return await _memory(ctx, {"action": "lookup", "context": _context(**context_updates), "unit_id": "E1S01"})


async def _inspect(ctx: ToolHarness, version: int, *, unit: str = "E1S01") -> str:
    outcome = await run_declared_tool(INSPECT_VIDEO_UNITS, ctx, {"unit_ids": [unit], "version": version, "frames": 2})
    assert outcome.problem is None, outcome.problem
    envelope = encode_outcome(INSPECT_VIDEO_UNITS, outcome)
    (review,) = envelope.structured["inspect_video_units"]["units"]
    assert review["status"] == "ok"
    assert review["frame_count"] == 2
    assert len(envelope.images) == 1
    assert envelope.images[0].mime_type == "image/jpeg"
    return review["review_receipt"]


def _reservation(receipt: str, **updates: Any) -> dict[str, Any]:
    return {
        "action": "reserve",
        "context": _context(),
        "unit_id": "E1S01",
        "baseline_version": 1,
        "baseline_receipt": receipt,
        "input_description": "A blue background, locked camera",
        "correction": "Use blue instead of red",
        **updates,
    }


def _resolution(attempt_id: str, before: str, after: str, **updates: Any) -> dict[str, Any]:
    return {
        "action": "resolve",
        "attempt_id": attempt_id,
        "outcome": "verified",
        "candidate_version": 2,
        "before_evidence": ["0.0-1.0s: original is red"],
        "after_evidence": ["0.0-1.0s: inspected candidate is blue"],
        "before_receipt": before,
        "after_receipt": after,
        **updates,
    }


async def _candidate(ctx: ToolHarness, clips: dict[str, Path]) -> tuple[str, str, str]:
    before = await _inspect(ctx, 1)
    attempt = (await _memory(ctx, _reservation(before)))["attempt"]
    assert _install(ctx, clips["blue"]) == 2
    after = await _inspect(ctx, 2)
    return attempt["attempt_id"], before, after


@pytest.mark.parametrize("outcome", ["failed", "unverified"])
async def test_nonverified_candidate_without_generation_provenance_does_not_become_a_lesson(
    ctx: ToolHarness, clips: dict[str, Path], outcome: str
) -> None:
    before = await _inspect(ctx, 1)
    attempt = (await _memory(ctx, _reservation(before)))["attempt"]["attempt_id"]
    assert install_current_video(ctx.project_path, "videos", "E1S01", clips["blue"]) == 2
    after = await _inspect(ctx, 2)

    result = await _memory(ctx, _resolution(attempt, before, after, outcome=outcome))
    remembered = await _lookup(ctx)

    assert result["attempt"]["status"] == outcome
    assert result["attempt"]["candidate_version"] == 2
    assert remembered["lessons"] == []
    assert remembered["attempts"][0]["status"] == outcome


async def test_actual_inspection_receipts_promote_a_scoped_lesson_then_retire_it(
    ctx: ToolHarness, clips: dict[str, Path]
) -> None:
    attempt, before, after = await _candidate(ctx, clips)

    result = await _memory(ctx, _resolution(attempt, before, after))
    remembered = await _lookup(ctx)

    assert result["attempt"]["status"] == "verified"
    assert remembered["lessons"][0]["attempt"]["attempt_id"] == attempt
    assert remembered["lessons"][0]["attempt"]["verification"]["source"] == "frame_review"
    assert "audio and artistic approval are unverified" in remembered["verification_scope"]
    assert (await _lookup(ctx, model="other-model"))["lessons"] == []
    assert (await _lookup(ctx, workflow="other-workflow"))["lessons"] == []

    Image.new("RGB", (8, 8), "blue").save(ctx.project_path / "references" / "subject.png")
    assert (await _lookup(ctx))["lessons"] == []
    Image.new("RGB", (8, 8), "red").save(ctx.project_path / "references" / "subject.png")
    assert len((await _lookup(ctx))["lessons"]) == 1

    assert await _memory(ctx, {"action": "retire", "attempt_id": attempt}) == {"retired": True}
    retired = await _lookup(ctx)
    assert retired["lessons"] == []
    assert retired["attempts"][0]["status"] == "verified"


async def test_new_shot_context_can_retrieve_evidence_without_claiming_a_verified_fix(
    ctx: ToolHarness, clips: dict[str, Path]
) -> None:
    attempt, before, after = await _candidate(ctx, clips)
    verified = (await _memory(ctx, _resolution(attempt, before, after)))["attempt"]
    Image.new("RGB", (8, 8), "green").save(ctx.project_path / "references" / "subject.png")

    result = await _lookup(ctx, workflow="new shot with a different camera")

    assert result["lessons"] == []
    assert result["related_lessons"] == [
        {
            "lesson": {"attempt": verified},
            "applicability": "unverified_for_current_context",
            "changed_scope": ["workflow", "references"],
        }
    ]
    assert result["attempts"][0]["attempt_id"] == attempt
    assert (await _lookup(ctx, model="another-model"))["related_lessons"] == []
    assert (await _lookup(ctx, provider="another-provider"))["related_lessons"] == []
    await _memory(ctx, {"action": "retire", "attempt_id": attempt})
    assert (await _lookup(ctx))["related_lessons"] == []


async def test_exact_lessons_take_priority_within_the_shared_three_example_budget(
    ctx: ToolHarness, clips: dict[str, Path]
) -> None:
    attempt_ids = []
    units = [f"E1S{index + 1:02}" for index in range(5)]
    ctx.pm.save_script(
        ctx.project_name,
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [{"segment_id": unit, "novel_text": "旁白"} for unit in units],
        },
        "episode_1.json",
        validate=False,
    )
    for index in range(5):
        unit = units[index]
        if index:
            install_current_video(ctx.project_path, "videos", unit, clips["red"])
        before = await _inspect(ctx, 1, unit=unit)
        context = _context(workflow="palette consistency" if index < 2 else "different camera")
        attempt = (
            await _memory(
                ctx,
                _reservation(before, unit_id=unit, context=context, input_description=f"Color correction {index}"),
            )
        )["attempt"]["attempt_id"]
        version = _install(ctx, clips["blue"], unit=unit)
        after = await _inspect(ctx, version, unit=unit)
        await _memory(ctx, _resolution(attempt, before, after, candidate_version=version))
        attempt_ids.append(attempt)

    result = await _lookup(ctx)
    assert [item["attempt"]["attempt_id"] for item in result["lessons"]] == list(reversed(attempt_ids[:2]))
    assert [item["lesson"]["attempt"]["attempt_id"] for item in result["related_lessons"]] == [attempt_ids[-1]]
    assert result["related_lessons"][0]["changed_scope"] == ["workflow"]
    exact_context = await _lookup(ctx, workflow="different camera")
    assert len(exact_context["lessons"]) == 3
    assert exact_context["related_lessons"] == []
    new_context = await _lookup(ctx, workflow="unseen shot")
    assert new_context["lessons"] == []
    assert [item["lesson"]["attempt"]["attempt_id"] for item in new_context["related_lessons"]] == list(
        reversed(attempt_ids[-3:])
    )


@pytest.mark.parametrize("case", ["missing", "forged", "wrong_version", "tampered"])
@pytest.mark.parametrize("side", ["before_receipt", "after_receipt"])
async def test_missing_forged_or_mismatched_receipts_cannot_promote_a_lesson(
    ctx: ToolHarness, clips: dict[str, Path], case: str, side: str
) -> None:
    attempt, before, after = await _candidate(ctx, clips)
    command = _resolution(attempt, before, after)
    if case == "missing":
        command.pop(side)
    elif case == "forged":
        command[side] = "0" * 64
    elif case == "wrong_version":
        command[side] = after if side == "before_receipt" else before
    else:
        receipt = command[side]
        path = ctx.project_path / ".cache" / "frame_reviews" / f"{receipt}.json"
        data = json.loads(path.read_text())
        data["content_hash"] = "0" * 64
        path.write_text(json.dumps(data))

    problem = problem_of(await run_declared_tool(VIDEO_CORRECTION_MEMORY, ctx, {"command": command}))
    remembered = await _lookup(ctx)

    assert problem.code == ("correction_evidence_unavailable" if case == "forged" else "correction_refused")
    assert remembered["lessons"] == []
    assert remembered["attempts"][0]["status"] == "reserved"


@pytest.mark.parametrize("changed_version", [1, 2])
async def test_receipt_becomes_stale_when_inspected_snapshot_bytes_change(
    ctx: ToolHarness, clips: dict[str, Path], changed_version: int
) -> None:
    attempt, before, after = await _candidate(ctx, clips)
    snapshot = version_snapshot(ctx.project_path, VersionManager(ctx.project_path), "videos", "E1S01", changed_version)
    assert snapshot is not None
    snapshot[0].write_bytes(clips["green"].read_bytes())

    problem = problem_of(
        await run_declared_tool(VIDEO_CORRECTION_MEMORY, ctx, {"command": _resolution(attempt, before, after)})
    )

    assert problem.code == "correction_refused"
    assert (await _lookup(ctx))["lessons"] == []


async def test_an_unchanged_video_with_a_new_version_cannot_prove_a_correction(
    ctx: ToolHarness, clips: dict[str, Path]
) -> None:
    before = await _inspect(ctx, 1)
    attempt = (await _memory(ctx, _reservation(before)))["attempt"]["attempt_id"]
    assert _install(ctx, clips["red"]) == 2
    after = await _inspect(ctx, 2)

    problem = problem_of(
        await run_declared_tool(VIDEO_CORRECTION_MEMORY, ctx, {"command": _resolution(attempt, before, after)})
    )

    assert problem.code == "correction_refused"
    assert "unchanged video" in problem.detail
    assert (await _lookup(ctx))["lessons"] == []


async def test_candidate_already_present_at_reservation_cannot_be_credited(
    ctx: ToolHarness, clips: dict[str, Path]
) -> None:
    before = await _inspect(ctx, 1)
    assert _install(ctx, clips["blue"]) == 2
    preexisting = await _inspect(ctx, 2)
    attempt = (await _memory(ctx, _reservation(before)))["attempt"]
    assert attempt["baseline_max_version"] == 2

    problem = problem_of(
        await run_declared_tool(
            VIDEO_CORRECTION_MEMORY,
            ctx,
            {"command": _resolution(attempt["attempt_id"], before, preexisting)},
        )
    )
    assert problem.code == "correction_refused"
    assert "newer than every version" in problem.detail
    assert (await _lookup(ctx))["lessons"] == []

    assert _install(ctx, clips["blue"]) == 3
    generated = await _inspect(ctx, 3)
    result = await _memory(
        ctx,
        _resolution(
            attempt["attempt_id"],
            before,
            generated,
            candidate_version=3,
            after_evidence=["0.0-1.0s: newly generated candidate is blue"],
        ),
    )
    assert result["attempt"]["status"] == "verified"
    assert (await _lookup(ctx))["lessons"][0]["attempt"]["candidate_version"] == 3


@pytest.mark.parametrize(
    "metadata",
    [
        {"execution_task_id": None},
        {"execution_request_digest": None},
        {"execution_provider_id": "another-provider"},
        {"execution_provider_model_id": "another-model"},
    ],
)
async def test_candidate_requires_recorded_generation_provenance_matching_reserved_scope(
    ctx: ToolHarness, clips: dict[str, Path], metadata: dict[str, Any]
) -> None:
    before = await _inspect(ctx, 1)
    attempt = (await _memory(ctx, _reservation(before)))["attempt"]["attempt_id"]
    assert _install(ctx, clips["blue"], **metadata) == 2
    after = await _inspect(ctx, 2)

    problem = problem_of(
        await run_declared_tool(VIDEO_CORRECTION_MEMORY, ctx, {"command": _resolution(attempt, before, after)})
    )

    assert problem.code == "correction_refused"
    assert "generation provenance" in problem.detail
    remembered = await _lookup(ctx)
    assert remembered["lessons"] == []
    assert remembered["attempts"][0]["status"] == "reserved"


async def test_attempt_bounds_and_duplicate_guard_survive_new_tool_harness(
    ctx: ToolHarness, clips: dict[str, Path]
) -> None:
    first, before, after = await _candidate(ctx, clips)
    await _memory(ctx, _resolution(first, before, after, outcome="failed"))
    reopened = ToolHarness("demo", ctx.data_root, ProjectManager(ctx.data_root))

    duplicate = problem_of(
        await run_declared_tool(
            VIDEO_CORRECTION_MEMORY,
            reopened,
            {
                "command": _reservation(
                    before, correction="Use a blue backdrop, restated", context=_context(workflow="restated workflow")
                )
            },
        )
    )
    assert duplicate.code == "correction_refused"
    assert "Identical generation inputs" in duplicate.detail

    second = (
        await _memory(
            reopened,
            _reservation(
                after,
                baseline_version=2,
                correction="Use green",
                input_description="A green background",
                context=_context(model="other-model"),
            ),
        )
    )["attempt"]
    await _memory(
        reopened,
        {
            "action": "resolve",
            "attempt_id": second["attempt_id"],
            "outcome": "failed",
            "before_evidence": ["Blue candidate remains unsuitable"],
            "after_evidence": ["Provider failed"],
        },
    )

    exhausted = problem_of(
        await run_declared_tool(
            VIDEO_CORRECTION_MEMORY,
            reopened,
            {
                "command": _reservation(
                    after,
                    baseline_version=2,
                    correction="Third approach",
                    input_description="Another prompt",
                )
            },
        )
    )
    assert exhausted.code == "correction_refused"
    assert "Two automatic correction attempts exhausted" in exhausted.detail
    assert len((await _lookup(reopened))["attempts"]) == 2


@pytest.mark.parametrize("target", ["unknown_unit", "outside_reference", "project_text", "symlink_reference"])
async def test_foreign_units_and_reference_paths_are_refused_without_reserving(ctx: ToolHarness, target: str) -> None:
    receipt = await _inspect(ctx, 1)
    command = _reservation(receipt)
    if target == "unknown_unit":
        command["unit_id"] = "FOREIGN_UNIT"
    elif target == "outside_reference":
        command["context"] = _context(reference_paths=["../outside.png"])
    elif target == "project_text":
        command["context"] = _context(reference_paths=["project.json"])
    else:
        outside = ctx.data_root / "outside.png"
        Image.new("RGB", (8, 8), "blue").save(outside)
        (ctx.project_path / "references" / "external.png").symlink_to(outside)
        command["context"] = _context(reference_paths=["references/external.png"])

    problem = problem_of(await run_declared_tool(VIDEO_CORRECTION_MEMORY, ctx, {"command": command}))

    assert problem.code == "correction_refused"
    assert (await _lookup(ctx))["attempts"] == []


async def test_a_different_units_real_receipt_cannot_reserve_an_attempt(
    ctx: ToolHarness, clips: dict[str, Path]
) -> None:
    install_current_video(ctx.project_path, "videos", "E1S02", clips["blue"])
    receipt = await _inspect(ctx, 1, unit="E1S02")

    problem = problem_of(await run_declared_tool(VIDEO_CORRECTION_MEMORY, ctx, {"command": _reservation(receipt)}))

    assert problem.code == "correction_refused"
    assert (await _lookup(ctx))["attempts"] == []
