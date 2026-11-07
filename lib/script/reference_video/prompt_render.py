"""Three-part reference-to-video rendering: unit body + asset registry + capabilities → video-model prompt.

Mechanically generate part one (subject bindings + voice declarations) and part three (style + exclusions),
without depending on LLM compliance. Builtin ``reference_video/unit`` renders the whole prompt; this module
projects the first two parts into slots. Pure rendering is never persisted, so legacy content needs no migration.

Three responsibilities:

- **Part one**: shorthand ``<X>@图片N`` bindings (numbers follow sent reference-image order), plus centralized
  voice declarations (``<X>: Dialogue voice reference @音频N; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue; Voice characteristics: …``). Audible A/B models receive voice descriptions;
  neither silent path (class C models without audio or episode audio disabled) does
- **Part two**: body with character-speech markers (``<X> says {台词}``); speakerless narration (bare ``{台词}``)
  never reaches video models, using TTS/post-production voiceover instead
- **Part three**: shared style + ``Avoid`` (all video exclusions for this route); append clone/twin exclusions
  when at least two different characters appear. Product references additionally receive fidelity instructions

All content modes take free-text unit bodies containing part two only. Parse ``@[X]`` mentions to derive
subject bindings/reference order (:func:`render_unit_prompt`). Text has no absolute seconds; request fields carry duration.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.project.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.reference_video.script_preview import (
    WARN_UNREGISTERED_MENTION,
    derive_utterances,
    derive_voice_bindings,
)
from lib.script.reference_video.text_parser import (
    SpeechMark,
    derive_references_from_text,
    extract_mentions,
    render_mentions_as_subjects,
    resolve_references,
    split_speech_line,
)
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_models import ReferenceResource
from lib.speech.audio_utils import resolve_audio_ref_path

#: Fixed project directory for character reference audio, shared with uploads/TTS samples.
ASSET_AUDIO_SUBDIR = "characters/refs_audio"


def _character_bucket(project: dict) -> dict[str, Any]:
    """Project characters keyed by normalized comparison names; treat nondicts (externally corrupted project.json) as empty.

    Validators inspect entries only when the field is a dict (``lib.project.data_validator``); malformed
    nondict fields are not rejected. Normalize here once for all name-indexing sites rather than repeating
    isinstance checks.

    Normalize names here too (:func:`lib.project.asset_types.normalize_asset_bucket`): parser-derived speakers/mentions
    are normalized, but the persisted character table's normalization form is uncontrolled.
    """
    return normalize_asset_bucket(project.get(BUCKET_KEY["character"]))


@dataclass(frozen=True)
class RenderedUnitPrompt:
    """One unit's rendering result.

    ``audio_speakers`` order determines ``@音频N`` numbers; callers assemble
    ``VideoGenerationRequest.reference_audio_files`` in the same order, the sole prompt/request binding
    contract (the request does not identify which character belongs to each audio segment).

    ``audio_speaker_reference_index`` matches ``audio_speakers`` length/order: item i is that speaker's
    zero-based ``reference_images`` index, or None for offscreen speakers without images. Audio order
    (first dialogue-speaker mention) and image order (first visual mention) derive independently. Backends
    requiring per-image audio (``VideoCapabilities.reference_audio_per_image``) need callers to construct
    ``VideoGenerationRequest.reference_audio_targets`` from these indices, never assume list orders match.
    """

    prompt: str
    audio_speakers: list[str]
    audio_speaker_reference_index: list[int | None] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


def render_unit_prompt(
    text: str,
    project: dict,
    references: list[ReferenceResource],
    settings: VoiceRenderSettings,
) -> RenderedUnitPrompt:
    """Render one unit's written body into a three-part backend prompt.

    ``references`` contains **actually sent** images after capability clamping; order gives
    ``图片N``, matching ``reference_images`` length/order exactly. Clamped names fall back to text without
    dangling bindings. Callers derive order from the same body (first ``@mention``, excluding dialogue
    speaker positions); consume, never recompute, here.

    Required ``settings`` contains voice inputs (:class:`~lib.script.reference_video.voice_settings
    .VoiceRenderSettings`), with no fallback: it determines audibility, and either default guesses for callers.
    Guessing audio injects voice traits into silent projects; requiring it catches new wiring mistakes immediately.
    True ``requires_reference_image`` (backend audio binds to specific reference items) excludes offscreen-only
    speakers from audio binding with degradation/warnings. Bound ``@音频N`` enters prompt text, so filtering
    later in backends would diverge promised bindings from actual ``reference_audio_files``; exclude before numbering.

    Silent rendering (``settings.is_silent``) creates no audio bindings: neither ``@音频N`` nor voice descriptions
    enter prompts; ``audio_speakers`` and caller-built ``reference_audio_files`` stay empty. Part-two dialogue
    ignores this flag; silent models still receive words for lip-sync reference.

    Warnings match preview ``{key, params}`` entries, merged by callers into task ``result.warnings``.

    Part-three style uses ``project`` ``style`` and ``style_description``. Clone exclusions use the full
    registered body character list without provider-image clamping; base/derivatives count as one (``docs/adr/0072``).
    Inject product-fidelity instructions only when product images are actually sent; claiming product references
    without images would mislead the model.
    """
    mentions = extract_mentions(text)
    utterances, warnings = derive_utterances(text)

    # ``references`` is an upstream persisted derivation with uncontrolled name normalization; parsed body names
    # are normalized. Match both sides so subject/image bindings align; otherwise bindings silently disappear
    # and audio cannot attach to images.
    references = [ReferenceResource(type=ref.type, name=asset_name_comparison_key(ref.name)) for ref in references]

    registered, missing = resolve_references(mentions, project)
    warnings = [_warning_unregistered(name) for name in missing] + warnings
    # Determine subject markers by **asset registration**, independently of numbering. Clamped names remain visual
    # subjects without sent images, like offscreen characters. Only unregistered mentions retain original text.
    subjects = {ref.name for ref in registered}

    # Audio attaches only to character references; filter by type before building name → index mappings.
    character_image_no = {ref.name: i for i, ref in enumerate(references, start=1) if ref.type == "character"}

    characters = _character_bucket(project)
    catalog = build_reference_catalog(project)
    bindings = derive_voice_bindings(
        utterances,
        characters,
        settings,
        speakers_with_reference_image=set(character_image_no),
    )
    warnings.extend(bindings.warnings)

    audio_no, audio_speaker_reference_index = _number_audio_speakers(bindings.audio_speakers, character_image_no)

    request_character_forms = _character_forms_by_entity(references, catalog)
    co_present_characters = list(_character_forms_by_entity(registered, catalog))
    prompt = builtin_templates.render(
        "reference_video/unit",
        declarations=_render_segment_one(
            [ref.name for ref in references],
            bindings.speakers,
            audio_no,
            characters,
            settings,
            request_character_forms,
        ),
        body=_render_segment_two(text, subjects, characters, catalog),
        style=normalize_style_value(project.get("style")),
        style_description=normalize_style_value(project.get("style_description")),
        co_present_characters=co_present_characters if len(co_present_characters) >= 2 else [],
        products=list(dict.fromkeys(ref.name for ref in references if ref.type == "product" and ref.name)),
    )
    return RenderedUnitPrompt(
        prompt=prompt,
        audio_speakers=list(bindings.audio_speakers),
        audio_speaker_reference_index=audio_speaker_reference_index,
        warnings=warnings,
    )


def render_video_unit_prompt(
    unit: dict,
    project: dict,
    settings: VoiceRenderSettings,
    *,
    request_references: list[ReferenceResource] | None = None,
) -> RenderedUnitPrompt:
    """Render the exact reference-video prompt from one current projected unit."""

    raw_text = unit.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    if not text.strip():
        raise ValueError("reference video unit prompt is empty: text is blank")
    references = request_references
    if references is None:
        references, _missing = derive_references_from_text(text, project)
    return render_unit_prompt(text, project, references, settings)


def _warning_unregistered(name: str) -> dict[str, Any]:
    return {"key": WARN_UNREGISTERED_MENTION, "params": {"name": name}}


def _render_voice_declarations(
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    settings: VoiceRenderSettings,
) -> list[str]:
    """Voice declarations: ``<X>: Dialogue voice reference @音频N; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue; Voice characteristics: …``. Shared by episodic/ad routes, whose
    subject bindings use mention-derived ``ReferenceResource``; voice declarations depend only on registered
    dialogue speakers, independent of image bindings and reusable whole.

    Both silent paths (``settings.is_silent``: class C no-audio model, or episode audio disabled) omit declarations.
    Audible A/B models receive voice traits, following official guidance to add descriptions for weak voice matching.
    Dialogue rendering remains unchanged in part two (:func:`_render_segment_two`).

    Nondict character records (external project.json corruption) act as absent voice traits; never index
    malformed data, preserving soft degradation rather than crashing.
    """
    if settings.is_silent:
        return []
    lines: list[str] = []
    for name in speakers:
        parts: list[str] = []
        if name in audio_no:
            parts.append(
                f"Dialogue voice reference @音频{audio_no[name]}; use only timbre, speech rate, and emotion; the body text is authoritative for dialogue"
            )
        char_data = characters.get(name)
        voice_style = str((char_data.get("voice_style") if isinstance(char_data, dict) else None) or "").strip()
        if voice_style:
            parts.append(f"Voice characteristics: {voice_style}")
        if parts:
            lines.append(f"<{name}>: " + "; ".join(parts) + ".")
    return lines


def _number_audio_speakers(
    audio_speakers: list[str], character_image_no: dict[str, int]
) -> tuple[dict[str, int], list[int | None]]:
    """Derive ``@音频N`` numbering and each audio segment's image index consistently for both routes.

    Numbers are ``audio_speakers`` positions (first speaker occurrence) and request ``reference_audio_files``
    order. Indices have equal length/order, with None for offscreen speakers without images. Derive both
    together so prompt ``@音频N`` numbers and request indices cannot drift independently.
    """
    audio_no = {name: i for i, name in enumerate(audio_speakers, start=1)}
    reference_index = [
        (character_image_no[name] - 1) if name in character_image_no else None for name in audio_speakers
    ]
    return audio_no, reference_index


def _render_segment_one(
    labels: list[str],
    speakers: list[str],
    audio_no: dict[str, int],
    characters: dict,
    settings: VoiceRenderSettings,
    character_forms: dict[str, list[str]],
) -> str:
    """Subject bindings + voice declarations.

    Official three-part prompts place face/camera/voice references together in the first reference-declaration
    section, so timbre references/traits live here while dialogue uses one uniform form. Declarations iterate
    registered dialogue speakers, not images: offscreen speakers have no images (speaker positions never
    derive references), but still receive voice declarations.

    ``labels`` contains mention-derived subject markers corresponding to sent images. Number by position,
    never name lookup. Empty placeholders emit no binding, but still advance numbering to preserve request
    alignment.

    ``character_forms`` groups via :func:`_character_forms_by_entity` to declare one character's multiple
    appearances. Group by asset entry, independently of positional ``labels``.

    Return the ``reference_video/unit`` ``declarations`` slot.
    """
    lines: list[str] = []
    bindings = "、".join(f"<{label}>@图片{i}" for i, label in enumerate(labels, start=1) if label)
    if bindings:
        lines.append(bindings + "。")
    lines.extend(_render_form_declarations(character_forms))
    lines.extend(_render_voice_declarations(speakers, audio_no, characters, settings))
    return "\n".join(lines)


def _character_forms_by_entity(references: list[ReferenceResource], catalog: ReferenceCatalog) -> dict[str, list[str]]:
    """Group character references by owning asset entry: ``本体名 → [该条目出现的形态记号]``, in image order.

    Base/derivative references have separate names/images but identify one person (``docs/adr/0072``). Part
    one uses actually sent images; part three uses full registered body references. Both merge identities
    using the same project catalog. Unregistered names stand alone, pointing to no entry and never merging.

    Inspect only ``character`` references: legacy cross-type duplicate names otherwise let a scene name
    match a character, falsely claiming unrelated images are alternate appearances of one character.
    """
    grouped: dict[str, list[str]] = {}
    for reference in references:
        if reference.type != "character" or not reference.name:
            continue
        entry = catalog.lookup("character", reference.name)
        grouped.setdefault(entry.asset_name if entry else reference.name, []).append(reference.name)
    return grouped


def _render_form_declarations(character_forms: dict[str, list[str]]) -> list[str]:
    """Declare co-present appearances as different looks of one person.

    Base/derivatives have independent reference names and sheets (``docs/adr/0072``), with no co-presence
    restriction. Without declarations the model sees similar faces as two people, so state ownership in
    reference declarations. Characters appearing in only one form emit no declaration.
    """
    lines: list[str] = []
    for forms in character_forms.values():
        if len(forms) < 2:
            continue
        marks = [f"<{form}>" for form in forms]
        lines.append(
            ", ".join(marks[:-1])
            + f" and {marks[-1]} are different appearances of the same character; depict each according to its corresponding reference image."
        )
    return lines


#: Separating punctuation/whitespace (Chinese and English) merged around removed narration markers.
#: Keep terminators separate: valid at line ends, unlike dangling joining punctuation.
_MARK_JOINERS = "，,、；;：:"
_MARK_TERMINATORS = "。.！!？?"
_MARK_SEPARATORS = _MARK_JOINERS + _MARK_TERMINATORS
_MARK_SPACES = " \t\u3000"


def _render_segment_two(text: str, subjects: Collection[str], characters: dict, catalog: ReferenceCatalog) -> str:
    """Render body visuals with mention replacement and speech markers rewritten in place to official phrasing.

    ``subjects`` contains registered mention names before capability clamping. Subject marker ``<X>`` means
    this person/object in the image, independently of numbering: clamped names remain subjects; only
    unregistered mentions retain editor text with ``ref_warn_unregistered_mention``.

    Preserve line order and descriptions, without reordering or section prefixes: the written body is
    authoritative. Markers may appear anywhere inline and are replaced at their original positions. Speaker
    recognition uses **asset registration**, not image lists; offscreen speakers still rewrite. Unregistered
    speakers retain original text (``derive_voice_bindings`` already warns), as do unrecognized braces, never
    stripped, leaving the author's malformed passage visible in the result.

    Speaker appearances match visual references: ``@[张三/劲装]{台词}`` renders as ``<张三/劲装> says {台词}``;
    each character has one subject-marker form in a prompt (``docs/adr/0072``). Voice still binds to the base
    via ``SpeechMark.speaker``, independent of appearance. Unregistered speaker derivatives fall back to
    base markers; unlike unregistered visual mentions, generated speaker markers have no original text to retain.

    Entire speakerless narration markers (bare ``{台词}``) are omitted from video prompts, using TTS or
    post-production (ADR 0040 / 0061). Audible video models otherwise read instructions too or lip-sync
    characters to narration. Same-line visual descriptions remain; only the marker disappears.
    """
    lines: list[str] = []
    for line in text.splitlines():
        pieces: list[str | None] = []
        for part in split_speech_line(line):
            if isinstance(part, str):
                pieces.append(render_mentions_as_subjects(part, subjects))
            elif not part.speaker:
                pieces.append(None)
            elif part.speaker in characters:
                pieces.append(f"<{_speaker_subject(part, catalog)}> says {{{part.text}}}")
            else:
                pieces.append(render_mentions_as_subjects(part.raw, subjects))
        lines.append(_join_line_pieces(pieces))
    return "\n".join(line for line in lines if line.strip())


def _speaker_subject(mark: SpeechMark, catalog: ReferenceCatalog) -> str:
    """Speaker subject marker: registered written derivatives use ``本体/衍生``, otherwise the base name."""
    reference = mark.speaker_reference
    if mark.derivative and catalog.lookup("character", reference) is not None:
        return reference
    return mark.speaker


def _join_line_pieces(pieces: list[str | None]) -> str:
    """Join rendered line pieces; ``None`` marks removed narration, merging excess adjacent punctuation/whitespace here.

    Narration between descriptions (``推门，{夜风灌进来}，他按住剑``) has separators on both sides, leaving
    duplicate commas on direct joining. If the left already separates, discard right separators; merge
    whitespace-only sides to one space.

    At line starts/ends, leftover separators lack a second side: leading (``{夜风灌进来}，
    他按住剑``) drops right separators/spaces; trailing (``推门，{夜风灌进来}``) drops left dangling
    joining punctuation. Final terminating punctuation (``推门。{夜风灌进来}``) ends a valid sentence and remains.
    """
    rendered = ""
    dropped = False
    for piece in pieces:
        if piece is None:
            dropped = True
            continue
        if dropped:
            dropped = False
            head = rendered.rstrip()
            if not head or head[-1] in _MARK_SEPARATORS:
                rendered = head
                piece = piece.lstrip(_MARK_SEPARATORS + _MARK_SPACES)
            elif rendered[-1:].isspace():
                # Whitespace on both sides collapses to one; removed markers must not leave extra body spaces.
                rendered = head
                piece = piece.lstrip()
                if piece and piece[0] not in _MARK_SEPARATORS:
                    rendered += " "
        rendered += piece
    if dropped:
        rendered = rendered.rstrip(_MARK_JOINERS + _MARK_SPACES)
    return rendered


def resolve_reference_audio_paths(project: dict, project_path: Path) -> dict[str, Path]:
    """Map project characters with actually usable reference audio to absolute paths.

    Include only existing files under ``characters/refs_audio``.
    :func:`lib.speech.audio_utils.resolve_audio_ref_path` rejects escapes; asset PATCH permits arbitrary
    project-relative strings. Rendering uses this map to bind audio, aligning numbering exactly with sent segments.

    Keys are normalized character names (``_character_bucket`` already normalizes). Passed as ``audio_ready``
    to :func:`lib.script.reference_video.script_preview.derive_voice_bindings` for speaker comparison; both
    sides must match to avoid silently treating usable audio as unavailable.
    """
    audio_refs_dir = project_path / ASSET_AUDIO_SUBDIR
    resolved: dict[str, Path] = {}
    for name, item in _character_bucket(project).items():
        if not isinstance(item, dict):
            continue
        path = resolve_audio_ref_path(project_path, audio_refs_dir, item.get("reference_audio"))
        if path is not None and path.exists():
            resolved[name] = path
    return resolved
