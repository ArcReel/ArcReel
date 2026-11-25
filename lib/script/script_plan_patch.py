"""Apply ordered edits to the persisted script-plan shape in memory."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lib.infra.content_digest import OPTIONAL_PREFIXED_DIGEST_PATTERN, prefixed, unprefixed
from lib.script.item_ids import ItemIdIssuer, max_item_number
from lib.script.patch_operations import (
    PatchInsertOperation,
    PatchMoveOperation,
    PatchRemoveOperation,
    PatchSplitOperation,
    PatchUpdateOperation,
)
from lib.script.plan_new_assets import NEW_ASSETS_FIELD
from lib.script.script_batch_edit import ScriptBatchEditLocation, ScriptBatchEditProblem
from lib.script.script_plan_entries import ScriptPlanKind, entry_id_field, plan_variant

EDITABLE_PLAN_FIELDS: dict[ScriptPlanKind, frozenset[str]] = {
    "drama": frozenset(
        {
            "duration_seconds",
            "segment_break",
            "characters_in_scene",
            "scenes",
            "props",
            "scene_description",
            "utterances",
            "source_text",
        }
    ),
    "narration": frozenset(
        {
            "novel_text",
            "duration_seconds",
            "segment_break",
            "characters_in_segment",
            "scenes",
            "props",
        }
    ),
    "reference_video": frozenset({"duration_seconds", "source_text", "text"}),
}


class SetPlanTitleOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["set_title"]
    title: str


class SetPlanAssetsOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["set_new_assets"]
    new_assets: list[dict[str, Any]]


ScriptPlanOperation = Annotated[
    PatchUpdateOperation
    | PatchInsertOperation
    | PatchMoveOperation
    | PatchRemoveOperation
    | PatchSplitOperation
    | SetPlanTitleOperation
    | SetPlanAssetsOperation,
    Field(discriminator="op"),
]


class PatchScriptPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: int = Field(ge=1, strict=True, description="分集账本中的集 ID")
    base_revision: str = Field(
        pattern=OPTIONAL_PREFIXED_DIGEST_PATTERN,
        description="get_script_plan_content 返回的 revision，带前缀与裸 hex 等价",
    )
    operations: list[ScriptPlanOperation] = Field(min_length=1, description="按顺序执行，整批原子提交")

    @field_validator("base_revision")
    @classmethod
    def _normalize_revision(cls, value: str) -> str:
        return prefixed(unprefixed(value))


class ScriptPlanPatchResult(BaseModel):
    success: bool
    episode: int
    before_revision: str | None
    revision: str | None
    affected_ids: tuple[str, ...] = ()
    problems: tuple[ScriptBatchEditProblem, ...] = ()
    blocking_issues: list[dict[str, Any]] = Field(default_factory=list)
    soft_violations: list[dict[str, Any]] = Field(default_factory=list)


class ScriptPlanPatchError(ValueError):
    def __init__(self, problem: ScriptBatchEditProblem) -> None:
        super().__init__(problem.reason)
        self.problem = problem


class ScriptPlanEdit:
    def __init__(self, previous: dict[str, Any], kind: ScriptPlanKind, episode: int, high_water: int) -> None:
        self.content = deepcopy(previous)
        variant = plan_variant(kind)
        self.root = variant.plan_items_key
        self.id_field = entry_id_field(kind)
        self.editable_fields = EDITABLE_PLAN_FIELDS[kind]
        entries = self.content.get(self.root)
        if not isinstance(entries, list):
            raise self.error("invalid_content", (self.root,), "脚本规划缺少条目列表")
        self.entries: list[Any] = entries
        ids = [entry.get(self.id_field) for entry in entries if isinstance(entry, dict)]
        self.issuer = ItemIdIssuer(episode, variant.skeleton_kind, max(high_water, max_item_number(ids, episode)))
        self.affected: dict[str, None] = {}
        self._changes: dict[tuple[str | None, str | None], int] = {}

    @staticmethod
    def error(
        code: str,
        location: tuple[str | int, ...],
        reason: str,
        *,
        operation_index: int | None = None,
        unit_id: str | None = None,
    ) -> ScriptPlanPatchError:
        return ScriptPlanPatchError(
            ScriptBatchEditProblem(
                code=code,
                operation_index=operation_index,
                unit_id=unit_id,
                locations=(ScriptBatchEditLocation(path=location),),
                reason=reason,
                next_action="fix_operation" if operation_index is not None else "repair_plan",
            )
        )

    def problem_for_content(
        self,
        code: str,
        location: tuple[str | int, ...],
        reason: str,
    ) -> ScriptPlanPatchError:
        unit_id = None
        field = str(location[0]) if location else None
        replacement = None
        if field == self.root and len(location) > 1 and isinstance(location[1], int):
            entry = self.entries[location[1]]
            if isinstance(entry, dict) and isinstance(entry.get(self.id_field), str):
                unit_id = entry[self.id_field]
            field = location[2] if len(location) > 2 and isinstance(location[2], str) else None
            replacement = self._changes.get((unit_id, None))
        touched = self._changes.get((unit_id, field))
        index = max((i for i in (replacement, touched) if i is not None), default=None)
        return self.error(code, location, reason, operation_index=index, unit_id=unit_id)

    def _index(self, item_id: str, index: int, argument: str) -> int:
        for position, entry in enumerate(self.entries):
            if isinstance(entry, dict) and entry.get(self.id_field) == item_id:
                return position
        raise self.error(
            "item_not_found",
            ("operations", index, argument),
            f"找不到条目 {item_id}",
            operation_index=index,
            unit_id=item_id,
        )

    def _check_fields(self, fields: dict[str, Any], index: int, location: tuple[str | int, ...]) -> None:
        if disallowed := fields.keys() - self.editable_fields:
            field = sorted(disallowed)[0]
            raise self.error(
                "field_not_editable",
                (*location, field),
                f"当前脚本规划不可编辑字段 {field}",
                operation_index=index,
            )

    def _new_item(self, fields: dict[str, Any], item_id: str, index: int) -> dict[str, Any]:
        self._changes[item_id, None] = index
        self.affected[item_id] = None
        return {**deepcopy(fields), self.id_field: item_id}

    def apply(self, operations: Sequence[ScriptPlanOperation]) -> None:
        for index, operation in enumerate(operations):
            path = ("operations", index)
            if isinstance(operation, SetPlanTitleOperation):
                if self.root != "scenes":
                    raise self.error(
                        "title_not_editable", (*path, "title"), "仅剧情演绎规划可改标题", operation_index=index
                    )
                self.content["title"] = operation.title
                self._changes[None, "title"] = index
                continue
            if isinstance(operation, SetPlanAssetsOperation):
                self.content[NEW_ASSETS_FIELD] = deepcopy(operation.new_assets)
                self._changes[None, NEW_ASSETS_FIELD] = index
                continue
            if isinstance(operation, PatchInsertOperation):
                anchor = -1 if operation.after_id is None else self._index(operation.after_id, index, "after_id")
                self._check_fields(operation.item, index, (*path, "item"))
                self.entries.insert(anchor + 1, self._new_item(operation.item, self.issuer.issue(), index))
                continue

            item_id = operation.id
            position = self._index(item_id, index, "id")
            self.affected[item_id] = None
            if isinstance(operation, PatchUpdateOperation):
                self._check_fields(operation.fields, index, (*path, "fields"))
                self.entries[position].update(deepcopy(operation.fields))
                for field in operation.fields:
                    self._changes[item_id, field] = index
            elif isinstance(operation, PatchRemoveOperation):
                self.entries.pop(position)
            elif isinstance(operation, PatchMoveOperation):
                if operation.after_id == item_id:
                    raise self.error(
                        "invalid_operation",
                        (*path, "after_id"),
                        "不能将条目移到自身之后",
                        operation_index=index,
                        unit_id=item_id,
                    )
                entry = self.entries.pop(position)
                anchor = -1 if operation.after_id is None else self._index(operation.after_id, index, "after_id")
                self.entries.insert(anchor + 1, entry)
            else:
                parts = []
                for offset, part in enumerate(operation.parts):
                    self._check_fields(part, index, (*path, "parts", offset))
                    parts.append(self._new_item(part, item_id if offset == 0 else self.issuer.issue(), index))
                self.entries[position : position + 1] = parts
