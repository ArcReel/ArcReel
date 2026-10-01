"""脚本规划的本集新增资产：处理决定的解析、规划校验的叠加视图、确认时的引用改写与登记。

规划条目里的引用一律写新增项的 ``name``（称呼），确认时按处理决定统一改写（见 ``docs/adr/0092``）：

- 称呼与已登记的同类资产（规范化后）相同的新增项，无论处理决定，一律归到该资产，不改它的描述。
- ``register``：登记为新资产，引用改为登记名；登记名与已登记的同类资产相同即归到该资产；
  本集新增项之间同类同名的合为一项。
- ``merge``：归到 ``target``（已登记的同类资产，或本集另一项新增资产的最终归属），称呼记为别名。
  ``target`` 写另一项新增资产时，取它的称呼或登记名均可；衍生的本体同理。
- ``derivative``：登记为 ``target`` 角色的衍生，画面引用改为 ``本体/衍生``，台词说话人改为本体。
- ``skip``：不登记。引用数组里移除；剧情演绎台词的说话人保留原名；参考生视频正文里画面位的
  ``@[名]`` 退为纯文本，说话人位保持原样。

规划从不改已登记资产的描述：登记只新增条目、衍生与别名。程序只按名字归并，别名不参与匹配。
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from lib.project.asset_derivatives import ensure_derivative_table
from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    asset_name_comparison_key,
    build_asset_entry,
    ensure_project_asset_name_available,
    record_asset_aliases,
    resolve_asset_key,
    validate_asset_name,
)
from lib.references.reference_catalog import derivative_reference
from lib.script.draft_violation import DraftViolation
from lib.script.reference_video.text_parser import remap_mentions
from lib.script.script_models import NewAssetType, PlanNewAsset

NEW_ASSETS_FIELD = "new_assets"

_TYPES: tuple[NewAssetType, ...] = ("character", "scene", "prop")


@dataclass(frozen=True)
class NewAssetProblem:
    """一项新增资产无法按处理决定落地的原因；``index`` 是它在 ``new_assets`` 里的下标。"""

    code: str
    message: str
    index: int


class NewAssetsError(ValueError):
    """新增资产的处理决定解析不出：确认整笔拒绝，项目与正式脚本都不改。"""

    def __init__(self, problems: Sequence[NewAssetProblem]):
        super().__init__("；".join(problem.message for problem in problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class _Outcome:
    kind: Literal["asset", "derivative", "skip"]
    #: ``asset`` 为资产名，``derivative`` 为本体角色名，``skip`` 为称呼本身。
    name: str
    derivative: str = ""

    @property
    def visual(self) -> str | None:
        if self.kind == "asset":
            return self.name
        if self.kind == "derivative":
            return derivative_reference(self.name, self.derivative)
        return None

    @property
    def speaker(self) -> str | None:
        return None if self.kind == "skip" else self.name


@dataclass
class _Registration:
    description: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class NewAssetResolution:
    """处理决定解析后的结果：引用怎么改、项目里新增哪些条目、衍生与别名。"""

    outcomes: dict[tuple[str, str], _Outcome]
    registrations: dict[str, dict[str, _Registration]]
    derivatives: list[tuple[str, str, str]]
    aliases: list[tuple[str, str, str]]
    #: 与已登记的同类资产同名、确认时自动归并的新增项：``(类型, 称呼, 资产名)``。
    auto_merged: list[tuple[str, str, str]]

    def rewrite_entries(self, entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """返回改写引用后的规划条目副本。"""
        by_name = {key: outcome for (_type, key), outcome in self.outcomes.items()}
        return [self._rewrite_entry(dict(copy.deepcopy(entry)), by_name) for entry in entries]

    def _rewrite_entry(self, entry: dict[str, Any], by_name: Mapping[str, _Outcome]) -> dict[str, Any]:
        for asset_type in _TYPES:
            for list_field in ASSET_SPECS[asset_type].reference_list_fields:
                values = entry.get(list_field)
                if isinstance(values, list):
                    entry[list_field] = self._rewrite_names(asset_type, values)
        utterances = entry.get("utterances")
        if isinstance(utterances, list):
            for utterance in utterances:
                if isinstance(utterance, dict) and isinstance(utterance.get("speaker"), str):
                    outcome = self.outcomes.get(("character", asset_name_comparison_key(utterance["speaker"])))
                    if outcome is not None and outcome.speaker is not None:
                        utterance["speaker"] = outcome.speaker
        text = entry.get("text")
        if isinstance(text, str):
            entry["text"] = remap_mentions(text, lambda name, speaker: _remap_mention(by_name.get(name), speaker))
        return entry

    def _rewrite_names(self, asset_type: str, values: list[Any]) -> list[Any]:
        result: list[Any] = []
        seen: set[str] = set()
        for value in values:
            if isinstance(value, str):
                outcome = self.outcomes.get((asset_type, asset_name_comparison_key(value)))
                if outcome is not None:
                    value = outcome.visual
                    if value is None:
                        continue
                key = asset_name_comparison_key(value)
                if key in seen:
                    continue
                seen.add(key)
            result.append(value)
        return result

    def apply_to_project(self, project: dict[str, Any]) -> None:
        """把新增资产、衍生与别名写进项目载荷；已登记资产的描述不动。"""
        for asset_type, registrations in self.registrations.items():
            spec = ASSET_SPECS[asset_type]
            bucket = project.setdefault(spec.bucket_key, {})
            for name, registration in registrations.items():
                existing = resolve_asset_key(bucket, name)
                if existing is None:
                    ensure_project_asset_name_available(project, name, requested_asset_type=asset_type)
                    bucket[name] = build_asset_entry(asset_type, registration.description)
                    existing = name
                entry = bucket[existing]
                if isinstance(entry, dict):
                    record_asset_aliases(entry, registration.aliases, asset_name=existing)
        for asset_type, name, alias in self.aliases:
            bucket = project.get(ASSET_SPECS[asset_type].bucket_key)
            key = resolve_asset_key(bucket, name) if isinstance(bucket, dict) else None
            entry = bucket.get(key) if isinstance(bucket, dict) and key is not None else None
            if isinstance(entry, dict):
                record_asset_aliases(entry, (alias,), asset_name=key or name)
        characters = project.get(ASSET_SPECS["character"].bucket_key)
        for base, derivative, description in self.derivatives:
            key = resolve_asset_key(characters, base) if isinstance(characters, dict) else None
            entry = characters.get(key) if isinstance(characters, dict) and key is not None else None
            if not isinstance(entry, dict):
                raise ValueError(f"衍生「{derivative}」的本体角色「{base}」不存在")
            table = ensure_derivative_table(entry)
            if resolve_asset_key(table, derivative) is None:
                table[derivative] = {"description": description, ASSET_SPECS["character"].sheet_field: ""}


def _remap_mention(outcome: _Outcome | None, speaker: bool) -> str | None:
    if outcome is None:
        return None
    if outcome.kind == "skip":
        return None if speaker else outcome.name
    return f"@[{outcome.visual}]"


def _parse_items(raw_items: object) -> tuple[list[PlanNewAsset | None], list[NewAssetProblem]]:
    if raw_items is None:
        return [], []
    if not isinstance(raw_items, list):
        return [], [NewAssetProblem("new_asset_invalid", "new_assets 必须是数组", -1)]
    items: list[PlanNewAsset | None] = []
    problems: list[NewAssetProblem] = []
    for index, raw in enumerate(raw_items):
        try:
            items.append(PlanNewAsset.model_validate(raw))
        except ValidationError as exc:
            items.append(None)
            problems.append(NewAssetProblem("new_asset_invalid", f"new_assets[{index}] 结构非法: {exc}", index))
    return items, problems


def _registered_names(project: Mapping[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """``(按类型的 判等键→登记名, 判等键→占用它的类型)``；名称空间跨四类资产。"""
    registered: dict[str, dict[str, str]] = {asset_type: {} for asset_type in _TYPES}
    owners: dict[str, str] = {}
    for asset_type, spec in ASSET_SPECS.items():
        bucket = project.get(spec.bucket_key)
        if not isinstance(bucket, Mapping):
            continue
        for name in bucket:
            key = asset_name_comparison_key(str(name))
            owners.setdefault(key, asset_type)
            if asset_type in registered:
                registered[asset_type].setdefault(key, str(name))
    return registered, owners


def _derivative_names(project: Mapping[str, Any], base: str) -> dict[str, str]:
    characters = project.get(ASSET_SPECS["character"].bucket_key)
    key = resolve_asset_key(characters, base) if isinstance(characters, dict) else None
    entry = characters.get(key) if isinstance(characters, dict) and key is not None else None
    table = entry.get(DERIVATIVES_FIELD) if isinstance(entry, Mapping) else None
    if not isinstance(table, Mapping):
        return {}
    return {asset_name_comparison_key(str(name)): str(name) for name in table}


class _Resolver:
    def __init__(self, project: Mapping[str, Any], raw_items: object):
        self.project = project
        parsed, self.problems = _parse_items(raw_items)
        self.registered, self.owners = _registered_names(project)
        #: (类型, 称呼判等键) → (下标, 新增项)；同类同名只留第一项。
        self.items: dict[tuple[str, str], tuple[int, PlanNewAsset]] = {}
        item_types: dict[str, str] = {}
        for index, item in enumerate(parsed):
            if item is None:
                continue
            key = asset_name_comparison_key(item.name)
            if not self._valid_name(item.name, index, "name"):
                continue
            if (item.type, key) in self.items:
                continue
            owner = self.owners.get(key, item_types.get(key))
            if owner is not None and owner != item.type:
                self._problem("new_asset_name_taken", index, f"「{item.name}」已是{_label(owner)}的名字")
                continue
            if item.decision == "derivative" and item.type != "character":
                self._problem("new_asset_derivative_not_character", index, "只有角色可以登记衍生")
                continue
            if item.asset_name and not self._valid_name(item.asset_name, index, "asset_name"):
                continue
            item_types[key] = item.type
            self.items[(item.type, key)] = (index, item)
        #: (类型, 登记名判等键) → 称呼判等键：``target`` 可以写另一项新增资产改过的登记名。
        self.registration_keys: dict[tuple[str, str], str] = {}
        for (asset_type, key), (_index, item) in self.items.items():
            if item.decision == "register" and item.asset_name:
                self.registration_keys.setdefault((asset_type, asset_name_comparison_key(item.asset_name)), key)
        self.registrations: dict[str, dict[str, _Registration]] = {asset_type: {} for asset_type in _TYPES}
        self.aliases: list[tuple[str, str, str]] = []
        self.derivatives: list[tuple[str, str, str]] = []
        self.auto_merged: list[tuple[str, str, str]] = []
        self.outcomes: dict[tuple[str, str], _Outcome] = {}
        self._assets: dict[tuple[str, str], str | None] = {}

    def _problem(self, code: str, index: int, message: str) -> None:
        self.problems.append(NewAssetProblem(code, f"new_assets[{index}]：{message}", index))

    def _valid_name(self, name: str, index: int, field_name: str) -> bool:
        try:
            validate_asset_name(name)
        except ValueError as exc:
            self._problem("new_asset_name_invalid", index, f"{field_name} {exc}")
            return False
        return True

    def resolve(self) -> NewAssetResolution:
        for (asset_type, key), (index, item) in self.items.items():
            registered = self.registered[asset_type].get(key)
            if registered is not None:
                self._auto_merge(asset_type, item, registered)
                self.outcomes[(asset_type, key)] = _Outcome("asset", registered)
            elif item.decision == "skip":
                self.outcomes[(asset_type, key)] = _Outcome("skip", asset_name_comparison_key(item.name))
            elif item.decision == "derivative":
                self._resolve_derivative(key, index, item)
            else:
                asset = self._asset(asset_type, key, ())
                if asset is not None:
                    self.outcomes[(asset_type, key)] = _Outcome("asset", asset)
        if self.problems:
            raise NewAssetsError(self.problems)
        return NewAssetResolution(
            outcomes=self.outcomes,
            registrations={asset_type: regs for asset_type, regs in self.registrations.items() if regs},
            derivatives=self.derivatives,
            aliases=self.aliases,
            auto_merged=self.auto_merged,
        )

    def _asset(self, asset_type: str, key: str, chain: tuple[str, ...]) -> str | None:
        """把一个名字解析为它最终归属的资产名；不是资产（衍生 / 不登记 / 未知 / 解析失败）时为 None。"""
        if (asset_type, key) in self._assets:
            return self._assets[(asset_type, key)]
        registered = self.registered[asset_type].get(key)
        found = self.items.get((asset_type, key))
        if found is None or registered is not None:
            return registered
        index, item = found
        if key in chain:
            self._problem("new_asset_merge_cycle", index, f"归并关系成环：{' → '.join((*chain, key))}")
            return None
        result: str | None = None
        if item.decision == "register":
            result = self._register(asset_type, key, index, item)
        elif item.decision == "merge":
            target_key = self._target_key(asset_type, item.target)
            target = self._asset(asset_type, target_key, (*chain, key)) if target_key else None
            if target is None:
                self._problem(
                    "new_asset_target_unresolved",
                    index,
                    f"归入目标「{item.target}」不是已登记的{_label(asset_type)}，也不是本集登记的新增项",
                )
            else:
                result = target
                self._record_alias(asset_type, target, item.name)
        self._assets[(asset_type, key)] = result
        return result

    def _register(self, asset_type: str, key: str, index: int, item: PlanNewAsset) -> str | None:
        final = validate_asset_name(item.asset_name or item.name)
        final_key = asset_name_comparison_key(final)
        registered = self.registered[asset_type].get(final_key)
        if registered is not None:
            self._auto_merge(asset_type, item, registered)
            return registered
        owner = self.owners.get(final_key)
        if owner is not None:
            self._problem("new_asset_name_taken", index, f"登记名「{final}」已是{_label(owner)}的名字")
            return None
        registrations = self.registrations[asset_type]
        registration = registrations.get(final)
        if registration is None:
            registrations[final] = registration = _Registration(item.description)
        registration.aliases.extend((item.name, *item.aliases))
        return final

    def _auto_merge(self, asset_type: str, item: PlanNewAsset, registered: str) -> None:
        self.auto_merged.append((asset_type, item.name, registered))
        for alias in (item.name, *item.aliases):
            self.aliases.append((asset_type, registered, alias))

    def _target_key(self, asset_type: str, target: str) -> str:
        """``target`` 的判等键：已登记名或新增项称呼原样取，否则按新增项的登记名找回它的称呼。"""
        key = asset_name_comparison_key(target)
        if not key or key in self.registered[asset_type] or (asset_type, key) in self.items:
            return key
        return self.registration_keys.get((asset_type, key), key)

    def _record_alias(self, asset_type: str, target: str, alias: str) -> None:
        registration = self.registrations[asset_type].get(target)
        if registration is not None:
            registration.aliases.append(alias)
        else:
            self.aliases.append((asset_type, target, alias))

    def _resolve_derivative(self, key: str, index: int, item: PlanNewAsset) -> None:
        target_key = self._target_key("character", item.target)
        base = self._asset("character", target_key, (key,)) if target_key else None
        if base is None:
            self._problem(
                "new_asset_base_unresolved",
                index,
                f"衍生的本体「{item.target}」不是已登记的角色，也不是本集登记的新角色",
            )
            return
        derivative = validate_asset_name(item.asset_name or item.name)
        existing = _derivative_names(self.project, base).get(asset_name_comparison_key(derivative))
        if existing is None:
            self.derivatives.append((base, derivative, item.description))
        self.outcomes[("character", key)] = _Outcome("derivative", base, existing or derivative)


def _label(asset_type: str) -> str:
    return ASSET_SPECS[asset_type].label_zh


def resolve_new_assets(project: Mapping[str, Any], raw_items: object) -> NewAssetResolution:
    """按项目现状解析规划的新增资产；有项落不了地时抛 :class:`NewAssetsError`。"""
    return _Resolver(project, raw_items).resolve()


def new_asset_violations(project: Mapping[str, Any], raw_items: object) -> list[DraftViolation]:
    """规划校验：新增资产的处理决定解析不出的项，逐条成为整集层面的违约。"""
    try:
        resolve_new_assets(project, raw_items)
    except NewAssetsError as exc:
        return [
            DraftViolation(
                f"{problem.message}；请改正该项的 decision / target，或改用 register / skip",
                code=problem.code,
                label=f"new_assets[{problem.index}]" if problem.index >= 0 else "new_assets",
            )
            for problem in exc.problems
        ]
    return []


def dedupe_new_assets(raw_items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """同类同名（规范化后）的新增项只留第一项。"""
    seen: set[tuple[object, str]] = set()
    result: list[dict[str, Any]] = []
    for item in raw_items:
        name = item.get("name")
        marker = (item.get("type"), asset_name_comparison_key(name) if isinstance(name, str) else "")
        if marker in seen:
            continue
        seen.add(marker)
        result.append(dict(item))
    return result


def with_new_assets(content: Mapping[str, Any], raw_items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """把去重后的本集新增项放进规划内容。没有新增项时不写这个键，内容与不带新增项的规划同形。"""
    result = {key: value for key, value in content.items() if key != NEW_ASSETS_FIELD}
    items = dedupe_new_assets(raw_items)
    if items:
        result[NEW_ASSETS_FIELD] = items
    return result


def planning_project(project: Mapping[str, Any], raw_items: object) -> dict[str, Any]:
    """规划校验用的项目视图：本集新增项按称呼叠加在同类资产表上，引用它们即视为已登记。

    只供校验读取，不落盘。与其他类型重名的新增项不叠加（它已由处理决定的校验报出）。
    """
    overlay = dict(project)
    items, _problems = _parse_items(raw_items)
    _registered, owners = _registered_names(project)
    for item in items:
        if item is None:
            continue
        key = asset_name_comparison_key(item.name)
        owner = owners.get(key)
        if owner is not None:
            continue
        spec = ASSET_SPECS[item.type]
        bucket = overlay.get(spec.bucket_key)
        bucket = dict(bucket) if isinstance(bucket, Mapping) else {}
        bucket[key] = {"description": item.description}
        overlay[spec.bucket_key] = bucket
        owners[key] = item.type
    return overlay
