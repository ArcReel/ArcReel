"""Finalize whole-episode scripts before persistence and project scripts from script plans.

Prompt authoring, content-confirmation conversion, and legacy migration all finalize here: episode numbers/ID prefixes,
pending-authoring/replanning flags, project fields, novel info, and metadata. Reference-to-video confirmed-duration
overrides and effective-tier validation require runtime video capabilities, so remain between these two finalization
stages in ``ScriptGenerator._add_metadata``.

This module touches neither text generation nor provider configuration; migration steps can import it directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from lib.episode.episode_ids import episode_title
from lib.script.script_models import PENDING_AUTHORING_FIELD
from lib.script.script_plan_entries import ScriptPlanKind, plan_entry_content, plan_variant
from lib.script.script_skeleton import resolve_declared_kind, resolve_kind_items, rewrite_episode_prefix
from lib.speech.speech_composition import admit_script_unit

#: ``metadata.generator`` marker for content-confirmed scripts: entries projected from plans without text models.
SCRIPT_PLAN_CONVERSION_GENERATOR = "script_plan_conversion"

#: ``metadata.generator`` marker for blank-start scripts: creator-written entries without text models.
BLANK_SCRIPT_GENERATOR = "blank"


def episode_ledger_entry(project: Mapping[str, Any], episode: int) -> dict[str, Any]:
    """Find project.json episodes entries by episode number; return an empty dict if absent."""

    episodes = project.get("episodes")
    return next(
        (entry for entry in episodes or [] if isinstance(entry, dict) and entry.get("episode") == episode),
        {},
    )


def ledger_outline(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize ledger outline to a dict, empty for absent/invalid shapes."""

    raw_outline = entry.get("outline")
    return raw_outline if isinstance(raw_outline, dict) else {}


def prepare_script_entries(script_data: dict[str, Any], *, project: Mapping[str, Any], episode: int) -> list[str]:
    """Write episode number, rewrite ID prefixes, reset pending/replan flags; return rewritten IDs in entry order."""

    # CLI --episode is the authoritative episode number. AI schemas omit episode;
    # fill it here before persistence.
    script_data["episode"] = int(episode)
    # Rewrite E\d+ prefixes in segment/scene/unit IDs defensively, avoiding cross-episode filename
    # collisions from incorrect LLM episode numbers (such as E2 overwriting storyboards/scene_E1S01.png).
    ep = int(episode)
    # Canonically resolve skeletons, then use resolve_kind_items for arrays/ID fields and rewrite all item prefixes
    # (all three reference-video content modes map to video_units, needing no generation-mode branch).
    kind = resolve_declared_kind(project.get("content_mode", "narration"), project.get("generation_mode"))
    raw_items, id_field, _kind = resolve_kind_items(script_data, kind=kind)
    # Raw dicts saved after failed validation may contain nonlist arrays (LLM scalars).
    # `... or []` catches only falsy values, not truthy scalars; isinstance prevents `for` crashes.
    items = raw_items if isinstance(raw_items, list) else []
    rewritten_ids: list[str] = []
    for item in items:
        if isinstance(item, dict) and id_field in item:
            item[id_field] = rewrite_episode_prefix(item.get(id_field), ep)
            rewritten_ids.append(str(item[id_field]))

    for item in items:
        if not isinstance(item, dict):
            continue
        # This round's authored entries already have visuals; content-confirmation conversion resets all flags afterward.
        item.pop(PENDING_AUTHORING_FIELD, None)
        admission = admit_script_unit(kind, item, ignore_marker=True)
        if admission.allowed:
            item.pop("needs_replan", None)
        else:
            item["needs_replan"] = True
    return rewritten_ids


def finish_script_document(
    script_data: dict[str, Any], *, project: Mapping[str, Any], episode: int, generator: str
) -> dict[str, Any]:
    """Fill project fields, ledger hooks, novel info, and metadata; strip deprecated fields."""

    content_mode = project.get("content_mode", "narration")
    # content_mode means content type only (narration/drama/ad); video source is project-level.
    # Scripts persist no generation-mode marker; generation dispatch always reads the project mode.
    # Force overwrite for reference scripts: ReferenceVideoScript.content_mode defaults to
    # "narration", so setdefault cannot recover project truth. Nonreference LLM schemas already emit
    # narration/drama; setdefault only supplies fallback.
    if content_mode != "ad" and project.get("generation_mode") == "reference_video":
        script_data["content_mode"] = content_mode
    else:
        script_data.setdefault("content_mode", content_mode)

    # Episode hooks/teasers: the ledger is authoritative, so overwrite from it
    # (LLMs do not fill these; model_dump leaves None defaults). None without ledger planning data.
    # Ads always have one episode, no planning ledger, and neither model field; skip injection.
    if content_mode != "ad":
        entry = episode_ledger_entry(project, int(episode))
        script_data["hook"] = entry.get("hook")
        script_data["next_episode_teaser"] = ledger_outline(entry).get("next_episode_teaser")

    # Add novel metadata
    # Guard semantics: novel is hidden by SkipJsonSchema, but default_factory=NovelInfo
    # ensures model_dump includes {"title":"","chapter":""}. Key presence cannot detect missing injection;
    # check content instead and reinject if either title/chapter is empty.
    novel = script_data.get("novel")
    if not isinstance(novel, dict) or not novel.get("title") or not novel.get("chapter"):
        script_data["novel"] = {
            "title": project.get("title", ""),
            "chapter": episode_title(project, int(episode)),
        }
    # Strip deprecated source_file, which AI may invent
    novel = script_data.get("novel")
    if isinstance(novel, dict):
        novel.pop("source_file", None)

    # Strip script-level generation_mode: project.json is authoritative; scripts retain no marker.
    # On validation failure script_data is the backend's unfiltered dict, and legacy regeneration may
    # carry old values forward; remove here or persistence would reintroduce them.
    script_data.pop("generation_mode", None)

    # Add timestamps
    now = datetime.now(UTC).isoformat()
    script_data.setdefault("metadata", {})
    script_data["metadata"]["created_at"] = now
    script_data["metadata"]["updated_at"] = now
    script_data["metadata"]["generator"] = generator

    # Strip deprecated episode aggregates: item counts, total duration, and character/scene/prop summaries derive
    # from script bodies and compute on project-summary reads; persisting duplicates would drift.
    script_data["metadata"].pop("total_scenes", None)
    script_data["metadata"].pop("estimated_duration_seconds", None)
    script_data.pop("duration_seconds", None)
    script_data.pop("characters_in_episode", None)
    script_data.pop("clues_in_episode", None)

    return script_data


def build_materialized_script(
    project: Mapping[str, Any],
    episode: int,
    *,
    plan_kind: ScriptPlanKind,
    plan_entries: Sequence[Mapping[str, Any]],
    title: str | None,
) -> dict[str, Any]:
    """Project plan entries into a whole formal script without disk writes or reading plan files.

    All entries await authoring with empty visuals; reference-unit bodies, durations, and source correspondence come from plans.
    Use ``title`` (only legacy migration keeps drama plan titles), otherwise the episode ledger title; leave empty if ledger
    title is empty, letting presentation derive names from broadcast position. Content confirmation always uses ledger titles.
    Both content confirmation and legacy migration share this projection.
    """

    items: list[dict[str, Any]] = []
    for entry in plan_entries:
        content = plan_entry_content(plan_kind, entry)
        if plan_kind != "reference_video":
            content = {**content, "image_prompt": None, "video_prompt": None}
        items.append(content)
    items_key = plan_variant(plan_kind).skeleton_kind
    script_data: dict[str, Any] = {"title": title or episode_title(project, episode), items_key: items}
    prepare_script_entries(script_data, project=project, episode=episode)
    finish_script_document(script_data, project=project, episode=episode, generator=SCRIPT_PLAN_CONVERSION_GENERATOR)
    for item in script_data[items_key]:
        item[PENDING_AUTHORING_FIELD] = True
    return script_data


def build_blank_script(project: Mapping[str, Any], episode: int) -> dict[str, Any]:
    """Whole formal script for “Start from blank”: project axes select the skeleton, entries empty, no persistence.

    Use the ledger title, leaving it empty if absent.
    """
    items_key = resolve_declared_kind(project.get("content_mode"), project.get("generation_mode"))
    script_data: dict[str, Any] = {"title": episode_title(project, episode), items_key: []}
    prepare_script_entries(script_data, project=project, episode=episode)
    return finish_script_document(script_data, project=project, episode=episode, generator=BLANK_SCRIPT_GENERATOR)


__all__ = [
    "BLANK_SCRIPT_GENERATOR",
    "SCRIPT_PLAN_CONVERSION_GENERATOR",
    "build_blank_script",
    "build_materialized_script",
    "episode_ledger_entry",
    "finish_script_document",
    "ledger_outline",
    "prepare_script_entries",
]
