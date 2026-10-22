"""Repeat a saved local edit using ArcReel's mixer and automatic export checks.

No models are called. Approved picture, narration, and music inputs stay unchanged.
Run with: python -m scripts.refine_episode recipe.json --output-dir exports
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import portalocker
from pydantic import BaseModel, ConfigDict, Field

from lib.final_cut.ffmpeg_render import BgmInput, NarrationInput, accept_final_cut, render_plan_to_file
from lib.final_cut.render_plan import OutputProfile, RenderClip, RenderPlan, plan_render
from lib.infra.ffmpeg import ffmpeg_executable
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.infra.media_probe import probe_media


class RecipeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Picture(RecipeModel):
    id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    in_frame: int = Field(default=0, ge=0)
    out_frame: int = Field(gt=0)
    source_volume: float = Field(default=0.0, ge=0, le=2)


class Narration(RecipeModel):
    file: str = Field(min_length=1)
    start_seconds: float = Field(default=0.0, ge=0)


class Music(RecipeModel):
    file: str = Field(min_length=1)
    volume: float = Field(default=1.0, gt=0, le=2)
    fade_in_seconds: float = Field(default=0.0, ge=0)
    fade_out_seconds: float = Field(default=0.0, ge=0)


class Recipe(RecipeModel):
    schema_version: Literal[1]
    width: int = Field(ge=64, le=4096)
    height: int = Field(ge=64, le=4096)
    fps: int = Field(ge=1, le=60)
    shots: list[Picture] = Field(min_length=1, max_length=500)
    required_tracks: list[Literal["narration", "music"]]
    narration: Narration | None = None
    music: Music | None = None


def source_path(base: Path, filename: str) -> Path:
    path = (base / filename).resolve()
    if not path.is_file():
        raise ValueError(f"Required input is missing: {filename}")
    return path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def recipe_sources(recipe: Recipe, base: Path) -> list[Path]:
    return list(
        dict.fromkeys(
            source_path(base, item.file) for item in [*recipe.shots, recipe.narration, recipe.music] if item is not None
        )
    )


def input_fingerprint(recipe: Recipe, base: Path) -> tuple[str, dict[str, str]]:
    sources = {str(path): digest(path) for path in recipe_sources(recipe, base)}
    payload = {"recipe": recipe.model_dump(), "sources": sources, "pass_version": 2}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), sources


async def prepare_plan(
    recipe: Recipe, base: Path
) -> tuple[RenderPlan, tuple[NarrationInput, ...], tuple[BgmInput, ...]]:
    if recipe.width % 2 or recipe.height % 2:
        raise ValueError("Picture dimensions must be even numbers.")
    if len({shot.id for shot in recipe.shots}) != len(recipe.shots):
        raise ValueError("Shot IDs must be unique.")
    for required in recipe.required_tracks:
        if getattr(recipe, required) is None:
            raise ValueError(f"Required {required} is missing; the export will not silently omit it.")
    clips: list[RenderClip] = []
    for shot in recipe.shots:
        if shot.out_frame <= shot.in_frame:
            raise ValueError(f"Shot {shot.id} has an empty or reversed trim.")
        path = await asyncio.to_thread(source_path, base, shot.file)
        probe = await probe_media(path)
        video = probe.first_stream("video")
        if video is None or video.duration_seconds is None:
            raise ValueError(f"Shot {shot.id} has no playable picture.")
        if shot.out_frame / recipe.fps > video.duration_seconds + 0.001:
            raise ValueError(f"Shot {shot.id} asks for frames beyond its source; no freeze extension will be invented.")
        if recipe.narration is not None and shot.source_volume != 0:
            raise ValueError(
                f"Shot {shot.id} retains mixed clip sound alongside replacement narration; mute it to avoid doubled voices."
            )
        clips.append(
            RenderClip(
                clip_id=shot.id,
                unit_id=shot.id,
                video_path=path,
                source_in_us=round(shot.in_frame / recipe.fps * 1_000_000),
                source_duration_us=round((shot.out_frame - shot.in_frame) / recipe.fps * 1_000_000),
                hold_us=0,
                source_volume=shot.source_volume,
                has_audio=probe.first_stream("audio") is not None,
                transition_to_next=None,
                media_duration_us=round(video.duration_seconds * 1_000_000),
            )
        )
    plan = plan_render(clips, OutputProfile(recipe.width, recipe.height, recipe.fps))
    narrations: tuple[NarrationInput, ...] = ()
    music: tuple[BgmInput, ...] = ()
    if recipe.narration is not None:
        path = await asyncio.to_thread(source_path, base, recipe.narration.file)
        track = (await probe_media(path)).first_stream("audio")
        if track is None or track.duration_seconds is None:
            raise ValueError("Narration input has no playable audio.")
        if recipe.narration.start_seconds + track.duration_seconds > plan.duration_seconds + 0.001:
            raise ValueError("Narration would be cut off; lengthen the picture edit before exporting.")
        narrations = (NarrationInput(path, round(recipe.narration.start_seconds * 1_000_000)),)
    if recipe.music is not None:
        path = await asyncio.to_thread(source_path, base, recipe.music.file)
        track = (await probe_media(path)).first_stream("audio")
        if track is None or track.duration_seconds is None or track.duration_seconds + 0.001 < plan.duration_seconds:
            raise ValueError("Music must contain audio for the entire edit; prepare a continuous cue before exporting.")
        if recipe.music.fade_in_seconds + recipe.music.fade_out_seconds > plan.duration_seconds:
            raise ValueError("Music fades overlap; shorten the fades.")
        music = (
            BgmInput(
                path,
                0,
                0,
                round(plan.duration_seconds * 1_000_000),
                recipe.music.volume,
                round(recipe.music.fade_in_seconds * 1_000_000),
                round(recipe.music.fade_out_seconds * 1_000_000),
            ),
        )
    return plan, narrations, music


def refine(recipe_file: Path, output_dir: Path) -> dict:
    recipe = Recipe.model_validate_json(recipe_file.read_text(encoding="utf-8"))
    base = recipe_file.resolve().parent
    fingerprint, sources = input_fingerprint(recipe, base)
    destination = output_dir.resolve() / fingerprint
    destination.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(destination / "run.lock", timeout=0):
        finished = destination / "finished"
        report_path = finished / "report.json"
        output = finished / "episode.mp4"
        saved = load_json_or_none(report_path)
        if (
            isinstance(saved, dict)
            and saved.get("complete") is True
            and saved.get("fingerprint") == fingerprint
            and output.is_file()
            and saved.get("output_sha256") == digest(output)
        ):
            return {**saved, "output": str(output), "reused": True}
        if finished.exists():
            raise ValueError(
                "An unverified output already exists. It has been preserved; use a fresh output directory."
            )
        progress = {"complete": False, "fingerprint": fingerprint, "stage": "checking_inputs"}
        atomic_write_json(destination / "progress.json", progress)
        try:
            plan, narration, music = asyncio.run(prepare_plan(recipe, base))
            progress["stage"] = "assembling"
            atomic_write_json(destination / "progress.json", progress)
            with tempfile.TemporaryDirectory(prefix="refine-", dir=destination) as folder:
                workspace = Path(folder)
                candidate = workspace / "candidate.mp4"
                asyncio.run(
                    render_plan_to_file(
                        ffmpeg_executable(), plan, candidate, workspace, narrations=narration, bgm=music
                    )
                )
                progress["stage"] = "checking_export"
                atomic_write_json(destination / "progress.json", progress)
                acceptance = asyncio.run(accept_final_cut(candidate, plan))
                if input_fingerprint(recipe, base)[0] != fingerprint:
                    raise ValueError("An input changed during assembly; no final output was published.")
                report = {
                    "complete": True,
                    "fingerprint": fingerprint,
                    "output": str(output),
                    "output_sha256": digest(candidate),
                    "reused": False,
                    "recipe": recipe.model_dump(),
                    "source_sha256": sources,
                    "acceptance": asdict(acceptance),
                    "narration_continuous": bool(narration),
                    "music_continuous": bool(music),
                    "model_calls": 0,
                    "creative_review": "Not assessed: voice identity, acting, character fidelity, lip sync, and musical fit require review.",
                }
                publication = workspace / "finished"
                publication.mkdir()
                candidate.replace(publication / output.name)
                atomic_write_json(publication / report_path.name, report)
                publication.replace(finished)
            atomic_write_json(
                destination / "progress.json", {**progress, "complete": True, "stage": "ready_for_review"}
            )
            return report
        except Exception as exc:
            atomic_write_json(destination / "progress.json", {**progress, "stage": "failed", "error": str(exc)})
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(refine(args.recipe, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
