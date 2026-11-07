"""script_models.py - Script data models

Pydantic script structures for:
1. Gemini API response_schema (Structured Outputs)
2. Output validation
"""

import logging
from typing import Annotated, Any, ClassVar, Literal, get_args

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, create_model, model_validator
from pydantic.json_schema import SkipJsonSchema

from lib.script.script_skeleton import resolve_declared_kind

# All script models forbid extra fields by default. Agent `patch_episode_script` uses `_set_nested` to create
# dict leaves (filling optional fields the LLM omitted). With default `extra="ignore"`, typo/hallucinated
# fields disappear from validation but atomic_write_json already persisted them, leaving junk in JSON.
# The no-worse error-set diff misses this (before/after both ignore → identical errors → new_errors=∅ → accepted).
# `extra="forbid"` exposes new ValidationError entries after typo writes, allowing no-worse checks to block them.
# ScriptGenerator (model_validate + model_dump on LLM output) also benefits: Structured Outputs rarely
# emits extras; when hallucinated extras appear, rejection is safer than silently discarding them.
_STRICT_CONFIG = ConfigDict(extra="forbid")

# ============ Enum type definitions ============

ShotType = Literal[
    "Extreme Close-up",
    "Close-up",
    "Medium Close-up",
    "Medium Shot",
    "Medium Long Shot",
    "Long Shot",
    "Extreme Long Shot",
    "Over-the-shoulder",
    "Point-of-view",
]

# Values must match official provider camera vocabularies (MiniMax Hailuo [command] instructions and Alibaba Wanxiang
# basic/advanced camera-movement tables). Downstream interpolates them verbatim without further translation.
CameraMotion = Literal[
    "Static",
    "Pan Left",
    "Pan Right",
    "Tilt Up",
    "Tilt Down",
    "Zoom In",
    "Zoom Out",
    "Push In",
    "Pull Out",
    "Truck Left",
    "Truck Right",
    "Pedestal Up",
    "Pedestal Down",
    "Orbit",
    "Tracking Shot",
    "Shake",
]

logger = logging.getLogger(__name__)


def _canon_enum_key(value: str) -> str:
    """Enum-drift normalization key: collapse underscores/hyphens to spaces, merge whitespace, and casefold."""
    return " ".join(value.replace("_", " ").replace("-", " ").split()).casefold()


# Schema enums are hard constraints only with provider constrained decoding. Proxy/OpenAI-compatible gateways may drop
# wire structure parameters, allowing upper/lower snake case (MEDIUM_SHOT / medium_shot)
# or out-of-vocabulary values (wide_shot / dolly_in). Mechanically normalize case/separators back to vocabulary.
# Never semantically map unknown synonyms (impossible to exhaust); use neutral defaults and warn instead.
# Downstream only interpolates these fields as prompt text; one shot's vocabulary drift should not fail an episode.
_DEFAULT_SHOT_TYPE: ShotType = "Medium Shot"
_DEFAULT_CAMERA_MOTION: CameraMotion = "Static"

_SHOT_TYPE_BY_KEY: dict[str, str] = {_canon_enum_key(v): v for v in get_args(ShotType)}
_CAMERA_MOTION_BY_KEY: dict[str, str] = {_canon_enum_key(v): v for v in get_args(CameraMotion)}


def _normalize_shot_type(value: object) -> object:
    if not isinstance(value, str):
        return value
    hit = _SHOT_TYPE_BY_KEY.get(_canon_enum_key(value))
    if hit is not None:
        return hit
    logger.warning("shot_type 枚举漂移无法归一，降级为 %s: %r", _DEFAULT_SHOT_TYPE, value)
    return _DEFAULT_SHOT_TYPE


def _normalize_camera_motion(value: object) -> object:
    if not isinstance(value, str):
        return value
    hit = _CAMERA_MOTION_BY_KEY.get(_canon_enum_key(value))
    if hit is not None:
        return hit
    logger.warning("camera_motion 枚举漂移无法归一，降级为 %s: %r", _DEFAULT_CAMERA_MOTION, value)
    return _DEFAULT_CAMERA_MOTION


def _none_to_empty_list(value: object) -> object:
    """Tolerate drift: unconstrained decoding may emit null for optional list fields instead of omitting them."""
    return [] if value is None else value


class Dialogue(BaseModel):
    """Dialogue entry."""

    model_config = _STRICT_CONFIG

    speaker: str = Field(description="Speaker name")
    line: str = Field(description="Dialogue text")


class Composition(BaseModel):
    """Composition information."""

    model_config = _STRICT_CONFIG

    shot_type: Annotated[ShotType, BeforeValidator(_normalize_shot_type)] = Field(description="Shot type")
    lighting: str = Field(description="Lighting description")
    ambiance: str = Field(description="Overall ambiance")


class ImagePrompt(BaseModel):
    """Storyboard image generation prompt."""

    model_config = _STRICT_CONFIG

    scene: str = Field(description="Static image description; dynamic content belongs in video_prompt.action")
    composition: Composition = Field(description="Composition information")


class _VideoPromptCore(BaseModel):
    """Shared video_prompt visual fields (action / camera movement / environmental audio); concrete variants decide whether to include dialogue."""

    model_config = _STRICT_CONFIG

    action: str = Field(
        description="Actions within this storyboard's duration; camera movement belongs in camera_motion"
    )
    camera_motion: Annotated[CameraMotion, BeforeValidator(_normalize_camera_motion)] = Field(
        description="Camera movement"
    )
    ambiance_audio: str = Field(description="Environmental sound effects (diegetic audio)")


class VideoPrompt(_VideoPromptCore):
    """narration / ad video generation prompt, including character dialogue.

    Persisted narration segments still load through this model; speech admission
    classifies legacy segments containing dialogue as mixed speech. Prompt-authoring
    visual layers use ``NarrationVideoPrompt``, which does not produce dialogue.

    Drama's ordered speech lives in ``DramaScene.utterances``; its video_prompt uses
    ``DramaVideoPrompt`` without dialogue (ADR 0040).
    """

    dialogue: Annotated[list[Dialogue], BeforeValidator(_none_to_empty_list)] = Field(
        default_factory=list, description="Dialogue list; populate only when the source contains quoted dialogue"
    )


def _require_no_dialogue(value: list[Dialogue]) -> list[Dialogue]:
    if value:
        raise ValueError("旁白/解说片段不写台词：角色说的话由旁白在正文里转述")
    return value


class NarrationVideoPrompt(_VideoPromptCore):
    """Video prompt for narration/commentary authoring: ``dialogue`` is excluded from response_schema and always empty.

    The required segment body is itself voiceover; adding dialogue mixes speech
    owners (ADR 0089), so the model must not write dialogue. Normalize ``null`` or
    ``[]`` from drifting backends to empty; reject nonempty dialogue. The persisted
    shape matches ``VideoPrompt`` (``dialogue: []``).
    """

    dialogue: SkipJsonSchema[
        Annotated[list[Dialogue], BeforeValidator(_none_to_empty_list), AfterValidator(_require_no_dialogue)]
    ] = Field(default_factory=list, description="Always empty: narration/commentary segments do not contain dialogue")


class DramaVideoPrompt(_VideoPromptCore):
    """Drama video prompt without dialogue; storyboard-level ``DramaScene.utterances`` owns spoken content.

    With ``extra="forbid"``, ``DramaScene`` read-time migration strips any remaining
    ``dialogue`` key first.
    """


def _require_non_blank_prompt(value: str) -> str:
    """Text prompts cannot be blank: empty strings mean no prompt, also rejected by the renderer with ValueError."""
    if not value.strip():
        raise ValueError("prompt text must not be empty")
    return value


#: Text prompts: the field itself is the model-bound body; rendering only injects necessities, without structured wrapping.
#: ``SkipJsonSchema`` excludes text from response_schema: LLM structured output still permits structured forms only;
#: creators explicitly select text authoring on the timeline, not as a model output choice.
PromptText = SkipJsonSchema[Annotated[str, AfterValidator(_require_non_blank_prompt)]]

#: Pending prompt state: mechanical plan→formal conversion omits visuals, persisting ``None`` while awaiting
#: model or manual authoring. ``SkipJsonSchema`` excludes ``null`` from response_schema: only conversion
#: writes pending state, never model output; ``PromptText`` still rejects empty strings.
PendingPrompt = SkipJsonSchema[None]

#: Entry-level pending-authoring field name; see each entry model's ``pending_authoring``.
PENDING_AUTHORING_FIELD = "pending_authoring"


class GeneratedAssets(BaseModel):
    """Generated resource status, initially empty."""

    model_config = _STRICT_CONFIG

    storyboard_image: str | None = Field(default=None, description="Storyboard image path")
    storyboard_last_image: str | None = Field(default=None, description="Storyboard last-frame image path")
    grid_id: str | None = Field(default=None, description="Associated grid-image generation ID")
    grid_cell_index: int | None = Field(default=None, description="Cell index within the grid image")
    video_clip: str | None = Field(default=None, description="Video clip path")
    # reference_video_tasks / generation_tasks extract video_thumbnail after generation through
    # lib.infra.thumbnail.extract_video_thumbnail, persisting it in ga["video_thumbnail"].
    # Without declaration, extra="forbid" exposes extra_forbidden to no-worse checks, rejecting episode writes.
    video_thumbnail: str | None = Field(default=None, description="Video thumbnail path")
    video_uri: str | None = Field(default=None, description="Video URI")
    # TTS tasks (generation_tasks.execute_tts_task) write narration_audio after synthesis.
    # Declaration satisfies extra="forbid" and no-worse guards; only narration segments write it, drama/refvideo stay None.
    narration_audio: str | None = Field(default=None, description="Narration audio path")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(
        default="pending", description="Generation status"
    )
    # Stamp generation time mechanically when writing video_clip (single write point apply_unit_video_assets),
    # comparing with character `voice_updated_at` to detect generation before current reference-audio settings.
    # Missing means earlier than any setting, retaining that semantics. Hidden from LLMs.
    video_generated_at: SkipJsonSchema[str | None] = Field(
        default=None, description="Video generation completion time (ISO8601 UTC)"
    )
    # Accept pre-migration history losslessly only; runtime never reads, compares, or adds this key.
    source_signature: SkipJsonSchema[str | None] = Field(
        default=None, description="Historical artifact provenance signature"
    )


def get_generated_assets(item: dict) -> dict:
    """Normalize access to an item's ``generated_assets`` container.

    This field comes from untrusted script JSON on disk: external editing may corrupt it into nondicts
    (such as lists/strings). Normalize to an empty dict rather than raising, allowing callers' existing
    missing-asset branches (skip individual items / readable refusal), instead of an uncaught
    ``AttributeError`` from one malformed item interrupting a whole batch enqueue.
    """
    assets = item.get("generated_assets")
    return assets if isinstance(assets, dict) else {}


# ============ Narration/commentary (Narration) ============


class NarrationSegment(BaseModel):
    """Narration/commentary storyboard.

    There is no separate `episode` field: the episode is encoded in `segment_id`
    (E{episode}S{sequence}), matching `DramaScene.scene_id` and
    `ReferenceVideoUnit.unit_id`. Avoid duplicate AI-generated episode numbers on
    each segment and the resulting hallucinated values; see `NarrationEpisodeScript`.
    """

    model_config = _STRICT_CONFIG

    # Explicitly pop deprecated fields potentially remaining in old JSON before extra="forbid" rejects them.
    # v0→v1 removed clues_in_segment (lib/project/project_migrations/
    # v0_to_v1_clues_to_scenes_props.py). Archive processing already pops it through project_archive.py,
    # but direct NarrationSegment.model_validate(legacy_dict), outside _guard_no_worse lenient wrapping,
    # needs this fallback, matching DramaScene.LEGACY_DROPPED_FIELDS.
    LEGACY_DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"clues_in_segment"})

    @model_validator(mode="before")
    @classmethod
    def _strip_legacy_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            for k in cls.LEGACY_DROPPED_FIELDS:
                data.pop(k, None)
        return data

    segment_id: str = Field(
        description="Storyboard ID in E{episode}S{sequence} or E{episode}S{sequence}_{subsequence} form"
    )
    duration_seconds: int = Field(ge=1, le=60, description="Storyboard duration in seconds")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition")
    novel_text: str = Field(description="Novel source text, preserved verbatim for post-production narration")
    characters_in_segment: list[str] = Field(description="Names of characters appearing in the shot")
    scenes: list[str] = Field(default_factory=list, description="Names of scenes appearing in the shot")
    props: list[str] = Field(default_factory=list, description="Names of props appearing in the shot")
    image_prompt: ImagePrompt | PromptText | PendingPrompt = Field(
        default=None, description="Storyboard image generation prompt"
    )
    video_prompt: VideoPrompt | PromptText | PendingPrompt = Field(default=None, description="Video generation prompt")
    # Fields below are hidden from LLMs (SkipJsonSchema): note is manual, generated_assets is post-LLM runtime state.
    # Retain them in Pydantic for persistence/validation, excluding response_schema to avoid model contamination.
    note: SkipJsonSchema[str | None] = Field(default=None, description="User notes, excluded from generation")
    # Project-relative tail-frame snapshot path (``end_frames/scene_{id}.png``). User intent rather than runtime output,
    # so outside generated_assets; only tail-frame set/clear endpoints write it (excluded from generic PATCH
    # to prevent bypassing snapshot copying with dangling/out-of-root paths). Whole-script regeneration drops it like note.
    end_frame_image: SkipJsonSchema[str | None] = Field(
        default=None, description="Last-frame snapshot path relative to the project"
    )
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="Generated resource status"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="This unit requires manual replanning")
    # Awaiting authoring: set for new entries without visuals; clear when model/manual authoring completes them.
    # Hidden from LLMs, excluded from every PATCH allowlist, persisted only when set.
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="This entry awaits prompt authoring")


class NovelInfo(BaseModel):
    """Novel source information.

    title/chapter have defaults so SkipJsonSchema[NovelInfo] can use
    ``default_factory=NovelInfo``. ``ScriptGenerator._add_metadata`` injects real
    values with setdefault (project title + episode-ledger title; an empty ledger
    title stays empty). The LLM is not asked to fill these fields, preventing invented
    chapter names from polluting downstream consumers.
    """

    model_config = _STRICT_CONFIG

    title: str = Field(default="", description="Novel title")
    chapter: str = Field(default="", description="Chapter name")


class NarrationEpisodeScript(BaseModel):
    """Narration/commentary script.

    The `episode` field is excluded from the schema. CLI `--episode N` is the sole
    source of truth, written by `ScriptGenerator._add_metadata`. Do not let the AI
    generate it: a hallucinated episode number can pollute project.json (for example,
    episode_10.json containing episode=1 could overwrite episode 1).

    The top level does not use ``extra="forbid"``: runtime-injected fields such as
    ``episode`` / ``metadata`` (``_add_metadata`` / ``_write_script_unlocked``) are
    outside the schema, so forbidding them would break persistence. This exception
    does not include ``generation_mode``: project.json owns generation mode; scripts
    carry no stamp. ``ScriptGenerator._add_metadata`` strips it before persistence,
    and legacy remnants are ignored as unknown fields. Nested models (VideoPrompt,
    ImagePrompt, NarrationSegment, etc.) use ``extra="forbid"`` to catch typos.
    """

    title: str = Field(description="Episode title")
    # _add_metadata setdefault injects authoritative project content_mode; asking LLMs for a singleton Literal is pointless
    content_mode: SkipJsonSchema[Literal["narration"]] = Field(default="narration", description="Content mode")
    # _add_metadata injects novel from {project title, episode ledger title}; free LLM invention would be unpredictable
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="Novel source information")
    # _add_metadata injects hook / next_episode_teaser from the episode ledger (authoritative hook design,
    # not LLM-authored); null when the ledger has no planning data.
    hook: SkipJsonSchema[str | None] = Field(default=None, description="Episode-ending hook from the episode ledger")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(
        default=None, description="Next-episode teaser from the episode ledger"
    )
    segments: list[NarrationSegment] = Field(description="Storyboard list")


# ============ Narration structured script_plan intermediate / prompt_authoring visuals ============
#
# Two-stage separation: script_plan splitting produces content (verbatim novel_text + boundaries + durations);
# prompt_authoring (generate-script) produces only image_prompt / video_prompt, merged by segment_id
# into confirmed script_plan structure. Never regenerate novel_text in prompt_authoring, preventing expansion drift.


NewAssetType = Literal["character", "scene", "prop"]
#: Top-level script-plan field for new episode assets (``list[PlanNewAsset]``).
NEW_ASSETS_FIELD = "new_assets"
NewAssetDecision = Literal["register", "merge", "derivative", "skip"]


class PlanNewAsset(BaseModel):
    """A new asset identified in this episode's script plan and its handling decision (``docs/adr/0092``).

    Plan references always use ``name``. Confirmation rewrites them according to the
    decision: ``register`` creates an asset; ``merge`` resolves to ``target`` (an
    existing same-type asset or another new asset in this episode) and records
    ``name`` as an alias; ``derivative`` registers a derivative of the ``target``
    character; ``skip`` registers nothing and uses text descriptions only.
    :mod:`lib.script.plan_new_assets` resolves these decisions.
    """

    model_config = _STRICT_CONFIG

    type: NewAssetType = Field(description="Asset type: character / scene / prop")
    name: str = Field(min_length=1, description="Name used in this episode plan's references; must not contain /")
    decision: NewAssetDecision = Field(
        description="Decision: register a new asset / merge into an existing asset / derivative to register a character derivative / skip registration"
    )
    reason: str = Field(description="One-sentence rationale")
    description: str = Field(
        default="",
        description="register: visual appearance description; derivative: appearance changes relative to the base; otherwise empty",
    )
    aliases: list[str] = Field(
        default_factory=list, description="register: other names used in the source; otherwise empty"
    )
    target: str = Field(
        default="",
        description="merge: target asset name of the same type; derivative: base character name; otherwise empty",
    )
    asset_name: str = Field(
        default="", description="register: registration name; derivative: derivative name; empty uses name"
    )


class NarrationScriptPlanSegment(BaseModel):
    """Structured storyboard content produced by narration/commentary script_plan splitting.

    Carries only established content fields: boundaries (segment_id / segment_break),
    verbatim novel_text and duration. prompt_authoring generates image_prompt /
    video_prompt visual layers, merged by segment_id. script_plan registers
    characters_in_segment / scenes / props as the single source of asset references.
    The visual schema excludes these fields; authoring consumes them read-only and
    neither registers nor rewrites assets. All three fields are therefore required:
    explicitly use [] without assets; missing fields fail loudly rather than silently
    turning omitted registration into empty arrays. Merge into one NarrationSegment.
    """

    model_config = _STRICT_CONFIG

    segment_id: str = Field(pattern=r"^E[1-9]\d*S\d{2,}$", description="Storyboard ID in E{episode}S## form")
    novel_text: str = Field(
        min_length=1, description="Novel source text, retained verbatim for narration and pass-through"
    )
    duration_seconds: int = Field(ge=1, le=60, description="Storyboard duration in seconds")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition")
    characters_in_segment: list[str] = Field(description="Names of appearing characters; explicitly use [] if none")
    scenes: list[str] = Field(description="Names of appearing scenes; explicitly use [] if none")
    props: list[str] = Field(description="Names of appearing props; explicitly use [] if none")


class NarrationScriptPlanDraft(BaseModel):
    """Structured narration/commentary script_plan intermediate state (schema for ``drafts/episode_N/script_plan_segments.json``).

    Allow extra top-level fields such as ``episode`` headers: a subagent writes the
    split storyboards through Write rather than enforced structured output. Validate
    against this model on reads.
    """

    model_config = ConfigDict(extra="ignore")

    segments: list[NarrationScriptPlanSegment] = Field(description="Storyboard list")
    new_assets: list[PlanNewAsset] = Field(
        default_factory=list,
        description="Unregistered characters / scenes / props referenced by this episode plan and their handling decisions",
    )


class NarrationVisualSegment(BaseModel):
    """Visual layer produced by prompt_authoring (generate-script), keyed by segment_id.

    The LLM produces only image_prompt / video_prompt plus the segment_id alignment
    anchor. script_plan already owns novel_text, duration, segment_break and
    characters_in_segment / scenes / props; the backend merges by segment_id.
    Excluding these fields from LLM output prevents structured-output drift.
    ``extra="forbid"`` rejects fields such as novel_text from nonstructured backends
    instead of silently overwriting content.
    """

    model_config = _STRICT_CONFIG

    segment_id: str = Field(
        min_length=1,
        description="Alignment anchor from the script_plan storyboard table; match one-to-one without additions or omissions",
    )
    image_prompt: ImagePrompt = Field(description="Storyboard image generation prompt")
    video_prompt: NarrationVideoPrompt = Field(description="Video generation prompt without dialogue")


class NarrationVisualEpisodeScript(BaseModel):
    """LLM ``response_schema`` for visual authoring: episode title and storyboard visual layers.

    The top level does not use ``extra="forbid"``, matching NarrationEpisodeScript;
    NarrationVisualSegment uses it on nested visual fields to reject typos/drift.
    """

    title: str = Field(description="Episode title")
    segments: list[NarrationVisualSegment] = Field(
        description="Visual layers for each storyboard, aligned one-to-one with script_plan by segment_id"
    )


# ============ Dramatization (Drama) ============


UtteranceKind = Literal["dialogue", "voiceover"]


class Utterance(BaseModel):
    """Ordered drama storyboard utterance; insertion order is the within-scene chronology.

    Discriminated ``{kind, speaker, text}`` union: ``kind`` controls downstream routing
    and the ``kind <-> speaker`` constraint:
    - ``dialogue``: character speech (dialogue, inner monologue or character voiceover)
      requires a nonempty ``speaker`` and enters video YAML for provider-generated
      lip-synced audio.
    - ``voiceover``: unattributed narration requires ``speaker=None``; it is excluded
      from the video prompt and retained for subtitles / TTS.

    Explicit ``kind`` rather than implicit speaker presence matches ReferenceResource.type
    and makes LLM structured output more reliable (ADR 0040).
    """

    model_config = _STRICT_CONFIG

    kind: UtteranceKind = Field(
        description="Utterance kind: dialogue is character speech assigned to a character; voiceover is narrative speech with no character attribution"
    )
    speaker: str | None = Field(
        default=None,
        description="Speaking character name; required and nonempty for dialogue, must be null for voiceover",
    )
    text: str = Field(description="Original utterance text, preserved verbatim")

    @model_validator(mode="before")
    @classmethod
    def _normalize_speaker(cls, data: object) -> object:
        # Normalize empty/whitespace speakers to None: speakerless voiceover accepts null or "";
        # kind ⇄ speaker validation below then decides validity (rejecting empty dialogue speakers).
        if isinstance(data, dict):
            speaker = data.get("speaker")
            if isinstance(speaker, str) and not speaker.strip():
                data = {**data, "speaker": None}
        return data

    @model_validator(mode="after")
    def _check_kind_speaker(self) -> "Utterance":
        if self.kind == "dialogue":
            if not self.speaker:
                raise ValueError("dialogue utterance 必须带非空 speaker")
        elif self.speaker is not None:
            raise ValueError("voiceover utterance 不得带 speaker")
        return self


class DramaScene(BaseModel):
    """Drama storyboard."""

    model_config = _STRICT_CONFIG

    # Pop known deprecated fields from old JSON before extra="forbid", while rejecting unknown
    # typo/hallucination fields. Known deprecations differ from the actual unknown-field problem forbid blocks.
    # Add names here when deprecating fields.
    # - scene_type: removed scene-type field
    # - clues_in_scene: clue field removed by v0→v1 (like NarrationSegment.clues_in_segment)
    LEGACY_DROPPED_FIELDS: ClassVar[frozenset[str]] = frozenset({"scene_type", "clues_in_scene"})

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        """Read-time migration: strip deprecated fields and combine legacy speech fields
        (``video_prompt.dialogue`` + ``voiceover``) into ordered ``utterances``.

        Missing utterances key identifies legacy data: dialogue comes first, voiceover last (old data has no
        interleaving information; deterministic best effort, never pretending to reconstruct it). Strip ``voiceover``
        and ``video_prompt.dialogue`` to satisfy ``DramaVideoPrompt`` ``extra="forbid"``. Legacy dialogue without
        speakers becomes speakerless voiceover, preserving content without inventing speakers. New data with
        ``utterances`` takes the fast path unchanged. Never mutate the caller's dict in place.
        """
        if not isinstance(data, dict):
            return data
        legacy_present = any(k in data for k in cls.LEGACY_DROPPED_FIELDS)
        needs_spoken_migration = "utterances" not in data
        if not legacy_present and not needs_spoken_migration:
            return data
        data = dict(data)
        for k in cls.LEGACY_DROPPED_FIELDS:
            data.pop(k, None)
        if needs_spoken_migration:
            data["utterances"] = cls._synthesize_utterances(data)
            data.pop("voiceover", None)
            video_prompt = data.get("video_prompt")
            if isinstance(video_prompt, dict) and "dialogue" in video_prompt:
                data["video_prompt"] = {k: v for k, v in video_prompt.items() if k != "dialogue"}
        return data

    @staticmethod
    def _synthesize_utterances(scene: dict[str, object]) -> list[dict[str, object]]:
        """Combine legacy ``video_prompt.dialogue`` and scene ``voiceover`` into ordered utterances, dialogue first."""
        utterances: list[dict[str, object]] = []
        video_prompt = scene.get("video_prompt")
        if isinstance(video_prompt, dict):
            dialogue = video_prompt.get("dialogue")
            if isinstance(dialogue, list):
                for entry in dialogue:
                    if not isinstance(entry, dict):
                        continue
                    text = str(entry.get("line") or "").strip()
                    if not text:
                        continue
                    speaker = str(entry.get("speaker") or "").strip()
                    if speaker:
                        utterances.append({"kind": "dialogue", "speaker": speaker, "text": text})
                    else:
                        # Legacy dialogue without speakers → speakerless voiceover (best effort, preserve content without inventing speakers)
                        utterances.append({"kind": "voiceover", "speaker": None, "text": text})
        voiceover = scene.get("voiceover")
        if isinstance(voiceover, list):
            utterances.extend(
                {"kind": "voiceover", "speaker": None, "text": line.strip()}
                for line in voiceover
                if isinstance(line, str) and line.strip()
            )
        return utterances

    scene_id: str = Field(
        min_length=1, description="Storyboard ID in E{episode}S{sequence} or E{episode}S{sequence}_{subsequence} form"
    )
    duration_seconds: int = Field(default=8, ge=1, le=60, description="Storyboard duration in seconds")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition")
    characters_in_scene: list[str] = Field(description="Names of characters appearing in the shot")
    scenes: list[str] = Field(default_factory=list, description="Names of scenes appearing in the shot")
    props: list[str] = Field(default_factory=list, description="Names of props appearing in the shot")
    image_prompt: ImagePrompt | PromptText | PendingPrompt = Field(
        default=None, description="Storyboard image generation prompt"
    )
    # Drama video_prompt carries only visual action, camera movement, and ambient audio; utterances below carries speech.
    video_prompt: DramaVideoPrompt | PromptText | PendingPrompt = Field(
        default=None, description="Video generation prompt"
    )
    # utterances unifies shot dialogue and voiceover; entry order is speech order within the scene (ADR 0040).
    utterances: list[Utterance] = Field(
        default_factory=list,
        description="Ordered storyboard-level utterances: character dialogue and voiceover in chronological order",
    )
    # Verbatim source excerpt (traceability anchor, like narration novel_text but tracing-only, never read aloud or voiced; best effort).
    # Filled by script_plan and passed unchanged through prompt_authoring; legacy omission defaults empty (no-worse accepts).
    source_text: str = Field(
        default="",
        description="Verbatim source excerpt (best-effort traceability anchor, not read aloud or rendered as audio)",
    )
    # Visual adaptation description: passed from planning at content confirmation as the visual-authoring basis; hidden from LLMs.
    # Legacy formal scripts without it use empty strings.
    scene_description: SkipJsonSchema[str] = Field(default="", description="Visual adaptation description")
    # See the same-named NarrationSegment field.
    note: SkipJsonSchema[str | None] = Field(default=None, description="User notes, excluded from generation")
    end_frame_image: SkipJsonSchema[str | None] = Field(
        default=None, description="Last-frame snapshot path relative to the project"
    )
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="Generated resource status"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="This unit requires manual replanning")
    # Awaiting authoring: set for new entries without visuals; clear when model/manual authoring completes them.
    # Hidden from LLMs, excluded from every PATCH allowlist, persisted only when set.
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="This entry awaits prompt authoring")


class DramaEpisodeScript(BaseModel):
    """Drama script.

    The schema excludes `episode`; the CLI source of truth supplies it through
    `_add_metadata`. See `NarrationEpisodeScript`. The same reasoning applies to
    not using ``extra="forbid"`` at the top level.
    """

    title: str = Field(description="Episode title")
    # See NarrationEpisodeScript.content_mode.
    content_mode: SkipJsonSchema[Literal["drama"]] = Field(default="drama", description="Content mode")
    # See NarrationEpisodeScript.novel.
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="Novel source information")
    # See the same-named NarrationEpisodeScript field.
    hook: SkipJsonSchema[str | None] = Field(default=None, description="Episode-ending hook from the episode ledger")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(
        default=None, description="Next-episode teaser from the episode ledger"
    )
    scenes: list[DramaScene] = Field(description="Storyboard list")


# ============ Two-stage drama: script_plan content / prompt_authoring visuals (ADR 0041) ============
#
# Extract content earlier in script_plan: finalize boundaries, characters/scenes/props, verbatim utterances, source_text
# anchors, and free-text scene_description once. prompt_authoring generates only image_prompt / video_prompt;
# its schema contains scene_id alignment anchors and visuals only. Excluding nonvisual fields from LLM output
# structurally prevents Structured Outputs drift; the backend writes them into formal entries by scene_id
# (ScriptGenerator._merge_visual_layer).


class DramaSceneContent(BaseModel):
    """Storyboard content from script_plan (normalize): every field except image_prompt / video_prompt.

    Structured intermediate contract for visual authoring and later web review/editing,
    persisted in ``drafts/episode_N/script_plan_normalized_script.json`` inside
    ``DramaNormalizedScript``. The three text fields have distinct roles:

    - ``scene_description``: free visual adaptation text with visible content only
      (actions, expressions, environment and lighting), forming the basis for
      prompt_authoring image_prompt / video_prompt. Embed no spoken copy. Creative
      adaptation relative to the source is allowed; loss/drift is tolerated here
      because this is not a fidelity field.
    - ``utterances``: ordered verbatim spoken text (dialogue with speaker; voiceover
      without speaker). Sole source for subtitles / TTS. prompt_authoring passes it
      through without modification or re-identification.
    - ``source_text``: verbatim source traceability excerpt for human comparison,
      locating distortion and regenerating one storyboard. It is not read aloud or
      rendered as audio. Utterances represent speech; source_text represents provenance.
    """

    model_config = _STRICT_CONFIG

    scene_id: str = Field(
        min_length=1, description="Storyboard ID in E{episode}S{sequence} or E{episode}S{sequence}_{subsequence} form"
    )
    duration_seconds: int = Field(default=8, ge=1, le=60, description="Storyboard duration in seconds")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition")
    characters_in_scene: list[str] = Field(description="Names of characters appearing in the shot")
    scenes: list[str] = Field(default_factory=list, description="Names of scenes appearing in the shot")
    props: list[str] = Field(default_factory=list, description="Names of props appearing in the shot")
    scene_description: str = Field(
        description="Storyboard visual adaptation description: free text with visual content only, used by prompt_authoring to generate visual layers"
    )
    utterances: list[Utterance] = Field(
        default_factory=list,
        description="Ordered storyboard-level utterances: character dialogue and voiceover in chronological order, preserved verbatim",
    )
    source_text: str = Field(
        default="",
        description="Verbatim source excerpt (best-effort traceability anchor, not read aloud or rendered as audio)",
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="This storyboard requires manual replanning")


class DramaNormalizedScript(BaseModel):
    """Normalized script_plan storyboard content: the sole basis for visual authoring and subsequent web review/editing.

    The top level does not use ``extra="forbid"``, matching ``DramaEpisodeScript``,
    so persisted runtime fields do not cause rejection.
    """

    title: str = Field(description="Episode title")
    scenes: list[DramaSceneContent] = Field(description="Storyboard content list")
    new_assets: list[PlanNewAsset] = Field(
        default_factory=list,
        description="Unregistered characters / scenes / props referenced by this episode plan and their handling decisions",
    )


class DramaSceneVisual(BaseModel):
    """Storyboard visual layer from prompt_authoring (generate-script): scene_id alignment anchor plus visual fields only.

    ``scene_id`` must equal the established script_plan storyboard ID. The backend
    merges by that ID, not by list position.
    """

    model_config = _STRICT_CONFIG

    scene_id: str = Field(
        min_length=1, description="Alignment anchor: must equal the scene_id of an established script_plan storyboard"
    )
    image_prompt: ImagePrompt = Field(description="Storyboard image generation prompt")
    video_prompt: DramaVideoPrompt = Field(
        description="Video generation prompt without dialogue; spoken content belongs in script_plan utterances"
    )


class DramaVisualScript(BaseModel):
    """Visual authoring script, aligned with script_plan content by scene_id.

    The top level does not use ``extra="forbid"``, matching ``DramaNormalizedScript``.
    ``title`` is optional and ignored; the episode ledger supplies the episode title.
    """

    title: str = Field(default="", description="Optional episode title; script_plan content is authoritative")
    scenes: list[DramaSceneVisual] = Field(
        description="Storyboard visual layers aligned with script_plan content by scene_id"
    )


# ============ Ad/short-film (Ad) ============


class AdShot(BaseModel):
    """Advertisement/short-film storyboard: the smallest item in flat shots[].

    ``section`` labels the sales framework (hook/pain_point/product_reveal/selling_point/
    demo/trust/price_promo/cta). These eight values guide rather than enforce an enum;
    prompt asset constraints govern them. ``voiceover_text`` is first-class spoken
    copy and the sole source for subtitle export and later TTS. ``products_in_shot``
    references product names in project.json's products bucket; atmospheric shots
    have an empty list.
    """

    model_config = _STRICT_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _fill_pending_prompts(cls, data: object) -> object:
        """Pending-authoring shots may omit prompt fields: fill null; other shots still require both sides."""
        if isinstance(data, dict) and data.get("pending_authoring") is True:
            data = {"image_prompt": None, "video_prompt": None, **data}
        return data

    shot_id: str = Field(
        description="Storyboard ID in E{episode}S{sequence} or E{episode}S{sequence}_{subsequence} form"
    )
    section: str = Field(
        description="Sales-framework section label, such as hook/pain_point/product_reveal/selling_point/demo/trust/price_promo/cta"
    )
    duration_seconds: int = Field(ge=1, le=60, description="Storyboard duration in seconds")
    voiceover_text: str = Field(
        description="Spoken copy: must be complete and ready to record verbatim; may be an empty string"
    )
    characters_in_shot: list[str] = Field(default_factory=list, description="Names of characters appearing in the shot")
    scenes: list[str] = Field(default_factory=list, description="Names of scenes appearing in the shot")
    props: list[str] = Field(default_factory=list, description="Names of props appearing in the shot")
    products_in_shot: list[str] = Field(
        default_factory=list, description="Names of appearing products; a nonempty list identifies a product storyboard"
    )
    # Whole-generation response_schema still requires both prompts; only manually added pending shots may default null,
    # later filled by prompt authoring (see _fill_pending_prompts and structure validation).
    image_prompt: ImagePrompt | PromptText | PendingPrompt = Field(description="Storyboard image generation prompt")
    video_prompt: VideoPrompt | PromptText | PendingPrompt = Field(description="Video generation prompt")
    # See the same-named NarrationSegment field.
    note: SkipJsonSchema[str | None] = Field(default=None, description="User notes, excluded from generation")
    end_frame_image: SkipJsonSchema[str | None] = Field(
        default=None, description="Last-frame snapshot path relative to the project"
    )
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="Generated resource status"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="This unit requires manual replanning")
    # Awaiting authoring: set for new entries without visuals; clear when model/manual authoring completes them.
    # Hidden from LLMs, excluded from every PATCH allowlist, persisted only when set.
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="This entry awaits prompt authoring")


class AdShotVisual(BaseModel):
    """Visual layer authored for an existing advertisement storyboard: shot_id alignment anchor plus visual fields only.

    Spoken copy, duration, section and appearing assets already belong to the formal
    script. Preserve them by shot_id and exclude them from LLM output.
    """

    model_config = _STRICT_CONFIG

    shot_id: str = Field(
        min_length=1, description="Alignment anchor: must exactly match the shot_id of a storyboard awaiting authoring"
    )
    image_prompt: ImagePrompt = Field(description="Storyboard image generation prompt")
    video_prompt: VideoPrompt = Field(description="Video generation prompt")


class AdVisualScript(BaseModel):
    """LLM ``response_schema`` for advertisement storyboard authoring: visual layers for every pending storyboard."""

    shots: list[AdShotVisual] = Field(
        description="Visual layers for every storyboard awaiting authoring, aligned one-to-one by shot_id"
    )


class AdEpisodeScript(BaseModel):
    """Advertisement/short-film script, always a single episode in the episode 1 script file.

    The schema excludes `episode`; the CLI source of truth supplies it through
    `_add_metadata`. See `NarrationEpisodeScript`. The same reasoning applies to
    not using ``extra="forbid"`` at the top level.
    """

    title: str = Field(description="Short-film title")
    # See NarrationEpisodeScript.content_mode.
    content_mode: SkipJsonSchema[Literal["ad"]] = Field(default="ad", description="Content mode")
    # See NarrationEpisodeScript.novel.
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="Novel source information")
    shots: list[AdShot] = Field(description="Storyboard list")


# ============ Reference-to-video (Reference-to-Video) ============

#: Structural range of planned unit seconds (``ReferenceVideoUnit.duration_seconds``).
#: Static models reject nonpositive/noninteger and grossly implausible values; preflight projects to execution-model
#: tiers and warns about offsets. Body shot-description count does not determine the upper bound: descriptions carry no duration; units are generation calls.
#: Legacy migration problem shells are the sole exception, permitting zero under ``needs_replan``.
REFERENCE_UNIT_DURATION_RANGE: tuple[int, int] = (1, 300)

#: Relative deviation threshold for ad total duration versus project target_duration. Provider duration enums
#: (such as [4,6,8]) quantize totals, so a relaxed threshold catches only clear deviations. Exceeding it
#: only warns (generation logger / validator warnings), never blocks saving or reaches frontend.
#: Shared source for ``ScriptGenerator`` and ``DataValidator``.
AD_TARGET_DURATION_DRIFT_THRESHOLD = 0.20


def ad_shot_duration_seconds(shot: object) -> int:
    """Normalize malformed ad shot durations in seconds: nondict entries and nonpositive/noninteger
    durations count as zero without raising (exclude bool despite its int subclass).

    Storyboard-to-video total-duration deviation checks share this rule through ``ad_script_total_duration``.
    """
    if not isinstance(shot, dict):
        return 0
    value = shot.get("duration_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def ad_script_total_duration(shots: object) -> int:
    """Sum ad script shot durations in seconds.

    Single source for target_duration deviation sums, shared by ``ScriptGenerator`` probes and
    ``DataValidator``. Malformed data counts as zero without raising (``ad_shot_duration_seconds``);
    sums support warning-only observations and metadata, remaining robust for degraded raw-dict saves.
    """
    if not isinstance(shots, list):
        return 0
    return sum(ad_shot_duration_seconds(shot) for shot in shots)


#: Fallback seconds by skeleton when duration_seconds is missing: authoritative entry-duration defaults.
#: segments/scenes retain historical defaults; shots (ad) and video_units have no per-shot default duration
#: preference (planned by target/budget), so missing counts zero, avoiding invented values distorting target comparisons.
#: All four skeletons are registered; adding a fifth raises KeyError in ``item_duration`` until registered.
_ITEM_FALLBACK_DURATIONS: dict[str, int] = {"segments": 4, "scenes": 8, "shots": 0, "video_units": 0}


def item_duration(kind: str, item: object) -> int:
    """Normalize one script entry's seconds, extending ``ad_shot_duration_seconds`` to all four skeletons.

    Nondict entries have no duration semantics and count as zero. Missing or malformed dict
    ``duration_seconds`` (None / bool / nonpositive integer / float / string) falls back by ``kind`` through
    ``_ITEM_FALLBACK_DURATIONS``. Accept only positive integers (exclude bool despite its int subclass),
    consistent with validators rejecting ``duration <= 0``.
    """
    if not isinstance(item, dict):
        return 0
    value = item.get("duration_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return _ITEM_FALLBACK_DURATIONS[kind]


def script_duration_total(kind: str, items: object) -> int:
    """Sum entry durations by skeleton kind, robustly without raising (see ``item_duration``).

    Nonlist ``items`` (including degraded null) count as empty and return zero. Read-time computation and
    write-time recalculation share this source, avoiding three duplicate fallback tables and guards.
    """
    if not isinstance(items, list):
        return 0
    return sum(item_duration(kind, item) for item in items)


class ReferenceResource(BaseModel):
    """Reference-image reference: name and type only; resolve its path from the corresponding project.json bucket on reads.

    Derived at execution time and not persisted. Video units persist only their body;
    rendering and request projection derive references from ``@[name]`` (see
    :func:`lib.script.reference_video.text_parser.derive_references_from_text`).
    """

    model_config = _STRICT_CONFIG

    type: Literal["product", "character", "scene", "prop"] = Field(description="Referenced resource type")
    name: str = Field(
        description="Product/character/scene/prop name, already registered in the corresponding project.json bucket"
    )


class ReferenceVideoUnit(BaseModel):
    """Video unit: the smallest generation granularity for one video file.

    ``text`` is the sole persisted content truth: a free-form body from which reference
    images and speech ownership are derived at read/execution time, without separate
    stored structures (ADR 0064). Each unit represents one generation call with one
    scheduled duration. ``duration_seconds`` is the sole script-duration truth;
    execution preflight projects it to a requested provider tier.
    """

    model_config = _STRICT_CONFIG

    unit_id: str = Field(description="E{episode}U{sequence} form")
    text: str = Field(
        description="Unit body; may contain @[product]/@[character]/@[scene]/@[prop] references; an issue shell from migration may be empty"
    )
    duration_seconds: int = Field(
        ge=0,
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="Unit duration in seconds",
    )
    # note / generated_assets are UI/runtime/manual fields, hidden from LLMs.
    note: SkipJsonSchema[str | None] = Field(default=None, description="User notes")
    generated_assets: SkipJsonSchema[GeneratedAssets] = Field(
        default_factory=GeneratedAssets, description="Generated resource status"
    )
    needs_replan: SkipJsonSchema[bool] = Field(default=False, description="This unit requires manual replanning")
    # Corresponding source: passed from script-plan units at confirmation for creator comparison. Hidden from LLMs and Web PATCH
    # allowlists; Agents may batch-edit, requiring an episode-source verbatim substring when project source exists. Empty for manual units.
    source_text: SkipJsonSchema[str] = Field(
        default="", description="Verbatim source excerpt on which this unit is based"
    )
    # Awaiting authoring: set for new entries without visuals; clear when model/manual authoring completes them.
    # Hidden from LLMs, excluded from every PATCH allowlist, persisted only when set.
    pending_authoring: SkipJsonSchema[bool] = Field(default=False, description="This entry awaits prompt authoring")

    @model_validator(mode="after")
    def _validate_replan_shell(self) -> "ReferenceVideoUnit":
        """A fully dangling migration shell may be empty and zero seconds; other units must remain executable."""
        if not self.text.strip():
            if not self.needs_replan or self.duration_seconds != 0:
                raise ValueError("空 video unit 仅允许 needs_replan=true 且 duration_seconds=0")
        elif self.duration_seconds < REFERENCE_UNIT_DURATION_RANGE[0]:
            raise ValueError("非空 video unit 的 duration_seconds 必须为正整数")
        return self


class ReferenceVideoScript(BaseModel):
    """Reference-to-video episode script.

    The schema excludes `episode`; the CLI source of truth supplies it through
    `_add_metadata`. See `NarrationEpisodeScript`. The same reasoning applies to
    not using ``extra="forbid"`` at the top level.

    ``content_mode`` represents content type only (narration/drama/ad). Video source
    is a project-level fact (project.json ``generation_mode``), not stored on scripts.
    Generation mode is locked at creation and is expressed by the script skeleton.
    """

    title: str = Field(description="Episode title")
    # Hidden from LLMs: injected by _add_metadata.
    content_mode: SkipJsonSchema[Literal["narration", "drama", "ad"]] = Field(
        default="narration", description="Content mode (narration/drama/ad)"
    )
    # See NarrationEpisodeScript.novel.
    novel: SkipJsonSchema[NovelInfo] = Field(default_factory=NovelInfo, description="Novel source information")
    # See the same-named NarrationEpisodeScript field.
    hook: SkipJsonSchema[str | None] = Field(default=None, description="Episode-ending hook from the episode ledger")
    next_episode_teaser: SkipJsonSchema[str | None] = Field(
        default=None, description="Next-episode teaser from the episode ledger"
    )
    video_units: list[ReferenceVideoUnit] = Field(description="Video unit list")


def resolve_content_mode(script: dict[str, Any], project: dict[str, Any]) -> str:
    """Fall back to project configuration for missing script-level ``content_mode`` (unstamped legacy episodes),
    consistent with ``lib.project.data_validator._validate_episode_payload``: legacy episodes may omit it
    and use project defaults. Generation readers must not introduce stricter independent validation.
    """
    return script.get("content_mode", project.get("content_mode", "narration"))


# ============ Structured reference-to-video script_plan intermediate ============
#
# Two-stage separation (same as narration/drama, ADR 0041): script_plan unit splitting produces
# content (boundaries + bodies/durations), the sole basis for prompt_authoring (generate-script) to generate
# ReferenceVideoScript visual planning (shot scale / composition / camera expansion).


class ReferenceScriptPlanUnit(BaseModel):
    """Structured content unit from reference-to-video script_plan splitting.

    ``text`` is the sole body truth; reference images are not persisted here (see
    :class:`ReferenceVideoUnit`). ``build_reference_units_script_plan_model`` narrows
    duration_seconds tiers dynamically using runtime video capabilities. Reference-image
    limits also depend on runtime capabilities, but the splitting tool validates
    them after deriving references from the body; they are not part of this model.
    """

    model_config = _STRICT_CONFIG

    unit_id: str = Field(min_length=1, description="E{episode}U{sequence} form")
    text: str = Field(min_length=1, description="Unit body referencing registered assets with @[name]")
    duration_seconds: int = Field(
        ge=REFERENCE_UNIT_DURATION_RANGE[0],
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="Unit duration in seconds",
    )
    # Verbatim source anchor: splitter validates substring membership, then persists unchanged for confirmation comparison and drift diagnosis.
    # Defaults empty so legacy drafts without the field still validate.
    source_text: SkipJsonSchema[str] = Field(
        default="", description="Verbatim source excerpt on which this unit is based (traceability anchor)"
    )


class ReferenceScriptPlanDraft(BaseModel):
    """Structured reference-to-video script_plan intermediate state (schema for ``drafts/episode_N/script_plan_reference_units.json``).

    Allow extra top-level fields, matching NarrationScriptPlanDraft; validate against
    this model on reads.
    """

    model_config = ConfigDict(extra="ignore")

    units: list[ReferenceScriptPlanUnit] = Field(description="video_unit list")
    new_assets: list[PlanNewAsset] = Field(
        default_factory=list,
        description="Unregistered characters / scenes / props referenced by this episode plan and their handling decisions",
    )


# ---------------------------------------------------------------------------
# Flat reference-to-video draft structures: output shapes for both LLM levels
# ---------------------------------------------------------------------------
#
# script_plan / prompt_authoring LLM output matches human editor syntax (builtin shared/writing_syntax),
# so schemas flatten to one level: bodies are text; unit_id, reference images, utterances, and audio numbering
# are all machine-derived, never LLM-authored. Schemas enforce enums and outer structure only
# (the only layer backend constrained-decoding retries protect); parsers post-validate in-text syntax
# (lib/script/reference_video/draft_validation.py).


class ReferenceScriptPlanFlatUnit(BaseModel):
    """LLM script_plan unit output: duration, source anchor and reference-syntax body."""

    model_config = _STRICT_CONFIG

    duration_seconds: int = Field(
        ge=REFERENCE_UNIT_DURATION_RANGE[0],
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="Unit duration in seconds",
    )
    source_text: str = Field(
        min_length=1, description="Verbatim novel excerpt on which this unit is based; do not paraphrase or translate"
    )
    text: str = Field(
        min_length=1,
        description="Unit body using reference syntax: visual description plus inline dialogue / voiceover markers",
    )


class ReferenceScriptPlanFlatDraft(BaseModel):
    """Top-level LLM script_plan output shape."""

    model_config = ConfigDict(extra="ignore")

    units: list[ReferenceScriptPlanFlatUnit] = Field(min_length=1, description="Units in narrative order")
    new_assets: list[PlanNewAsset] = Field(
        default_factory=list,
        description="Unregistered characters / scenes / props referenced by this episode plan and their handling decisions",
    )


class ReferencePromptAuthoringFlatUnit(BaseModel):
    """LLM prompt_authoring unit output: only the expanded reference-syntax body.

    Duration and unit order are finalized script_plan content already confirmed by
    the user. Exclude them from prompt_authoring output: fields the LLM cannot write
    cannot drift and require no drift validation.
    """

    model_config = _STRICT_CONFIG

    text: str = Field(
        min_length=1,
        description="Reference-syntax body after prompt authoring: visual description plus inline dialogue / voiceover markers",
    )


class ReferencePromptAuthoringFlatScript(BaseModel):
    """Top-level LLM prompt_authoring output: title and unit bodies matching script_plan in count and order."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="Episode title")
    units: list[ReferencePromptAuthoringFlatUnit] = Field(
        min_length=1, description="One-to-one correspondence with script_plan units in unchanged order"
    )


class AdReferenceFlatUnit(BaseModel):
    """Self-contained authored unit from single-pass advertisement/short-film reference-to-video generation."""

    model_config = _STRICT_CONFIG

    duration_seconds: int = Field(
        ge=REFERENCE_UNIT_DURATION_RANGE[0],
        le=REFERENCE_UNIT_DURATION_RANGE[1],
        description="Scheduled unit duration in seconds, not quantized to provider tiers",
    )
    text: str = Field(
        min_length=1, description="Reference-syntax body: visual description plus inline dialogue / voiceover markers"
    )


class AdReferenceFlatScript(BaseModel):
    """Single-stage LLM output for advertisement/short-film reference-to-video; IDs and status are machine-derived."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="Short-film title")
    units: list[AdReferenceFlatUnit] = Field(min_length=1, description="Video units in playback order")
    new_assets: list[PlanNewAsset] = Field(
        default_factory=list,
        description="Unregistered characters / scenes / props referenced by this film and their handling decisions",
    )


# ============ Hard duration enums (dynamic script schemas from video capabilities) ============


def _coerce_digit_string(value: object) -> object:
    """Mechanically coerce digit-only strings to int; pass others unchanged to ``Literal`` validation.

    Gemini ``responseSchema`` ``enum`` supports strings only; integer-duration enums become wire string
    enums (``["4","6","8"]``, see ``lib/backends/text_backends/gemini.py``). Constrained decoding emits
    ``"4"``; restore int here so revalidation and parsing both match ``Literal[4,6,8]``.
    """
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _duration_literal(supported_durations: list[int]) -> Any:
    """Deduplicate/sort supported_durations into a digit-string-coercible ``Literal[...]``.

    ``model_json_schema()`` renders multiple values as JSON-schema ``enum`` and one as ``const``; both are hard constraints.
    Normalize with ``int(d)`` like ``ConfigResolver`` (``lib/config/resolver.py`` custom branch). Empty sets raise ValueError.
    """
    values = tuple(sorted({int(d) for d in supported_durations}))
    if not values:
        raise ValueError("supported_durations 为空，无法构造 duration 枚举约束")
    return Annotated[Literal[values], BeforeValidator(_coerce_digit_string)]


def _list_of(item: type[BaseModel]) -> Any:
    """``list[item]`` for ``create_model`` field definitions.

    Since pydantic 2.14, ``create_model`` field arguments are annotated ``TypeForm``, so type checkers evaluate
    arguments as type expressions. Direct ``list[<变量>]`` raises reportInvalidTypeForm; returning ``Any``
    here bypasses type-expression evaluation without changing runtime behavior.
    """
    return list[item]


def _constrained_duration_item(item_base: type[BaseModel], duration_type: Any, description: str) -> type[BaseModel]:
    """Constrain ``item_base`` ``duration_seconds`` to ``duration_type`` (field-constraint skeleton shared by three factories)."""
    return create_model(
        item_base.__name__,
        __base__=item_base,
        duration_seconds=(duration_type, Field(description=description)),
    )


def build_episode_script_model(content_mode: str, supported_durations: list[int]) -> type[BaseModel]:
    """Construct episode models with ``duration_seconds`` hard-constrained by ``supported_durations`` enums.

    Static NarrationSegment / DramaScene ``duration_seconds`` definitions use the ``Field(ge=1, le=60)`` range. Nonmember
    durations (such as 5/7 for supported [4,6,8]) pass Pydantic, only failing later at execution-layer
    ``assert_duration_supported`` or even reaching provider API errors. Constrain this field to
    ``Literal[*supported_durations]`` using the current video model's ``supported_durations``:
    - response_schema emits JSON-schema ``enum`` (``const`` for one value), constraining generation itself;
    - ``model_validate`` enforces membership.

    Supports narration / drama / ad. Canonically resolve skeleton kind through
    ``resolve_declared_kind`` (unknown/missing content_mode loudly raises ``ValueError``, never falling back to drama).
    Keep kind→model behavior mapping local, outside the registry. reference_video bypasses this path: its API consumes
    ``unit.duration_seconds`` (sum of shots), not individual-shot enums, and retains static ``ReferenceVideoScript``.
    """
    duration_type = _duration_literal(supported_durations)
    # Storyboard schema generation excludes reference paths; pass generation_mode None (narration→segments,
    # drama→scenes, ad→shots); unknown content_mode raises ValueError here.
    kind = resolve_declared_kind(content_mode, None)
    if kind == "segments":
        segment = _constrained_duration_item(
            NarrationSegment, duration_type, "Storyboard duration in seconds; must be one of supported_durations"
        )
        return create_model(
            "NarrationEpisodeScript",
            __base__=NarrationEpisodeScript,
            segments=(_list_of(segment), Field(description="Storyboard list")),
        )
    if kind == "shots":
        return _ad_episode_model(duration_type, "Storyboard duration in seconds; must be one of supported_durations")
    scene = _constrained_duration_item(
        DramaScene, duration_type, "Storyboard duration in seconds; must be one of supported_durations"
    )
    return create_model(
        "DramaEpisodeScript",
        __base__=DramaEpisodeScript,
        scenes=(_list_of(scene), Field(description="Storyboard list")),
    )


def build_drama_normalized_script_model(supported_durations: list[int]) -> type[BaseModel]:
    """Construct script_plan normalization models with ``duration_seconds`` hard-constrained by ``supported_durations`` enums.

    Moving extraction earlier makes script_plan determine shot durations; constrain the content layer
    ``DramaSceneContent`` (like ``build_episode_script_model``, emitting response_schema enum / const).
    The prompt_authoring visual layer has no duration and retains static ``DramaVisualScript``.
    """
    scene = _constrained_duration_item(
        DramaSceneContent,
        _duration_literal(supported_durations),
        "Storyboard duration in seconds; must be one of supported_durations",
    )
    return create_model(
        "DramaNormalizedScript",
        __base__=DramaNormalizedScript,
        scenes=(_list_of(scene), Field(description="Storyboard content list")),
    )


def _ad_episode_model(duration_type: Any, description: str) -> type[BaseModel]:
    """Dynamic ad-episode wrapper shared by both generation paths; only ``duration_seconds`` constraint types differ."""
    shot = _constrained_duration_item(AdShot, duration_type, description)
    return create_model(
        "AdEpisodeScript",
        __base__=AdEpisodeScript,
        shots=(_list_of(shot), Field(description="Storyboard list")),
    )


def with_new_assets_field(model: type[BaseModel]) -> type[BaseModel]:
    """Add new assets to a whole-generation ``response_schema``; persisted script models omit this field."""
    return create_model(
        model.__name__,
        __base__=model,
        new_assets=(
            list[PlanNewAsset],
            Field(
                default_factory=list,
                description="Unregistered characters / scenes / props referenced by this film and their handling decisions",
            ),
        ),
    )


def build_reference_units_script_plan_model(supported_durations: list[int]) -> type[BaseModel]:
    """Construct flat reference-to-video script_plan models with unit durations hard-constrained by ``supported_durations`` enums.

    Each unit is one generation call, and splitting determines provider-requested seconds; constrain
    ``ReferenceScriptPlanFlatUnit.duration_seconds`` (response_schema enum / const constrains LLM generation),
    matching prompt_authoring, which retains script_plan unit durations and only authors prompts.
    Reference limits and in-text syntax depend on runtime capabilities/project registries, so splitter tools
    post-validate them outside schemas.

    prompt_authoring needs no equivalent factory: it outputs no durations or capability-narrowed enums,
    using static ``ReferencePromptAuthoringFlatScript`` directly.
    """
    unit = _constrained_duration_item(
        ReferenceScriptPlanFlatUnit,
        _duration_literal(supported_durations),
        "Unit duration in seconds; must be one of supported_durations",
    )
    return create_model(
        "ReferenceScriptPlanFlatDraft",
        __base__=ReferenceScriptPlanFlatDraft,
        units=(_list_of(unit), Field(min_length=1, description="Units in narrative order")),
    )
