# Grok Provider Multi-Issue Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four Grok provider issues: oversized upload images causing gRPC 4MB errors, image concurrency issues, only using the first reference image, and incorrect aspect ratio in drama mode.

**Architecture:** Compress user images at the upload entry point (JPEG + resolution limit), switch the Grok image backend to the multi-image editing API (`image_urls`) to simultaneously support multiple reference images and enable `aspect_ratio`, and add pool configuration logging in the worker to diagnose concurrency issues.

**Tech Stack:** Python, Pillow, xai_sdk, pytest

---

## File Map

| File | Action | Responsibility |
|------|------|------|
| `lib/image_utils.py` | Modify | Add `compress_image_bytes()` |
| `tests/test_image_utils.py` | Create | `compress_image_bytes()` unit tests |
| `server/routers/files.py` | Modify | Upload entry uses compression + `.jpg` extension |
| `lib/data_validator.py` | Modify | Add `style_reference.jpg` to `ALLOWED_ROOT_ENTRIES` |
| `server/services/project_archive.py` | Modify | `style_reference.png` → `.jpg` |
| `lib/image_backends/grok.py` | Modify | `image_url` → `image_urls` + aspect_ratio validation |
| `tests/test_image_backends/test_grok.py` | Modify | Adapt to `image_urls` |
| `lib/generation_worker.py` | Modify | Add pool configuration logging |

---

### Task 1: Image Compression Function — TDD

**Files:**
- Modify: `lib/image_utils.py`
- Create: `tests/test_image_utils.py`

- [ ] **Step 1: Write failing tests for `compress_image_bytes`**

```python
# tests/test_image_utils.py
"""Unit tests for image_utils."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lib.image_utils import compress_image_bytes


class TestCompressImageBytes:
    """Tests for compress_image_bytes."""

    def _make_png(self, width: int, height: int) -> bytes:
        """Generate PNG bytes of the specified size."""
        img = Image.new("RGB", (width, height), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_small_image_unchanged_dimensions(self):
        """Small images (long edge < 2048) are not resized, but still converted to JPEG."""
        raw = self._make_png(800, 600)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.size == (800, 600)

    def test_large_image_resized(self):
        """Large images (long edge > 2048) are scaled to long edge 2048."""
        raw = self._make_png(4096, 3072)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert max(img.size) == 2048
        # Proportional scaling
        assert img.size == (2048, 1536)

    def test_portrait_large_image(self):
        """Portrait large images are also scaled correctly."""
        raw = self._make_png(2000, 4000)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert max(img.size) == 2048
        assert img.size == (1024, 2048)

    def test_rgba_converted_to_rgb(self):
        """RGBA images are converted to RGB (JPEG does not support alpha)."""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = BytesIO()
        img.save(buf, format="PNG")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.mode == "RGB"

    def test_jpeg_input(self):
        """JPEG input can also be processed normally."""
        img = Image.new("RGB", (500, 500), color="blue")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=95)
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_webp_input(self):
        """WebP input can also be processed normally."""
        img = Image.new("RGB", (500, 500), color="green")
        buf = BytesIO()
        img.save(buf, format="WEBP")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_invalid_input_raises(self):
        """Non-image bytes raise ValueError."""
        with pytest.raises(ValueError, match="Invalid image"):
            compress_image_bytes(b"not an image")

    def test_output_smaller_than_input(self):
        """Compressed output should be significantly smaller."""
        raw = self._make_png(3000, 2000)
        result = compress_image_bytes(raw)
        assert len(result) < len(raw)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_image_utils.py -v`
Expected: FAIL — `ImportError: cannot import name 'compress_image_bytes'`

- [ ] **Step 3: Implement `compress_image_bytes`**

Add the following below the `convert_image_bytes_to_png` function in `lib/image_utils.py`:

```python
_MAX_LONG_EDGE = 2048
_JPEG_QUALITY = 85


def compress_image_bytes(
    content: bytes,
    *,
    max_long_edge: int = _MAX_LONG_EDGE,
    quality: int = _JPEG_QUALITY,
) -> bytes:
    """
    Compress arbitrary image bytes to JPEG: proportionally scale so the long edge
    does not exceed max_long_edge; quality controls the JPEG compression quality.

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with Image.open(BytesIO(content)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            w, h = img.size
            long_edge = max(w, h)
            if long_edge > max_long_edge:
                scale = max_long_edge / long_edge
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            out = BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception as e:
        raise ValueError("Invalid image") from e
```

Also confirm that `from io import BytesIO` is imported at the top of the file (already present).

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_image_utils.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/image_utils.py tests/test_image_utils.py
git commit -m "feat: add compress_image_bytes function with JPEG compression + resolution limit"
```

---

### Task 2: Upload Entry — Compress When Larger Than 2MB

**Files:**
- Modify: `server/routers/files.py:92-138` — general upload logic
- Modify: `server/routers/files.py:486-541` — style reference image upload
- Modify: `lib/data_validator.py:50` — ALLOWED_ROOT_ENTRIES
- Modify: `server/services/project_archive.py:492` — archive path

**Strategy**: When an uploaded image is > 2MB, compress to JPEG (`.jpg`); if ≤ 2MB, save original content directly (preserve original format extension).

- [ ] **Step 1: Modify the general upload logic**

In `server/routers/files.py`:

1. Import `compress_image_bytes`:

```python
from lib.image_utils import compress_image_bytes
```

2. Add threshold constant (in the constants section at the top of the file):

```python
_COMPRESS_THRESHOLD = 2 * 1024 * 1024  # 2MB
```

3. Replace the image processing section (original lines 132-138). Remove the `convert_image_bytes_to_png` call, and instead only compress when larger than 2MB:

```python
        content = await file.read()
        if upload_type in ("character", "character_ref", "clue", "storyboard"):
            if len(content) > _COMPRESS_THRESHOLD:
                try:
                    content = compress_image_bytes(content)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid image file, unable to parse")
                # Replace file extension with .jpg after compression
                filename = Path(filename).with_suffix(".jpg").name
```

Images ≤ 2MB are saved directly with original content; the filename extension retains the `.png` already in the branch above (i.e., existing logic unchanged).

- [ ] **Step 2: Modify the style reference image upload**

In the `upload_style_image` function in `server/routers/files.py`, apply the same 2MB threshold:

```python
        content = await file.read()
        if len(content) > _COMPRESS_THRESHOLD:
            try:
                content = compress_image_bytes(content)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid image file, unable to parse")
            style_filename = "style_reference.jpg"
        else:
            style_filename = f"style_reference{Path(file.filename).suffix.lower() or '.png'}"

        output_path = project_dir / style_filename
        with open(output_path, "wb") as f:
            f.write(content)
```

Subsequent `project_data["style_image"]` and return values use the `style_filename` variable.

Also update the `delete_style_image` function to try deleting both suffixes:

```python
        for suffix in (".jpg", ".png"):
            image_path = project_dir / f"style_reference{suffix}"
            if image_path.exists():
                image_path.unlink()
                break
```

- [ ] **Step 3: Update data_validator and project_archive**

`lib/data_validator.py` line 50 — add the `.jpg` variant to `ALLOWED_ROOT_ENTRIES` (keep `.png` for backward compatibility with old projects):

```python
    ALLOWED_ROOT_ENTRIES = {
        "project.json",
        "style_reference.png",
        "style_reference.jpg",
        "source",
        ...
    }
```

`server/services/project_archive.py` line 492 — the archive repair logic needs to handle both suffixes. Change `canonical_rel` to check for the actually existing file: check `.jpg` first, then `.png` if not found. If modifying `_repair_path_to_canonical` logic is too invasive, leave this file unchanged (old projects still use `.png`, and the `style_image` field in new projects already points to the actual file correctly).

- [ ] **Step 4: Run existing tests to confirm no regressions**

Run: `uv run python -m pytest tests/ -v -k "upload or style or archive or validator or fingerprint" --no-header`
Expected: all PASS (some tests may need to adapt to the extension changes)

- [ ] **Step 5: Commit**

```bash
git add server/routers/files.py lib/data_validator.py
git commit -m "feat: compress images larger than 2MB to JPEG + limit long edge to 2048px on upload"
```

---

### Task 3: Grok Image Backend — Multiple Reference Images + Ratio Validation

**Files:**
- Modify: `lib/image_backends/grok.py:52-86`
- Modify: `tests/test_image_backends/test_grok.py`

- [ ] **Step 1: Update tests — I2I switches to `image_urls`**

Modify the `TestGenerateI2I` class in `tests/test_image_backends/test_grok.py`:

```python
class TestGenerateI2I:
    async def test_i2i_sends_image_urls(self, backend, tmp_path):
        """I2I converts reference images to data URI list and passes to image_urls."""
        ref_image = tmp_path / "ref.png"
        ref_image.write_bytes(b"\x89PNG\r\n\x1a\nfake_png_data")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/edited.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="Make it darker",
                output_path=output,
                reference_images=[ReferenceImage(path=str(ref_image), label="base")],
            )
            result = await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert "image_urls" in call_kwargs
        assert "image_url" not in call_kwargs
        assert len(call_kwargs["image_urls"]) == 1
        assert call_kwargs["image_urls"][0].startswith("data:image/png;base64,")
        assert result.provider == "grok"

    async def test_i2i_multiple_refs(self, backend, tmp_path):
        """Multiple reference images are all passed via image_urls."""
        ref1 = tmp_path / "ref1.png"
        ref1.write_bytes(b"\x89PNG\r\n\x1a\nfake1")
        ref2 = tmp_path / "ref2.jpg"
        ref2.write_bytes(b"\xff\xd8\xff\xe0fake2")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/merged.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="Merge subjects",
                output_path=output,
                reference_images=[
                    ReferenceImage(path=str(ref1)),
                    ReferenceImage(path=str(ref2)),
                ],
            )
            await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert len(call_kwargs["image_urls"]) == 2

    async def test_i2i_skips_missing_ref(self, backend, tmp_path):
        """Falls back to T2I when reference image does not exist."""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/generated.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n"

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="A cat",
                output_path=output,
                reference_images=[ReferenceImage(path="/nonexistent/ref.png")],
            )
            await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert "image_urls" not in call_kwargs
        assert "image_url" not in call_kwargs
```

Add aspect_ratio validation tests:

```python
class TestAspectRatioValidation:
    def test_supported_ratios_pass_through(self):
        from lib.image_backends.grok import _validate_aspect_ratio

        for ratio in ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "2:1", "1:2", "auto"):
            assert _validate_aspect_ratio(ratio) == ratio

    def test_unsupported_ratio_passed_through_with_warning(self):
        from lib.image_backends.grok import _validate_aspect_ratio

        # Unsupported ratios are passed through to the API without mapping
        assert _validate_aspect_ratio("5:4") == "5:4"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_image_backends/test_grok.py -v`
Expected: FAIL — old tests assert `image_url` (singular), new tests assert `image_urls` (plural)

- [ ] **Step 3: Implement Grok image backend changes**

Replace the I2I logic in the `generate` method of `lib/image_backends/grok.py` and add the new validation function:

Add to the constants section at the top of the file (below `DEFAULT_MODEL`):

```python
_SUPPORTED_ASPECT_RATIOS = {
    "1:1",
    "16:9", "9:16",
    "4:3", "3:4",
    "3:2", "2:3",
    "2:1", "1:2",
    "19.5:9", "9:19.5",
    "20:9", "9:20",
    "auto",
}
```

Add validation function (before `_map_image_size_to_resolution`):

```python
def _validate_aspect_ratio(aspect_ratio: str) -> str:
    """Validates whether aspect_ratio is in the Grok supported list; warns and passes through if not."""
    if aspect_ratio not in _SUPPORTED_ASPECT_RATIOS:
        logger.warning("Grok may not support aspect_ratio=%s, passing through to API", aspect_ratio)
    return aspect_ratio
```

Replace the I2I section in the `generate` method (lines 52-86):

```python
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate image (T2I or I2I)."""
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
            "resolution": _map_image_size_to_resolution(request.image_size),
        }

        # I2I: convert all reference images to base64 data URI list
        if request.reference_images:
            data_uris = []
            for ref in request.reference_images:
                ref_path = Path(ref.path)
                if ref_path.exists():
                    data_uris.append(image_to_base64_data_uri(ref_path))
            if data_uris:
                generate_kwargs["image_urls"] = data_uris
                logger.info("Grok I2I mode: %d reference images", len(data_uris))

        logger.info("Grok image generation started: model=%s", self._model)
        response = await self._client.image.sample(**generate_kwargs)

        # Moderation check
        if not response.respect_moderation:
            raise RuntimeError("Grok image generation rejected by content moderation")

        # Download image locally
        await _download_image(response.url, request.output_path)

        logger.info("Grok image download complete: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            image_uri=response.url,
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_image_backends/test_grok.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/image_backends/grok.py tests/test_image_backends/test_grok.py
git commit -m "fix: Grok image backend uses image_urls for multiple reference images, fix I2I ratio being ignored"
```

---

### Task 4: Generation Worker — Add Pool Configuration Logging

**Files:**
- Modify: `lib/generation_worker.py:128-149`

- [ ] **Step 1: Add logging to `_load_pools_from_db`**

Add the following at the end of the `_load_pools_from_db` function (before return):

```python
    logger.info(
        "Loaded provider pool configuration from DB: %s",
        {pid: (p.image_max, p.video_max) for pid, p in pools.items()},
    )
    return pools
```

- [ ] **Step 2: Add initial pool logging in `__init__`**

Add the following after the `self._pools` assignment in `GenerationWorker.__init__` (after line 186):

```python
        logger.info(
            "Worker initial pool configuration: %s",
            {pid: (p.image_max, p.video_max) for pid, p in self._pools.items()},
        )
```

- [ ] **Step 3: Enhance logging in the fallback path of `_get_or_create_pool`**

The current warning log at line 241 already exists. Confirm that the warning log in `_get_or_create_pool` contains sufficient information (already satisfied, no changes needed).

- [ ] **Step 4: Run worker tests to confirm no regressions**

Run: `uv run python -m pytest tests/test_generation_worker_module.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/generation_worker.py
git commit -m "fix: add pool configuration logging to Generation Worker to help diagnose concurrency issues"
```

---

### Task 5: Lint + Full Test Suite

**Files:** No new changes

- [ ] **Step 1: Ruff lint + format**

Run: `uv run ruff check lib/image_utils.py lib/image_backends/grok.py lib/generation_worker.py server/routers/files.py lib/data_validator.py server/services/project_archive.py && uv run ruff format --check lib/image_utils.py lib/image_backends/grok.py lib/generation_worker.py server/routers/files.py lib/data_validator.py server/services/project_archive.py`

Expected: No errors. If any, fix and re-run.

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m pytest tests/ -v --no-header`

Expected: all PASS. If any failures, fix and re-run.

- [ ] **Step 3: Commit if fixes were made**

```bash
git add -A
git commit -m "chore: lint fixes"
```
