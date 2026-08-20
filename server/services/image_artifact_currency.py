"""Cancellation compensation for selected formal image artifacts."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_activation import ArtifactRegistrationReceipt
from lib.version_manager import VersionManager

_MISSING = object()


@dataclass(frozen=True, slots=True)
class OptimisticMappingPatch:
    """Reverse only leaves still equal to values written by one mutation."""

    before: Mapping[str, Any]
    after: Mapping[str, Any]

    @classmethod
    def capture(cls, before: Mapping[str, Any], after: Mapping[str, Any]) -> OptimisticMappingPatch:
        return cls(copy.deepcopy(dict(before)), copy.deepcopy(dict(after)))

    def restore(self, current: MutableMapping[str, Any]) -> bool:
        changed = False
        for key in self.before.keys() | self.after.keys():
            before = self.before.get(key, _MISSING)
            selected = self.after.get(key, _MISSING)
            if before == selected:
                continue
            live = current.get(key, _MISSING)
            if live != selected:
                continue
            if before is _MISSING:
                current.pop(key, None)
            else:
                current[key] = copy.deepcopy(before)
            changed = True
        return changed


@dataclass(frozen=True, slots=True)
class OptimisticMappingMemberPatch:
    """Reverse a mapping-valued member without erasing later sibling edits."""

    member: str
    before_present: bool
    before: Any
    selected: Mapping[str, Any]

    @classmethod
    def capture(
        cls,
        container: Mapping[str, Any],
        member: str,
        selected: Mapping[str, Any],
    ) -> OptimisticMappingMemberPatch:
        return cls(
            member=member,
            before_present=member in container,
            before=copy.deepcopy(container.get(member)),
            selected=copy.deepcopy(dict(selected)),
        )

    def restore(self, container: MutableMapping[str, Any]) -> bool:
        current = container.get(self.member, _MISSING)
        if isinstance(self.before, Mapping):
            if not isinstance(current, MutableMapping):
                return False
            return OptimisticMappingPatch.capture(self.before, self.selected).restore(current)
        if current != self.selected:
            return False
        if self.before_present:
            container[self.member] = copy.deepcopy(self.before)
        else:
            container.pop(self.member, None)
        return True


class SelectedImageArtifactReceipt:
    """Undo media, version pointer, metadata, and Manifest as one guarded unit."""

    def __init__(
        self,
        *,
        versions: VersionManager,
        resource_type: str,
        resource_id: str,
        version: int,
        current_file: Path,
        manifest: ArtifactRegistrationReceipt,
        compensate_metadata: Callable[[Callable[[], None]], None],
    ) -> None:
        self._versions = versions
        self._resource_type = resource_type
        self._resource_id = resource_id
        self._version = version
        self._current_file = current_file
        self._manifest = manifest
        self._compensate_metadata = compensate_metadata

    def compensate_cancelled(self) -> None:
        def _compensate_sidecars() -> None:
            self._compensate_metadata(self._manifest.compensate_cancelled)

        restored = self._versions.reject_current_version(
            self._resource_type,
            self._resource_id,
            rejected_version=self._version,
            current_file=self._current_file,
            on_reject=_compensate_sidecars,
        )
        if not restored and self._versions.get_current_version(self._resource_type, self._resource_id) == self._version:
            raise RuntimeError("image artifact remains selected after compensation")


def reject_failed_image_selection(
    *,
    versions: VersionManager,
    resource_type: str,
    resource_id: str,
    version: int,
    current_file: Path,
) -> None:
    """Reject a generated selection whose metadata/Manifest finalization failed."""

    restored = versions.reject_current_version(
        resource_type,
        resource_id,
        rejected_version=version,
        current_file=current_file,
    )
    if not restored and versions.get_current_version(resource_type, resource_id) == version:
        raise RuntimeError("failed image artifact remains selected after compensation")


__all__ = [
    "OptimisticMappingMemberPatch",
    "OptimisticMappingPatch",
    "SelectedImageArtifactReceipt",
    "reject_failed_image_selection",
]
