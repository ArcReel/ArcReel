from pathlib import Path

import pytest

from lib import image_reference_snapshot
from lib.visual_artifact_provenance import VisualReference


def test_freeze_image_references_rejects_a_group_changed_between_copies(tmp_path, monkeypatch):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first-a")
    second.write_bytes(b"second-a")
    references = [first, second]
    visuals = [
        VisualReference(path=first, role="asset_sheet", logical_type="character", logical_id="Alice"),
        VisualReference(path=second, role="asset_sheet", logical_type="character", logical_id="Bob"),
    ]
    original_copy = image_reference_snapshot.shutil.copyfile
    copy_count = 0

    def _copy_then_replace_next(source: Path, destination: Path) -> None:
        nonlocal copy_count
        original_copy(source, destination)
        copy_count += 1
        if copy_count == 1:
            second.write_bytes(b"second-b")

    monkeypatch.setattr(image_reference_snapshot.shutil, "copyfile", _copy_then_replace_next)

    with pytest.raises(OSError, match="reference images changed while they were frozen"):
        image_reference_snapshot.freeze_image_references(references, visuals)


def test_sent_view_keeps_the_leading_references_and_shares_the_snapshot_directory(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    visuals = [
        VisualReference(path=first, role="asset_sheet", logical_type="character", logical_id="Alice"),
        VisualReference(path=second, role="asset_sheet", logical_type="character", logical_id="Bob"),
    ]
    frozen = image_reference_snapshot.freeze_image_references([first, second], visuals)
    try:
        sent = frozen.sent(1)
        assert sent.reference_images == [frozen.reference_images[0]]
        assert sent.visual_references == frozen.visual_references[:1]
        assert frozen.sent(2) is frozen
        with pytest.raises(ValueError, match="within the frozen references"):
            frozen.sent(3)
        assert Path(sent.reference_images[0]).exists()
    finally:
        frozen.cleanup()
    assert not Path(sent.reference_images[0]).exists()


def test_sent_view_of_an_empty_snapshot_has_no_provider_references():
    frozen = image_reference_snapshot.freeze_image_references(None, ())
    assert frozen.sent(0) is frozen
    assert frozen.reference_images is None
