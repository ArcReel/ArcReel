"""
script_generator.py - Script generator

Reads the Step 1/2 Markdown intermediate files and calls the text generation backend
to produce the final JSON script.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from lib.config.registry import PROVIDER_REGISTRY
from lib.prompt_builders_script import (
    build_drama_prompt,
    build_narration_prompt,
)
from lib.script_models import (
    DramaEpisodeScript,
    NarrationEpisodeScript,
)
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator

logger = logging.getLogger(__name__)


class ScriptGenerator:
    """
    Script generator.

    Reads the Step 1/2 Markdown intermediate files and calls the TextBackend
    to produce the final JSON script.
    """

    def __init__(self, project_path: str | Path, generator: Optional["TextGenerator"] = None):
        """
        Initialise the generator.

        Args:
            project_path: Project directory path, e.g. projects/test0205
            generator: TextGenerator instance (optional). If None, only build_prompt() dry-run is supported.
        """
        self.project_path = Path(project_path)
        self.generator = generator

        # Load project.json
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get("content_mode", "narration")

    @classmethod
    async def create(cls, project_path: str | Path) -> "ScriptGenerator":
        """Async factory method: automatically loads provider config from DB to create a TextGenerator."""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name)
        return cls(project_path, generator)

    async def generate(
        self,
        episode: int,
        output_path: Path | None = None,
    ) -> Path:
        """
        Asynchronously generate the episode script.

        Args:
            episode: Episode number
            output_path: Output path; defaults to scripts/episode_{episode}.json

        Returns:
            Path to the generated JSON file
        """
        if self.generator is None:
            raise RuntimeError("TextGenerator is not initialised. Use the ScriptGenerator.create() factory method.")

        # 1. Load the intermediate file
        step1_md = self._load_step1(episode)

        # 2. Extract characters and clues (from project.json)
        characters = self.project_json.get("characters", {})
        clues = self.project_json.get("clues", {})

        # 3. Build the prompt
        if self.content_mode == "narration":
            prompt = build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                clues=clues,
                segments_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
            schema = NarrationEpisodeScript
        else:
            prompt = build_drama_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                clues=clues,
                scenes_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
            schema = DramaEpisodeScript

        # 4. Call the TextBackend
        logger.info("Generating episode %d script...", episode)
        project_name = self.project_path.name
        result = await self.generator.generate(
            TextGenerationRequest(prompt=prompt, response_schema=schema),
            project_name=project_name,
        )
        response_text = result.text

        # 5. Parse and validate the response
        script_data = self._parse_response(response_text, episode)

        # 6. Supplement metadata
        script_data = self._add_metadata(script_data, episode)

        # 7. Save the file
        if output_path is None:
            output_path = self.project_path / "scripts" / f"episode_{episode}.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)

        logger.info("Script saved to %s", output_path)
        return output_path

    def build_prompt(self, episode: int) -> str:
        """
        Build the prompt (for dry-run mode).

        Args:
            episode: Episode number

        Returns:
            The constructed prompt string
        """
        step1_md = self._load_step1(episode)
        characters = self.project_json.get("characters", {})
        clues = self.project_json.get("clues", {})

        if self.content_mode == "narration":
            return build_narration_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                clues=clues,
                segments_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )
        else:
            return build_drama_prompt(
                project_overview=self.project_json.get("overview", {}),
                style=self.project_json.get("style", ""),
                style_description=self.project_json.get("style_description", ""),
                characters=characters,
                clues=clues,
                scenes_md=step1_md,
                supported_durations=self._resolve_supported_durations(),
                default_duration=self.project_json.get("default_duration"),
                aspect_ratio=self._resolve_aspect_ratio(),
            )

    def _resolve_supported_durations(self) -> list[int] | None:
        """Resolve the supported duration list for the current video model from the project config or registry."""
        durations = self.project_json.get("_supported_durations")
        if durations and isinstance(durations, list):
            return durations
        video_backend = self.project_json.get("video_backend")
        if video_backend and isinstance(video_backend, str) and "/" in video_backend:
            provider_id, model_id = video_backend.split("/", 1)
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta:
                model_info = provider_meta.models.get(model_id)
                if model_info and model_info.supported_durations:
                    return list(model_info.supported_durations)
        return None

    def _resolve_aspect_ratio(self) -> str:
        """Resolve the project's aspect_ratio with backward compatibility."""
        if "aspect_ratio" in self.project_json and isinstance(self.project_json["aspect_ratio"], str):
            return self.project_json["aspect_ratio"]
        return "9:16" if self.content_mode == "narration" else "16:9"

    def _load_project_json(self) -> dict:
        """Load project.json."""
        path = self.project_path / "project.json"
        if not path.exists():
            raise FileNotFoundError(f"project.json not found: {path}")

        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_step1(self, episode: int) -> str:
        """Load the Step 1 Markdown file, supporting two filename conventions."""
        drafts_path = self.project_path / "drafts" / f"episode_{episode}"
        if self.content_mode == "narration":
            primary_path = drafts_path / "step1_segments.md"
            fallback_path = drafts_path / "step1_normalized_script.md"
        else:
            primary_path = drafts_path / "step1_normalized_script.md"
            fallback_path = drafts_path / "step1_segments.md"

        if not primary_path.exists():
            if fallback_path.exists():
                logger.warning("Step 1 file not found: %s, falling back to %s", primary_path, fallback_path)
                primary_path = fallback_path
            else:
                raise FileNotFoundError(f"Step 1 file not found: {primary_path}")

        with open(primary_path, encoding="utf-8") as f:
            return f.read()

    def _parse_response(self, response_text: str, episode: int) -> dict:
        """
        Parse and validate the TextBackend response.

        Args:
            response_text: JSON text returned by the API
            episode: Episode number

        Returns:
            Validated script data dictionary
        """
        # Strip possible markdown wrapper
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Parse JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON parse failed: {e}")

        # Pydantic validation
        try:
            if self.content_mode == "narration":
                validated = NarrationEpisodeScript.model_validate(data)
            else:
                validated = DramaEpisodeScript.model_validate(data)
            return validated.model_dump()
        except ValidationError as e:
            logger.warning("Data validation warning: %s", e)
            # Return raw data, allowing partial schema non-conformance
            return data

    def _add_metadata(self, script_data: dict, episode: int) -> dict:
        """
        Supplement script metadata.

        Args:
            script_data: Script data
            episode: Episode number

        Returns:
            Script data with metadata added
        """
        # Ensure essential fields exist
        script_data.setdefault("episode", episode)
        script_data.setdefault("content_mode", self.content_mode)

        # Add novel info
        if "novel" not in script_data:
            script_data["novel"] = {
                "title": self.project_json.get("title", ""),
                "chapter": f"Episode {episode}",
            }
        # Strip deprecated source_file (AI may fabricate it)
        novel = script_data.get("novel")
        if isinstance(novel, dict):
            novel.pop("source_file", None)

        # Add timestamps
        now = datetime.now().isoformat()
        script_data.setdefault("metadata", {})
        script_data["metadata"]["created_at"] = now
        script_data["metadata"]["updated_at"] = now
        script_data["metadata"]["generator"] = self.generator.model if self.generator else "unknown"

        # Compute statistics + aggregate episode-level characters/clues (collected from segment/scene)
        if self.content_mode == "narration":
            segments = script_data.get("segments", [])
            script_data["metadata"]["total_segments"] = len(segments)
            script_data["duration_seconds"] = sum(int(s.get("duration_seconds", 4)) for s in segments)
            chars_field, clues_field = "characters_in_segment", "clues_in_segment"
            items = segments
        else:
            scenes = script_data.get("scenes", [])
            script_data["metadata"]["total_scenes"] = len(scenes)
            script_data["duration_seconds"] = sum(int(s.get("duration_seconds", 8)) for s in scenes)
            chars_field, clues_field = "characters_in_scene", "clues_in_scene"
            items = scenes

        all_chars: set[str] = set()
        all_clues: set[str] = set()
        for item in items:
            for name in item.get(chars_field, []):
                if isinstance(name, str):
                    all_chars.add(name)
            for name in item.get(clues_field, []):
                if isinstance(name, str):
                    all_clues.add(name)
        script_data.pop("characters_in_episode", None)
        script_data.pop("clues_in_episode", None)

        return script_data
