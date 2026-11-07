"""Read-time derivation of video-unit bodies: utterances and visible-degradation warnings.

The body is the sole source of truth. Like reference images, utterances are mechanically derived,
not persisted (see ADR 0064).

Dialogue syntax shares inline splitting primitives with :mod:`lib.script.reference_video.text_parser`:

- ``@[角色]{台词}`` (whitespace or ASCII/Chinese colons allowed between mention and braces) → ``dialogue`` utterance
- Bare ``{台词}`` → ``voiceover`` utterance
- Both can appear anywhere inline, with multiple marks per line. Unrecognized braces produce no
  utterances and remain verbatim, with warnings only. Never guess a speaker from the nearest inline
  mention: mistaken inference silently binds dialogue to the wrong character's reference audio.

Warnings are locale-neutral ``{"key", "params"}`` entries, matching existing ``result.warnings``;
routers/task lists render them in the request language.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from lib.backends.input_limits import InputLimit
from lib.project.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.references.reference_catalog import build_reference_catalog, split_derivative_reference
from lib.script.reference_video.text_parser import (
    derive_references_from_text,
    speech_line_description,
    speech_speaker_references,
    split_speech_line,
)
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import Utterance

#: Maximum original-line excerpt length echoed in unclosed-brace warnings.
_EXCERPT_LEN = 30

WARN_UNREGISTERED_MENTION = "ref_warn_unregistered_mention"
WARN_UNCLOSED_BRACE = "ref_warn_unclosed_brace"
WARN_BRACES_NOT_SPEECH = "ref_warn_braces_not_speech"
WARN_UNREGISTERED_SPEAKER = "ref_warn_unregistered_speaker"
WARN_SPEAKER_WITHOUT_AUDIO = "ref_warn_speaker_without_audio"
WARN_SPEAKER_AUDIO_UNAVAILABLE = "ref_warn_speaker_audio_unavailable"
WARN_REFERENCE_AUDIO_OVERFLOW = "ref_warn_reference_audio_overflow"
WARN_SILENT_MODEL = "ref_warn_silent_model"
WARN_SILENT_EPISODE = "ref_warn_silent_episode"
WARN_SPEAKER_AUDIO_NEEDS_IMAGE = "ref_warn_speaker_audio_needs_image"
WARN_UNIT_WITHOUT_SCENE = "ref_warn_unit_without_scene"


@dataclass(frozen=True)
class ScriptPreview:
    """Read-time derivation of a video-unit body, supplying the editor's parsed-preview panel."""

    utterances: list[Utterance]
    warnings: list[dict[str, Any]] = field(default_factory=list)


def _warning(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": params}


def derive_utterances(text: str) -> tuple[list[Utterance], list[dict[str, Any]]]:
    """Derive utterances per line and collect syntax warnings for unclosed/unrecognized braces.

    Speech marks may appear anywhere inline; multiple utterances per line follow body order. Warnings
    inspect only residual descriptions **outside marks**: other braces on a line must not swallow its
    recognized speech. The ``line`` parameter is 1-based, anchoring warnings to specific body lines.

    Syntax only: project asset tables are unknown here. :func:`build_script_preview` separately checks
    whether speakers are registered.
    """
    utterances: list[Utterance] = []
    warnings: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        parts = split_speech_line(line)
        for part in parts:
            if isinstance(part, str):
                continue
            if part.speaker:
                utterances.append(Utterance(kind="dialogue", speaker=part.speaker, text=part.text))
            else:
                utterances.append(Utterance(kind="voiceover", text=part.text))
        rest = speech_line_description(parts)
        if rest.count("{") != rest.count("}"):
            warnings.append(_warning(WARN_UNCLOSED_BRACE, line=index, excerpt=line.strip()[:_EXCERPT_LEN]))
        elif "{" in rest or "}" in rest:
            warnings.append(_warning(WARN_BRACES_NOT_SPEECH, line=index))
    return utterances, warnings


@dataclass(frozen=True)
class VoiceBindings:
    """Voice derivation for a script: who speaks and which numbered reference audio each speaker binds.

    ``speakers`` lists registered dialogue speakers in first-appearance order, used to emit the first
    section's voice-characteristic declarations. ``audio_speakers`` is the subset actually bound to
    reference audio. **Its order determines both ``@音频N`` numbering and
    ``VideoGenerationRequest.reference_audio_files`` field order**, from one derivation rather than
    separate recomputations.
    """

    speakers: list[str]
    audio_speakers: list[str]
    warnings: list[dict[str, Any]]


def derive_voice_bindings(
    utterances: list[Utterance],
    characters: dict,
    settings: VoiceRenderSettings,
    *,
    speakers_with_reference_image: Collection[str] | None = None,
) -> VoiceBindings:
    """Mechanically derive speaker order, reference-audio numbering, and degradation warnings from utterances.

    ``settings`` centralizes this render's voice-input tier (see :class:`~lib.script.reference_video.voice_settings
    .VoiceRenderSettings`). Only ``native`` (class A, native audio references) supports reference-audio
    bindings and limits, so missing-reference-audio and count-overflow warnings occur only there;
    ``none`` (truly silent) emits a silent-output notice instead.

    False ``settings.requested_generate_audio`` produces no ``audio_speakers`` regardless of model tier,
    instead notifying that this episode is silent. Reference audio is centralized here, so prompt
    ``@音频N`` and ``reference_audio_files`` disappear together: text cannot promise a voice reference
    without the corresponding audio segment in the request. Dialogue rendering is independent of
    this switch (see :func:`lib.script.reference_video.prompt_render
    ._render_segment_two`); providers can still use dialogue text for lip-sync guidance. The episode
    silence notice precedes the ``none`` tier's ``WARN_SILENT_MODEL``: both explain inaudibility, but
    blaming model silence when users disabled audio would misdirect troubleshooting.

    None ``settings.audio_ready`` checks whether character assets' ``reference_audio`` fields are nonempty.
    Execution supplies character names with resolved, existing files, keeping numbering exactly equal
    to the count of audio segments actually sent. Without synchronized numbering, deleted files make
    ``@音频N`` point to absent segments. Both paths share this function to avoid preview/execution binding
    divergence. For non-None values, distinguish two degradation reasons: unset ``reference_audio`` emits
    ``WARN_SPEAKER_WITHOUT_AUDIO``; a set field absent from the collection emits
    ``WARN_SPEAKER_AUDIO_UNAVAILABLE``. The first requires adding character audio; the second requires
    investigating the already configured audio, so they must not share one warning. Several causes
    can explain the latter: deleted files and values outside ``characters/refs_audio`` are excluded by
    :func:`lib.script.reference_video.prompt_render.resolve_reference_audio_paths`. Say only unavailable,
    without asserting a specific cause.

    True ``settings.requires_reference_image`` means the target backend attaches each audio segment
    to a specific reference item, as wan2.7-r2v does. Offscreen speakers without reference images then
    remain unbound even with usable audio. Binding would consume ``max_reference_audio`` without an
    item for backend attachment, causing another character/scene's misbinding or hard failure. Emit a
    separate degradation warning rather than reusing missing-audio: audio is usable but lacks an image.
    ``speakers_with_reference_image`` contains character names for images actually sent with this
    request and is read only when this flag is True.

    Normalize the character table and both name-based collections (``audio_ready`` /
    ``speakers_with_reference_image``) to asset-name comparison coordinates first
    (:func:`lib.project.asset_types.asset_name_comparison_key`). Omitting either side's normalization
    silently degrades instead of raising: characters appear unregistered and lose voice declarations,
    or lack usable audio and lose bindings. Users receive wrongly voiced output and a nonblocking warning.
    """
    warnings: list[dict[str, Any]] = []
    characters = normalize_asset_bucket(characters)

    seen: list[str] = []
    for entry in utterances:
        speaker = asset_name_comparison_key(entry.speaker or "")
        if speaker and speaker not in seen:
            seen.append(speaker)

    registered: list[str] = []
    for speaker in seen:
        if speaker in characters:
            registered.append(speaker)
        else:
            warnings.append(_warning(WARN_UNREGISTERED_SPEAKER, name=speaker))

    audio_speakers: list[str] = []
    if settings.is_silent:
        # Notify only for attributed dialogue. Unattributed narration is excluded entirely from prompts
        # (see :func:`lib.script.reference_video.prompt_render._render_segment_two`). Both messages
        # describe dialogue as prompt-only guidance, misleading for narration-only scripts. Unregistered
        # speakers still count as attributed: their dialogue renders verbatim. Episode audio-off takes
        # precedence over model silence because users can change that switch now.
        if any(entry.speaker for entry in utterances):
            if not settings.requested_generate_audio:
                warnings.append(_warning(WARN_SILENT_EPISODE))
            else:
                warnings.append(_warning(WARN_SILENT_MODEL, provider=settings.provider_id, model=settings.model_id))
    elif settings.voice_consistency == "native":
        image_names = {asset_name_comparison_key(name) for name in speakers_with_reference_image or ()}
        audio_ready = (
            {asset_name_comparison_key(name) for name in settings.audio_ready}
            if settings.audio_ready is not None
            else None
        )
        # Audio numbering follows first dialogue-speaker appearance, truncated by max_reference_audio.
        for speaker in registered:
            char_data = characters.get(speaker)
            audio_field_set = bool(char_data.get("reference_audio")) if isinstance(char_data, dict) else False
            has_audio = speaker in audio_ready if audio_ready is not None else audio_field_set
            if not has_audio:
                if audio_ready is not None and audio_field_set:
                    warnings.append(_warning(WARN_SPEAKER_AUDIO_UNAVAILABLE, name=speaker))
                else:
                    warnings.append(_warning(WARN_SPEAKER_WITHOUT_AUDIO, name=speaker))
            elif settings.requires_reference_image and speaker not in image_names:
                warnings.append(_warning(WARN_SPEAKER_AUDIO_NEEDS_IMAGE, name=speaker))
            elif settings.max_reference_audio is not None and len(audio_speakers) >= settings.max_reference_audio:
                warnings.append(
                    _warning(WARN_REFERENCE_AUDIO_OVERFLOW, limit=settings.max_reference_audio, name=speaker)
                )
            else:
                audio_speakers.append(speaker)

    return VoiceBindings(speakers=registered, audio_speakers=audio_speakers, warnings=warnings)


def unit_lacks_scene_reference(text: str, project: dict) -> bool:
    """Whether the body references no registered scene asset, provided the project has scenes.

    Without scene references, models freely choose locations, causing adjacent indoor/outdoor units
    to disagree. Always False when the project has no scene assets: no referencable scene exists and
    the notice suggests no actionable change, as with product-centered advertising projects.

    The single predicate shared by editor read-time warnings and per-unit split receipts prevents
    contradictory conclusions across panels. Use unclipped derivations: this asks whether the author
    wrote a scene reference, independent of which images capability limits removed.
    """
    if not (project.get(BUCKET_KEY["scene"]) or {}):
        return False
    references, _missing = derive_references_from_text(text, project)
    return not any(ref.type == "scene" for ref in references)


def build_script_preview(
    text: str,
    project: dict,
    settings: VoiceRenderSettings,
    *,
    max_reference_images: int | None = None,
) -> ScriptPreview:
    """Derive utterances and visible-degradation warnings from a video-unit body.

    ``settings`` is required without fallback, like ``render_unit_prompt``, and must match execution's
    voice-input tier; otherwise previews show bound audio absent from requests. In particular,
    ``requires_reference_image`` (whether the backend requires per-image audio attachment) involves no
    IO and must be synchronized even though previews never access the filesystem. Omitting it shows
    bindings that degrade only during execution, so users discover ineffective voices after generation.
    Leave ``settings.audio_ready`` None for previews: they resolve no files and check only nonempty
    character ``reference_audio`` fields.

    ``max_reference_images`` must match execution capabilities (``VideoLaneResult.max_reference_images``).
    Execution clips body-derived images before rendering, keeping ``图片N`` indices exactly equal to
    sent images (see ``render_unit_prompt`` docstring). Deriving ``character_image_names`` from unclipped
    references delays offscreen degradation past the cutoff: previews show bound audio after images
    were removed, and execution alone warns. Neither ``None`` (unlisted/unresolvable capabilities) nor
    ``0`` (no reference-image acceptance) clips, matching request projection's ``clamp_reference_assets``.

    Unregistered ``@[名称]`` only warns, never blocks: authors wrote the body, with no machine contract
    for previews to protect. Apply the same rule to missing character derivatives in speaker slots:
    ``@[角色/衍生]`` never derives an image because speaker slots never do, so report its unregistered
    status separately or the editor stays silent. Do not repeat missing base characters here:
    ``derive_voice_bindings`` already reports unregistered speakers.
    """
    references, missing = derive_references_from_text(text, project)

    character_references = build_reference_catalog(project).reference_names("character")
    # Names may appear both outside marks and in speaker slots, independently deduplicated without
    # mutual exclusion. Subtract missing to avoid identical preview notices for one unregistered derivative.
    already_reported = set(missing)
    unknown_derivatives = [
        reference
        for reference, (base, derivative) in (
            (reference, split_derivative_reference(reference)) for reference in speech_speaker_references(text)
        )
        if derivative
        and base in character_references
        and reference not in character_references
        and reference not in already_reported
    ]
    warnings = [_warning(WARN_UNREGISTERED_MENTION, name=name) for name in [*missing, *unknown_derivatives]]
    if unit_lacks_scene_reference(text, project):
        warnings.append(_warning(WARN_UNIT_WITHOUT_SCENE))
    utterances, syntax_warnings = derive_utterances(text)
    warnings.extend(syntax_warnings)

    # Audio aligns only to character reference images. Clip by capability limits before checking,
    # matching execution's clip-before-render order; execution likewise does not clip for a zero limit.
    bound = InputLimit.from_json(max_reference_images).clamp_bound()
    clipped_references = references[:bound] if bound is not None else references
    character_image_names = {ref.name for ref in clipped_references if ref.type == "character"}

    bindings = derive_voice_bindings(
        utterances,
        project.get(BUCKET_KEY["character"]) or {},
        settings,
        speakers_with_reference_image=character_image_names,
    )
    warnings.extend(bindings.warnings)

    return ScriptPreview(utterances=utterances, warnings=warnings)
