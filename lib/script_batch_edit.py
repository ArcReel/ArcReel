"""Transactional batch editing for complete episode-script aggregates.

The module is the single admission and commit seam used by transport adapters.  It applies
an ordered command to an in-memory candidate, validates the whole aggregate, checks
optimistic concurrency under the episode lock, then commits the script, project index, and
applicable Artifact Manifest entry as one recoverable operation.
"""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.artifact_manifest import (
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestAdapter,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
)
from lib.artifact_provenance import build_episode_script_basis
from lib.data_validator import DataValidator
from lib.episode_paths import episode_script_filename
from lib.json_io import load_json
from lib.project_manager import EpisodeScriptReboundError, ProjectManager
from lib.reference_video import rederive_unit_references
from lib.script_editor import ScriptEditError, patch_field, resolve_items
from lib.script_review import content_fingerprint_of_data, step1_path
from lib.script_structure_validator import validate_script_structure
from lib.speech_composition import SpeechAdmission, admit_script_unit, refresh_video_unit_replan_state
from lib.validation_messages import ValidationMessage

_REVISION_PATTERN = r"^sha256-v1:[0-9a-f]{64}$"

logger = logging.getLogger(__name__)


class UpdateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["update"]
    id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(min_length=1)


class InsertAfterOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["insert_after"]
    after_id: str | None
    item: dict[str, Any]


class MoveAfterOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["move_after"]
    id: str = Field(min_length=1)
    after_id: str | None


class RemoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["remove"]
    id: str = Field(min_length=1)


ScriptBatchOperation = Annotated[
    UpdateOperation | InsertAfterOperation | MoveAfterOperation | RemoveOperation,
    Field(discriminator="op"),
]


class ScriptBatchEditCommand(BaseModel):
    """Transport-neutral ordered edit command.

    Exactly one target coordinate is accepted: ``episode`` resolves the project ledger
    binding, while ``script`` addresses an already known script filename. Episode-scoped
    compatibility adapters may also pin the binding they originally loaded so a same-content
    rebind cannot evade the revision check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str | None = Field(default=None, min_length=1)
    episode: int | None = Field(default=None, ge=1, strict=True)
    expected_script_file: str | None = Field(default=None, min_length=1)
    expected_revision: str = Field(pattern=_REVISION_PATTERN)
    operations: list[ScriptBatchOperation] = Field(min_length=1)

    @field_validator("script", "expected_script_file")
    @classmethod
    def _safe_script_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.removeprefix("scripts/")
        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
            or not normalized.endswith(".json")
        ):
            raise ValueError("script must be a JSON filename, optionally prefixed by scripts/")
        return value

    @model_validator(mode="after")
    def _exactly_one_target(self) -> ScriptBatchEditCommand:
        if (self.script is None) == (self.episode is None):
            raise ValueError("exactly one of script or episode is required")
        if self.expected_script_file is not None and self.episode is None:
            raise ValueError("expected_script_file requires an episode target")
        return self


class ScriptBatchEditLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: tuple[str | int, ...]
    line: int | None = None


class ScriptBatchEditProblem(BaseModel):
    """Stable machine-facing failure detail shared by REST and MCP adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    operation_index: int | None
    unit_id: str | None
    locations: tuple[ScriptBatchEditLocation, ...] = ()
    reason: str
    next_action: str


class ScriptBatchEditResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    script: str
    episode: int | None
    before_revision: str
    revision: str
    affected_ids: tuple[str, ...] = ()
    problems: tuple[ScriptBatchEditProblem, ...] = ()


class _ManifestAdapterFactory(Protocol):
    def __call__(self, project_dir: Path) -> ArtifactManifestAdapter:
        raise NotImplementedError


class _AbortEdit(Exception):
    def __init__(self, result: ScriptBatchEditResult) -> None:
        super().__init__(result.problems[0].code if result.problems else "script_batch_edit_rejected")
        self.result = result


class _OperationApplyError(ScriptEditError):
    def __init__(self, message: str, *, location: tuple[str | int, ...]) -> None:
        super().__init__(message)
        self.location = location


def script_revision(script: object) -> str:
    """Return the canonical JSON optimistic-concurrency token for a script aggregate."""

    return f"sha256-v1:{content_fingerprint_of_data(script)}"


class ScriptBatchEditor:
    """Preflight and commit ordered script operations through one deep public interface."""

    def __init__(
        self,
        project_manager: ProjectManager,
        *,
        manifest_adapter_factory: _ManifestAdapterFactory = ProjectArtifactManifestAdapter,
    ) -> None:
        self._pm = project_manager
        self._manifest_adapter_factory = manifest_adapter_factory

    def execute(self, project_name: str, command: ScriptBatchEditCommand) -> ScriptBatchEditResult:
        expected_script_file = (
            self._pm.normalize_script_filename(command.expected_script_file)
            if command.expected_script_file is not None
            else None
        )
        resolved_script = (
            self._pm.normalize_script_filename(command.script)
            if command.script is not None
            else expected_script_file or episode_script_filename(command.episode or 0)
        )
        episode_number: int | None = command.episode
        before_revision = command.expected_revision
        affected_ids: list[str] = []
        commit_manifest: Callable[[], None] | None = None
        resolved_project: dict[str, Any] = {}

        def resolve_script_file(project: dict[str, Any]) -> str:
            nonlocal resolved_script
            resolved_project["value"] = project
            if command.script is not None:
                resolved_script = self._pm.normalize_script_filename(command.script)
                return resolved_script
            episodes = project.get("episodes")
            if not isinstance(episodes, list):
                raise FileNotFoundError(f"episode {command.episode} has no script binding")
            entry = next(
                (item for item in episodes if isinstance(item, Mapping) and item.get("episode") == command.episode),
                None,
            )
            if entry is None or not isinstance(entry.get("script_file"), str):
                raise FileNotFoundError(f"episode {command.episode} has no script binding")
            bound_script = self._pm.normalize_script_filename(entry["script_file"])
            if expected_script_file is not None and bound_script != expected_script_file:
                raise EpisodeScriptReboundError(
                    f"episode script binding changed: {expected_script_file} -> {bound_script}"
                )
            resolved_script = bound_script
            return resolved_script

        def finalize_manifest(_script_path: Path) -> None:
            if commit_manifest is not None:
                commit_manifest()

        try:
            with self._pm.locked_episode_script(
                project_name,
                resolve_script_file,
                on_commit=finalize_manifest,
            ) as candidate:
                before_revision = script_revision(candidate)
                episode_value = candidate.get("episode")
                if command.episode is None and isinstance(episode_value, int) and not isinstance(episode_value, bool):
                    episode_number = episode_value
                if command.expected_revision != before_revision:
                    raise _AbortEdit(
                        self._failure(
                            script=resolved_script,
                            episode=episode_number,
                            revision=before_revision,
                            code="revision_conflict",
                            reason="revision_mismatch",
                            next_action="refresh_script",
                        )
                    )

                expected_episode = command.episode or _filename_episode(resolved_script)
                if expected_episode is not None and episode_value != expected_episode:
                    raise _AbortEdit(
                        self._failure(
                            script=resolved_script,
                            episode=episode_number,
                            revision=before_revision,
                            code="schema_invalid",
                            reason="episode_binding_mismatch",
                            next_action="refresh_script",
                            locations=(ScriptBatchEditLocation(path=("episode",)),),
                        )
                    )

                original = copy.deepcopy(candidate)
                project = resolved_project["value"]
                try:
                    before_admissions = _admissions(original)
                except ScriptEditError as exc:
                    kind = exc.params.get("kind")
                    locations = (ScriptBatchEditLocation(path=(kind,)),) if isinstance(kind, str) else ()
                    raise _AbortEdit(
                        self._failure(
                            script=resolved_script,
                            episode=episode_number,
                            revision=before_revision,
                            code="schema_invalid",
                            reason="stored_schema_invalid",
                            next_action="repair_script",
                            locations=locations,
                        )
                    ) from exc
                last_touch: dict[str, int] = {}
                speech_change: dict[str, int] = {}
                removed_items: dict[str, dict[str, Any]] = {}

                for index, operation in enumerate(command.operations):
                    try:
                        item_id, before_admission, after_admission = _apply_operation(
                            candidate,
                            operation,
                            project,
                            removed_items,
                        )
                    except ScriptEditError as exc:
                        raise _AbortEdit(
                            self._failure(
                                script=resolved_script,
                                episode=episode_number,
                                revision=before_revision,
                                code="operation_invalid",
                                reason="operation_invalid",
                                next_action="fix_operation",
                                operation_index=index,
                                unit_id=_operation_id(operation),
                                locations=(_operation_error_location(index, operation, exc),),
                            )
                        ) from exc
                    if item_id is not None:
                        last_touch[item_id] = index
                        if item_id not in affected_ids:
                            affected_ids.append(item_id)
                        if before_admission != after_admission:
                            speech_change[item_id] = index

                speech_problems = _new_speech_problems(
                    candidate,
                    before_admissions=before_admissions,
                    speech_change=speech_change,
                    last_touch=last_touch,
                )
                if speech_problems:
                    raise _AbortEdit(
                        ScriptBatchEditResult(
                            success=False,
                            script=resolved_script,
                            episode=episode_number,
                            before_revision=before_revision,
                            revision=before_revision,
                            affected_ids=(),
                            problems=tuple(speech_problems),
                        )
                    )

                structure = validate_script_structure(copy.deepcopy(candidate))
                if not structure.valid:
                    message = structure.error_messages[0]
                    location = _validation_location(message)
                    item_id = _unit_id_at_location(candidate, location.path)
                    operation_index = _responsible_operation(
                        item_id,
                        location.path,
                        command.operations,
                    )
                    raise _AbortEdit(
                        self._failure(
                            script=resolved_script,
                            episode=episode_number,
                            revision=before_revision,
                            code="schema_invalid",
                            reason="candidate_schema_invalid",
                            next_action="fix_operation" if operation_index is not None else "repair_script",
                            operation_index=operation_index,
                            unit_id=item_id,
                            locations=(location,),
                        )
                    )

                project_dir = self._pm.get_project_path(project_name)
                reference_validation = DataValidator(self._pm.projects_root).validate_episode_payload(
                    project_dir,
                    project,
                    candidate,
                    validate_artifacts=False,
                    validate_route=False,
                )
                validation_errors = _candidate_validation_errors(candidate, reference_validation.error_messages)
                if validation_errors:
                    message = validation_errors[0]
                    location = _validation_location(message)
                    item_id = _unit_id_at_location(candidate, location.path)
                    operation_index = _responsible_operation(
                        item_id,
                        location.path,
                        command.operations,
                    )
                    raise _AbortEdit(
                        self._failure(
                            script=resolved_script,
                            episode=episode_number,
                            revision=before_revision,
                            code="references_invalid",
                            reason="candidate_references_invalid",
                            next_action="fix_operation" if operation_index is not None else "repair_script",
                            operation_index=operation_index,
                            unit_id=item_id,
                            locations=(location,),
                        )
                    )

                try:
                    commit_manifest = self._prepare_manifest_commit(
                        project_dir=project_dir,
                        project=project,
                        script=candidate,
                        script_file=resolved_script,
                    )
                except (ArtifactManifestError, OSError, UnicodeError, ValueError) as exc:
                    raise _AbortEdit(
                        self._failure(
                            script=resolved_script,
                            episode=episode_number,
                            revision=before_revision,
                            code="manifest_invalid",
                            reason="artifact_manifest_unavailable",
                            next_action="retry",
                        )
                    ) from exc
        except _AbortEdit as exc:
            return exc.result
        except FileNotFoundError:
            raise
        except EpisodeScriptReboundError:
            return self._failure(
                script=resolved_script,
                episode=episode_number,
                revision=before_revision,
                code="revision_conflict",
                reason="script_binding_changed",
                next_action="refresh_script",
            )
        except Exception:
            logger.exception("script batch edit commit failed")
            return self._failure(
                script=resolved_script,
                episode=episode_number,
                revision=before_revision,
                code="commit_failed",
                reason="durable_commit_failed",
                next_action="retry",
            )

        revision = script_revision(candidate)
        return ScriptBatchEditResult(
            success=True,
            script=resolved_script,
            episode=episode_number,
            before_revision=before_revision,
            revision=revision,
            affected_ids=tuple(affected_ids),
        )

    def _prepare_manifest_commit(
        self,
        *,
        project_dir: Path,
        project: dict[str, Any],
        script: dict[str, Any],
        script_file: str,
    ) -> Callable[[], None] | None:
        episode = script.get("episode")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
            return None
        direct_input_path = step1_path(project_dir, project, episode)
        if direct_input_path is None or not direct_input_path.is_file():
            return None
        step1_content = load_json(direct_input_path)
        basis = build_episode_script_basis(step1_content, project=project)
        adapter = self._manifest_adapter_factory(project_dir)
        key = ArtifactKey.episode_script(episode)
        previous_entry = adapter.get_entry(key)
        artifact_path = f"scripts/{self._pm.normalize_script_filename(script_file)}"
        observation = adapter.inspect_artifact(artifact_path)
        if observation.blocker is not None:
            raise ArtifactManifestError(observation.blocker.detail)
        manifest = ArtifactManifest(adapter)

        def commit() -> None:
            try:
                manifest.register(key, artifact_path=artifact_path, basis=basis)
            except BaseException:
                try:
                    if previous_entry is None:
                        adapter.delete_entry(key)
                    else:
                        adapter.put_entry(key, previous_entry)
                except BaseException as rollback_error:
                    raise RuntimeError(
                        "artifact manifest commit failed and rollback was incomplete"
                    ) from rollback_error
                raise

        return commit

    @staticmethod
    def _failure(
        *,
        script: str,
        episode: int | None,
        revision: str,
        code: str,
        reason: str,
        next_action: str,
        operation_index: int | None = None,
        unit_id: str | None = None,
        locations: tuple[ScriptBatchEditLocation, ...] = (),
    ) -> ScriptBatchEditResult:
        return ScriptBatchEditResult(
            success=False,
            script=script,
            episode=episode,
            before_revision=revision,
            revision=revision,
            affected_ids=(),
            problems=(
                ScriptBatchEditProblem(
                    code=code,
                    operation_index=operation_index,
                    unit_id=unit_id,
                    locations=locations,
                    reason=reason,
                    next_action=next_action,
                ),
            ),
        )


def _operation_id(operation: ScriptBatchOperation) -> str | None:
    if isinstance(operation, InsertAfterOperation):
        _items = operation.item
        for field in ("segment_id", "scene_id", "shot_id", "unit_id"):
            value = _items.get(field)
            if isinstance(value, str):
                return value
        return None
    return operation.id


def _filename_episode(script_file: str) -> int | None:
    match = re.search(r"episode[-_\s]*(\d+)", script_file, re.IGNORECASE)
    return int(match.group(1)) if match is not None else None


def _find_index(items: list[dict[str, Any]], id_field: str, item_id: str) -> int:
    for index, item in enumerate(items):
        if isinstance(item, dict) and str(item.get(id_field)) == item_id:
            return index
    raise ScriptEditError(f"item not found: {item_id}")


def _admission_for(script: dict[str, Any], item_id: str) -> SpeechAdmission | None:
    items, id_field, kind = resolve_items(script)
    for item in items:
        if isinstance(item, dict) and str(item.get(id_field)) == item_id:
            return admit_script_unit(kind, item)
    return None


def _rederive_video_unit_references(item: dict[str, Any], project: dict[str, Any]) -> None:
    """Derive only from a shot list; structured preflight reports malformed containers."""

    if isinstance(item.get("shots"), list):
        rederive_unit_references([item], project)


def _apply_operation(
    script: dict[str, Any],
    operation: ScriptBatchOperation,
    project: dict[str, Any],
    removed_items: dict[str, dict[str, Any]],
) -> tuple[str | None, SpeechAdmission | None, SpeechAdmission | None]:
    if isinstance(operation, UpdateOperation):
        item_id = operation.id
        before = _admission_for(script, item_id)
        items, id_field, kind = resolve_items(script)
        try:
            item_index = _find_index(items, id_field, item_id)
        except ScriptEditError as exc:
            raise _OperationApplyError(str(exc), location=("id",)) from exc
        item = items[item_index]
        before_content_admission = admit_script_unit(kind, item, ignore_marker=True)
        previous_shots = copy.deepcopy(item.get("shots"))
        previous_references = copy.deepcopy(item.get("references"))
        for field, value in operation.fields.items():
            try:
                patch_field(script, item_id, field, value)
            except ScriptEditError as exc:
                raise _OperationApplyError(
                    f"{field}: {exc}",
                    location=("fields", *_parse_path(field)),
                ) from exc
        roots = {field.split(".", 1)[0] for field in operation.fields}
        if kind == "video_units":
            body_changed = "shots" in roots and item.get("shots") != previous_shots
            if body_changed and "references" not in roots:
                _rederive_video_unit_references(item, project)
            references_changed = "references" in roots and item.get("references") != previous_references
            if roots & {"shots", "references", "duration_seconds"}:
                refresh_video_unit_replan_state(
                    item,
                    allow_clear=body_changed or references_changed or "duration_seconds" in roots,
                    content_changed=body_changed,
                )
        else:
            after_content_admission = admit_script_unit(kind, item, ignore_marker=True)
            if (
                after_content_admission.preparation != before_content_admission.preparation
                and after_content_admission.allowed
            ):
                item.pop("needs_replan", None)
        return item_id, before, _admission_for(script, item_id)

    if isinstance(operation, InsertAfterOperation):
        items, id_field, kind = resolve_items(script)
        item = copy.deepcopy(operation.item)
        item_id = item.get(id_field)
        if not isinstance(item_id, str) or not item_id:
            raise _OperationApplyError(
                f"inserted item must contain non-empty {id_field}",
                location=("item", id_field),
            )
        if any(isinstance(existing, dict) and str(existing.get(id_field)) == item_id for existing in items):
            raise _OperationApplyError(f"duplicate item id: {item_id}", location=("item", id_field))
        if operation.after_id is None:
            insert_at = 0
        else:
            try:
                insert_at = _find_index(items, id_field, operation.after_id) + 1
            except ScriptEditError as exc:
                raise _OperationApplyError(str(exc), location=("after_id",)) from exc
        removed = removed_items.pop(item_id, None)
        if removed is None:
            item["generated_assets"] = {}
            item.pop("end_frame_image", None)
        else:
            assets = removed.get("generated_assets")
            item["generated_assets"] = copy.deepcopy(assets) if isinstance(assets, dict) else {}
            if removed.get("end_frame_image") is None:
                item.pop("end_frame_image", None)
            else:
                item["end_frame_image"] = removed["end_frame_image"]
        if kind == "video_units":
            if "references" not in item:
                _rederive_video_unit_references(item, project)
            refresh_video_unit_replan_state(item, content_changed=True)
        items.insert(insert_at, item)
        return item_id, None, _admission_for(script, item_id)

    if isinstance(operation, MoveAfterOperation):
        if operation.after_id == operation.id:
            raise _OperationApplyError("move target cannot be its own anchor", location=("after_id",))
        items, id_field, _kind = resolve_items(script)
        try:
            source_index = _find_index(items, id_field, operation.id)
        except ScriptEditError as exc:
            raise _OperationApplyError(str(exc), location=("id",)) from exc
        before = _admission_for(script, operation.id)
        item = items.pop(source_index)
        if operation.after_id is None:
            insert_at = 0
        else:
            try:
                insert_at = _find_index(items, id_field, operation.after_id) + 1
            except ScriptEditError as exc:
                raise _OperationApplyError(str(exc), location=("after_id",)) from exc
        items.insert(insert_at, item)
        return operation.id, before, _admission_for(script, operation.id)

    items, id_field, _kind = resolve_items(script)
    try:
        index = _find_index(items, id_field, operation.id)
    except ScriptEditError as exc:
        raise _OperationApplyError(str(exc), location=("id",)) from exc
    before = _admission_for(script, operation.id)
    removed_items[operation.id] = copy.deepcopy(items.pop(index))
    return operation.id, before, None


def _admissions(script: dict[str, Any]) -> dict[str, SpeechAdmission]:
    items, id_field, kind = resolve_items(script)
    return {
        str(item.get(id_field)): admit_script_unit(kind, item)
        for item in items
        if isinstance(item, dict) and isinstance(item.get(id_field), str)
    }


def _candidate_validation_errors(
    candidate: dict[str, Any],
    errors: list[ValidationMessage],
) -> list[ValidationMessage]:
    """Drop archive-only nonempty rules after the Pydantic aggregate schema passed.

    Empty scripts are valid editable drafts in every script model. DataValidator also
    serves export/readiness checks and intentionally rejects those drafts; this command
    uses it for project-reference validation, not generation-route admission or to turn
    remove-last into an impossible operation.
    """

    items, _id_field, _kind = resolve_items(candidate)
    if items:
        return errors
    archive_nonempty = {"val_array_empty", "val_ad_shots_missing", "val_video_units_missing"}
    return [message for message in errors if message.key not in archive_nonempty]


def _new_speech_problems(
    candidate: dict[str, Any],
    *,
    before_admissions: dict[str, SpeechAdmission],
    speech_change: dict[str, int],
    last_touch: dict[str, int],
) -> list[ScriptBatchEditProblem]:
    problems: list[ScriptBatchEditProblem] = []
    for item_id, admission in _admissions(candidate).items():
        if admission.allowed:
            continue
        previous = before_admissions.get(item_id)
        if previous is not None and previous.preparation == admission.preparation:
            continue
        operation_index = speech_change.get(item_id, last_touch.get(item_id))
        actionable = admission.problems
        if any(problem.code.value != "needs_replan" for problem in actionable):
            actionable = tuple(problem for problem in actionable if problem.code.value != "needs_replan")
        for problem in actionable:
            problems.append(
                ScriptBatchEditProblem(
                    code=problem.code.value,
                    operation_index=operation_index,
                    unit_id=problem.unit_id,
                    locations=tuple(
                        ScriptBatchEditLocation(path=location.path, line=location.line)
                        for location in problem.locations
                    ),
                    reason=problem.reason.value,
                    next_action=problem.action.value,
                )
            )
    return problems


def _operation_error_location(
    index: int,
    operation: ScriptBatchOperation,
    error: ScriptEditError,
) -> ScriptBatchEditLocation:
    if isinstance(error, _OperationApplyError):
        return ScriptBatchEditLocation(path=("operations", index, *error.location))
    if isinstance(operation, UpdateOperation):
        field = next(iter(operation.fields), "fields")
        return ScriptBatchEditLocation(path=("operations", index, "fields", *field.split(".")))
    if isinstance(operation, InsertAfterOperation):
        return ScriptBatchEditLocation(path=("operations", index, "item"))
    return ScriptBatchEditLocation(path=("operations", index, "id"))


def _validation_location(message: ValidationMessage) -> ScriptBatchEditLocation:
    prefix = message.params.get("prefix")
    field = message.params.get("field")
    if isinstance(prefix, str):
        path = _parse_path(prefix)
        if isinstance(field, str):
            field_path = _parse_path(field)
            if not field_path[: len(path)] == path:
                path += field_path
        elif message.key == "val_reference_not_in_bucket":
            path += ("references",)
        return ScriptBatchEditLocation(path=path)
    if isinstance(field, str):
        return ScriptBatchEditLocation(path=_parse_path(field))
    rendered = message.render()
    prefix = rendered.split(":", 1)[0]
    return ScriptBatchEditLocation(path=_parse_path(prefix))


def _parse_path(value: str) -> tuple[str | int, ...]:
    value = value.replace(": ", ".").replace(":", ".")
    parts: list[str | int] = []
    for name, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", value):
        if name:
            parts.extend(int(part) if part.isdecimal() else part for part in name.split(".") if part)
        elif index:
            parts.append(int(index))
    return tuple(parts)


def _unit_id_at_location(script: dict[str, Any], path: tuple[str | int, ...]) -> str | None:
    items, id_field, kind = resolve_items(script)
    if len(path) < 2 or path[0] != kind or not isinstance(path[1], int):
        return None
    index = path[1]
    if index < 0 or index >= len(items) or not isinstance(items[index], dict):
        return None
    value = items[index].get(id_field)
    return value if isinstance(value, str) else None


def _responsible_operation(
    item_id: str | None,
    location: tuple[str | int, ...],
    operations: list[ScriptBatchOperation],
) -> int | None:
    relative_path = location[2:] if len(location) >= 3 and isinstance(location[1], int) else ()
    if item_id is not None and relative_path:
        for index in range(len(operations) - 1, -1, -1):
            operation = operations[index]
            if isinstance(operation, UpdateOperation) and operation.id == item_id:
                if any(_operation_field_affects_location(field, relative_path) for field in operation.fields):
                    return index
            elif isinstance(operation, InsertAfterOperation) and _operation_id(operation) == item_id:
                return index
    return None


def _operation_field_affects_location(field: str, location: tuple[str | int, ...]) -> bool:
    field_path = _parse_path(field)
    overlaps = field_path[: len(location)] == location or location[: len(field_path)] == field_path
    if overlaps:
        return True
    return bool(location and location[0] == "references" and field_path and field_path[0] == "shots")


__all__ = [
    "InsertAfterOperation",
    "MoveAfterOperation",
    "RemoveOperation",
    "ScriptBatchEditCommand",
    "ScriptBatchEditLocation",
    "ScriptBatchEditProblem",
    "ScriptBatchEditResult",
    "ScriptBatchEditor",
    "ScriptBatchOperation",
    "UpdateOperation",
    "script_revision",
]
