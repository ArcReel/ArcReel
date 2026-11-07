"""Data validation utilities.

Validate project.json/episode JSON structure and reference consistency.

Errors/warnings are locale-neutral ``ValidationMessage`` key/params, rendered at consumer boundaries:
archive routers use request languages; Agents/CLI use default language."""

from __future__ import annotations

import logging
import re
from collections.abc import Container, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import ValidationError

from arcreel_market_core.validation_messages import MessageJoin, MessagePart, MessageRef, ValidationMessage
from lib.agent.profile_manifest import VALID_CONTENT_MODES as _VALID_CONTENT_MODES
from lib.episode.episode_ledger import (
    LEDGER_STATUSES,
    EpisodeOutline,
    SourceRange,
    parse_positive_episode_num,
)
from lib.episode.episode_sources import (
    SOURCE_ORIGIN_FIELD,
    SOURCE_ORIGINS,
    WHOLE_SOURCE_FILES_KEY,
    SourceOrigin,
    is_whole_source_file_path,
)
from lib.episode.episode_target_duration import (
    EPISODE_TARGET_DURATION_FIELD,
    MAX_EPISODE_TARGET_DURATION,
    MIN_EPISODE_TARGET_DURATION,
    is_valid_episode_target_duration,
)
from lib.episode.source_kinds import SOURCE_KIND_FIELD, SOURCE_KINDS, is_source_kind
from lib.infra.json_io import load_json_or_none
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.validation_messages import ValidationResult
from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    asset_name_comparison_key,
    project_asset_name_conflicts,
)
from lib.project.project_manager import VALID_GENERATION_MODES as _VALID_GENERATION_MODES
from lib.project.project_migrations.backups import is_versioned_backup_name
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.script_models import (
    AD_TARGET_DURATION_DRIFT_THRESHOLD,
    REFERENCE_UNIT_DURATION_RANGE,
    ad_script_total_duration,
    resolve_content_mode,
)
from lib.script.script_skeleton import (
    STORYBOARD_ITEM_ID_PATTERN,
    SkeletonRouteMismatchError,
    ensure_route_skeleton,
    resolve_declared_kind,
    resolve_script_kind,
)
from lib.speech.speech_rate import (
    MAX_SPEECH_RATE_UPS,
    MIN_SPEECH_RATE_UPS,
    SPEECH_RATE_FIELD,
    estimate_spoken_seconds,
    is_valid_speech_rate,
    project_speech_rate_override,
)

__all__ = [
    "DataValidator",
    "validate_episode",
    "validate_project",
]

#: One-sided drama dialogue/voiceover-to-shot-duration upper tolerance. Estimated rates
#: (``lib.speech.speech_rate``) are approximate; 20% headroom warns only clearly excessive speech
#: (long dialogue would truncate or become abnormally fast). Warn excess, never too little;
#: visuals drive duration and silence is valid, without rewriting. Only DataValidator consumes this, sharing ad-drift
#: scale and warn-only/nonblocking semantics.
DRAMA_SPEECH_OVERFLOW_TOLERANCE = 0.20

logger = logging.getLogger(__name__)


def _m(key: str, **params: Any) -> ValidationMessage:
    """Build key/parameter messages without rendering at production sites."""
    return ValidationMessage(key, params)


def _asset(asset_type: str) -> MessageRef:
    """Nested asset-noun translation keys, reusing ``asset_type_*`` vocabulary."""
    return MessageRef(f"asset_type_{asset_type}")


def _allowed(values: Iterable[str]) -> str:
    """Stable text of allowed values; direct set ``repr`` changes process order under randomized string hashing."""
    return ", ".join(sorted(values))


def _pydantic_error_summary(exc: ValidationError) -> MessageJoin:
    """Compress ValidationError into ``字段: 原因`` fragments for embedded error lists.

    Custom validators raise translation keys (``lib.episode.episode_ledger``), restored as ``MessageRef``
    for request-language rendering. Built-in Pydantic errors lack translatable structure and remain unchanged."""
    parts: list[MessagePart] = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        reason = _custom_error_key(err)
        if reason:
            parts.append(MessageJoin((f"{location}: ", MessageRef(reason)), separator=""))
        else:
            parts.append(f"{location}: {err['msg']}")
    return MessageJoin(tuple(parts))


def _custom_error_key(err: Mapping[str, Any]) -> str | None:
    """Translation keys raised by custom validators; None for built-ins/unregistered wording."""
    if err.get("type") != "value_error":
        return None
    candidate = str(err.get("ctx", {}).get("error", ""))
    return candidate if candidate.startswith("val_") else None


def _is_parseable_iso_timestamp(value: str) -> bool:
    """Validate ISO8601 parseability, beyond string type.

    Frontend ``computeVoiceLegacyNotice`` compares ``new Date(iso).getTime()`` values; invalid strings
    produce ``NaN``, whose comparisons are always false, silently disabling banners rather than raising.
    This is subtler than type errors, requiring value validation too."""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_source_kind(item: Mapping[str, Any], prefix: str, errors: list[ValidationMessage]) -> None:
    """Missing source types default to novel; reject invalid values such as ``screen_play`` only."""
    value = item.get(SOURCE_KIND_FIELD)
    if value is not None and not is_source_kind(value):
        errors.append(_m("val_source_kind_invalid", prefix=prefix, value=value, allowed=", ".join(SOURCE_KINDS)))


class DataValidator:
    """Data validator."""

    # content_mode expresses content type only; project generation_mode expresses video origin.
    # Valid values come from lib.agent.profile_manifest, avoiding duplicate enums.
    VALID_CONTENT_MODES: ClassVar[set[str]] = set(_VALID_CONTENT_MODES)
    # Valid generation modes (storyboard / reference_video) come from creating writer lib.project.project_manager,
    # avoiding divergent enums. Required; v4→v5 backfills legacy values, absence is invalid.
    VALID_GENERATION_MODES: ClassVar[set[str]] = set(_VALID_GENERATION_MODES)
    # Reference-unit structural-duration ranges share that source; tier membership needs runtime models
    # and is outside archive validation.
    VALID_UNIT_DURATION_RANGE = REFERENCE_UNIT_DURATION_RANGE
    ID_PATTERN = STORYBOARD_ITEM_ID_PATTERN
    EXTERNAL_URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
    ALLOWED_ROOT_ENTRIES: ClassVar[set[str]] = {
        "project.json",
        "style_reference.png",
        "style_reference.jpg",
        "style_reference.jpeg",
        "style_reference.webp",
        "source",
        "scripts",
        "drafts",
        "edit_timelines",
        "characters",
        "scenes",
        "props",
        "products",
        "reference_videos",
        "storyboards",
        "end_frames",
        "videos",
        "audio",
        "subtitles",
        "presentations",
        "thumbnails",
        "output",
        "versions",
        "grids",
        "bgm",
        "renders",
    }

    def __init__(self, projects_dir: str | Path | None = None):
        """Initialize the validator.

        Args:
            projects_dir: Project directory, defaulting to current data-root layout."""
        if projects_dir is None:
            from lib.infra.data_root_layout import DataRootLayout

            self.projects_dir = DataRootLayout.current().projects_dir
        else:
            self.projects_dir = Path(projects_dir)

    @staticmethod
    def _is_hidden_path(path: Path) -> bool:
        return any(part.startswith(".") or part == "__MACOSX" for part in path.parts)

    def _resolve_existing_path(
        self,
        project_dir: Path,
        raw_path: str,
        *,
        default_dir: str | None = None,
        missing_ok: bool = False,
        confine_to_default_dir: bool = False,
    ) -> tuple[str | None, tuple[str, dict[str, Any]] | None]:
        """Return ``(相对路径, 失败原因)``; failure is ``(key, params)``, with ``field`` supplied by callers."""
        normalized = str(raw_path).strip().replace("\\", "/")
        if not normalized:
            return None, ("val_path_empty", {})

        candidate_paths = [Path(normalized)]
        if default_dir and len(candidate_paths[0].parts) == 1:
            candidate_paths.append(Path(default_dir) / candidate_paths[0])

        confine_root = (
            safe_join(project_dir, default_dir, allow_base=True) if confine_to_default_dir and default_dir else None
        )

        seen: set[str] = set()
        saw_out_of_confine = False
        for candidate in candidate_paths:
            candidate_key = candidate.as_posix()
            if candidate_key in seen:
                continue
            seen.add(candidate_key)

            try:
                resolved = safe_join(project_dir, candidate)
            except PathTraversalError:
                return None, ("val_path_traversal", {"path": normalized})

            if confine_root is not None:
                try:
                    resolved.relative_to(confine_root)
                except ValueError:
                    saw_out_of_confine = True
                    continue

            if resolved.is_file():
                return candidate.as_posix(), None

        if saw_out_of_confine:
            return None, ("val_path_outside_dir", {"dir": default_dir, "path": normalized})
        if missing_ok:
            return None, None
        return None, ("val_path_missing", {"path": normalized})

    def _validate_local_reference(
        self,
        project_dir: Path,
        value: Any,
        errors: list[ValidationMessage],
        field_name: str,
        *,
        default_dir: str | None = None,
        allow_external: bool = False,
        missing_ok: bool = False,
        confine_to_default_dir: bool = False,
    ) -> str | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            errors.append(_m("val_field_must_be_string", field=field_name))
            return None

        raw_value = value.strip()
        if not raw_value:
            return None

        if self.EXTERNAL_URI_PATTERN.match(raw_value):
            if allow_external:
                return raw_value
            errors.append(_m("val_path_must_be_relative", field=field_name, path=raw_value))
            return None

        resolved_path, error = self._resolve_existing_path(
            project_dir,
            raw_value,
            default_dir=default_dir,
            missing_ok=missing_ok,
            confine_to_default_dir=confine_to_default_dir,
        )
        if error:
            key, params = error
            errors.append(ValidationMessage(key, {**params, "field": field_name}))
        return resolved_path

    @staticmethod
    def _validate_episode_ledger_fields(episode: dict[str, Any], prefix: str, errors: list[ValidationMessage]) -> None:
        """Validate ledger field shapes, all optional for episodes without positional records, using lib.episode.episode_ledger models.

        Check ``ledger_status`` type only; it is advisory consumption state, while ``source_range`` owns
        positions. Tolerate unknown statuses as absent: legacy deprecated values must not corrupt whole projects."""
        ledger_status = episode.get("ledger_status")
        if ledger_status is not None and not isinstance(ledger_status, str):
            errors.append(_m("val_ledger_status_type", prefix=prefix, value=repr(ledger_status)))
        elif isinstance(ledger_status, str) and ledger_status not in LEDGER_STATUSES:
            # Tolerate and retain diagnostics: cannot distinguish old values from manual typos; neither corrupts whole projects.
            logger.debug("%s: ledger_status 取值 %r 不在当前状态集内，按无状态处理", prefix, ledger_status)

        source_origin = episode.get(SOURCE_ORIGIN_FIELD)
        if source_origin is not None and source_origin not in SOURCE_ORIGINS:
            errors.append(
                _m(
                    "val_source_origin_invalid",
                    prefix=prefix,
                    value=source_origin,
                    allowed=", ".join(SOURCE_ORIGINS),
                )
            )

        _validate_source_kind(episode, prefix, errors)

        source_range = episode.get("source_range")
        if source_range is not None and source_origin in (SourceOrigin.OWN.value, SourceOrigin.NONE.value):
            errors.append(_m("val_source_range_requires_whole_source", prefix=prefix))
        if source_range is not None:
            try:
                SourceRange.model_validate(source_range)
            except ValidationError as exc:
                errors.append(
                    _m("val_field_invalid", field=f"{prefix}: source_range", detail=_pydantic_error_summary(exc))
                )

        hook = episode.get("hook")
        if hook is not None and not isinstance(hook, str):
            errors.append(_m("val_field_must_be_string", field=f"{prefix}: hook"))

        outline = episode.get("outline")
        if outline is not None:
            try:
                EpisodeOutline.model_validate(outline)
            except ValidationError as exc:
                errors.append(_m("val_field_invalid", field=f"{prefix}: outline", detail=_pydantic_error_summary(exc)))

    @staticmethod
    def _validate_ad_project_fields(
        project: dict[str, Any],
        content_mode: Any,
        errors: list[ValidationMessage],
    ) -> None:
        """Ad/short-film exclusive fields and single-episode invariant.

        target_duration/brief are ad-only. Ad excludes default_duration (shots individually budget total
        targets, making per-shot preference meaningless) and episode_target_duration (competing total-size
        semantics with target_duration). episodes always contains episode 1 only."""
        if content_mode != "ad":
            if project.get("target_duration") is not None:
                errors.append(_m("val_ad_only_field", field="target_duration"))
            if project.get("brief") is not None:
                errors.append(_m("val_ad_only_field", field="brief"))
            return

        target_duration = project.get("target_duration")
        if target_duration is None:
            errors.append(_m("val_ad_missing_target_duration"))
        elif not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0:
            errors.append(_m("val_ad_target_duration_invalid", value=repr(target_duration)))

        brief = project.get("brief")
        if brief is not None and not isinstance(brief, str):
            errors.append(_m("val_field_must_be_string", field="brief"))

        if project.get("default_duration") is not None:
            errors.append(_m("val_ad_no_default_duration"))

        if project.get(EPISODE_TARGET_DURATION_FIELD) is not None:
            errors.append(_m("val_ad_no_episode_target_duration"))

        if project.get("grid_storyboard") is True:
            errors.append(_m("val_ad_no_grid_storyboard"))

        episodes = project.get("episodes")
        if not isinstance(episodes, list) or (
            len(episodes) != 1 or not isinstance(episodes[0], dict) or episodes[0].get("episode") != 1
        ):
            errors.append(_m("val_ad_episodes_single"))

    def _validate_project_payload(
        self,
        project: dict[str, Any],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
    ) -> None:
        if "title" not in project:
            errors.append(_m("val_missing_field", field="title"))
        elif not isinstance(project["title"], str):
            errors.append(_m("val_field_type_string", field="title"))

        content_mode = project.get("content_mode")
        if not content_mode:
            errors.append(_m("val_missing_field", field="content_mode"))
        elif not isinstance(content_mode, str) or content_mode not in self.VALID_CONTENT_MODES:
            errors.append(
                _m("val_content_mode_invalid", value=content_mode, allowed=_allowed(self.VALID_CONTENT_MODES))
            )

        # Generation mode is required/binary, with no omission semantics; v4→v5 backfills including grid reencoding.
        generation_mode = project.get("generation_mode")
        if not generation_mode:
            errors.append(_m("val_missing_field", field="generation_mode"))
        elif not isinstance(generation_mode, str) or generation_mode not in self.VALID_GENERATION_MODES:
            errors.append(
                _m("val_generation_mode_invalid", value=generation_mode, allowed=_allowed(self.VALID_GENERATION_MODES))
            )

        grid_storyboard = project.get("grid_storyboard")
        if grid_storyboard is not None and not isinstance(grid_storyboard, bool):
            errors.append(_m("val_field_type_bool", field="grid_storyboard"))

        # Optional project reading-units/second override; absence uses language defaults, presence requires hard bounds.
        speech_rate = project.get(SPEECH_RATE_FIELD)
        if speech_rate is not None:
            if isinstance(speech_rate, bool) or not isinstance(speech_rate, (int, float)):
                errors.append(_m("val_field_type_number", field=SPEECH_RATE_FIELD))
            elif not is_valid_speech_rate(speech_rate):
                errors.append(
                    _m(
                        "val_field_out_of_range",
                        field=SPEECH_RATE_FIELD,
                        value=speech_rate,
                        min=MIN_SPEECH_RATE_UPS,
                        max=MAX_SPEECH_RATE_UPS,
                    )
                )

        # Optional integer episode-target seconds in hard bounds; absence means no target.
        # Like SPEECH_RATE_FIELD but integer-valued: report floats as type errors without rounding.
        episode_target_duration = project.get(EPISODE_TARGET_DURATION_FIELD)
        if episode_target_duration is not None:
            if isinstance(episode_target_duration, bool) or not isinstance(episode_target_duration, int):
                errors.append(_m("val_field_type_integer", field=EPISODE_TARGET_DURATION_FIELD))
            elif not is_valid_episode_target_duration(episode_target_duration):
                errors.append(
                    _m(
                        "val_field_out_of_range",
                        field=EPISODE_TARGET_DURATION_FIELD,
                        value=episode_target_duration,
                        min=MIN_EPISODE_TARGET_DURATION,
                        max=MAX_EPISODE_TARGET_DURATION,
                    )
                )

        self._validate_ad_project_fields(project, content_mode, errors)

        # Style is optional: missing/empty is valid (no style/custom references); present values require strings,
        # rejecting null too. v13→v14 preserves nonstrings for this report; planners raise on them.
        # Selected templates require expanded style snapshots (ADR 0023); empty loses selected styles.
        # Empty template IDs equal null, matching creation truthiness.
        style = project.get("style")
        style_template_id = project.get("style_template_id")
        if style_template_id is not None and not isinstance(style_template_id, str):
            errors.append(_m("val_field_type_string", field="style_template_id"))
            style_template_id = None
        if "style" in project and not isinstance(style, str):
            errors.append(_m("val_field_type_string", field="style"))
        elif style_template_id and not (style or "").strip():
            errors.append(_m("val_missing_field", field="style"))

        episodes = project.get("episodes", [])
        if not isinstance(episodes, list):
            errors.append(_m("val_field_must_be_array", field="episodes"))
        else:
            for index, episode in enumerate(episodes):
                prefix = f"episodes[{index}]"
                if not isinstance(episode, dict):
                    errors.append(_m("val_item_format_object", prefix=prefix))
                    continue

                episode_num = episode.get("episode")
                if not isinstance(episode_num, int) or isinstance(episode_num, bool):
                    errors.append(_m("val_episode_missing_num_at", prefix=prefix))
                # Empty titles are allowed: writers (script sync/orphan registration) use "" for unknown titles,
                # awaiting user/Agent naming.
                if not isinstance(episode.get("title"), str):
                    errors.append(_m("val_episode_missing_title_at", prefix=prefix))

                script_file = episode.get("script_file")
                if not script_file:
                    errors.append(_m("val_missing_field_at", prefix=prefix, field="script_file"))
                elif not isinstance(script_file, str):
                    errors.append(_m("val_field_must_be_string", field=f"{prefix}: script_file"))

                self._validate_episode_ledger_fields(episode, prefix, errors)

        whole_source = project.get(WHOLE_SOURCE_FILES_KEY)
        if whole_source is not None:
            if not isinstance(whole_source, list):
                errors.append(_m("val_field_must_be_array", field=WHOLE_SOURCE_FILES_KEY))
            else:
                for index, item in enumerate(whole_source):
                    if not isinstance(item, dict) or not is_whole_source_file_path(item.get("source_file")):
                        errors.append(_m("val_whole_source_file_invalid", index=index))
                    else:
                        _validate_source_kind(item, f"{WHOLE_SOURCE_FILES_KEY}[{index}]", errors)

        for first, duplicate in project_asset_name_conflicts(project):
            errors.append(
                _m(
                    "val_asset_name_duplicate",
                    first_type=_asset(first.asset_type),
                    first_name=first.name,
                    duplicate_type=_asset(duplicate.asset_type),
                    duplicate_name=duplicate.name,
                )
            )

        characters = project.get("characters", {})
        if isinstance(characters, dict):
            char_spec = ASSET_SPECS["character"]
            char_extra_fields = char_spec.extra_string_fields
            for char_name, char_data in characters.items():
                if not isinstance(char_data, dict):
                    errors.append(_m("val_asset_format_object", asset_type=_asset("character"), name=char_name))
                    continue
                self._validate_asset_description(char_data, "character", char_name, errors)
                for field_name in char_extra_fields:
                    # Present extra_string_fields (voice_style / reference_image, etc.) require
                    # strings, possibly empty, or downstream consumers such as path assembly crash.
                    # None counts unset; nonstr types fail loudly.
                    val = char_data.get(field_name)
                    if val is not None and not isinstance(val, str):
                        errors.append(
                            _m(
                                "val_asset_field_must_be_string",
                                asset_type=_asset("character"),
                                name=char_name,
                                field=field_name,
                                actual=type(val).__name__,
                            )
                        )
                    elif field_name == "voice_notice_dismissed_at" and val and not _is_parseable_iso_timestamp(val):
                        errors.append(
                            _m(
                                "val_asset_field_bad_timestamp",
                                asset_type=_asset("character"),
                                name=char_name,
                                field=field_name,
                                value=repr(val),
                            )
                        )
                # voice_updated_at is deliberately outside extra_string_fields/generic PATCH,
                # but needs type/value validation: external edits/imports can write invalid
                # strings/nonstrings, silently yielding NaN in frontend computeVoiceLegacyNotice
                # date parsing.
                voice_updated_at = char_data.get("voice_updated_at")
                if voice_updated_at is not None and not isinstance(voice_updated_at, str):
                    errors.append(
                        _m(
                            "val_asset_field_must_be_string",
                            asset_type=_asset("character"),
                            name=char_name,
                            field="voice_updated_at",
                            actual=type(voice_updated_at).__name__,
                        )
                    )
                elif voice_updated_at and not _is_parseable_iso_timestamp(voice_updated_at):
                    errors.append(
                        _m(
                            "val_asset_field_bad_timestamp",
                            asset_type=_asset("character"),
                            name=char_name,
                            field="voice_updated_at",
                            value=repr(voice_updated_at),
                        )
                    )
                self._validate_asset_list_fields(
                    char_data, char_spec.extra_list_fields, _asset("character"), char_name, errors
                )
                if char_spec.supports_derivatives:
                    self._validate_derivatives(char_data, "character", char_name, errors)

        if project.get("clues") is not None:
            errors.append(_m("val_deprecated_clues"))

        self._validate_project_catalog(project.get("scenes") or {}, errors, field_label="scenes")
        self._validate_project_catalog(project.get("props") or {}, errors, field_label="props")
        self._validate_project_catalog(project.get("products") or {}, errors, field_label="products")

    @staticmethod
    def _validate_asset_description(
        entry: dict[str, Any],
        asset_type: str,
        entry_name: str,
        errors: list[ValidationMessage],
    ) -> None:
        """Descriptions drive sheet generation, requiring nonempty only at that stage; validate types here.

        Empty/absent are valid; present values require strings, rejecting null too. LLMs write description
        directly; reject accidental numeric/object Agent inputs at guards, not after valid-asset persistence
        when downstream text consumers crash."""
        if "description" not in entry:
            return
        value = entry["description"]
        if not isinstance(value, str):
            errors.append(
                _m(
                    "val_asset_field_must_be_string",
                    asset_type=_asset(asset_type),
                    name=entry_name,
                    field="description",
                    actual=type(value).__name__,
                )
            )

    @staticmethod
    def _validate_derivatives(
        entry: dict[str, Any],
        asset_type: str,
        entry_name: str,
        errors: list[ValidationMessage],
    ) -> None:
        """Validate derivative tables on enabled asset types.

        Nested name → {description, <sheet_field>} fields both feed text consumers (editing instructions/
        paths), so nonstrings would crash downstream. Missing means unset and passes; migrations fill
        legacy empty tables, while new registration uses derivative subresources."""
        spec = ASSET_SPECS[asset_type]
        kind = _asset(asset_type)
        table = entry.get(DERIVATIVES_FIELD)
        if table is None:
            return
        if not isinstance(table, dict):
            errors.append(
                _m("val_asset_field_must_be_object", asset_type=kind, name=entry_name, field=DERIVATIVES_FIELD)
            )
            return
        for derivative_name, derivative in table.items():
            qualified = f"{entry_name}/{derivative_name}"
            if not isinstance(derivative, dict):
                errors.append(_m("val_asset_format_object", asset_type=kind, name=qualified))
                continue
            for field_name in ("description", spec.sheet_field):
                value = derivative.get(field_name)
                if value is not None and not isinstance(value, str):
                    errors.append(
                        _m(
                            "val_asset_field_must_be_string",
                            asset_type=kind,
                            name=qualified,
                            field=field_name,
                            actual=type(value).__name__,
                        )
                    )

    @staticmethod
    def _validate_asset_list_fields(
        data: dict,
        list_fields: tuple[str, ...],
        kind: MessageRef,
        name: str,
        errors: list[ValidationMessage],
    ) -> None:
        for field_name in list_fields:
            # Present extra_list_fields (aliases / reference_images / selling_points, etc.) must be string lists:
            # elements become paths/prompt text, so nonstrings crash consumers.
            # None counts unset; other types fail loudly.
            val = data.get(field_name)
            if val is None:
                continue
            if not isinstance(val, list):
                errors.append(
                    _m(
                        "val_asset_field_must_be_string_list",
                        asset_type=kind,
                        name=name,
                        field=field_name,
                        actual=type(val).__name__,
                    )
                )
                continue
            for idx, item in enumerate(val):
                if not isinstance(item, str):
                    errors.append(
                        _m(
                            "val_asset_field_item_must_be_string",
                            asset_type=kind,
                            name=name,
                            field=field_name,
                            index=idx,
                            actual=type(item).__name__,
                        )
                    )

    def _validate_project_catalog(
        self,
        catalog: Any,
        errors: list[ValidationMessage],
        *,
        field_label: str,
    ) -> None:
        if not isinstance(catalog, dict):
            errors.append(_m("val_field_must_be_object", field=field_label))
            return
        # Scene/prop extra_string_fields are currently empty (ASSET_SPECS), but read specs anyway,
        # preserving synchronized validation when future extras are added.
        asset_type = field_label.rstrip("s")  # "scenes" → "scene"; "products" → "product"
        spec = ASSET_SPECS.get(asset_type)
        extra_fields = spec.extra_string_fields if spec else ()
        extra_list_fields = spec.extra_list_fields if spec else ()
        kind = _asset(asset_type)
        for name, data in catalog.items():
            if not isinstance(data, dict):
                errors.append(_m("val_asset_format_object", asset_type=kind, name=name))
                continue
            self._validate_asset_description(data, asset_type, name, errors)
            for field_name in extra_fields:
                val = data.get(field_name)
                if val is not None and not isinstance(val, str):
                    errors.append(
                        _m(
                            "val_asset_field_must_be_string",
                            asset_type=kind,
                            name=name,
                            field=field_name,
                            actual=type(val).__name__,
                        )
                    )
            self._validate_asset_list_fields(data, extra_list_fields, kind, name, errors)

    def _unregistered_refs(self, refs: list[Any], registered: Container[str]) -> list[Any]:
        """Select unregistered references using ``lib.project.asset_types`` NFC coordinates.

        Script names/project.json keys may independently use NFC/NFD (registration normalizes, legacy
        scripts/buckets do not migrate). Normalize both for correct membership, matching downstream
        collectors. ``registered`` always comes from :class:`lib.references.reference_catalog.ReferenceCatalog`
        already normalized, so normalize script sides only. Use this for every asset equality check, never
        raw field-level set differences."""
        return [r for r in refs if not isinstance(r, str) or asset_name_comparison_key(r) not in registered]

    def _validate_segment_refs(
        self,
        prefix: str,
        refs: Any,
        catalog: ReferenceCatalog,
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        field_label: str,
        asset_type: str,
    ) -> None:
        """Optional storyboard references: warn when missing, error for nonarrays/unregistered values."""
        if refs is None:
            warnings.append(_m("val_missing_defaults_empty_array", prefix=prefix, field=field_label))
            return
        if not isinstance(refs, list):
            errors.append(_m("val_field_must_be_array", field=f"{prefix}: {field_label}"))
            return
        invalid = self._unregistered_refs(refs, catalog.reference_names(asset_type))
        if invalid:
            errors.append(
                _m(
                    "val_refs_unregistered",
                    prefix=prefix,
                    field=field_label,
                    asset_type=_asset(asset_type),
                    names=invalid,
                )
            )

    def validate_project_payload(self, project: dict[str, Any]) -> ValidationResult:
        """Validate in-memory project.json dict structure without disk reads.

        Shared prewrite validation: `patch_project` calls inside `update_project` mutation after applying
        changes, before persistence, aborting invalid writes instead of writing dirt then validating too late."""
        errors: list[ValidationMessage] = []
        warnings: list[ValidationMessage] = []
        self._validate_project_payload(project, errors, warnings)
        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)

    def validate_asset_definitions(self, project: dict[str, Any]) -> ValidationResult:
        """Validate the asset-entry subset of an in-memory project payload."""
        result = self.validate_project_payload(project)
        errors = [message for message in result.error_messages if message.key.startswith("val_asset_")]
        return ValidationResult(valid=not errors, error_messages=errors)

    def validate_project(self, project_name: str) -> ValidationResult:
        """Validate project.json."""
        return self.validate_project_dir(self.projects_dir / project_name)

    def validate_project_dir(self, project_dir: Path) -> ValidationResult:
        """Validate project.json in the specified directory."""
        errors: list[ValidationMessage] = []
        warnings: list[ValidationMessage] = []

        project_path = Path(project_dir) / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_project_json", path=project_path)],
            )

        self._validate_project_payload(project, errors, warnings)
        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)

    def _validate_generated_assets(
        self,
        project_dir: Path,
        prefix: str,
        assets: Any,
        errors: list[ValidationMessage],
    ) -> None:
        if assets in (None, ""):
            return
        if not isinstance(assets, dict):
            errors.append(_m("val_field_must_be_object", field=f"{prefix}.generated_assets"))
            return

        self._validate_local_reference(
            project_dir,
            assets.get("storyboard_image"),
            errors,
            f"{prefix}.generated_assets.storyboard_image",
            default_dir="storyboards",
        )
        self._validate_local_reference(
            project_dir,
            assets.get("storyboard_last_image"),
            errors,
            f"{prefix}.generated_assets.storyboard_last_image",
            default_dir="storyboards",
        )
        self._validate_local_reference(
            project_dir,
            assets.get("video_clip"),
            errors,
            f"{prefix}.generated_assets.video_clip",
            default_dir="videos",
        )
        self._validate_local_reference(
            project_dir,
            assets.get("video_uri"),
            errors,
            f"{prefix}.generated_assets.video_uri",
            default_dir="videos",
            allow_external=True,
        )
        self._validate_local_reference(
            project_dir,
            assets.get("narration_audio"),
            errors,
            f"{prefix}.generated_assets.narration_audio",
            default_dir="audio",
        )

        # video_generated_at lets computeVoiceLegacyNotice compare video creation against
        # character-voice updates. Invalid/nonstring dates become NaN,
        # making comparisons false and silently disabling checks.
        video_generated_at = assets.get("video_generated_at")
        field_name = f"{prefix}.generated_assets.video_generated_at"
        if video_generated_at is not None and not isinstance(video_generated_at, str):
            errors.append(
                _m("val_field_must_be_string_typed", field=field_name, actual=type(video_generated_at).__name__)
            )
        elif video_generated_at and not _is_parseable_iso_timestamp(video_generated_at):
            errors.append(_m("val_field_bad_timestamp", field=field_name, value=repr(video_generated_at)))

    def _validate_end_frame_image(
        self,
        project_dir: Path,
        prefix: str,
        item: dict[str, Any],
        errors: list[ValidationMessage],
    ) -> None:
        """Validate end-frame snapshot paths: errors for escaping, missing, or wrong-directory paths; missing means intolerable dangling references.

        Unlike generated_assets, accept only service-produced `end_frames/` snapshots through
        `confine_to_default_dir`, never existing storyboards/scripts files. Otherwise direct source references
        would bypass copying and break snapshot decoupling."""
        self._validate_local_reference(
            project_dir,
            item.get("end_frame_image"),
            errors,
            f"{prefix}.end_frame_image",
            default_dir="end_frames",
            confine_to_default_dir=True,
        )

    def _validate_segments(
        self,
        segments: Any,
        catalog: ReferenceCatalog,
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """Validate narration/commentary segments."""
        if not isinstance(segments, list):
            errors.append(_m("val_field_must_be_array", field="segments"))
            return

        for index, segment in enumerate(segments):
            prefix = f"segments[{index}]"
            if not isinstance(segment, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            segment_id = segment.get("segment_id")
            if not segment_id:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="segment_id"))
            elif not self.ID_PATTERN.match(segment_id):
                errors.append(_m("val_id_format", prefix=prefix, field="segment_id", value=segment_id))

            duration = segment.get("duration_seconds")
            if duration is None:
                warnings.append(_m("val_missing_duration_default", prefix=prefix, default=4))
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(_m("val_duration_invalid", prefix=prefix, value=duration))

            if not segment.get("novel_text"):
                errors.append(_m("val_missing_field_at", prefix=prefix, field="novel_text"))

            chars_in_segment = segment.get("characters_in_segment")
            if chars_in_segment is None:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="characters_in_segment"))
            elif not isinstance(chars_in_segment, list):
                errors.append(_m("val_field_must_be_array", field=f"{prefix}: characters_in_segment"))
            else:
                invalid = self._unregistered_refs(chars_in_segment, catalog.reference_names("character"))
                if invalid:
                    errors.append(
                        _m(
                            "val_refs_unregistered",
                            prefix=prefix,
                            field="characters_in_segment",
                            asset_type=_asset("character"),
                            names=invalid,
                        )
                    )

            self._validate_segment_refs(
                prefix,
                segment.get("scenes"),
                catalog,
                errors,
                warnings,
                field_label="scenes",
                asset_type="scene",
            )
            self._validate_segment_refs(
                prefix,
                segment.get("props"),
                catalog,
                errors,
                warnings,
                field_label="props",
                asset_type="prop",
            )

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    segment.get("generated_assets"),
                    errors,
                )
                self._validate_end_frame_image(project_dir, prefix, segment, errors)

    def _validate_scenes(
        self,
        scenes: Any,
        catalog: ReferenceCatalog,
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
        language: str | None = None,
        speech_rate_override: float | None = None,
    ) -> None:
        """Validate drama scenes.

        ``language`` is project ``source_language`` for upper-speech-volume rates, matching subtitles.
        ``speech_rate_override`` is project-level, None falling back to language defaults."""
        if not isinstance(scenes, list):
            errors.append(_m("val_field_must_be_array", field="scenes"))
            return

        for index, scene in enumerate(scenes):
            prefix = f"scenes[{index}]"
            if not isinstance(scene, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            scene_id = scene.get("scene_id")
            if not scene_id:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="scene_id"))
            elif not self.ID_PATTERN.match(scene_id):
                errors.append(_m("val_id_format", prefix=prefix, field="scene_id", value=scene_id))

            duration = scene.get("duration_seconds")
            if duration is None:
                warnings.append(_m("val_missing_duration_default", prefix=prefix, default=8))
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(_m("val_duration_invalid", prefix=prefix, value=duration))

            chars_in_scene = scene.get("characters_in_scene")
            if chars_in_scene is None:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="characters_in_scene"))
            elif not isinstance(chars_in_scene, list):
                errors.append(_m("val_field_must_be_array", field=f"{prefix}: characters_in_scene"))
            else:
                invalid = self._unregistered_refs(chars_in_scene, catalog.reference_names("character"))
                if invalid:
                    errors.append(
                        _m(
                            "val_refs_unregistered",
                            prefix=prefix,
                            field="characters_in_scene",
                            asset_type=_asset("character"),
                            names=invalid,
                        )
                    )

            self._validate_segment_refs(
                prefix,
                scene.get("scenes"),
                catalog,
                errors,
                warnings,
                field_label="scenes",
                asset_type="scene",
            )
            self._validate_segment_refs(
                prefix,
                scene.get("props"),
                catalog,
                errors,
                warnings,
                field_label="props",
                asset_type="prop",
            )

            # utterances is ordered storyboard speech; allow absence, validate present shape and
            # kind ⇄ speaker constraints like characters_in_scene, appending per item without raising.
            self._validate_utterances(scene.get("utterances"), prefix, errors)

            # Warn only when estimated dialogue/voiceover exceeds duration × tolerance:
            # never block/rewrite visually driven duration.
            self._warn_scene_speech_overflow(scene, prefix, language, speech_rate_override, warnings)

            # Planning fills source_text (verbatim anchors)/scene_description (visual adaptation), passed through conversion. Mirror
            # shared str models with extra=forbid: present values require strings, including rejection of explicit null.
            # Absent keys pass with empty defaults for legacy data; use in to distinguish absence/null.
            errors.extend(
                _m("val_field_must_be_string", field=f"{prefix}: {text_field}")
                for text_field in ("source_text", "scene_description")
                if text_field in scene and not isinstance(scene[text_field], str)
            )

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    scene.get("generated_assets"),
                    errors,
                )
                self._validate_end_frame_image(project_dir, prefix, scene, errors)

    @staticmethod
    def _validate_utterances(utterances: Any, prefix: str, errors: list[ValidationMessage]) -> None:
        """Validate ordered drama utterance structure and kind ⇄ speaker constraints, allowing absence.

        Each is ``{kind, speaker, text}``: kind ∈ {dialogue, voiceover}, nonempty text. Dialogue requires
        nonempty speakers; voiceover prohibits speakers. Append per-item errors without raising."""
        if utterances is None:
            return
        if not isinstance(utterances, list):
            errors.append(_m("val_field_must_be_array", field=f"{prefix}: utterances"))
            return
        for ui, item in enumerate(utterances):
            uprefix = f"{prefix}: utterances[{ui}]"
            if not isinstance(item, dict):
                errors.append(_m("val_utterance_must_be_object", prefix=uprefix))
                continue
            kind = item.get("kind")
            if kind not in ("dialogue", "voiceover"):
                errors.append(_m("val_utterance_kind_invalid", prefix=uprefix))
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                errors.append(_m("val_utterance_text_invalid", prefix=uprefix))
            speaker = item.get("speaker")
            if speaker is not None and not isinstance(speaker, str):
                # speaker accepts str/null only, matching Utterance.speaker: str | None.
                # Pydantic rejects numeric/boolean/object values; fail loudly here too, preventing
                # invalid shapes silently passing structural checks then failing Pydantic. has_speaker=True
                # records presence so dialogue does not add missing-speaker errors over root type errors.
                errors.append(_m("val_utterance_speaker_type", prefix=uprefix))
                has_speaker = True
            else:
                # String (empty/whitespace normalize absent via Utterance._normalize_speaker) or None.
                has_speaker = isinstance(speaker, str) and bool(speaker.strip())
            if kind == "dialogue" and not has_speaker:
                errors.append(_m("val_utterance_dialogue_speaker", prefix=uprefix))
            elif kind == "voiceover" and has_speaker:
                errors.append(_m("val_utterance_voiceover_speaker", prefix=uprefix))

    @staticmethod
    def _warn_scene_speech_overflow(
        scene: dict[str, Any],
        prefix: str,
        language: str | None,
        speech_rate_override: float | None,
        warnings: list[ValidationMessage],
    ) -> None:
        """Warn only when estimated dialogue + voiceover exceeds ``duration ×（1 + 容差）`` (one-sided, nonblocking).

        Sum ``estimate_spoken_seconds`` per utterance, sharing speech-rate truth and drama subtitle spans.
        Empty/whitespace/dirty entries estimate 0. Visuals drive duration; this never rewrites it. Warn only
        unspeakably much text, not too little. Skip nonpositive/noninteger duration or absent utterances.
        Like ad-duration drift, warnings do not block or push to frontend."""
        duration = scene.get("duration_seconds")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            return
        utterances = scene.get("utterances")
        if not isinstance(utterances, list):
            return
        spoken = 0.0
        for item in utterances:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                spoken += estimate_spoken_seconds(text, language, speech_rate_override)
        budget = duration * (1 + DRAMA_SPEECH_OVERFLOW_TOLERANCE)
        if spoken > budget:
            warnings.append(
                _m(
                    "val_scene_speech_overflow",
                    prefix=prefix,
                    spoken=spoken,
                    duration=duration,
                    tolerance=DRAMA_SPEECH_OVERFLOW_TOLERANCE,
                    budget=budget,
                )
            )

    def _validate_shots(
        self,
        shots: Any,
        catalog: ReferenceCatalog,
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """Validate flat ad/short-film shots, with first-class speech and named products.

        Storyboard duration membership belongs to generation schema supported_durations enums; validators
        lack provider capabilities and check positive integers only. Reference-to-video uses ``video_units``,
        bypassing this function. Normalize asset references by NFC (``_validate_segment_refs``), matching
        collectors in both generation paths."""
        if not isinstance(shots, list):
            errors.append(_m("val_ad_shots_missing"))
            return

        for index, shot in enumerate(shots):
            prefix = f"shots[{index}]"
            if not isinstance(shot, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            shot_id = shot.get("shot_id")
            if not shot_id:
                errors.append(_m("val_missing_field_at", prefix=prefix, field="shot_id"))
            elif not isinstance(shot_id, str) or not self.ID_PATTERN.match(shot_id):
                errors.append(_m("val_id_format", prefix=prefix, field="shot_id", value=shot_id))

            duration = shot.get("duration_seconds")
            if duration is None:
                warnings.append(_m("val_shot_duration_missing_zero", prefix=prefix))
            elif not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
                errors.append(_m("val_duration_invalid", prefix=prefix, value=duration))

            if "voiceover_text" not in shot:
                errors.append(_m("val_shot_missing_voiceover_text", prefix=prefix))
            elif not isinstance(shot.get("voiceover_text"), str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: voiceover_text"))

            section = shot.get("section")
            if section is not None and not isinstance(section, str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: section"))

            self._validate_segment_refs(
                prefix,
                shot.get("characters_in_shot"),
                catalog,
                errors,
                warnings,
                field_label="characters_in_shot",
                asset_type="character",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("scenes"),
                catalog,
                errors,
                warnings,
                field_label="scenes",
                asset_type="scene",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("props"),
                catalog,
                errors,
                warnings,
                field_label="props",
                asset_type="prop",
            )
            self._validate_segment_refs(
                prefix,
                shot.get("products_in_shot"),
                catalog,
                errors,
                warnings,
                field_label="products_in_shot",
                asset_type="product",
            )

            # Ad/short-film prompts may be empty only while pending authoring; all other missing/empty visual sides are incomplete structures.
            if shot.get("pending_authoring") is not True:
                if not shot.get("image_prompt"):
                    errors.append(_m("val_missing_field_at", prefix=prefix, field="image_prompt"))
                if not shot.get("video_prompt"):
                    errors.append(_m("val_missing_field_at", prefix=prefix, field="video_prompt"))

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    shot.get("generated_assets"),
                    errors,
                )
                self._validate_end_frame_image(project_dir, prefix, shot, errors)

    @staticmethod
    def _warn_ad_target_duration_drift(
        project: dict[str, Any],
        shots: Any,
        warnings: list[ValidationMessage],
    ) -> None:
        """Warn only for ad total-duration drift beyond thresholds, without blocking/frontend pushes (lightweight observation)."""
        target = project.get("target_duration")
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            return
        total = ad_script_total_duration(shots)
        if total <= 0:
            return
        delta_ratio = abs(total - target) / target
        if delta_ratio > AD_TARGET_DURATION_DRIFT_THRESHOLD:
            warnings.append(
                _m(
                    "val_ad_duration_drift",
                    total=total,
                    target=target,
                    delta=delta_ratio,
                    threshold=AD_TARGET_DURATION_DRIFT_THRESHOLD,
                )
            )

    @staticmethod
    def _check_unit_id_unique(
        unit_id: Any,
        seen_unit_ids: set[str],
        prefix: str,
        errors: list[ValidationMessage],
        *,
        missing_key: str,
    ) -> None:
        """Validate single unit_id presence/uniqueness.

        Centralize ``video_units`` identity checks."""
        if not unit_id or not isinstance(unit_id, str):
            errors.append(_m(missing_key, prefix=prefix))
            return
        if unit_id in seen_unit_ids:
            errors.append(_m("val_unit_id_duplicate", prefix=prefix, value=unit_id))
        seen_unit_ids.add(unit_id)

    def _validate_reference_video_script(
        self,
        video_units: Any,
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        project_dir: Path | None = None,
    ) -> None:
        """Validate reference-to-video video_units."""
        if not isinstance(video_units, list):
            errors.append(_m("val_video_units_missing"))
            return

        seen_unit_ids: set[str] = set()

        for index, unit in enumerate(video_units):
            prefix = f"video_units[{index}]"
            if not isinstance(unit, dict):
                errors.append(_m("val_item_must_be_object", prefix=prefix))
                continue

            self._check_unit_id_unique(
                unit.get("unit_id"),
                seen_unit_ids,
                prefix,
                errors,
                missing_key="val_unit_id_missing",
            )

            duration = unit.get("duration_seconds")
            needs_replan = unit.get("needs_replan", False)
            if not isinstance(needs_replan, bool):
                errors.append(_m("val_field_type_bool", field=f"{prefix}: needs_replan"))
            text = unit.get("text")
            if not isinstance(text, str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: text"))
            elif not text.strip() and needs_replan is not True:
                errors.append(_m("val_field_must_be_nonempty_string", field=f"{prefix}: text"))

            if "source_text" in unit and not isinstance(unit["source_text"], str):
                errors.append(_m("val_field_must_be_string", field=f"{prefix}: source_text"))

            low, high = self.VALID_UNIT_DURATION_RANGE
            blank_shell = needs_replan is True and (not isinstance(text, str) or not text.strip())
            valid_duration = False
            if isinstance(duration, int) and not isinstance(duration, bool):
                valid_duration = duration == 0 if blank_shell else low <= duration <= high
            if not valid_duration:
                errors.append(_m("val_unit_duration_range", prefix=prefix, low=low, high=high))

            # Unregistered body `@[名称]` is not a validation error: references derive at execution; unresolved mentions produce
            # nonblocking render/preview warnings (ADR 0064), never promoted to structural errors here.

            if project_dir is not None:
                self._validate_generated_assets(
                    project_dir,
                    prefix,
                    unit.get("generated_assets"),
                    errors,
                )

    def _validate_episode_payload(
        self,
        project_dir: Path,
        project: dict[str, Any],
        episode: dict[str, Any],
        errors: list[ValidationMessage],
        warnings: list[ValidationMessage],
        *,
        validate_artifacts: bool = True,
        validate_route: bool = True,
    ) -> None:
        catalog = build_reference_catalog(project)

        if not isinstance(episode.get("episode"), int):
            errors.append(_m("val_episode_missing_num"))

        # Empty titles are valid; presentation names episodes by broadcast position.
        if "title" not in episode:
            errors.append(_m("val_missing_field", field="title"))
        elif not isinstance(episode["title"], str):
            errors.append(_m("val_field_type_string", field="title"))

        content_mode = resolve_content_mode(episode, project)

        warnings.extend(
            _m("val_deprecated_field_removable", field=deprecated_field)
            for deprecated_field in ("characters_in_episode", "scenes_in_episode", "props_in_episode")
            if episode.get(deprecated_field) is not None
        )

        novel = episode.get("novel")
        if novel is not None and not isinstance(novel, dict):
            errors.append(_m("val_novel_must_be_object"))

        # Drama speech-warning rates come from lib.speech.speech_rate: project override first,
        # otherwise source_language defaults, with absent/dirty language falling back to default rates.
        source_language = project.get("source_language")
        scene_language = source_language if isinstance(source_language, str) else None
        scene_speech_rate = project_speech_rate_override(project)

        # Video origin is project generation_mode, absent from scripts; resolve_declared_kind
        # centralizes skeleton resolution. Ad reference-to-video uses video_units;
        # `storyboard` uses shots.
        gen_mode = project.get("generation_mode")
        try:
            kind = resolve_declared_kind(content_mode, gen_mode)
        except ValueError:
            # Present but invalid content_mode makes resolve_declared_kind fail loudly,
            # but validators report structured dirt rather than propagate exceptions. Skip later kind-dependent
            # checks without knowing which of segments/scenes/shots applies.
            errors.append(
                _m("val_content_mode_invalid", value=content_mode, allowed=_allowed(self.VALID_CONTENT_MODES))
            )
            return
        if validate_route:
            try:
                # Gates permit in-family legacy shapes (narration under scenes), returning actual skeletons for dispatch.
                # Declared kinds would read nonexistent segments and validate none of the data.
                kind = ensure_route_skeleton(episode, content_mode, gen_mode)
            except SkeletonRouteMismatchError as exc:
                # Cross-family mode/skeleton mismatch lacks the required array; per-field missing-segments errors
                # hide causes. Report structural conclusions/replanning guidance directly and skip later
                # skeleton checks. Generation gates refuse the same fact; validation only reports it.
                errors.append(exc.to_validation_message())
                return
        else:
            # Editing/viewing must permit repair of mismatched legacy scripts; reference checks dispatch actual disk shapes,
            # while default-on generation gates still prohibit production.
            kind = resolve_script_kind(episode)
        artifact_root = project_dir if validate_artifacts else None
        if kind == "video_units":
            self._validate_reference_video_script(
                episode.get("video_units", []),
                errors,
                warnings,
                project_dir=artifact_root,
            )
        elif kind == "segments":
            self._validate_segments(
                episode.get("segments", []),
                catalog,
                errors,
                warnings,
                project_dir=artifact_root,
            )
        elif kind == "shots":
            shots = episode.get("shots", [])
            self._validate_shots(
                shots,
                catalog,
                errors,
                warnings,
                project_dir=artifact_root,
            )
            self._warn_ad_target_duration_drift(project, shots, warnings)
        elif kind == "scenes":
            self._validate_scenes(
                episode.get("scenes", []),
                catalog,
                errors,
                warnings,
                project_dir=artifact_root,
                language=scene_language,
                speech_rate_override=scene_speech_rate,
            )
        else:  # pragma: no cover - resolve_declared_kind returns four skeletons; unhandled fifth kinds raise
            raise ValueError(f"未处置的骨架种类: {kind!r}")

    def validate_episode(self, project_name: str, episode_file: str) -> ValidationResult:
        """Validate episode JSON."""
        return self.validate_episode_file(self.projects_dir / project_name, episode_file)

    def validate_episode_payload(
        self,
        project_dir: Path,
        project: dict[str, Any],
        episode: dict[str, Any],
        *,
        validate_artifacts: bool = True,
        validate_route: bool = True,
    ) -> ValidationResult:
        """Validate an already loaded episode without reading or rewriting project files.

        Callers that classify artifact readiness separately can disable filesystem artifact
        checks while retaining the shared episode structure and reference validation. Editing
        flows can also disable the generation-mode gate while validating references against
        the supplied episode's actual skeleton.
        """
        errors: list[ValidationMessage] = []
        warnings: list[ValidationMessage] = []
        self._validate_episode_payload(
            Path(project_dir),
            project,
            episode,
            errors,
            warnings,
            validate_artifacts=validate_artifacts,
            validate_route=validate_route,
        )
        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)

    def validate_episode_file(
        self,
        project_dir: Path,
        episode_file: str | Path,
    ) -> ValidationResult:
        """Validate scripts in the specified directory."""
        project_dir = Path(project_dir)
        project_path = project_dir / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_project_json", path=project_path)],
            )

        resolved_episode_path, error = self._resolve_existing_path(
            project_dir,
            str(episode_file),
            default_dir="scripts",
        )
        if error or resolved_episode_path is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_script", path=project_dir / str(episode_file))],
            )

        episode_path = project_dir / resolved_episode_path
        episode = load_json_or_none(episode_path)
        if episode is None:
            return ValidationResult(
                valid=False,
                error_messages=[_m("val_cannot_load_script", path=episode_path)],
            )

        return self.validate_episode_payload(project_dir, project, episode)

    def validate_project_tree(self, project_dir: str | Path) -> ValidationResult:
        """Validate complete project directories.

        Beyond project.json/episode structure, validate local file references and additional top-level files."""
        project_dir = Path(project_dir)
        project_result = self.validate_project_dir(project_dir)
        errors = list(project_result.error_messages)
        warnings = list(project_result.warning_messages)

        project_path = project_dir / "project.json"
        project = load_json_or_none(project_path)
        if project is None:
            return ValidationResult(valid=False, error_messages=errors, warning_messages=warnings)

        self._validate_local_reference(
            project_dir,
            project.get("style_image"),
            errors,
            "project.style_image",
        )

        characters = project.get("characters", {})
        if isinstance(characters, dict):
            for char_name, char_data in characters.items():
                if not isinstance(char_data, dict):
                    continue
                self._validate_local_reference(
                    project_dir,
                    char_data.get("character_sheet"),
                    errors,
                    f"characters[{char_name}].character_sheet",
                    default_dir="characters",
                )
                self._validate_local_reference(
                    project_dir,
                    char_data.get("reference_image"),
                    errors,
                    f"characters[{char_name}].reference_image",
                    default_dir="characters/refs",
                )
                self._validate_local_reference(
                    project_dir,
                    char_data.get("reference_audio"),
                    errors,
                    f"characters[{char_name}].reference_audio",
                    default_dir="characters/refs_audio",
                )

        scenes_dict = project.get("scenes", {})
        if isinstance(scenes_dict, dict):
            for scene_name, scene_data in scenes_dict.items():
                if not isinstance(scene_data, dict):
                    continue
                self._validate_local_reference(
                    project_dir,
                    scene_data.get("scene_sheet"),
                    errors,
                    f"scenes[{scene_name}].scene_sheet",
                    default_dir="scenes",
                )

        props_dict = project.get("props", {})
        if isinstance(props_dict, dict):
            for prop_name, prop_data in props_dict.items():
                if not isinstance(prop_data, dict):
                    continue
                self._validate_local_reference(
                    project_dir,
                    prop_data.get("prop_sheet"),
                    errors,
                    f"props[{prop_name}].prop_sheet",
                    default_dir="props",
                )

        episodes = project.get("episodes", [])
        if isinstance(episodes, list):
            for index, episode_meta in enumerate(episodes):
                if not isinstance(episode_meta, dict):
                    continue

                script_file = episode_meta.get("script_file")
                if not isinstance(script_file, str) or not script_file.strip():
                    continue

                resolved_path = self._validate_local_reference(
                    project_dir,
                    script_file,
                    errors,
                    f"episodes[{index}].script_file",
                    default_dir="scripts",
                    # Ledger script_file is forward-looking, filled on script generation;
                    # splitting before scripts is expected, while escaping paths still error. Ignore ledger_status:
                    # v2→v3 no longer backfills it, so upgraded entries may never
                    # have it; valid shapes (positive-integer episode IDs) suffice.
                    missing_ok=parse_positive_episode_num(episode_meta.get("episode")) is not None,
                )
                if not resolved_path:
                    continue

                episode = load_json_or_none(project_dir / resolved_path)
                if episode is None:
                    errors.append(_m("val_cannot_load_script", path=project_dir / resolved_path))
                    continue

                episode_errors: list[ValidationMessage] = []
                episode_warnings: list[ValidationMessage] = []
                self._validate_episode_payload(
                    project_dir,
                    project,
                    episode,
                    episode_errors,
                    episode_warnings,
                )
                errors.extend(episode_errors)
                warnings.extend(episode_warnings)

        if project_dir.exists():
            for child in sorted(project_dir.iterdir(), key=lambda item: item.name):
                if self._is_hidden_path(Path(child.name)) or is_versioned_backup_name(child.name):
                    continue
                if child.name not in self.ALLOWED_ROOT_ENTRIES:
                    warnings.append(_m("val_unrecognized_entry", name=child.name))

        return ValidationResult(valid=len(errors) == 0, error_messages=errors, warning_messages=warnings)


def validate_project(
    project_name: str,
    projects_dir: str | None = None,
) -> ValidationResult:
    """Validate project.json."""
    validator = DataValidator(projects_dir)
    return validator.validate_project(project_name)


def validate_episode(
    project_name: str,
    episode_file: str,
    projects_dir: str | None = None,
) -> ValidationResult:
    """Validate episode JSON."""
    validator = DataValidator(projects_dir)
    return validator.validate_episode(project_name, episode_file)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python data_validator.py <project_name> [episode_file]")
        print("  验证 project.json: python data_validator.py my_project")
        print("  验证 episode JSON: python data_validator.py my_project episode_1.json")
        sys.exit(1)

    project_name = sys.argv[1]

    if len(sys.argv) >= 3:
        episode_file = sys.argv[2]
        result = validate_episode(project_name, episode_file)
        print(f"验证 {project_name}/scripts/{episode_file}:")
    else:
        result = validate_project(project_name)
        print(f"验证 {project_name}/project.json:")

    print(result)
    sys.exit(0 if result.valid else 1)
