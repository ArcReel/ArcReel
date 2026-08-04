"""Validation and archive diagnostic messages (English)."""

MESSAGES = {
    # ---- passthrough ----
    "val_literal": "{text}",
    # ---- generic field shape ----
    "val_missing_field": "Missing required field: {field}",
    "val_missing_field_at": "{prefix}: missing required field {field}",
    "val_field_type_string": "Field type error: {field} must be a string",
    "val_field_type_bool": "Field type error: {field} must be a boolean",
    "val_field_must_be_string": "{field} must be a string",
    "val_field_must_be_string_typed": "{field} must be a string, got {actual}",
    "val_field_must_be_array": "{field} must be an array",
    "val_field_must_be_nonempty_array": "{field} must be a non-empty array",
    "val_field_must_be_object": "{field} must be an object",
    "val_field_invalid": "{field} is invalid: {detail}",
    "val_ledger_source_file_not_relative": "source_file must be a project-relative POSIX path",
    "val_ledger_source_file_escapes": "source_file must not be absolute or contain ..",
    "val_ledger_start_after_end": "start must not be greater than end",
    "val_field_bad_timestamp": "{field} is not a valid ISO8601 timestamp: {value}",
    "val_array_empty": "{field} array is empty",
    "val_item_must_be_object": "{prefix}: must be an object",
    "val_item_format_object": "{prefix}: malformed data, expected an object",
    # ---- path references ----
    "val_path_empty": "{field}: path must not be empty",
    "val_path_traversal": "{field}: reference path escapes the project: {path}",
    "val_path_outside_dir": "{field}: reference path must live under {dir}/: {path}",
    "val_path_missing": "{field}: referenced file does not exist: {path}",
    "val_path_must_be_relative": "{field} must be a project-relative path: {path}",
    # ---- project-level fields ----
    "val_content_mode_invalid": "Invalid content_mode: '{value}', must be one of {allowed}",
    "val_source_kind_invalid": "Invalid source_kind: '{value}', must be one of {allowed}",
    "val_generation_mode_invalid": "Invalid generation_mode: '{value}', must be one of {allowed}",
    "val_deprecated_clues": (
        "project.json contains the deprecated field clues; wait for automatic migration or restart the service"
    ),
    "val_deprecated_field_removable": "{field} is deprecated (now computed on read) and can be safely removed",
    "val_cannot_load_project_json": "Cannot load project.json: {path}",
    "val_cannot_load_script": "Cannot load script file: {path}",
    "val_unrecognized_entry": "Unrecognized extra file/directory found: {name}",
    "val_novel_must_be_object": "The novel field must be an object",
    # ---- episode entries and ledger ----
    "val_ledger_status_type": "{prefix}: ledger_status must be a string, got: {value}",
    "val_episode_missing_num_at": "{prefix}: missing required field episode (integer)",
    "val_episode_missing_title_at": "{prefix}: missing required field title (string, may be empty)",
    "val_episode_missing_num": "Missing required field: episode (integer)",
    # ---- ad / short-film projects ----
    "val_ad_only_field": "{field} is only available for ad/short-film projects (content_mode=ad)",
    "val_ad_missing_target_duration": (
        "Missing required field: target_duration (target total duration in seconds for ad/short-film projects)"
    ),
    "val_ad_target_duration_invalid": "Invalid target_duration: {value}, must be a positive integer number of seconds",
    "val_ad_no_default_duration": (
        "Ad/short-film projects do not carry default_duration "
        "(shot durations are budgeted per shot against target_duration)"
    ),
    "val_ad_no_grid_storyboard": "Ad/short-film projects do not support grid storyboards (grid_storyboard)",
    "val_ad_episodes_single": "Ad/short-film projects must always have exactly one episode entry (episode 1)",
    "val_ad_shots_missing": "The ad script is missing the shots array, or it is empty",
    "val_ad_duration_drift": (
        "Script total duration {total}s deviates from target_duration {target}s by {delta:.0%}, "
        "beyond the {threshold:.0%} observation threshold (informational only, saving is not blocked)"
    ),
    # ---- asset catalogs ----
    "val_asset_format_object": "{asset_type} '{name}' has malformed data, expected an object",
    "val_asset_missing_description": (
        "{asset_type} '{name}' is missing the required field: description (must be a non-empty string)"
    ),
    "val_asset_field_must_be_string": "{asset_type} '{name}'.{field} must be a string, got {actual}",
    "val_asset_field_bad_timestamp": "{asset_type} '{name}'.{field} is not a valid ISO8601 timestamp: {value}",
    "val_asset_field_must_be_string_list": "{asset_type} '{name}'.{field} must be a list of strings, got {actual}",
    "val_asset_field_item_must_be_string": "{asset_type} '{name}'.{field}[{index}] must be a string, got {actual}",
    # ---- item-level references ----
    "val_refs_unregistered": "{prefix}: {field} references {asset_type} entries missing from project.json: {names}",
    "val_missing_defaults_empty_array": "{prefix}: {field} is missing, defaulting to an empty array",
    # ---- generic item checks ----
    "val_id_format": "{prefix}: invalid {field} format '{value}', expected E{{n}}S{{nn}}",
    "val_missing_duration_default": "{prefix}: duration_seconds is missing, defaulting to {default}",
    "val_duration_invalid": "{prefix}: invalid duration_seconds '{value}', must be a positive integer",
    # ---- drama utterances ----
    "val_utterance_must_be_object": "{prefix} must be an object",
    "val_utterance_kind_invalid": "{prefix} kind must be dialogue or voiceover",
    "val_utterance_text_invalid": "{prefix} text must be a non-empty string",
    "val_utterance_speaker_type": "{prefix} speaker must be a string or null",
    "val_utterance_dialogue_speaker": "{prefix} dialogue must carry a non-empty speaker",
    "val_utterance_voiceover_speaker": "{prefix} voiceover must not carry a speaker",
    "val_scene_speech_overflow": (
        "{prefix}: estimated speech runs {spoken:.1f}s, exceeding the {duration}s scene duration by more than "
        "{tolerance:.0%} (tolerance ceiling {budget:.1f}s); long dialogue may not fit or may sound rushed "
        "(informational only, saving is not blocked)"
    ),
    # ---- ad shots ----
    "val_shot_duration_missing_zero": "{prefix}: duration_seconds is missing, counted as 0 toward the total",
    "val_shot_duration_out_of_range": (
        "{prefix}: invalid duration_seconds '{value}', the reference_video route requires an integer "
        "between {low} and {high}"
    ),
    "val_shot_missing_voiceover_text": (
        "{prefix}: missing required field voiceover_text (voiceover copy, may be an empty string)"
    ),
    # ---- reference-video units ----
    "val_unit_id_missing": "{prefix}: unit_id is missing",
    "val_unit_id_missing_required": "{prefix}: missing required field unit_id",
    "val_unit_id_duplicate": "{prefix}: duplicate unit_id '{value}'",
    "val_video_units_missing": "The reference_video script is missing the video_units array, or it is empty",
    "val_unit_duration_range": "{prefix}: duration_seconds must be an integer between {low} and {high}",
    "val_reference_entry_must_be_object": "{prefix}: each reference entry must be an object",
    "val_reference_type_invalid": "{prefix}: invalid reference.type: {value}",
    "val_reference_name_invalid": "{prefix}: reference.name must be a non-empty string: {value}",
    "val_reference_not_in_bucket": (
        "{prefix}: referenced {asset_type} '{name}' is not in the matching project.json bucket"
    ),
    "val_ref_type_invalid": "{prefix}: invalid type: {value}",
    "val_ref_name_invalid": "{prefix}: name must be a non-empty string: {value}",
    "val_ref_unregistered_regroup": (
        "{prefix}: referenced {asset_type} “{name}” is not registered; regenerate the grouping"
    ),
    "val_reference_units_dangling_shots": ("{prefix}: referenced shots do not exist ({ids}); regenerate the grouping"),
    # ---- skeleton / route mismatch ----
    "val_skeleton_noun_segments": "segments",
    "val_skeleton_noun_scenes": "scenes",
    "val_skeleton_noun_shots": "shots",
    "val_skeleton_noun_video_units": "video units",
    "val_route_reference_video": "reference-to-video (reference_video)",
    "val_route_storyboard": "storyboard-to-video (storyboard)",
    "val_skeleton_mismatch_reference_known": (
        "Script skeleton does not match the project generation route: the route is {route}, which requires the "
        "{expected} ({expected_noun}) skeleton, but this script uses {actual} ({actual_noun}). "
        "Re-run split-reference-video-units to re-split this episode, then regenerate the script. "
        "The script can still be viewed, edited and exported."
    ),
    "val_skeleton_mismatch_reference_none": (
        "Script skeleton does not match the project generation route: the route is {route}, which requires the "
        "{expected} ({expected_noun}) skeleton, but this script has no skeleton array at all. "
        "Re-run split-reference-video-units to re-split this episode, then regenerate the script. "
        "The script can still be viewed, edited and exported."
    ),
    "val_skeleton_mismatch_storyboard_known": (
        "Script skeleton does not match the project generation route: the route is {route}, which requires the "
        "{expected} ({expected_noun}) skeleton, but this script uses {actual} ({actual_noun}). "
        "Re-run episode splitting (step1) to re-split this episode, then regenerate the script. "
        "The script can still be viewed, edited and exported."
    ),
    "val_skeleton_mismatch_storyboard_none": (
        "Script skeleton does not match the project generation route: the route is {route}, which requires the "
        "{expected} ({expected_noun}) skeleton, but this script has no skeleton array at all. "
        "Re-run episode splitting (step1) to re-split this episode, then regenerate the script. "
        "The script can still be viewed, edited and exported."
    ),
    # ---- reference-video duration consolidation migration ----
    "val_unit_duration_clamped": (
        "unit {unit_id} duration {target}s is outside the sensible {low}-{high}s range; clamped to {clamped}s"
    ),
    "val_unit_duration_slotted": (
        "unit {unit_id} duration {duration}s is not one of the model's duration options ({durations}); "
        "snapped to {slot}s"
    ),
    # ---- archive repair and import/export diagnostics ----
    "arch_source_encoding_unconverted": (
        "Source file encoding could not be detected and was not converted to UTF-8: source/{name} "
        "(episode planning cannot read this file)"
    ),
    "arch_non_standard_entry_excluded": "Non-standard top-level directory/file '{entry}' was excluded from the export",
    "arch_invalid_project_json": "Cannot parse {file}: {path}",
    "arch_script_file_repaired": "{location}: automatically repaired to {path}",
    "arch_missing_script_file_pending": "{location}: script not generated yet: {path}",
    "arch_missing_script_file": "{location}: referenced file does not exist: {path}",
    "arch_invalid_script_json": "Cannot parse script file: {path}",
    "arch_deprecated_source_file_removed": "The novel.source_file field is deprecated and was removed",
    "arch_deprecated_field_removed": "{field} is deprecated (now computed on read) and was removed",
    "arch_deprecated_clue_field_removed": (
        "{items_key}[{index}]: deprecated field {field} was removed (use scenes/props instead)"
    ),
    "arch_missing_field_filled": "{items_key}[{index}]: filled in the missing field {field}",
    "arch_missing_asset_definition": (
        "{items_key}[{index}]: {field} references {asset_type} entries missing from project.json: {names}"
    ),
    "arch_unit_missing_asset_definition": (
        "video_units[{index}]: references point to {asset_type} entries missing from project.json: {names}"
    ),
    "arch_generated_assets_defaults": "{label}[{index}].generated_assets: filled in default fields {fields}",
    "arch_missing_generated_assets": "{label}[{index}]: filled in the missing field generated_assets",
    "arch_invalid_generated_assets": (
        "{label}[{index}]: generated_assets is malformed ({actual}) and was reset to the default structure"
    ),
    "arch_placeholder_character_added": "Automatically added the missing character definition: {name}",
    "arch_canonical_path_normalized": "{location}: normalized to {path}",
    "arch_current_asset_materialized": "{location}: restored the current file {target} from {source}",
    "arch_current_asset_restored_from_version": "{location}: restored the current file {target} from {source}",
    # ---- archive import errors ----
    "arch_invalid_conflict_policy": "Invalid conflict policy",
    "arch_conflict_policy_unsupported": "conflict_policy only supports prompt, rename or overwrite; got: {value}",
    "arch_import_validation_failed": "Import package validation failed",
    "arch_not_a_zip": "The uploaded file is not a valid ZIP archive",
    "arch_zip_encrypted_entry": "The ZIP contains an encrypted entry and cannot be imported: {name}",
    "arch_zip_absolute_path_entry": "The ZIP contains an absolute-path entry: {name}",
    "arch_zip_traversal_entry": "The ZIP contains a path-traversal entry: {name}",
    "arch_zip_symlink_entry": "The ZIP contains a symlink entry: {name}",
    "arch_zip_unparsable_member": "Cannot parse {label}: {path}",
    "arch_multiple_manifests": (
        "The ZIP contains multiple arcreel-export.json files; the project root cannot be determined"
    ),
    "arch_manifest_missing_project_json": "The official export package is missing project.json",
    "arch_no_project_json": "No project.json found in the ZIP",
    "arch_multiple_project_json": "The ZIP contains multiple project.json files; the project root cannot be determined",
    "arch_extract_path_traversal": "Extraction path escapes the target directory: {path}",
    "arch_conflict_detected": "Project ID conflict detected",
    "arch_project_name_conflict": (
        "Project ID '{name}' already exists. Choose to overwrite the existing project or import under a new name."
    ),
}
