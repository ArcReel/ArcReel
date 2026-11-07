"""Reference upload-copy compression.

The single convergence point for all I2I / I2V / R2V generation (``MediaGenerator`` throat layer)
processes **reference upload copies**, temporary bytes deleted after sending, before embedding
them as base64 request content: uniform JPEG q92 + 4:4:4, baseline scaling + conditional passthrough,
role-specific treatment (multi-image arrays use the full ladder; first/last frames reencode only
when over byte budgets, never resize), aggregate preflight downshifts past conservative generic
limits, and passive 413 fallback continues down the ladder.

Only upload copies change; source assets and generation outputs retain full quality (4K outputs
stay 4K). Build on ``lib.infra.image_utils.compress_image_bytes``, separating pure functions
from disk writes for unit testing.

Design: ``docs/adr/0012-reference-compression-conservative-ceiling.md``.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path

from PIL import Image

from lib.infra.image_utils import compress_image_bytes

# ── Conservative generic limits (ArcReel safety-policy constants, not claims of provider limits; passive 413 self-corrects) ──
# Match lib/config/service.py _DEFAULT_REFERENCE_*_MAX_BYTES values (asserted by unit tests).
DEFAULT_TOTAL_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_SINGLE_MAX_BYTES = 4 * 1024 * 1024

# Downshift ladder prioritizes resolution, with quality floor q80. Step 0 = baseline (2048,q92); step >= len uses the floor.
_LADDER: tuple[tuple[int, int], ...] = (
    (2048, 92),
    (1536, 90),
    (1280, 90),
    (1024, 88),
)
_FLOOR: tuple[int, int] = (1024, 80)

# Uniform 4:4:4 chroma sampling preserves text-sheet edges, avoiding 4:2:0 color bleeding around text.
_SUBSAMPLING_444 = 0
_BASELINE_QUALITY = 92
# FRAME never resizes: an edge limit far above any real image prevents compress_image_bytes from resizing.
_FRAME_NO_RESIZE_EDGE = 1_000_000

# Conditional step-0 passthrough: existing JPEG below this threshold and within dimensions passes unchanged, avoiding recompression of reasonably small images.
_PASSTHROUGH_MAX_BYTES = 1 * 1024 * 1024

# Number of downshift steps excluding the floor; passive throat-layer 413 retries use it to determine floor exhaustion.
LADDER_STEPS = len(_LADDER)


class RefRole(Enum):
    """Reference-image role determining compression strategy."""

    ARRAY = "array"  # Multi-image reference arrays: full baseline, downshift ladder, and byte budget.
    FRAME = "frame"  # First/last frames: reencode only over byte limits; never resize (protect Sora pixel matching).


@dataclass(frozen=True)
class PayloadLimits:
    """Request-body limits, overridable per provider; default to conservative generic limits."""

    total_max_bytes: int = DEFAULT_TOTAL_MAX_BYTES
    single_max_bytes: int = DEFAULT_SINGLE_MAX_BYTES


@dataclass
class ReferenceSpec:
    """Source of a reference image to compress."""

    source: Path
    role: RefRole
    # Sent as provider asset references, not uploaded image content: no compression or byte-budget participation; pass original paths through to preserve positions.
    provider_asset: bool = False


@dataclass
class CompressedRef:
    """Compressed or passed-through reference image used by the throat layer to construct requests.

    path points to a temporary file deleted after sending, or the original source path on passthrough.
    """

    path: Path
    role: RefRole


class ReferencePayloadFloorError(RuntimeError):
    """User-visible hard error when references still exceed limits at the compression floor.

    Matches ``lib.backends.image_backends.base.ImageCapabilityError`` structure (``.code`` +
    ``.params`` for route-layer ``_t(code, **params)``), but defaults ``code`` because this exception
    has only one. Preflight floor overflow and exhausted passive 413 retries produce that same code.
    """

    def __init__(self, code: str = "ref_payload_floor_exceeded", **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


def _ladder_params(step: int) -> tuple[int, int]:
    """Return (longest-edge limit, quality) for a step; out-of-range steps use the floor."""
    if 0 <= step < LADDER_STEPS:
        return _LADDER[step]
    return _FLOOR


def _image_meta(raw: bytes) -> tuple[str, int]:
    """Decode (uppercase format, longest-edge pixels); callers must ensure raw is decodable."""
    with Image.open(BytesIO(raw)) as img:
        fmt = (img.format or "").upper()
        w, h = img.size
        return fmt, max(w, h)


def compress_single_at_step(
    raw: bytes,
    role: RefRole,
    step: int,
    *,
    single_max_bytes: int,
) -> bytes:
    """Compress one reference by role/step, returning JPEG or unchanged passthrough bytes.

    - FRAME: never resize; reencode JPEG q92/4:4:4 **only above the per-image budget**, otherwise
      pass through unchanged. Never force conversions based on format (PNG etc.), maximizing
      first/last-frame pixel preservation (ADR: do not worsen Sora pixel matching).
    - ARRAY: reencode JPEG/4:4:4 at the ``step`` ladder (edge, q). At step==0, pass through only
      if already JPEG, below the passthrough threshold, and within dimension limits.
    """
    if role is RefRole.FRAME:
        if len(raw) <= single_max_bytes:
            return raw
        return compress_image_bytes(
            raw,
            max_long_edge=_FRAME_NO_RESIZE_EDGE,
            quality=_BASELINE_QUALITY,
            subsampling=_SUBSAMPLING_444,
        )

    edge, quality = _ladder_params(step)
    if step == 0 and len(raw) <= _PASSTHROUGH_MAX_BYTES:
        fmt, long_edge = _image_meta(raw)
        if fmt == "JPEG" and long_edge <= edge:
            return raw
    return compress_image_bytes(
        raw,
        max_long_edge=edge,
        quality=quality,
        subsampling=_SUBSAMPLING_444,
    )


def select_ladder_step(
    raws: list[bytes],
    roles: list[RefRole],
    limits: PayloadLimits,
    *,
    start_step: int = 0,
) -> tuple[int, list[bytes]]:
    """Downshift from start_step until aggregate ≤ total and each image ≤ single, or the floor.

    When one image exceeds the total budget, its effective limit is min(single, total).

    Returns:
        (landed_step, compressed_bytes). landed_step is the **actual final step** (≥ start_step);
        byte-limit preflight may downshift beyond start_step, and passive throat-layer 413 retries
        must continue from that actual step. Preserve order/count: returned lists correspond 1:1
        to input raws, never dropping images.

    Raises:
        ReferencePayloadFloorError: Limits still exceeded at the floor.
    """
    effective_single = min(limits.single_max_bytes, limits.total_max_bytes)
    step = max(start_step, 0)
    while True:
        compressed = [
            compress_single_at_step(raw, role, step, single_max_bytes=effective_single)
            for raw, role in zip(raws, roles, strict=True)
        ]
        total = sum(len(b) for b in compressed)
        # The single-image hard gate applies only to ARRAY: FRAME never resizes, so downshifting is a no-op;
        # a frame over single must not floor preflight before provider invocation (conservative limits are estimates; defer to passive 413 fallback).
        single_ok = all(
            len(b) <= effective_single for b, role in zip(compressed, roles, strict=True) if role is RefRole.ARRAY
        )
        if total <= limits.total_max_bytes and single_ok:
            return step, compressed
        if step >= LADDER_STEPS:
            raise ReferencePayloadFloorError()
        step += 1


def _try_read_image(source: Path) -> bytes | None:
    """Read local decodable image bytes; return None for nonlocal/unreadable/undecodable inputs, leaving passthrough handling.

    Fully decode with ``load()``, not ``verify()``: the latter admits truncated images with valid
    headers but incomplete pixels. Later full decoding in ``compress_image_bytes`` would then let
    ValueError escape the throat layer, violating the invariant that compression is an optimization
    and must not newly fail otherwise working calls. Pass truncated/undecodable source paths back to backends.
    """
    try:
        if not source.is_file():
            return None
        raw = source.read_bytes()
        with Image.open(BytesIO(raw)) as img:
            img.load()
        return raw
    except Exception:
        return None


@contextlib.contextmanager
def compressed_reference_payload(
    specs: list[ReferenceSpec],
    *,
    limits: PayloadLimits,
    start_step: int = 0,
) -> Generator[tuple[int, list[CompressedRef]]]:
    """Compress references into temporary files, yield (landed_step, refs), and clean up on exit.

    Nonlocal/undecodable sources skip compression and pass original paths through, never raising:
    theoretically impossible because backends read reference bytes, proving they are local files,
    but URL/data-URI/corrupt-file edge cases exist. Compression is an optimization and must not
    introduce failures into otherwise working calls.

    Passthrough entries do not enter select_ladder_step byte budgets because their bytes cannot
    be read/measured. The ladder operates only on compressible subsets; merge passthrough entries
    and compressed temporary files into original spec order, preserving ARRAY 1:1 counts and [图N] alignment.

    Entries passed through (compress returns the identical input object without reencoding) always
    use **original source paths**, without temporary copies: save copying, avoid PNG bytes under
    .jpg suffixes causing MIME mismatches, and maximize FRAME pixel-matching fidelity. Reencoded
    entries use temporary files retaining **source stems** (e.g. ``张三.jpg``), solely for readable
    logs/directories. Prompt-order “图N” markers identify references; backends infer no names from filenames.

    Yield landed_step as select_ladder_step’s actual final step; passive 413 retries must continue
    from it to avoid off-by-step errors.
    """
    compressible_indices: list[int] = []
    compressible_raws: list[bytes] = []
    compressible_roles: list[RefRole] = []
    for i, spec in enumerate(specs):
        raw = None if spec.provider_asset else _try_read_image(spec.source)
        if raw is None:
            continue
        compressible_indices.append(i)
        compressible_raws.append(raw)
        compressible_roles.append(spec.role)

    landed_step, compressed_bytes = select_ladder_step(
        compressible_raws,
        compressible_roles,
        limits,
        start_step=start_step,
    )

    temp_root: Path | None = None
    try:
        by_index: dict[int, CompressedRef] = {}
        for k, (idx, data) in enumerate(zip(compressible_indices, compressed_bytes, strict=True)):
            spec = specs[idx]
            if data is compressible_raws[k]:
                # Passthrough: no reencoding; use original source paths without temporary copies.
                by_index[idx] = CompressedRef(path=Path(spec.source), role=spec.role)
                continue
            # Reencoded: write temporary files under per-idx directories to avoid collisions; retain source stems for reference names.
            if temp_root is None:
                temp_root = Path(tempfile.mkdtemp(prefix="refcomp-"))
            sub = temp_root / str(idx)
            sub.mkdir()
            tmp_path = sub / f"{Path(spec.source).stem}.jpg"
            tmp_path.write_bytes(data)
            by_index[idx] = CompressedRef(path=tmp_path, role=spec.role)

        merged: list[CompressedRef] = []
        for i, spec in enumerate(specs):
            ref = by_index.get(i)
            if ref is None:
                # Nonlocal/undecodable passthrough: preserve original paths and return to backends for existing behavior.
                ref = CompressedRef(path=Path(spec.source), role=spec.role)
            merged.append(ref)

        yield landed_step, merged
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)  # hard-delete: temp -- Reference-compression temp directory
