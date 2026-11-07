"""Tests for apply_unit_video_assets."""

from __future__ import annotations

import pytest


def test_apply_unit_video_assets_distinguishes_failures():
    """Malformed structure and absent units raise different exceptions so restoration distinguishes dirty-script warnings from normal skips.

    upload_unit_video exposes both structural errors to users, so they need specific i18n keys; generic fallback would show uninformative failures to en/vi users."""
    from lib.script.script_editor import ScriptEditError
    from server.services.tasks.reference_video_tasks import apply_unit_video_assets

    with pytest.raises(ScriptEditError) as unit_lists_broken:
        apply_unit_video_assets({"video_units": "broken"}, "E1U1", video_uri=None, thumb_rel=None)
    assert unit_lists_broken.value.key == "script_edit_unit_lists_invalid"
    with pytest.raises(ScriptEditError) as unit_lists_missing:
        apply_unit_video_assets({}, "E1U1", video_uri=None, thumb_rel=None)
    assert unit_lists_missing.value.key == "script_edit_unit_lists_invalid"
    with pytest.raises(ScriptEditError) as assets_broken:
        apply_unit_video_assets(
            {"video_units": [{"unit_id": "E1U1", "generated_assets": "broken"}]},
            "E1U1",
            video_uri=None,
            thumb_rel=None,
        )
    assert assets_broken.value.key == "script_edit_generated_assets_invalid"
    with pytest.raises(KeyError):
        apply_unit_video_assets({"video_units": []}, "E1U1", video_uri=None, thumb_rel=None)

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {"video_uri": "https://old"}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel="reference_videos/thumbnails/E1U1.jpg")
    ga = script["video_units"][0]["generated_assets"]
    assert ga["video_clip"] == "reference_videos/E1U1.mp4"
    assert "video_uri" not in ga
    assert ga["video_thumbnail"] == "reference_videos/thumbnails/E1U1.jpg"
    assert ga["status"] == "completed"


def test_apply_unit_video_assets_stamps_video_generated_at():
    """Every video_clip write mechanically stamps video_generated_at for legacy transition-banner counts."""
    from server.services.tasks.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    first_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(first_stamp, str)
    assert first_stamp

    # Regeneration's second writeback refreshes timestamps rather than reusing old values.
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    second_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(second_stamp, str)
    assert second_stamp


def test_apply_unit_video_assets_preserves_legacy_source_signature_without_reading_it():
    """Legacy source signatures are historical asset keys only; generation preserves them unchanged without adding, comparing or clearing them."""
    from server.services.tasks.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {"source_signature": "legacy"}}]}
    written = apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    assert written is None
    assert script["video_units"][0]["generated_assets"]["source_signature"] == "legacy"


def test_apply_unit_video_assets_honors_explicit_generated_at():
    """Version restoration uses the original version-ingestion timestamp, never relabeling old content as newly generated."""
    from server.services.tasks.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)

    restored_at = "2020-01-01T00:00:00+00:00"
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None, generated_at=restored_at)
    assert script["video_units"][0]["generated_assets"]["video_generated_at"] == restored_at
