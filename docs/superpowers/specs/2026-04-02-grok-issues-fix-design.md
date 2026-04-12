# Grok Provider Multi-Issue Fix Design

## Background

The Grok provider has exposed four issues during actual usage, affecting image/video generation quality and experience:

1. User-uploaded character reference images/character images are too large; base64 encoding exceeds the gRPC 4MB limit, causing immediate errors during image/video generation
2. Grok image generation concurrency is actually only 1 (default should be 5); root cause needs investigation
3. When generating scene images, Grok only uses the first reference image; all other providers support multiple images
4. In drama mode, scene image generation uses portrait aspect ratio; it should be 16:9 landscape

## Issue 1: Upload Image Compression

### Current State

- `convert_image_bytes_to_png()` in `image_utils.py` only does format conversion (→ PNG), no compression/resizing
- `image_to_base64_data_uri()` directly reads raw file bytes for base64 encoding, no size check
- High-resolution PNGs uploaded by users can easily exceed 4MB (even larger after base64, approximately 1.33x)
- Both Grok image and video backends pass reference images/start frames via base64 data URIs

### Solution

Add a compression function in `image_utils.py`, called at the upload entry point when the image exceeds 2MB:

- **Trigger condition**: uploaded image raw size > 2MB
- **Format**: convert to JPEG (quality=85)
- **Resolution**: long edge no more than 2048px, proportionally scaled
- **≤ 2MB**: save original content directly, no conversion
- **Scope**: all user-uploaded images (character reference images, character images, clue images, style reference images); AI-generated images are not processed for now

### Modified Files

| File | Modification |
|------|-------------|
| `lib/image_utils.py` | Add `compress_image_bytes()` function: JPEG conversion + resolution limiting |
| `server/routers/files.py` | Upload entry changed to call `compress_image_bytes()` instead of `convert_image_bytes_to_png()` |

### Notes

- Only images larger than 2MB trigger compression (JPEG + resizing), extension changed to `.jpg`
- Images ≤ 2MB are saved with original content, retaining original format extension (`.png`/`.jpg`/`.webp`)
- Reference paths in `project.json` will naturally reflect the actual extension
- Downstream code (`image_to_base64_data_uri`, `_collect_reference_images`, etc.) reads files by path and does not depend on specific extensions; no changes needed
- Existing stored old files need no migration and can still be used normally
- Style reference images (`style_reference`) also apply this rule, because Grok text backend's vision calls also pass images via `image_to_base64_data_uri()`, also subject to the gRPC 4MB limit

## Issue 2: Grok Image Concurrency Anomaly

### Current State

- `_load_pools_from_db()` in `generation_worker.py` defaults to image_max=5, video_max=3
- User has not manually modified configuration, but Grok image tasks are observed to execute serially
- Video concurrency is normal (3)

### Investigation Directions

1. **DB config residue**: check whether Grok's `image_max_workers` in the `provider_configs` table has been set to 1
2. **Pool loading logic**: does `_load_pools_from_db()` have type conversion or default value issues when parsing config
3. **Fallback pool**: if DB loading fails, is `_build_default_pools()` being used and behaving correctly

### Solution

- During worker startup and `reload_limits()`, add INFO-level logging to print the actual pool configuration (image_max, video_max) for each provider
- If DB residue values are confirmed (e.g., `"1"` or empty string), fix the default value fallback logic in `_load_pools_from_db()`

### Modified Files

| File | Modification |
|------|-------------|
| `lib/generation_worker.py` | Add pool configuration logging in `_load_pools_from_db()` and `reload_limits()` |

## Issue 3: Grok Reference Images Only Uses First Image

### Current State

- `grok.py` image backend only takes `request.reference_images[0]`, passing it via `image_url` (singular) to the API
- Grok API actually supports `image_urls` (plural list), allowing multiple reference images
- All other providers already support multiple reference images: Gemini (unlimited + labels), Ark (list), OpenAI (up to 16)

### Solution

Change the I2I logic in `grok.py` from single image to multiple images:

```python
# Before
if request.reference_images:
    ref_path = Path(request.reference_images[0].path)
    if ref_path.exists():
        generate_kwargs["image_url"] = image_to_base64_data_uri(ref_path)

# After
if request.reference_images:
    data_uris = []
    for ref in request.reference_images:
        ref_path = Path(ref.path)
        if ref_path.exists():
            data_uris.append(image_to_base64_data_uri(ref_path))
    if data_uris:
        generate_kwargs["image_urls"] = data_uris
```

### Modified Files

| File | Modification |
|------|-------------|
| `lib/image_backends/grok.py` | `generate()` method: switch to `image_urls` to pass all reference images |

## Issue 4: Drama Mode Scene Aspect Ratio Is Wrong

### Investigation Conclusion

**ArcReel's code parameter-passing chain is completely correct** — `aspect_ratio="16:9"` is passed through to the xAI SDK.

**Root cause confirmed**: Grok API ignores the `aspect_ratio` parameter in single-image edit mode (singular `image_url` parameter), using the reference image's original aspect ratio. In multi-image edit mode (`image_urls` list parameter), `aspect_ratio` takes effect correctly.

### Solution

**Merged with Issue 3 as the same fix**: always use `image_urls` (list), even with only one reference image, always going through the multi-image edit path. This allows the `aspect_ratio` parameter to be correctly recognized.

Additionally add aspect_ratio support list validation as a fallback. Grok supports far more ratios than initially expected: `1:1`, `16:9`/`9:16`, `4:3`/`3:4`, `3:2`/`2:3`, `2:1`/`1:2`, `19.5:9`/`9:19.5`, `20:9`/`9:20`, `auto`. Ratios not in the list are passed through to the API with a warning (no mapping).

### Modified Files

| File | Modification |
|------|-------------|
| `lib/image_backends/grok.py` | Already changed to use `image_urls` in Issue 3; additionally add aspect_ratio validation |

## Cross-Issue Impact

- Issue 1 (image compression) reduces reference image size, indirectly alleviating the burden of Issue 3 (larger total size with multiple reference images)
- Issue 3 (switching to `image_urls`) directly fixes Issue 4: `aspect_ratio` parameter works correctly in multi-image edit mode
- All changes only affect the Grok provider (compression is an exception, but compression is universal upload logic)

## Testing Strategy

- Issue 1: unit test `compress_image_bytes()` handling of large/small images and various formats
- Issue 2: check log output to confirm pool configuration is correct
- Issue 3: integration test passing multiple reference images to Grok API
- Issue 4: integration test to verify drama mode generates landscape images with correct dimensions
