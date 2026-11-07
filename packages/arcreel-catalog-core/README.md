# arcreel-catalog-core

ArcReel model-catalog data format and validator. The ArcReel application uses this implementation to load factory snapshots and official synchronization data, and catalog-data repository CI uses it too, giving the same data the same validation result in both places. This package depends only on the standard library and no part of the ArcReel application.

See the main repository's `GLOSSARY.md` sections “Providers and models” and “Billing” for terminology, and `docs/adr/0099`, `0100`, and `0103`–`0105` for decisions.

## Files

The catalog-data repository publishes two files:

| File | Contents |
|---|---|
| `manifest.json` | Manifest: `dataset_version` (dataset version) and `sha256` (data-file SHA-256, lowercase hexadecimal) |
| `catalog.json` | Data file: `dataset_version`, `entries` (model entries), and `builtin_providers` (built-in provider list) |

Dataset versions are UTC timestamps in `YYYY-MM-DDTHH:MM:SSZ` format; string ordering gives age ordering. The format has no schema version number.

### Model entries

```json
{
  "maker": "google",
  "name": "gemini-3.1-pro",
  "media_type": "text",
  "display_name": "Gemini 3.1 Pro",
  "aliases": ["gemini-3.1-pro-latest"],
  "discontinued": false,
  "release_date": "2026-02-19",
  "shutdown_date": "2027-03-31",
  "successor": "google/gemini-3.5-pro",
  "context_length": 1048576,
  "facts": {"image_input": true, "structured_output": true, "max_output_tokens": 65536}
}
```

- Identity is “model maker/official model name” (`maker`/`name`); the two entries of a dual-media model are further distinguished by `media_type`. `maker` contains no `/`.
- `aliases` are model aliases used only to match model names to entries. Within one media type, every entry's official model name and aliases must remain unique after `normalize_model_name` normalization (case-insensitive, namespace removal, dots equivalent to hyphens).
- `discontinued` means retired; `context_length` is context length in tokens.
- Lifecycle fields are optional; absence means unrecorded and fields are omitted when writing:
  - `release_date` and `shutdown_date` are launch and shutdown dates in `YYYY-MM-DD` format, precise to the day. Judge shutdown by UTC date: the shutdown day itself counts as retired. Catalog builds set `discontinued` true when shutdown is on or before build day, allowing older instances unaware of shutdown dates to recognize retired models.
  - `successor` is the maker's officially designated successor, in “model maker/official model name” form, referencing an entry with the same media type. Therefore references to dual-media models need no additional media type. Record only the direct successor.
  - Lifecycle describes retirement at the model-maker level and applies to every provider, including custom providers.
- `facts` holds model facts with different fields per media type:
  - `text`: `image_input`, `structured_output`, `max_output_tokens`;
  - `image`: `text_to_image`, `image_to_image`, `resolutions` (available resolution tiers), and optional `max_reference_images` and `output_token_bases`;
  - `video`: `durations` (available durations, required), `resolutions`, `duration_constraints` (`by_resolution` narrows available durations by resolution, and `with_reference_images` gives allowed durations with reference images), and optional `max_reference_images`, `text_to_video`, `first_frame`, `last_frame`, `reference_audio`, `max_reference_audio_count`, and `max_reference_audio_total_seconds`;
  - `audio`: none.
- `price` is the price table; omit it when prices are absent.

For images and videos, absent model-fact fields marked “optional” mean unrecorded and remain omitted when writing, without a separate “unknown” value:

- `max_reference_images` is a nonnegative integer; `0` means a reference-image count limit of zero, not unlimited. Image-to-image support is expressed by `image_to_image`; video first and last frames are independent input slots.
- Video `text_to_video`, `first_frame`, `last_frame`, and `reference_audio` are optional booleans indicating acceptance of text-to-video, first frame, last frame, and reference audio respectively. Explicit `false` differs from absence.
- `max_reference_audio_count` is the reference-audio count limit (nonnegative integer); `max_reference_audio_total_seconds` is the combined duration limit across reference-audio clips (nonnegative finite number in seconds). Explicit `0` differs from absence. If `reference_audio` is `true`, `max_reference_audio_count` cannot be `0`.
- Image `output_token_bases` maps quality tiers to positive-integer estimation bases, for example `{"low": 16, "medium": 48, "high": 96}`. When unrecorded it is an empty map, omitted when writing. Resolution-tier-to-request-`quality` mapping belongs to endpoint code, not entries.

### Price tables

```json
{
  "currency": "CNY",
  "meters": [
    {
      "meter": "output_seconds",
      "dims": ["resolution", "audio"],
      "rows": [
        {"when": {"resolution": "720p", "audio": false}, "price": 0.6},
        {"when": {"resolution": "1080p"}, "price": 1.2}
      ],
      "defaults": {"resolution": "720p"}
    },
    {"meter": "input_images", "dims": [], "rows": [{"when": {}, "price": 0.1}], "free_allowance": 2}
  ]
}
```

- `currency` is `USD` or `CNY`, using the currency listed on the model maker's main site.
- Each meter (`meter`) appears only once, with tiers along the selection dimensions in `dims`. Each row is “condition → unit price”; dimensions absent from `when` accept any value. A request matches at most one row.
- `defaults` supplies values when requests omit a dimension; `free_allowance` is the per-request free allowance in that meter's counting unit. `cjk_double` applies only to `characters` and counts Han characters as 2 characters.
- Meters and selection dimensions are closed sets defined in `arcreel_catalog_core.vocabulary`: `METERS` gives each meter's applicable media types, count represented by its unit price (per million for token meters, per ten thousand for characters, per 1 otherwise), and category. `DIMENSIONS` gives each dimension's value shape; `DIMENSION_MEDIA_TYPES` gives its applicable media types (guidance for editing price tables, not a format constraint).
- Meters have two categories. Token meters (such as uncached input and cache reads) partition one token total; if a call reports a meter absent from the price table, the entire charge is unpriced. Count meters (seconds, video tokens, images, characters, calls, etc.) are independent billing axes; an absent meter means the maker does not charge on that axis.

### Built-in provider list

```json
{
  "kling": {
    "models": [
      {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "video",
       "endpoint": "kling-video-audio"},
      {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "image",
       "multiplier": 1.0, "min_app_version": "0.34.0", "endpoint": "kling-image"}
    ],
    "defaults": {"video": "kling-v3-omni", "image": "kling-v3-omni"}
  }
}
```

- Each provider model specifies its API model name, referenced entry (“model maker/official model name”), provider model multiplier (default 1), and optional minimum application version. Only references to dual-media models need `media_type` to identify the entry.
- `endpoint` is the provider model's attached built-in invocation endpoint, required in published data. It must exist in the endpoint catalog, match the referenced entry's media type, and be an available endpoint for that provider and media type. Instances still tolerate absence and use the provider's default endpoint.
- Within a provider, distinguish provider models by API model name and referenced-entry media type.
- `defaults` gives default API model names by media type. Each must appear in that provider's list for that media type, and its entry must be neither retired nor assigned a shutdown date.

### Endpoint catalog

The endpoint catalog lives in `arcreel_catalog_core.endpoints`, a copy of code invocation endpoints kept consistent by main-repository tests:

- `BUILTIN_ENDPOINTS`: each built-in endpoint available to built-in providers, with media type and optional starting version `since`. Endpoints existing at the catalog's initial publication are baseline endpoints with no starting version.
- `BUILTIN_PROVIDER_ENDPOINTS`: available and default endpoints per built-in provider and media type.
- `ENDPOINT_DECLARATIONS_SINCE`: application version at which instances begin reading endpoint declarations.

`endpoint_version_floor(provider, media_type, endpoint)` gives the endpoint-determined minimum application version: the higher of the endpoint's starting version and, when the provider has multiple available endpoints for this media type, the version at which instances begin reading endpoint declarations. Catalog builds then take the higher of this and the handwritten minimum application version. For individually verified launch models, pass `include_declaration_version=False` to waive only the declaration-reading version; the endpoint starting version still counts. `app_version_key(version)` returns a numeric comparison key for `max(..., key=app_version_key)`, treating omitted minor and patch numbers as 0 and raising `ValueError` for invalid format. See main-repository `docs/adr/0103` for derivation rules and how to specify starting versions.

## Validation on both sides

| | Instance side (`parse_catalog`, `load_catalog`, `parse_entry`) | Build side (`check_catalog`, `check_catalog_files`, `check_entry`) |
|---|---|---|
| Unknown fields | Ignore | Report |
| Unknown meters, selection dimensions, or currencies | Read normally; `PriceTable.is_priceable` is False, handled as unpriced | Report |
| Unknown media types | Skip the entry | Report |
| Unparseable structure, mismatched hash or dataset version | Raise `CatalogFormatError`, rejecting the entire dataset | Report |
| Duplicate entry identities, name collisions, list references to absent entries, absent or media-type-mismatched successor models, default models with shutdown dates, overlapping price rows, etc. | Do not check | Report |
| Missing endpoint, endpoint absent from the catalog, media type differing from the entry, or endpoint unavailable to that provider | Read normally | Report |

Issues use `CatalogIssue(path, code, detail)`; `code` is the stable comparison basis.

## Usage

```bash
python -m arcreel_catalog_core check <directory>           # Validate manifest.json and catalog.json in the directory
python -m arcreel_catalog_core write-manifest <directory>  # Write manifest.json from catalog.json
```

A nonzero exit code means failure. Issues appear one per line as `location: [code] explanation`.

```python
from arcreel_catalog_core import check_catalog_files, load_catalog

issues = check_catalog_files(manifest_bytes, catalog_bytes)  # Build side
data = load_catalog(manifest_bytes, catalog_bytes)  # Instance side
```

## Development

This package belongs to the ArcReel repository's uv workspace. Tests live in this directory's `tests/` and run separately from application tests:

```bash
cd packages/arcreel-catalog-core && uv run python -m pytest
```
