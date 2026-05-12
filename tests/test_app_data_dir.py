"""Tests for lib.app_data_dir env resolution."""

from __future__ import annotations

from pathlib import Path

import pytest


def _import_fresh():
    """Re-import the module fresh — `@functools.cache` traps env values."""
    from lib.app_data_dir import _reset_for_tests, app_data_dir

    _reset_for_tests()
    return app_data_dir


@pytest.fixture()
def app_data_dir_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ARCREEL_DATA_DIR", raising=False)
    monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
    return _import_fresh()


def test_default_returns_project_root_projects(app_data_dir_fn):
    from lib.env_init import PROJECT_ROOT

    result = app_data_dir_fn()
    assert result == (PROJECT_ROOT / "projects").resolve()
    assert result.exists()


def test_absolute_arcreel_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "custom-data"
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(target))
    monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
    app_data_dir_fn = _import_fresh()

    result = app_data_dir_fn()
    assert result == target.resolve()
    assert result.exists()


def test_relative_arcreel_data_dir_resolved_against_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from lib.env_init import PROJECT_ROOT

    # Use a relative path; should be joined to PROJECT_ROOT.
    rel_name = "test_relative_data_dir_xyz"
    monkeypatch.setenv("ARCREEL_DATA_DIR", rel_name)
    monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
    app_data_dir_fn = _import_fresh()

    try:
        result = app_data_dir_fn()
        expected = (PROJECT_ROOT / rel_name).resolve()
        assert result == expected
        assert result.exists()
    finally:
        # Cleanup: remove the dir we accidentally created in repo root.
        created = PROJECT_ROOT / rel_name
        if created.exists():
            created.rmdir()


def test_ai_anime_projects_legacy_alias_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "legacy-data"
    monkeypatch.delenv("ARCREEL_DATA_DIR", raising=False)
    monkeypatch.setenv("AI_ANIME_PROJECTS", str(target))
    app_data_dir_fn = _import_fresh()

    result = app_data_dir_fn()
    assert result == target.resolve()


def test_arcreel_data_dir_takes_precedence_over_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(primary))
    monkeypatch.setenv("AI_ANIME_PROJECTS", str(secondary))
    app_data_dir_fn = _import_fresh()

    result = app_data_dir_fn()
    assert result == primary.resolve()


def test_empty_env_value_falls_through_to_default(
    monkeypatch: pytest.MonkeyPatch,
):
    """`ARCREEL_DATA_DIR=` (empty) should not be treated as a path."""
    from lib.env_init import PROJECT_ROOT

    monkeypatch.setenv("ARCREEL_DATA_DIR", "")
    monkeypatch.setenv("AI_ANIME_PROJECTS", "   ")  # whitespace-only
    app_data_dir_fn = _import_fresh()

    result = app_data_dir_fn()
    assert result == (PROJECT_ROOT / "projects").resolve()


def test_directory_is_created_on_first_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "not-yet-created" / "nested" / "dir"
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(target))
    monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
    app_data_dir_fn = _import_fresh()

    assert not target.exists()
    result = app_data_dir_fn()
    assert result.exists()
    assert result.is_dir()


def test_result_is_cached_within_single_call_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Confirm the @functools.cache wrapper; changing env after first call has no effect."""
    target_a = tmp_path / "a"
    target_b = tmp_path / "b"
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(target_a))
    monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
    app_data_dir_fn = _import_fresh()

    first = app_data_dir_fn()
    # mutate env, but don't reset cache — same result expected.
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(target_b))
    second = app_data_dir_fn()
    assert first == second == target_a.resolve()
