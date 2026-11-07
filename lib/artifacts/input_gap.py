"""Generation-input gaps and their absent records in artifact bases.

Gaps block generation actions, not basis projection (``docs/adr/0098``). Construct expected bases from
available inputs, recording missing items in ``absent``. Omit the key without gaps: bases are byte-for-byte
identical to complete-input shapes, leaving registered digests unchanged. Filling gaps changes bases,
outdating registered artifacts normally."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass

from lib.project.project_schema import parse_project_schema_version

#: Basis key recording absent inputs.
ABSENT_INPUTS_FIELD = "absent"

#: Generation gaps stop blocking basis projection from this schema onward.
GAP_TOLERANT_BASIS_SCHEMA_VERSION = 18


def project_basis_records_gaps(project: Mapping[str, object]) -> bool:
    """Whether expected project bases record generation gaps as absent.

    Projects below schema 18 occur only within migration chains. Full activation/provenance backfills
    before v17→v18 retain the rule that gaps prevent projection/registration. v17→v18 plans before/after
    targets and backfills artifacts previously unregistered due to gaps."""

    return parse_project_schema_version(project) >= GAP_TOLERANT_BASIS_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class InputGap:
    """One gap invalidating a generation input.

    ``name`` identifies its subject: the written script reference name for reference gaps, or the target's
    own resource ID for semantic gaps."""

    code: str
    asset_type: str | None = None
    name: str | None = None

    def evidence(self) -> dict[str, object]:
        """Absent record: gap code and its subject."""

        evidence: dict[str, object] = {"code": self.code}
        if self.asset_type is not None:
            evidence["type"] = self.asset_type
        if self.name is not None:
            evidence["id"] = self.name
        return evidence


def add_absent_inputs(inputs: MutableMapping[str, object], gaps: Sequence[InputGap]) -> None:
    """Record absent inputs in basis only when gaps exist, deduplicating items in gap order."""

    if gaps:
        inputs[ABSENT_INPUTS_FIELD] = [gap.evidence() for gap in dict.fromkeys(gaps)]


__all__ = [
    "ABSENT_INPUTS_FIELD",
    "GAP_TOLERANT_BASIS_SCHEMA_VERSION",
    "InputGap",
    "add_absent_inputs",
    "project_basis_records_gaps",
]
