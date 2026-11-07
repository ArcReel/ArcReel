"""Episode-new assets in script planning: resolve decisions, overlay validation views, rewrite references, and register on confirmation.

Planning references always use new items' ``name`` (appellation); confirmation rewrites them according
to handling decisions (see ``docs/adr/0092``):

- New items whose appellation matches an existing asset of the same type after normalization always
  resolve to that asset, regardless of their handling decision, without changing its description.
- ``register``: register a new asset and rewrite references to its registered name. Names matching
  existing same-type assets resolve to those assets; same-type/name episode-new items merge into one.
- ``merge``: resolve to ``target``, an existing same-type asset or another new item's final asset,
  recording the appellation as an alias. ``target`` may address other new items by appellation or registered
  name; the same applies to derivative base characters.
- ``derivative``: register a derivative under the ``target`` character. Rewrite visual references as
  ``本体/衍生`` and dialogue speakers (drama ``utterances`` and ad storyboard video-prompt ``dialogue``)
  to the base character.
- ``skip``: do not register. Remove from reference arrays; retain drama speakers' original names.
  In reference-to-video bodies, visual ``@[名]`` references become plain text, while speaker slots remain unchanged.

Planning never changes existing asset descriptions: registration only adds entries, derivatives,
and aliases. Programs merge by name only; aliases do not participate in matching.

After rewriting, names left in reference arrays and reference-to-video visual positions must belong
to existing assets, episode-registered new assets, or their derivatives
(:func:`unregistered_references`). Speakers are excluded and may name unregistered extras.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from lib.project.asset_derivatives import derivative_table, ensure_derivative_table, find_derivative_name
from lib.project.asset_types import (
    ASSET_SPECS,
    asset_name_comparison_key,
    asset_name_namespace_key,
    build_asset_entry,
    ensure_project_asset_name_available,
    record_asset_aliases,
    resolve_asset_key,
    validate_asset_name,
)
from lib.references.reference_catalog import build_reference_catalog, derivative_reference
from lib.script.draft_violation import DraftViolation
from lib.script.reference_video.text_parser import extract_mentions, remap_mentions
from lib.script.script_models import NEW_ASSETS_FIELD, NewAssetType, PlanNewAsset

_TYPES: tuple[NewAssetType, ...] = ("character", "scene", "prop")


@dataclass(frozen=True)
class NewAssetProblem:
    """Why a new asset cannot realize its handling decision; ``index`` is its position in ``new_assets``."""

    code: str
    message: str
    index: int


class NewAssetsError(ValueError):
    """Unresolvable new-asset decisions reject the entire confirmation, changing neither project nor formal script."""

    def __init__(self, problems: Sequence[NewAssetProblem]):
        super().__init__("；".join(problem.message for problem in problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class UnregisteredReference:
    """A planning item references a name neither already registered nor newly registered for this episode."""

    item_id: str
    names: tuple[str, ...]


class UnregisteredReferencesError(ValueError):
    """References still unresolved after rewriting reject the entire confirmation, changing neither project nor formal script."""

    def __init__(self, references: Sequence[UnregisteredReference]):
        self.references = list(references)
        super().__init__("; ".join(f"{ref.item_id}: {', '.join(ref.names)}" for ref in self.references))


@dataclass(frozen=True)
class _Outcome:
    kind: Literal["asset", "derivative", "skip"]
    #: ``asset`` is the asset name, ``derivative`` the base character name, and ``skip`` the appellation itself.
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
    """Resolved handling decisions: reference rewrites and project additions of entries, derivatives, and aliases."""

    outcomes: dict[tuple[str, str], _Outcome]
    registrations: dict[str, dict[str, _Registration]]
    derivatives: list[tuple[str, str, str]]
    aliases: list[tuple[str, str, str]]
    #: New items matching existing same-type names, automatically merged on confirmation: ``(类型, 称呼, 资产名)``.
    auto_merged: list[tuple[str, str, str]]

    def rewrite_entries(self, entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Return copies of planning items with rewritten references."""
        by_name = {key: outcome for (_type, key), outcome in self.outcomes.items()}
        return [self._rewrite_entry(dict(copy.deepcopy(entry)), by_name) for entry in entries]

    def _rewrite_entry(self, entry: dict[str, Any], by_name: Mapping[str, _Outcome]) -> dict[str, Any]:
        for asset_type in _TYPES:
            for list_field in ASSET_SPECS[asset_type].reference_list_fields:
                values = entry.get(list_field)
                if isinstance(values, list):
                    entry[list_field] = self._rewrite_names(asset_type, values)
        video_prompt = entry.get("video_prompt")
        for speeches in (
            entry.get("utterances"),
            video_prompt.get("dialogue") if isinstance(video_prompt, dict) else None,
        ):
            if not isinstance(speeches, list):
                continue
            for speech in speeches:
                if isinstance(speech, dict) and isinstance(speech.get("speaker"), str):
                    outcome = self.outcomes.get(("character", asset_name_comparison_key(speech["speaker"])))
                    if outcome is not None and outcome.speaker is not None:
                        speech["speaker"] = outcome.speaker
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

    def registered(self) -> list[dict[str, str]]:
        """Assets/derivatives newly registered here (``{"type", "name"}``, derivatives named ``本体/衍生``), in type/registration order."""
        assets = [
            {"type": asset_type, "name": name}
            for asset_type, registrations in self.registrations.items()
            for name in registrations
        ]
        assets.extend(
            {"type": "character", "name": derivative_reference(base, derivative)}
            for base, derivative, _description in self.derivatives
        )
        return assets

    def apply_to_project(self, project: dict[str, Any]) -> None:
        """Write new assets, derivatives, and aliases into project payloads, leaving existing asset descriptions unchanged."""
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
            existing = find_derivative_name(table, derivative)
            if existing is None:
                table[derivative] = {"description": description, ASSET_SPECS["character"].sheet_field: ""}
            elif asset_name_comparison_key(existing) != asset_name_comparison_key(derivative):
                raise ValueError(f"衍生名「{derivative}」与「{base}」已有的衍生「{existing}」重名")


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
    """``(按类型的 判等键→登记名, 判等键→占用它的类型)``; the namespace spans all four asset types."""
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


def _namespace_names(project: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """``判重键→(类型, 登记名)``: new names differing from existing assets only by case also collide,
    matching :func:`ensure_project_asset_name_available`.
    """
    names: dict[str, tuple[str, str]] = {}
    for asset_type, spec in ASSET_SPECS.items():
        bucket = project.get(spec.bucket_key)
        if not isinstance(bucket, Mapping):
            continue
        for name in bucket:
            names.setdefault(asset_name_namespace_key(str(name)), (asset_type, str(name)))
    return names


def _derivatives_of(project: Mapping[str, Any], base: str) -> dict[str, Any]:
    characters = project.get(ASSET_SPECS["character"].bucket_key)
    key = resolve_asset_key(characters, base) if isinstance(characters, dict) else None
    return derivative_table(characters.get(key) if isinstance(characters, dict) and key is not None else None)


class _Resolver:
    def __init__(self, project: Mapping[str, Any], raw_items: object):
        self.project = project
        parsed, self.problems = _parse_items(raw_items)
        self.registered, self.owners = _registered_names(project)
        self.namespace = _namespace_names(project)
        #: Deduplication key → newly registered (type, registered name); case-only new-name collisions reject the latter item.
        self.claimed: dict[str, tuple[str, str]] = {}
        #: (Type, appellation equality key) → (index, new item); retain only the first same-type/name item.
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
        #: (Type, registered-name equality key) → appellation equality key: ``target`` may use another new asset's renamed registration.
        self.registration_keys: dict[tuple[str, str], str] = {}
        for (asset_type, key), (_index, item) in self.items.items():
            if item.decision == "register" and item.asset_name:
                self.registration_keys.setdefault((asset_type, asset_name_comparison_key(item.asset_name)), key)
        self.registrations: dict[str, dict[str, _Registration]] = {asset_type: {} for asset_type in _TYPES}
        self.aliases: list[tuple[str, str, str]] = []
        self.derivatives: list[tuple[str, str, str]] = []
        #: Base → newly registered derivative names (values unused); case-only collisions under one base reject the latter derivative.
        self.claimed_derivatives: dict[str, dict[str, None]] = {}
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
        """Resolve a name to its final asset name; None for nonassets (derivatives / skipped / unknown / resolution failures)."""
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
        namespace_key = asset_name_namespace_key(final)
        taken = self.namespace.get(namespace_key)
        if taken is not None:
            owner, existing = taken
            self._problem("new_asset_name_taken", index, f"登记名「{final}」与已有{_label(owner)}「{existing}」重名")
            return None
        claimed = self.claimed.setdefault(namespace_key, (asset_type, final))
        if claimed != (asset_type, final):
            owner, existing = claimed
            self._problem(
                "new_asset_name_taken", index, f"登记名「{final}」与本集新增的{_label(owner)}「{existing}」重名"
            )
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
        """Equality key of ``target``: use existing names/new appellations directly, otherwise recover appellations by new registered names."""
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
        existing = find_derivative_name(_derivatives_of(self.project, base), derivative)
        owner = "已有的"
        if existing is None:
            owner = "本集新增的"
            claimed = self.claimed_derivatives.setdefault(base, {})
            existing = find_derivative_name(claimed, derivative)
            if existing is None:
                claimed[derivative] = None
                self.derivatives.append((base, derivative, item.description))
        if existing is not None and asset_name_comparison_key(existing) != asset_name_comparison_key(derivative):
            self._problem(
                "new_asset_name_taken", index, f"衍生名「{derivative}」与「{base}」{owner}衍生「{existing}」重名"
            )
            return
        self.outcomes[("character", key)] = _Outcome("derivative", base, existing or derivative)


def _label(asset_type: str) -> str:
    return ASSET_SPECS[asset_type].label_zh


def resolve_new_assets(project: Mapping[str, Any], raw_items: object) -> NewAssetResolution:
    """Resolve planned new assets against current project state; raise :class:`NewAssetsError` if any cannot be realized."""
    return _Resolver(project, raw_items).resolve()


def new_asset_violations(project: Mapping[str, Any], raw_items: object) -> list[DraftViolation]:
    """Planning validation: turn each unresolved new-asset handling decision into an episode-level violation."""
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


def unregistered_references(
    project: Mapping[str, Any], entries: Sequence[Mapping[str, Any]], *, id_field: str
) -> list[UnregisteredReference]:
    """List unresolved item references against ``project`` with this episode's new assets registered, in per-item appearance order.

    Check reference arrays and visual ``@[名]`` positions in reference-to-video bodies, excluding dialogue speakers.
    """
    catalog = build_reference_catalog(dict(project))
    visual = set().union(*(catalog.reference_names(asset_type) for asset_type in ASSET_SPECS))
    result: list[UnregisteredReference] = []
    for index, entry in enumerate(entries):
        bad: list[str] = []
        for asset_type in _TYPES:
            known = catalog.reference_names(asset_type)
            for list_field in ASSET_SPECS[asset_type].reference_list_fields:
                values = entry.get(list_field)
                if isinstance(values, list):
                    bad.extend(
                        value
                        for value in values
                        if isinstance(value, str) and asset_name_comparison_key(value) not in known
                    )
        text = entry.get("text")
        if isinstance(text, str):
            bad.extend(name for name in extract_mentions(text) if asset_name_comparison_key(name) not in visual)
        if bad:
            item_id = entry.get(id_field)
            result.append(
                UnregisteredReference(
                    item_id=item_id if isinstance(item_id, str) and item_id else f"#{index + 1}",
                    names=tuple(dict.fromkeys(bad)),
                )
            )
    return result


def dedupe_new_assets(raw_items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the first new item of each normalized same-type/name pair."""
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
    """Add deduplicated episode-new items to planning content. Omit the key when none exist, matching plans without new assets."""
    result = {key: value for key, value in content.items() if key != NEW_ASSETS_FIELD}
    items = dedupe_new_assets(raw_items)
    if items:
        result[NEW_ASSETS_FIELD] = items
    return result


def planning_project(project: Mapping[str, Any], raw_items: object) -> dict[str, Any]:
    """Project view for planning validation: overlay episode-new items by appellation in same-type asset tables, treating references as registered.

    Read-only validation view, never persisted. Exclude names colliding with other types, already reported
    by handling-decision validation.
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
