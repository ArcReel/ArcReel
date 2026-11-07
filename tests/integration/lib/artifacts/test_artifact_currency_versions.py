"""Per-episode plans built by one comparer share parsed version history only when the bytes read are identical.

Each test first compares episode one so the comparer parses version history, then changes
``versions/versions.json`` before comparing episode two. Its plan must see the modified file,
never reuse the parsing result left by episode one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.project.resource_paths import resource_relative_path
from tests.integration.lib.episode_media_support import episodes_with_media
from tests.integration.lib.manifest_parse_support import count_versions_parses
from tests.integration.lib.workflow.test_workflow_state import _make_project


def _compare_video(resolver: ArtifactCurrencyResolver, episode: int) -> ArtifactStatus:
    resource_id = f"E{episode}S01"
    return resolver.compare(
        ArtifactKey.episode_video(episode, resource_id),
        artifact_path=resource_relative_path("videos", resource_id),
    ).status


@pytest.fixture
def two_episode_project(tmp_path: Path) -> Path:
    pm, project_path = _make_project(tmp_path, "narration")
    episodes_with_media(pm, project_path, 2)
    return project_path


@pytest.fixture
def resolver_after_first_episode(two_episode_project: Path) -> ArtifactCurrencyResolver:
    resolver = ArtifactCurrencyResolver(two_episode_project)
    assert _compare_video(resolver, 1) is ArtifactStatus.CURRENT
    return resolver


def test_unchanged_versions_are_parsed_once_across_episodes(
    two_episode_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions_parses = count_versions_parses(monkeypatch)
    resolver = ArtifactCurrencyResolver(two_episode_project)

    assert [_compare_video(resolver, episode) for episode in (1, 2)] == [ArtifactStatus.CURRENT] * 2
    assert versions_parses["parses"] == 1


def test_replaced_versions_reach_the_next_episode(
    two_episode_project: Path, resolver_after_first_episode: ArtifactCurrencyResolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions_path = two_episode_project / "versions" / "versions.json"
    versions = json.loads(versions_path.read_text(encoding="utf-8"))
    del versions["videos"]["E2S01"]
    versions_path.write_text(json.dumps(versions), encoding="utf-8")
    versions_parses = count_versions_parses(monkeypatch)

    # New content removed the second episode video’s version record; missing execution facts mean stale.
    assert _compare_video(resolver_after_first_episode, 2) is ArtifactStatus.STALE
    assert versions_parses["parses"] == 1


def test_deleted_versions_read_as_empty_for_the_next_episode(
    two_episode_project: Path, resolver_after_first_episode: ArtifactCurrencyResolver
) -> None:
    (two_episode_project / "versions" / "versions.json").unlink()

    assert _compare_video(resolver_after_first_episode, 2) is ArtifactStatus.STALE


def test_corrupt_versions_are_refused_for_the_next_episode(
    two_episode_project: Path, resolver_after_first_episode: ArtifactCurrencyResolver
) -> None:
    (two_episode_project / "versions" / "versions.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="version metadata"):
        _compare_video(resolver_after_first_episode, 2)


def test_blocked_versions_are_refused_for_the_next_episode(
    two_episode_project: Path, resolver_after_first_episode: ArtifactCurrencyResolver
) -> None:
    versions_path = two_episode_project / "versions" / "versions.json"
    real_path = versions_path.with_name("versions.real.json")
    versions_path.rename(real_path)
    versions_path.symlink_to(real_path.name)

    with pytest.raises(ArtifactManifestError):
        _compare_video(resolver_after_first_episode, 2)
