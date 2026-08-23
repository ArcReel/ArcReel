"""Shared project-level Unified Video Style operation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from weakref import WeakKeyDictionary

from pydantic import ValidationError

from lib.project_manager import ProjectManager, get_project_manager
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from lib.video_style import (
    UnifiedVideoStyle,
    UnifiedVideoStyleDraft,
    UnifiedVideoStylePatch,
    VideoStyleSource,
)

_ANALYSIS_CONTEXT_LIMIT = 18_000
_STYLE_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()

_ANALYSIS_SYSTEM_PROMPT = """You are ArcReel's project-level video director.
Infer one unified video style that can guide every video unit in the project.
Return only the requested JSON schema with one `prompt` string. Write one concise, coherent paragraph in the
project's source language. Do not use headings, bullets, field labels or JSON-like fragments inside the paragraph.

Before writing the paragraph, internally check these dimensions:
- moving-image treatment beyond the existing still-image style anchor;
- shot scale, lens feeling, camera motion, movement intensity and stability;
- shot duration, cut density, action density and rhythm;
- sound focus: balanced, ASMR, dialogue, ambience or silence;
- background music: forbid it only when project material explicitly forbids it, describe it only when explicit,
  otherwise leave the choice open in natural language;
- ambience and physical sound design; for ASMR, name the close-miked material and action sounds;
- any other constraint that truly applies to every video unit.

Do not invent a hard prohibition such as no music. Do not repeat character, plot or per-shot facts as project-wide style.
"""

GeneratorFactory = Callable[[str], Awaitable[TextGenerator]]


async def _default_generator_factory(project_name: str) -> TextGenerator:
    return await TextGenerator.create(TextTaskType.VIDEO_STYLE_ANALYSIS, project_name)


def _parse_persisted(raw: object) -> UnifiedVideoStyle | None:
    if raw is None:
        return None
    return UnifiedVideoStyle.model_validate(raw)


def _episode_script_names(project: Mapping[str, Any], preferred_episode: int | None) -> list[str]:
    entries: list[tuple[int, str]] = []
    for entry in project.get("episodes") or []:
        if not isinstance(entry, Mapping):
            continue
        episode = entry.get("episode")
        script_file = entry.get("script_file")
        if not isinstance(episode, int) or isinstance(episode, bool) or not isinstance(script_file, str):
            continue
        clean = script_file.removeprefix("scripts/").strip()
        if clean:
            entries.append((episode, clean))
    entries.sort(key=lambda item: (item[0] != preferred_episode, item[0]))
    if preferred_episode is not None and not any(episode == preferred_episode for episode, _ in entries):
        entries.insert(0, (preferred_episode, f"episode_{preferred_episode}.json"))
    return list(dict.fromkeys(name for _, name in entries))


def _script_excerpt(script: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(script.get("title"), str):
        result["title"] = script["title"]
    for key in ("video_units", "scenes", "segments", "shots"):
        items = script.get(key)
        if not isinstance(items, list):
            continue
        selected: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            fields = {
                field: item[field]
                for field in (
                    "unit_id",
                    "scene_id",
                    "segment_id",
                    "duration_seconds",
                    "text",
                    "visual_description",
                    "video_prompt",
                    "action",
                    "source_text",
                )
                if item.get(field) not in (None, "", [])
            }
            if fields:
                selected.append(fields)
        if selected:
            result[key] = selected
            break
    return result


class VideoStyleService:
    """Read, edit and lazily infer the one project-level video style."""

    def __init__(
        self,
        project_manager: ProjectManager | None = None,
        *,
        generator_factory: GeneratorFactory = _default_generator_factory,
    ) -> None:
        self._pm = project_manager or get_project_manager()
        self._generator_factory = generator_factory

    def get(self, project_name: str) -> UnifiedVideoStyle | None:
        project = self._pm.load_project_readonly(project_name)
        return _parse_persisted(project.get("video_style"))

    def update(
        self,
        project_name: str,
        patch: UnifiedVideoStylePatch | UnifiedVideoStyleDraft | Mapping[str, Any],
        *,
        source: VideoStyleSource = "user",
    ) -> UnifiedVideoStyle:
        if isinstance(patch, UnifiedVideoStylePatch | UnifiedVideoStyleDraft):
            values = patch.model_dump(exclude_none=True)
        else:
            values = dict(patch)
        saved: UnifiedVideoStyle | None = None

        def _mutate(project: dict[str, Any]) -> None:
            nonlocal saved
            current = _parse_persisted(project.get("video_style"))
            base: dict[str, Any] = {}
            if current is not None:
                base.update(current.model_dump(exclude={"source", "updated_at"}))
            base.update(values)
            draft = UnifiedVideoStyleDraft.model_validate(base)
            saved = UnifiedVideoStyle(
                **draft.model_dump(),
                source=source,
                updated_at=datetime.now(UTC),
            )
            project["video_style"] = saved.model_dump(mode="json")

        self._pm.update_project(project_name, _mutate)
        assert saved is not None
        return saved

    def _analysis_payload(self, project_name: str, preferred_episode: int | None) -> dict[str, Any]:
        project = self._pm.load_project_readonly(project_name)
        scripts: list[dict[str, Any]] = []
        for script_name in _episode_script_names(project, preferred_episode):
            try:
                script = self._pm.load_script_readonly(project_name, script_name)
            except FileNotFoundError:
                continue
            excerpt = _script_excerpt(script)
            if excerpt:
                scripts.append(excerpt)
            candidate = json.dumps(scripts, ensure_ascii=False)
            if len(candidate) >= _ANALYSIS_CONTEXT_LIMIT:
                break
        payload = {
            "project": {
                "title": project.get("title"),
                "content_mode": project.get("content_mode"),
                "source_language": project.get("source_language"),
                "overview": project.get("overview"),
                "brief": project.get("brief"),
                "visual_style": project.get("style_description") or project.get("style"),
            },
            "scripts": scripts,
        }
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) <= _ANALYSIS_CONTEXT_LIMIT:
            return payload
        scripts_encoded = json.dumps(scripts, ensure_ascii=False)
        payload["scripts"] = scripts_encoded[:_ANALYSIS_CONTEXT_LIMIT]
        return payload

    async def ensure(
        self,
        project_name: str,
        *,
        preferred_episode: int | None = None,
    ) -> tuple[UnifiedVideoStyle, bool]:
        """Return the existing style, or infer and persist it exactly once per process."""

        loop = asyncio.get_running_loop()
        lock = _STYLE_LOCKS.setdefault(loop, {}).setdefault(project_name, asyncio.Lock())
        async with lock:
            current = await asyncio.to_thread(self.get, project_name)
            if current is not None:
                return current, False
            payload = await asyncio.to_thread(self._analysis_payload, project_name, preferred_episode)
            generator = await self._generator_factory(project_name)
            result = await generator.generate(
                TextGenerationRequest(
                    prompt=json.dumps(payload, ensure_ascii=False, indent=2),
                    system_prompt=_ANALYSIS_SYSTEM_PROMPT,
                    response_schema=UnifiedVideoStyleDraft,
                    max_output_tokens=2048,
                ),
                project_name=project_name,
            )
            try:
                draft = UnifiedVideoStyleDraft.model_validate_json(strip_json_code_fences(result.text))
            except ValidationError as exc:
                raise ValueError(f"video style analysis output is invalid: {exc}") from exc

            created = False
            effective: UnifiedVideoStyle | None = None

            def _create_if_missing(project: dict[str, Any]) -> None:
                nonlocal created, effective
                existing = _parse_persisted(project.get("video_style"))
                if existing is not None:
                    effective = existing
                    return
                effective = UnifiedVideoStyle(
                    **draft.model_dump(),
                    source="agent",
                    updated_at=datetime.now(UTC),
                )
                project["video_style"] = effective.model_dump(mode="json")
                created = True

            await asyncio.to_thread(self._pm.update_project, project_name, _create_if_missing)
            assert effective is not None
            return effective, created


__all__ = ["VideoStyleService"]
