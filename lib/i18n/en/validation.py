"""Validation and archive diagnostic messages (English)."""

MESSAGES = {
    # ---- passthrough ----
    "val_literal": "{text}",
    # ---- generic field shape ----
    "val_missing_field": "Missing required field: {field}",
    "val_missing_field_at": "{prefix}: missing required field {field}",
    "val_field_type_string": "Field type error: {field} must be a string",
    "val_field_type_bool": "Field type error: {field} must be a boolean",
    "val_field_type_number": "Field type error: {field} must be a number",
    "val_speech_rate_out_of_range": "{field} value {value} is out of range; it must be between {min} and {max}",
    "val_field_must_be_string": "{field} must be a string",
    "val_field_must_be_string_typed": "{field} must be a string, got {actual}",
    "val_field_must_be_array": "{field} must be an array",
    "val_field_must_be_nonempty_array": "{field} must be a non-empty array",
    "val_field_must_be_nonempty_string": "{field} must be a non-empty string",
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
    "val_ad_no_grid_storyboard": "Ad/short-film projects do not support multi-grid storyboards (grid_storyboard)",
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
    "val_asset_name_duplicate": (
        "Duplicate project asset name: {duplicate_type} '{duplicate_name}' conflicts with "
        "{first_type} '{first_name}' after strip + Unicode NFC normalization"
    ),
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
        "{prefix}: invalid duration_seconds '{value}', reference_video mode requires an integer "
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
    # ---- skeleton / route mismatch ----
    "val_skeleton_noun_segments": "segments",
    "val_skeleton_noun_scenes": "scenes",
    "val_skeleton_noun_shots": "shots",
    "val_skeleton_noun_video_units": "video units",
    "val_route_reference_video": "reference-to-video (reference_video)",
    "val_route_storyboard": "storyboard-to-video (storyboard)",
    "val_skeleton_mismatch_reference_known": (
        "Script skeleton does not match the project generation mode: the mode is {route}, which requires the "
        "{expected} ({expected_noun}) skeleton, but this script uses {actual} ({actual_noun}). "
        "Call generate_step1 to re-split this episode, then regenerate the script. "
        "The script can still be viewed, edited and exported."
    ),
    "val_skeleton_mismatch_reference_none": (
        "Script skeleton does not match the project generation mode: the mode is {route}, which requires the "
        "{expected} ({expected_noun}) skeleton, but this script has no skeleton array at all. "
        "Call generate_step1 to re-split this episode, then regenerate the script. "
        "The script can still be viewed, edited and exported."
    ),
    "val_skeleton_mismatch_storyboard_known": (
        "Script skeleton does not match the project generation mode: the mode is {route}, which requires the "
        "{expected} ({expected_noun}) skeleton, but this script uses {actual} ({actual_noun}). "
        "Re-run episode splitting (step1) to re-split this episode, then regenerate the script. "
        "The script can still be viewed, edited and exported."
    ),
    "val_skeleton_mismatch_storyboard_none": (
        "Script skeleton does not match the project generation mode: the mode is {route}, which requires the "
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
    "arch_unit_unresolved_mentions": (
        "video_units[{index}]: the body mentions asset names missing from project.json: {names}; "
        "they will not produce reference images"
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
    "arch_artifact_activation_failed": "The imported project's artifact state is inconsistent",
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
    # ---- Custom endpoint definition validation ----
    "val_ce_missing_field": "Missing required field: {field}",
    "val_ce_unknown_field": "Unknown field: {field}",
    "val_ce_removed_field": "Field removed from the format: {field} - {reason}",
    "val_ce_invalid_type": "Wrong type; expected {expected}",
    "val_ce_invalid_enum_value": "Value is not allowed here; allowed: {allowed}",
    "val_ce_invalid_value": "Value does not match the format: {detail}",
    "val_ce_schema_violation": "Does not match the definition format: {detail}",
    "val_ce_removed_reason_request_query": (
        "static and dynamic query parameters belong in the url template, credential query in auth.query"
    ),
    "val_ce_removed_reason_status_codes": (
        "HTTP status handling is a runtime policy: 2xx succeeds, 429 and 5xx retry, everything else fails"
    ),
    "val_ce_removed_reason_polling_policy": "polling interval and timeout are runtime policy, not part of a definition",
    "val_ce_removed_reason_extract_source": "extraction always starts at the response body; HTTP status is not a path",
    "val_ce_removed_reason_extract_usage_keys": "usage now lives under poll.extract.usage",
    "val_ce_removed_reason_mime_types": "asset formats are not allow-listed; the provider rejects what it cannot take",
    "val_ce_removed_reason_media_type": "video is the only media type in this release",
    "val_ce_malformed_placeholder": (
        "{fragment} is not a valid placeholder: only bare variables are supported "
        "(such as prompt or inputs.first_frame) — no filters, indexes or expressions, "
        "and every opening brace must be closed"
    ),
    "val_ce_undeclared_variable": "Placeholder {name} references a variable that is not declared",
    "val_ce_api_key_outside_auth": (
        "api_key may only appear in the auth section: credentials stay out of the body and URL, "
        "and out of definitions you share"
    ),
    "val_ce_auth_without_api_key": (
        "The auth section is not empty but never references api_key: leave it empty for a credential-free API, "
        "otherwise make it write the credential"
    ),
    "val_ce_auth_header_conflict": (
        "{header} collides with auth.headers (case-insensitive): only the auth section may write credential headers"
    ),
    "val_ce_header_name_duplicate": (
        "{header} differs from {first} in the same map only by case: HTTP header names are case-insensitive, "
        "so both would be sent"
    ),
    "val_ce_auth_query_conflict": (
        "The URL already carries the query parameter {param} declared in auth.query: "
        "only the auth section may write credential query parameters"
    ),
    "val_ce_task_id_out_of_scope": "task_id is only available in the poll and result sections",
    "val_ce_result_id_out_of_scope": "result_id is only available in the result section",
    "val_ce_result_id_without_extract": "result_id is referenced but poll.extract does not declare result_id",
    "val_ce_input_out_of_scope": (
        "Asset {name} may only be referenced from submit: poll and result requests carry no assets"
    ),
    "val_ce_list_input_requires_each": "{name} is a list asset; expand it with $each instead of interpolating it",
    "val_ce_each_in_not_list_input": "$each.in points at {name}, which is not a declared list asset",
    "val_ce_each_shape_invalid": (
        "$each takes either item, to spread array elements, or both key and value, to spread object entries; "
        "the two forms cannot be mixed"
    ),
    "val_ce_each_position_mismatch": (
        "The $each form does not match its position: use item in an array position to spread elements, "
        "and key with value in an object position to spread entries"
    ),
    "val_ce_each_alias_reserved": (
        "{name} is a reserved variable inside the loop body and cannot be used as the $each element alias"
    ),
    "val_ce_when_unknown_input": "$when points at {name}, which is not a declared asset",
    "val_ce_input_not_referenced": (
        "The asset is declared but never referenced from submit: it is never sent, and cannot back a capability"
    ),
    "val_ce_enum_map_variable_not_allowed": "{variable} cannot be mapped; mappable variables: {allowed}",
    "val_ce_status_map_target_invalid": "Status {target} is outside {allowed}; map expiry semantics to failed",
    "val_ce_capability_declared_without_input": (
        "{capability} is declared but submit references no {source} asset, so the capability would lie"
    ),
    "val_ce_capability_input_without_declaration": (
        "submit references a {source} asset without declaring {capability}, "
        "so the asset is sent while the UI hides the capability"
    ),
    "val_ce_capability_incoherent": "Capability {capability} conflicts with its group; required: {requirement}",
    "val_ce_jsonpath_not_a_string": "An extraction path must be a string: {path_expression}",
    "val_ce_jsonpath_surrounding_whitespace": "An extraction path may not be padded with whitespace: {path_expression}",
    "val_ce_jsonpath_missing_root": "An extraction path must start with $: {path_expression}",
    "val_ce_jsonpath_recursive_descent": (
        "Recursive descent is not allowed in extraction paths (at character {position}): {path_expression}"
    ),
    "val_ce_jsonpath_union": (
        "Union selectors are not allowed in extraction paths (at character {position}): {path_expression}"
    ),
    "val_ce_jsonpath_slice_step": (
        "Slice steps are not allowed in extraction paths (at character {position}): {path_expression}"
    ),
    "val_ce_jsonpath_function_extension": (
        "Function extensions are not allowed in extraction paths (at character {position}): {path_expression}"
    ),
    "val_ce_jsonpath_filter_root_reference": (
        "A filter may not reference the root node (at character {position}): {path_expression}"
    ),
    "val_ce_jsonpath_filter_non_singular": (
        "A filter may only use singular queries (at character {position}): {path_expression}"
    ),
    "val_ce_jsonpath_regex_operator": (
        "The regex match operator is not allowed in extraction paths (at character {position}): {path_expression}"
    ),
    "val_ce_jsonpath_syntax": "Extraction path syntax error (at character {position}): {path_expression}",
    "val_ce_template_render_failed": "Could not render the request template: {detail}",
    "val_ce_poll_without_task_id": "The polling request never references task_id; confirm that this is intended",
    "val_ce_jsonpath_wildcard_order": (
        "{path_expression} uses a wildcard: an object wildcard takes the first member only, "
        "and key order may differ between the preview and the backend"
    ),
}
