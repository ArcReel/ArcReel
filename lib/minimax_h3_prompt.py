"""Durable MiniMax H3 prompt artifacts and the Ref2VA six-section contract."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.json_io import atomic_write_json, load_json_or_none

H3_MODEL_TOKEN = "minimax-h3"
H3_PROMPT_SCHEMA_VERSION = 1
H3_PROMPT_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)
H3_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "minimax_h3" / "ref-en.txt"
H3_SYSTEM_PROMPT_SHA256 = "1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7"

_UNIT_ID = re.compile(r"^E0*[1-9]\d*U0*[1-9]\d*$", re.IGNORECASE)
_HEADER = re.compile(
    r"(?m)^(subject_definitions|summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music)\s*:\s*"
)
_TIMESTAMP = re.compile(r"\bAt\s+(\d{2,}):(\d{2})\.(\d{3})\b")
_PICTURE_LABEL = re.compile(r"<Picture\s+(\d+)>")
_AUDIO_LABEL = re.compile(r"<Audio\s+(\d+)>")


def is_minimax_h3_model(model_id: str | None) -> bool:
    """Return whether a configured or namespaced model identifies MiniMax H3."""

    return H3_MODEL_TOKEN in (model_id or "").lower()


def load_h3_system_prompt() -> str:
    """Load the byte-pinned system prompt without appending runtime instructions."""

    raw = H3_SYSTEM_PROMPT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != H3_SYSTEM_PROMPT_SHA256:
        raise RuntimeError(
            f"MiniMax H3 system prompt integrity check failed: expected {H3_SYSTEM_PROMPT_SHA256}, got {digest}"
        )
    return raw.decode("utf-8")


class H3PromptSections(BaseModel):
    """The universal Ref2VA output fields in their fixed serialization order."""

    model_config = ConfigDict(extra="forbid")

    subject_definitions: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    retention_analysis: str = Field(min_length=1)
    detailed_description: str = Field(min_length=1)
    overall_soundscape: str = Field(min_length=1)
    non_diegetic_music: str = Field(min_length=1)

    def render(self) -> str:
        return "\n\n".join(f"{name}:\n{getattr(self, name).strip()}" for name in H3_PROMPT_SECTIONS)


class H3PromptReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    kind: str
    name: str
    path: str | None = None


class H3PromptArtifact(BaseModel):
    """One reviewable provider prompt derived from a finalized request projection."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = H3_PROMPT_SCHEMA_VERSION
    episode: int = Field(ge=1)
    unit_id: str
    status: Literal["pending_review", "confirmed"] = "pending_review"
    sections: H3PromptSections
    rendered_prompt: str
    basis_digest: str
    system_prompt_sha256: Literal["1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7"] = (
        H3_SYSTEM_PROMPT_SHA256
    )
    model_id: str
    optimizer_provider: str
    optimizer_model: str
    request_duration_seconds: int = Field(gt=0)
    resolution: str | None = None
    aspect_ratio: str
    narration_delivery: str
    reference_images: list[H3PromptReference] = Field(default_factory=list)
    reference_audio: list[H3PromptReference] = Field(default_factory=list)
    optimized_at: str
    confirmed_at: str | None = None


class H3PromptState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str
    state: Literal["not_applicable", "missing", "stale", "pending_review", "confirmed"]
    artifact: H3PromptArtifact | None = None


def parse_h3_prompt(raw: str, *, duration_seconds: int, picture_count: int, audio_count: int) -> H3PromptSections:
    """Parse and validate the six ordered sections returned by the optimizer model."""

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    matches = list(_HEADER.finditer(text))
    names = [match.group(1) for match in matches]
    if names != list(H3_PROMPT_SECTIONS):
        raise ValueError(f"H3 prompt sections must appear exactly once and in order: {', '.join(H3_PROMPT_SECTIONS)}")

    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip()
        if not value:
            raise ValueError(f"H3 prompt section is empty: {match.group(1)}")
        values[match.group(1)] = value
    sections = H3PromptSections.model_validate(values)
    rendered = sections.render()
    if len(rendered) > 7000:
        raise ValueError(f"H3 prompt exceeds the provider limit: {len(rendered)} > 7000 characters")

    for minutes, seconds, millis in _TIMESTAMP.findall(sections.detailed_description):
        timestamp = int(minutes) * 60 + int(seconds) + int(millis) / 1000
        if timestamp >= duration_seconds:
            raise ValueError(
                f"H3 prompt timestamp {minutes}:{seconds}.{millis} must be earlier than {duration_seconds}s"
            )
    picture_labels = [int(value) for value in _PICTURE_LABEL.findall(rendered)]
    if picture_labels and max(picture_labels) > picture_count:
        raise ValueError(f"H3 prompt references <Picture {max(picture_labels)}> but only {picture_count} images exist")
    audio_labels = [int(value) for value in _AUDIO_LABEL.findall(rendered)]
    if audio_labels and max(audio_labels) > audio_count:
        raise ValueError(f"H3 prompt references <Audio {max(audio_labels)}> but only {audio_count} audio inputs exist")
    return sections


def h3_prompt_artifact_path(project_path: Path, episode: int, unit_id: str) -> Path:
    if not _UNIT_ID.fullmatch(unit_id):
        raise ValueError(f"invalid reference video unit id: {unit_id!r}")
    return project_path / "drafts" / f"episode_{episode}" / "h3_prompts" / f"{unit_id}.json"


def load_h3_prompt_artifact(project_path: Path, episode: int, unit_id: str) -> H3PromptArtifact | None:
    raw = load_json_or_none(h3_prompt_artifact_path(project_path, episode, unit_id))
    if not isinstance(raw, dict):
        return None
    try:
        return H3PromptArtifact.model_validate(raw)
    except ValueError:
        return None


def save_h3_prompt_artifact(project_path: Path, artifact: H3PromptArtifact) -> None:
    path = h3_prompt_artifact_path(project_path, artifact.episode, artifact.unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, artifact.model_dump(mode="json"))


def confirm_h3_prompt_artifact(
    project_path: Path,
    episode: int,
    unit_id: str,
    *,
    expected_basis_digest: str,
) -> H3PromptArtifact:
    artifact = load_h3_prompt_artifact(project_path, episode, unit_id)
    if artifact is None:
        raise ValueError(f"H3 prompt is missing for {unit_id}")
    if artifact.basis_digest != expected_basis_digest:
        raise ValueError(f"H3 prompt is stale for {unit_id}")
    updated = artifact.model_copy(
        update={"status": "confirmed", "confirmed_at": datetime.now(UTC).isoformat()}
    )
    save_h3_prompt_artifact(project_path, updated)
    return updated


def canonical_basis_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


__all__ = [
    "H3PromptArtifact",
    "H3PromptReference",
    "H3PromptSections",
    "H3PromptState",
    "H3_SYSTEM_PROMPT_SHA256",
    "canonical_basis_digest",
    "confirm_h3_prompt_artifact",
    "file_sha256",
    "h3_prompt_artifact_path",
    "is_minimax_h3_model",
    "load_h3_prompt_artifact",
    "load_h3_system_prompt",
    "parse_h3_prompt",
    "save_h3_prompt_artifact",
]
