"""Single asset-image regeneration impacts only currently current artifacts whose generation inputs include that image.

The casting-project fixture is in adjacent ``conftest.py``."""

from __future__ import annotations

import pytest

from lib.project.project_manager import ProjectManager
from server.services.currency.asset_regeneration_impact import AssetRegenerationImpact, asset_regeneration_impact

#: Same casting-project name as ``conftest.py``.
PROJECT = "demo"


def test_counts_current_storyboards_videos_and_derivatives(current_cast_artifacts: ProjectManager) -> None:
    impact = asset_regeneration_impact(current_cast_artifacts, PROJECT, "character", "Alice")

    assert impact == AssetRegenerationImpact(stale=False, storyboards=1, videos=1, derivatives=1)


def test_an_unknown_asset_raises_key_error(cast_projects: ProjectManager) -> None:
    with pytest.raises(KeyError):
        asset_regeneration_impact(cast_projects, PROJECT, "scene", "不存在")


def test_derivative_impact_counts_only_its_compound_references(current_cast_artifacts: ProjectManager) -> None:
    impact = asset_regeneration_impact(current_cast_artifacts, PROJECT, "character", "Alice", derivative_name="战损")

    assert impact == AssetRegenerationImpact(stale=False, storyboards=1, videos=1, derivatives=0)


def test_reports_whether_the_sheet_itself_is_stale_now(current_cast_artifacts: ProjectManager) -> None:
    """UI uses this to request impact confirmation, independent of potentially outdated local state rows."""
    impact = asset_regeneration_impact(current_cast_artifacts, PROJECT, "character", "Alice", derivative_name="雨夜")

    assert impact.stale is True
