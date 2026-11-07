"""Unit tests for lib.references.reference_compression.

Like tests/test_image_utils.py, generate image bytes with PIL to verify role-based compression,
quality ladders, tempfile lifecycles, and regression-safe passthrough.
"""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from lib.references.reference_compression import (
    DEFAULT_SINGLE_MAX_BYTES,
    DEFAULT_TOTAL_MAX_BYTES,
    LADDER_STEPS,
    CompressedRef,
    PayloadLimits,
    ReferencePayloadFloorError,
    ReferenceSpec,
    RefRole,
    compress_single_at_step,
    compressed_reference_payload,
    select_ladder_step,
)


def _noise_jpeg_bytes(w: int, h: int) -> bytes:
    """High-entropy noise JPEG, whose compressed size varies with resolution."""
    img = Image.effect_noise((w, h), 80).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _noise_png_bytes(w: int, h: int) -> bytes:
    img = Image.effect_noise((w, h), 80).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solid_jpeg_bytes(w: int, h: int, color=(200, 100, 50)) -> bytes:
    img = Image.new("RGB", (w, h), color=color)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _solid_png_bytes(w: int, h: int, color=(200, 100, 50)) -> bytes:
    img = Image.new("RGB", (w, h), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _dims(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as img:
        return img.size


def _fmt(data: bytes) -> str:
    with Image.open(BytesIO(data)) as img:
        return (img.format or "").upper()


# ── Constant consistency ─────────────────────────────────────────────────


def test_default_constants_match_service():
    from lib.config import service

    assert DEFAULT_TOTAL_MAX_BYTES == service.DEFAULT_REFERENCE_TOTAL_MAX_BYTES
    assert DEFAULT_SINGLE_MAX_BYTES == service.DEFAULT_REFERENCE_SINGLE_MAX_BYTES


# ── compress_single_at_step ─────────────────────────────────────────────────


def test_array_step0_scales_to_2048():
    raw = _noise_jpeg_bytes(3000, 3000)
    out = compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert max(_dims(out)) == 2048
    assert _fmt(out) == "JPEG"


def test_array_floor_step_scales_to_1024():
    raw = _noise_jpeg_bytes(3000, 3000)
    out = compress_single_at_step(raw, RefRole.ARRAY, LADDER_STEPS, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert max(_dims(out)) == 1024


def test_array_step0_passthrough_small_jpeg():
    raw = _solid_jpeg_bytes(800, 600)  # Already JPEG, small, and dimension-compliant.
    out = compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out is raw  # Pass through unchanged without recompression.


def test_array_step0_png_is_reencoded():
    raw = _solid_png_bytes(800, 600)  # Heavy format → no passthrough.
    out = compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out != raw
    assert _fmt(out) == "JPEG"


def test_frame_never_resizes_even_above_2048():
    # Heavy formats reencode, but FRAME never resizes: output stays 3000x2000.
    raw = _noise_png_bytes(3000, 2000)
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert _dims(out) == (3000, 2000)
    assert _fmt(out) == "JPEG"


def test_frame_small_jpeg_passthrough():
    raw = _solid_jpeg_bytes(1200, 800)
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out is raw


def test_frame_small_heavy_format_passthrough():
    # Heavy PNG within single budget → FRAME passthrough preserves first/last pixels (no-worse Sora ADR),
    # without forced JPEG conversion, unlike ARRAY.
    raw = _solid_png_bytes(1200, 800)
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out is raw


def test_frame_over_single_budget_reencoded_no_resize():
    raw = _solid_jpeg_bytes(2048, 2048)
    # Tiny single_max → reencode only over budget, still without resizing.
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=1)
    assert _dims(out) == (2048, 2048)


# ── select_ladder_step ──────────────────────────────────────────────────────


def test_ladder_fits_at_step0():
    raws = [_solid_jpeg_bytes(512, 512)]
    landed, out = select_ladder_step(raws, [RefRole.ARRAY], PayloadLimits(), start_step=0)
    assert landed == 0
    assert len(out) == 1


def test_ladder_downgrades_when_over_total():
    raw = _noise_jpeg_bytes(2048, 2048)
    size0 = len(compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES))
    # total below step0 size → at least one ladder downgrade required.
    limits = PayloadLimits(total_max_bytes=size0 - 1, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    landed, out = select_ladder_step([raw], [RefRole.ARRAY], limits, start_step=0)
    assert landed >= 1
    assert sum(len(b) for b in out) <= limits.total_max_bytes


def test_ladder_floor_still_over_raises():
    raw = _noise_jpeg_bytes(2048, 2048)
    limits = PayloadLimits(total_max_bytes=1, single_max_bytes=1)
    with pytest.raises(ReferencePayloadFloorError):
        select_ladder_step([raw], [RefRole.ARRAY], limits, start_step=0)


def test_ladder_floor_error_has_code():
    err = ReferencePayloadFloorError()
    assert err.code == "ref_payload_floor_exceeded"
    assert err.params == {}


def test_ladder_single_over_budget_raises_floor():
    raw = _noise_jpeg_bytes(2048, 2048)
    # Ample total but single=1 → effective_single=1; floor still exceeds it → raise floor.
    limits = PayloadLimits(total_max_bytes=DEFAULT_TOTAL_MAX_BYTES, single_max_bytes=1)
    with pytest.raises(ReferencePayloadFloorError):
        select_ladder_step([raw], [RefRole.ARRAY], limits, start_step=0)


def test_ladder_frame_over_single_does_not_floor():
    # Large FRAME exceeds single but not total: excluded from single hard gating, so no pre-provider floor;
    # passive 413 remains fallback. Within total, allow reencoding without resizing or raising.
    raw = _noise_jpeg_bytes(2048, 2048)
    single = len(raw) - 1  # Force above single → FRAME reencodes without flooring.
    limits = PayloadLimits(total_max_bytes=DEFAULT_TOTAL_MAX_BYTES, single_max_bytes=single)
    landed, out = select_ladder_step([raw], [RefRole.FRAME], limits, start_step=0)
    assert landed == 0
    assert len(out) == 1
    assert _dims(out[0]) == (2048, 2048)  # Never resize.


def test_ladder_frame_over_total_still_floors():
    # FRAME exceeds even total → still floor, genuinely excessive data.
    raw = _noise_jpeg_bytes(2048, 2048)
    limits = PayloadLimits(total_max_bytes=1, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    with pytest.raises(ReferencePayloadFloorError):
        select_ladder_step([raw], [RefRole.FRAME], limits, start_step=0)


def test_ladder_start_step_respected():
    raws = [_solid_jpeg_bytes(512, 512)]
    landed, _ = select_ladder_step(raws, [RefRole.ARRAY], PayloadLimits(), start_step=2)
    assert landed >= 2
    assert landed == 2  # Small prompt_authoring images already comply, without further compression.


def test_ladder_empty_input():
    landed, out = select_ladder_step([], [], PayloadLimits(), start_step=3)
    assert landed == 3
    assert out == []


# ── compressed_reference_payload ────────────────────────────────────────────


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_payload_writes_and_cleans_tempfiles(tmp_path: Path):
    a = _write(tmp_path, "a.png", _solid_png_bytes(800, 600))
    b = _write(tmp_path, "b.png", _solid_png_bytes(800, 600))
    specs = [
        ReferenceSpec(source=a, role=RefRole.ARRAY),
        ReferenceSpec(source=b, role=RefRole.ARRAY),
    ]
    written: list[Path] = []
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (landed, refs):
        assert landed == 0
        assert [r.role for r in refs] == [RefRole.ARRAY, RefRole.ARRAY]
        assert all(isinstance(r, CompressedRef) for r in refs)
        for r in refs:
            assert r.path.exists()
            written.append(r.path)
    # Clean temporary files on exit.
    for p in written:
        assert not p.exists()
    # Source stays unchanged.
    assert a.exists()
    assert b.exists()


def test_payload_preserves_order_and_count(tmp_path: Path):
    paths = [_write(tmp_path, f"r{i}.png", _solid_png_bytes(400, 400)) for i in range(5)]
    specs = [ReferenceSpec(source=p, role=RefRole.ARRAY) for p in paths]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert len(refs) == 5
        assert [r.path.stem for r in refs] == [f"r{i}" for i in range(5)]


def test_payload_empty_noop():
    with compressed_reference_payload([], limits=PayloadLimits(), start_step=2) as (landed, refs):
        assert landed == 2
        assert refs == []


def test_payload_passthrough_non_local_source_no_raise(tmp_path: Path):
    real = _write(tmp_path, "real.png", _solid_png_bytes(800, 600))
    missing = tmp_path / "does-not-exist.png"
    specs = [
        ReferenceSpec(source=real, role=RefRole.ARRAY),
        ReferenceSpec(source=missing, role=RefRole.ARRAY),
    ]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert len(refs) == 2
        # Passthrough preserves original paths/order, aligned with [图N].
        assert refs[1].path == missing
        # Compressible entries become temporary files.
        assert refs[0].path != real
        assert refs[0].path.exists()


def test_payload_passthrough_undecodable_source(tmp_path: Path):
    bad = _write(tmp_path, "bad.png", b"not an image at all")
    specs = [ReferenceSpec(source=bad, role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert len(refs) == 1
        assert refs[0].path == bad  # Undecodable input passes through its original path without raising.


def test_payload_passthrough_excluded_from_budget(tmp_path: Path):
    # Passthrough does not consume byte budget: tiny total never raises when the compressible subset is empty.
    missing = tmp_path / "missing.png"
    specs = [ReferenceSpec(source=missing, role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits(total_max_bytes=1, single_max_bytes=1)) as (
        _landed,
        refs,
    ):
        assert refs[0].path == missing


def test_payload_tempfiles_cleaned_on_floor_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Active floor preflight occurs in __enter__, and select_ladder_step precedes tempfile writing; no leak.
    # Test-private temporary root: shared system tempdirs may receive refcomp-* from parallel tests/other worktrees.
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    real = _write(tmp_path, "big.jpg", _noise_jpeg_bytes(2048, 2048))
    specs = [ReferenceSpec(source=real, role=RefRole.ARRAY)]
    with (
        pytest.raises(ReferencePayloadFloorError),
        compressed_reference_payload(specs, limits=PayloadLimits(total_max_bytes=1, single_max_bytes=1)) as (
            _landed,
            _refs,
        ),
    ):
        pass
    assert list(temp_root.glob("refcomp-*")) == []


def test_payload_reencoded_tempfile_preserves_source_stem(tmp_path: Path):
    # Reencoded copies retain source stems (e.g. 张三), allowing name-based tracing inside temporary directories.
    src = _write(tmp_path, "张三.png", _solid_png_bytes(800, 600))
    specs = [ReferenceSpec(source=src, role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path != src  # Temporary copy.
        assert refs[0].path.stem == "张三"  # Retains source stem.
        assert refs[0].path.suffix == ".jpg"


def test_payload_passthrough_uses_original_path_not_copy(tmp_path: Path):
    # Small JPEG ARRAY passes original path without copying: lower overhead, no suffix mismatch, maximum fidelity.
    src = _write(tmp_path, "small.jpg", _solid_jpeg_bytes(600, 400))
    specs = [ReferenceSpec(source=src, role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path == src


def test_payload_frame_under_budget_uses_original_path(tmp_path: Path):
    # Within-budget FRAME passes original paths including PNG, without forced JPEG, preserving first/last pixels.
    src = _write(tmp_path, "frame.png", _solid_png_bytes(1000, 1600))
    specs = [ReferenceSpec(source=src, role=RefRole.FRAME)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path == src


def test_payload_truncated_image_passthrough_no_raise(tmp_path: Path):
    # Truncated JPEG passes verify() but fails load(): pass through, never leaking compress ValueError.
    full = _solid_jpeg_bytes(1200, 900)
    truncated = full[: len(full) // 2]
    src = _write(tmp_path, "truncated.jpg", truncated)
    specs = [ReferenceSpec(source=src, role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path == src  # Pass through without raising.
