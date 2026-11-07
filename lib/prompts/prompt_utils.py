"""
Prompt utilities.

Convert structured prompts to YAML.
"""

import logging
import re
from collections.abc import Mapping
from typing import Any, get_args

import yaml

from lib.project.asset_types import normalize_asset_bucket, normalize_asset_name
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.prompts.reference_image_numbering import REFERENCE_IMAGES_KEY
from lib.script.script_models import CameraMotion, ShotType

logger = logging.getLogger(__name__)

# Prompt YAML line-width limit. PyYAML defaults to 80 columns and wraps long scalars at ASCII spaces.
# English/Vietnamese prompts commonly exceed it; wrapping would inject newlines into provider-bound text.
# Use an unreachable width to preserve author-written values verbatim.
_PROMPT_YAML_WIDTH = 1_000_000


def _dump_prompt_yaml(ordered: Mapping[str, Any]) -> str:
    """Shared prompt YAML serialization: insertion-order keys, unchanged Unicode, block layout, no wrapping."""
    return yaml.dump(
        dict(ordered),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=_PROMPT_YAML_WIDTH,
    )


# Body exclusion lines start at column zero with ``Avoid:`` followed by exclusion wording. Match prefixes,
# not whole lines: plain-text round trips may contain any historical declaration (such as a pre-Logo line),
# while whole-line matching recognizes only one hardcoded version. Consume spaces/tabs after colons, never newlines.
_AVOID_LINE = re.compile(r"^Avoid:[ \t]*(.*)$")

# Separator for joining exclusions and appending them to negative-node literal values.
_AVOID_SEPARATOR = "，"

# Collapse blank lines left by removed Avoid lines into one, consistent with template-origin newline collapsing.
_BLANK_RUN = re.compile(r"\n{3,}")

# Derive presets from authoritative lib.script.script_models Literal vocabularies to prevent duplicate drift.
SHOT_TYPES: list[str] = list(get_args(ShotType))
CAMERA_MOTIONS: list[str] = list(get_args(CameraMotion))


def image_prompt_to_yaml(
    image_prompt: dict, project_style: str, *, reference_images: str = "", style_description: str = ""
) -> str:
    """Convert imagePrompt structure into a YAML string.

    Args:
        image_prompt: segment image_prompt object, shaped as:
            {
                "scene": "scene description",
                "composition": {
                    "shot_type": "shot type",
                    "lighting": "lighting description",
                    "ambiance": "mood description"
                }
            }
        project_style: project-level style from project.json.
        reference_images: reference-type declaration value (``lib.prompts.reference_image_numbering``);
            when nonempty, insert ``Reference_Images`` between the style block and ``Scene``.
        style_description: project style description.

    Returns:
        YAML with key order Style / Visual style / Reference_Images / Scene / Composition / Avoid.
    """
    ordered: dict[str, Any] = {"Scene": image_prompt["scene"]}
    ordered["Composition"] = {
        "shot_type": image_prompt["composition"]["shot_type"],
        "lighting": image_prompt["composition"]["lighting"],
        "ambiance": image_prompt["composition"]["ambiance"],
    }
    return (
        builtin_templates.render(
            "storyboard/image",
            style=normalize_style_value(project_style),
            style_description=normalize_style_value(style_description),
            reference_images=yaml_section({REFERENCE_IMAGES_KEY: reference_images}) if reference_images else "",
            structured_body=_dump_prompt_yaml(ordered).rstrip(),
            text_body="",
        )
        + "\n"
    )


def require_storyboard_scene(image_prompt: Mapping[str, Any]) -> str:
    """Return the trimmed ``scene`` of a structured storyboard prompt, or raise ``ValueError``.

    Single criterion for what counts as a usable ``scene``: the enqueue guard
    (``TaskSpec.from_request``) and the rendering projection below both call it, so a prompt
    accepted at submission is exactly one the renderer can project.
    """

    scene = image_prompt.get("scene")
    if not isinstance(scene, str) or not scene.strip():
        raise ValueError("image_prompt.scene must be a non-empty string")
    return scene.strip()


def project_storyboard_image_prompt(image_prompt: object, project_style: str) -> tuple[str | dict[str, Any], str]:
    """Project one script prompt into the canonical semantics shared by rendering and currency."""

    if isinstance(image_prompt, str):
        prompt = image_prompt.strip()
        if not prompt:
            raise ValueError("image_prompt must not be empty")
        return prompt, project_style
    if not isinstance(image_prompt, Mapping):
        raise ValueError("image_prompt must be a string or object")
    scene = require_storyboard_scene(image_prompt)
    raw_composition = image_prompt.get("composition")
    composition = raw_composition if isinstance(raw_composition, Mapping) else {}
    return (
        {
            "scene": scene,
            "composition": {
                "shot_type": str(composition.get("shot_type") or "Medium Shot"),
                "lighting": str(composition.get("lighting") or ""),
                "ambiance": str(composition.get("ambiance") or ""),
            },
        },
        project_style,
    )


def video_prompt_to_yaml(video_prompt: dict) -> str:
    """Convert videoPrompt structure into YAML.

    Args:
        video_prompt: segment video_prompt object, shaped as:
            {
                "action": "action description",
                "camera_motion": "camera movement",
                "ambiance_audio": "ambient sound description",
                "dialogue": [{"speaker": "character name", "line": "dialogue"}],
                "voice_profiles": [{"Speaker": "character name", "Voice_Style": "voice style"}]
            }

    Returns:
        YAML ending with the negative-constraint key ``Avoid``.
    """
    dialogue = [{"Speaker": d["speaker"], "Line": d["line"]} for d in video_prompt.get("dialogue", [])]
    voice_profiles = video_prompt.get("voice_profiles") or []

    ordered: dict[str, Any] = {}
    # Voice_Profiles is a centralized top declaration. Callers already derive dialogue speakers intersected
    # with nonempty asset voice_style; inject in order without filtering again.
    if voice_profiles:
        ordered["Voice_Profiles"] = voice_profiles
    ordered["Action"] = video_prompt["action"]
    ordered["Camera_Motion"] = video_prompt["camera_motion"]
    ordered["Ambiance_Audio"] = video_prompt.get("ambiance_audio", "")

    # Add Dialogue only when dialogue exists.
    if dialogue:
        ordered["Dialogue"] = dialogue
    return builtin_templates.render("storyboard/video", body=_dump_prompt_yaml(ordered).rstrip()) + "\n"


def normalize_video_prompt(prompt: object) -> str:
    """Normalize the exact text sent to a video provider."""

    if isinstance(prompt, str):
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        body, _ = split_avoid_lines(prompt)
        return builtin_templates.render("storyboard/video", body=body.rstrip())
    if not isinstance(prompt, dict):
        raise ValueError("prompt must be a string or object")
    if not is_structured_video_prompt(prompt):
        raise ValueError("prompt must be a string or include action/camera_motion")

    action_text = str(prompt.get("action", "")).strip()
    if not action_text:
        raise ValueError("prompt.action must not be empty")
    dialogue = prompt.get("dialogue", [])
    if dialogue is None:
        dialogue = []
    if not isinstance(dialogue, list):
        raise ValueError("prompt.dialogue must be an array")

    normalized_dialogue = []
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "") or "").strip()
        line = str(item.get("line", "") or "").strip()
        if speaker or line:
            normalized_dialogue.append({"speaker": speaker, "line": line})

    normalized_prompt: dict[str, Any] = {
        "action": action_text,
        "camera_motion": str(prompt.get("camera_motion", "") or "") or "Static",
        "ambiance_audio": str(prompt.get("ambiance_audio", "") or ""),
        "dialogue": normalized_dialogue,
        "voice_profiles": prompt.get("voice_profiles") or [],
    }
    return video_prompt_to_yaml(normalized_prompt).rstrip()


def render_storyboard_video_prompt(
    prompt: object,
    item: Mapping[str, Any] | None = None,
    *,
    content_mode: str,
    voice_characters: dict[str, Any] | None,
) -> str:
    """Single final-text rendering entry point for storyboard-to-video prompts.

    Execution, batch admission, execution-sensitive summaries, and previews share this function, structurally
    ensuring verbatim consistency rather than coordinating four implementations by convention. ``prompt`` is the
    entry's current ``video_prompt`` (structured or text); ``item`` supplies drama shot ``utterances``
    (``None`` means no utterances field).

    Text bodies occupy the template body slot. Drama speech sequence still comes from script-plan
    ``utterances``; bodies carry no dialogue. This function appends dialogue and voice styles under shared gates.
    """

    if isinstance(prompt, dict):
        prompt = strip_voice_profiles(prompt)
        if content_mode == "drama":
            if isinstance(item, Mapping) and "utterances" in item:
                prompt = build_drama_video_prompt(prompt, item.get("utterances"), characters=voice_characters)
            else:
                # Pre-utterances scripts: load_script reads raw JSON without pydantic, so
                # DramaScene._migrate_legacy does not fill fields; dialogue remains in video_prompt.dialogue.
                prompt = build_drama_video_prompt_from_legacy_dialogue(prompt, characters=voice_characters)
    elif isinstance(prompt, str) and content_mode == "drama":
        prompt = _attach_drama_speech_text(prompt, item, characters=voice_characters)
    return normalize_video_prompt(prompt)


def _attach_drama_speech_text(text: str, item: Mapping[str, Any] | None, *, characters: dict[str, Any] | None) -> str:
    """Append drama speech declarations after text-form video_prompt bodies.

    Shares ``utterances_to_dialogue`` and ``_build_voice_profiles`` with structured rendering; retains
    ``video_prompt_to_yaml`` keys ``Voice_Profiles`` / ``Dialogue``, giving both forms one model vocabulary.
    Append nothing when no dialogue or no injectable voice styles under silent gating. Deduplicate declarations
    individually by content: structured→text starts from current rendering, which already contains them;
    whole-prefix detection would miss them and duplicate declarations.
    """
    utterances = item.get("utterances") if isinstance(item, Mapping) else None
    dialogue = utterances_to_dialogue(utterances)
    sections: list[str] = []
    if characters is not None:
        voice_profiles = _build_voice_profiles(dialogue, characters)
        if voice_profiles:
            sections.append(yaml_section({"Voice_Profiles": voice_profiles}))
    if dialogue:
        sections.append(
            yaml_section({"Dialogue": [{"Speaker": entry["speaker"], "Line": entry["line"]} for entry in dialogue]})
        )
    pending = [section for section in sections if section not in text]
    if not pending:
        return text
    return f"{text.rstrip()}\n\n" + "\n".join(pending) + "\n"


def split_avoid_lines(text: str) -> tuple[str, str]:
    """Separate body exclusion lines, returning ``(body, exclusion_text)``.

    ``Avoid:`` lines are shared channel exclusions injected by media-specific templates. Most providers lack
    negative parameters, so these remain body wording; ComfyUI endpoints bind negative-prompt nodes, requiring
    separate extraction. Both paths share detection, so one rule determines removal rather than channels diverging.

    Remove every exclusion line, collapse consecutive blank lines to one, and trim outer whitespace. Removed
    lines often carry adjacent blanks; without collapse they leave body gaps. Join exclusion text in occurrence
    order with “，”, preserving wording after each prefix verbatim. Empty wording contributes nothing
    (it is only a bare ``Avoid:``).
    """
    kept: list[str] = []
    avoided: list[str] = []
    for line in text.split("\n"):
        match = _AVOID_LINE.match(line)
        if match is None:
            kept.append(line)
            continue
        phrase = match.group(1).strip()
        if phrase:
            avoided.append(phrase)
    body = _BLANK_RUN.sub("\n\n", "\n".join(kept)).strip()
    return body, _AVOID_SEPARATOR.join(avoided)


def append_avoid_text(literal: str, avoid_text: str) -> str:
    """Append extracted exclusions after a negative node's literal value.

    Append rather than overwrite: workflow-authored negative wording (trigger/quality words) belongs to that
    workflow, and erasing it rewrites the author's source. Empty literals receive text without a leading separator.
    """
    if not literal.strip():
        return avoid_text
    if not avoid_text:
        return literal
    return f"{literal}{_AVOID_SEPARATOR}{avoid_text}"


def yaml_section(ordered: dict[str, Any]) -> str:
    """Render a standalone YAML section appendable to text prompts, without a trailing newline.

    Retain ``image_prompt_to_yaml`` / ``video_prompt_to_yaml`` keys and indentation so speech declarations
    and reference-type declarations are verbatim-identical across structured/text forms, enabling content deduplication.
    """
    return _dump_prompt_yaml(ordered).rstrip()


def strip_voice_profiles(video_prompt: dict[str, Any]) -> dict[str, Any]:
    """Strip caller-supplied ``voice_profiles``.

    Orchestration mechanically derives ``Voice_Profiles`` from character assets; neither script JSON nor caller
    payload carries it. Both injection sites (worker execution and SDK enqueueing) must first use this function
    before deciding whether to call :func:`build_drama_video_prompt`, preventing non-drama modes or entries
    without ``utterances`` from bypassing class C (truly silent) gating through caller-supplied or leftover profiles.
    """
    return {k: v for k, v in video_prompt.items() if k != "voice_profiles"}


def build_drama_video_prompt(
    video_prompt: dict[str, Any],
    utterances: object,
    *,
    characters: dict[str, Any] | None,
) -> dict[str, Any]:
    """Single drama video_prompt injection entry point for dialogue and Voice_Profiles.

    Shared by worker execution and SDK enqueueing, structurally ensuring equivalent injection rather than
    keeping separate implementations aligned by convention.

    ``characters`` of ``None`` means no Voice_Profiles injection (class C truly silent models). Always first
    strip caller-supplied ``voice_profiles``: orchestration derives this declaration from assets, never script
    JSON, and leftover script values must not bypass class C gating into YAML.
    """
    prompt = strip_voice_profiles(video_prompt)
    dialogue = utterances_to_dialogue(utterances)
    return _attach_voice_profiles(prompt, dialogue, characters=characters)


def build_drama_video_prompt_from_legacy_dialogue(
    video_prompt: dict[str, Any],
    *,
    characters: dict[str, Any] | None,
) -> dict[str, Any]:
    """Voice_Profiles injection for legacy drama scenes before utterances migration, with dialogue still in
    ``video_prompt.dialogue`` and no shot-level ``utterances`` field.

    ``load_script`` reads raw JSON without pydantic; ``DramaScene._migrate_legacy`` does not automatically
    fill ``utterances`` for these scripts. Callers detect ``"utterances" not
    in item`` and use this function instead of :func:`build_drama_video_prompt`. Legacy ``dialogue``
    already has target ``{speaker, line}`` shape, so derive Voice_Profiles directly without
    :func:`utterances_to_dialogue` conversion.
    """
    prompt = strip_voice_profiles(video_prompt)
    dialogue = prompt.get("dialogue")
    if not isinstance(dialogue, list):
        dialogue = []
    return _attach_voice_profiles(prompt, dialogue, characters=characters)


def _attach_voice_profiles(
    prompt: dict[str, Any], dialogue: list[dict[str, str]], *, characters: dict[str, Any] | None
) -> dict[str, Any]:
    prompt["dialogue"] = dialogue
    if characters is not None:
        voice_profiles = _build_voice_profiles(dialogue, characters)
        if voice_profiles:
            prompt["voice_profiles"] = voice_profiles
    return prompt


def _build_voice_profiles(dialogue: list[Any], characters: dict[str, Any]) -> list[dict[str, str]]:
    """Derive Voice_Profiles from dialogue speakers and character assets.

    Include only existing character assets with nonempty ``voice_style``, one entry per speaker in first-dialogue
    order. Silently skip missing assets/empty styles (no user-facing notice channel; callers log as needed).

    Speakers and character-table keys may use NFC or NFD (legacy scripts/buckets need no migration); index
    and deduplicate using ``lib.project.asset_types`` comparison normalization, retaining original dialogue in ``Speaker``.

    ``characters`` comes from plain project.json, which manual editing/external scripts may corrupt into
    non-dicts. Narrow types at each level rather than indexing directly (following ``lib.config.resolver._safe_dict``).
    """
    characters = normalize_asset_bucket(characters)
    seen: set[str] = set()
    profiles: list[dict[str, str]] = []
    for entry in dialogue:
        speaker = entry.get("speaker") if isinstance(entry, dict) else None
        if not isinstance(speaker, str) or not speaker:
            continue
        speaker_key = normalize_asset_name(speaker)
        if speaker_key in seen:
            continue
        seen.add(speaker_key)
        character = characters.get(speaker_key)
        if not isinstance(character, dict):
            logger.debug("Voice_Profiles 跳过：speaker %r 未命中角色资产", speaker)
            continue
        voice_style = character.get("voice_style")
        if isinstance(voice_style, str) and voice_style.strip():
            profiles.append({"Speaker": speaker, "Voice_Style": voice_style.strip()})
        else:
            logger.debug("Voice_Profiles 跳过：角色 %r 的 voice_style 为空", speaker)
    return profiles


def utterances_to_dialogue(utterances: object) -> list[dict[str, str]]:
    """Drama lip-sync track projection: take dialogue-kind entries from ordered ``utterances`` and produce
    video-YAML ``{speaker, line}`` lists, preserving order.

    Voiceover-kind never enters video prompts (no speaker; reserved for subtitles/TTS). Robustly skip
    nonlist inputs, nondict/nonobject elements, and nondialogue entries. Dialogue needs nonempty speaker and line;
    malformed missing-speaker dialogue (contract requires a nonempty speaker) must not reenter lip-sync/video prompts.

    Supports raw JSON ``dict`` entries and instantiated Pydantic ``Utterance`` objects with matching attributes.
    Explicit ``isinstance(_, str)`` checks read speaker/text; nonstrings (such as numbers) become empty, never
    ``str()``-coerced, preventing silent malformed-type stringification into YAML.
    """
    dialogue: list[dict[str, str]] = []
    if not isinstance(utterances, list):
        return dialogue
    for entry in utterances:
        if isinstance(entry, dict):
            kind = entry.get("kind")
            speaker_val = entry.get("speaker")
            text_val = entry.get("text")
        elif hasattr(entry, "kind"):
            kind = getattr(entry, "kind", None)
            speaker_val = getattr(entry, "speaker", None)
            text_val = getattr(entry, "text", None)
        else:
            continue

        if kind != "dialogue":
            continue

        speaker = speaker_val.strip() if isinstance(speaker_val, str) else ""
        line = text_val.strip() if isinstance(text_val, str) else ""
        if speaker and line:
            dialogue.append({"speaker": speaker, "line": line})
    return dialogue


def is_structured_image_prompt(image_prompt) -> bool:
    """Check whether image_prompt is structured.

    Args:
        image_prompt: image_prompt field value.

    Returns:
        True for structured dicts, False for legacy strings.
    """
    return isinstance(image_prompt, dict) and "scene" in image_prompt


def is_structured_video_prompt(video_prompt) -> bool:
    """Check whether video_prompt is structured.

    Args:
        video_prompt: video_prompt field value.

    Returns:
        True for structured dicts, False for legacy strings.
    """
    return isinstance(video_prompt, dict) and "action" in video_prompt


def validate_shot_type(shot_type: str) -> bool:
    """Validate shot type against preset options."""
    return shot_type in SHOT_TYPES


def validate_camera_motion(camera_motion: str) -> bool:
    """Validate camera motion against preset options."""
    return camera_motion in CAMERA_MOTIONS
