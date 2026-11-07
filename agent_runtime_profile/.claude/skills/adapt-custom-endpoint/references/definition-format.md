# ArcReel custom-endpoint definition format

The shared server validator is authoritative for saving, request previews, response checks, and connection tests. The complete current JSON Schema is at
[`packages/arcreel-market-core/src/arcreel_market_core/endpoint_definition/schema.json`](https://github.com/ArcReel/ArcReel/blob/main/packages/arcreel-market-core/src/arcreel_market_core/endpoint_definition/schema.json);
start with a minimal definition, then correct the field paths and error codes returned by `validate`.

## Minimal shape

```json
{
  "kind": "declarative",
  "schema_version": "1.2.0",
  "meta": {"name": "Demo Video", "author": "user", "version": "1.0.0"},
  "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
  "submit": {
    "method": "POST",
    "url": "{{ base_url }}/videos",
    "body": {"model": "{{ model }}", "prompt": "{{ prompt }}", "duration": "{{ duration }}"},
    "extract": {"task_id": ["$.id"], "error": ["$.error.message", "$.message"]}
  },
  "poll": {
    "method": "GET",
    "url": "{{ base_url }}/videos/{{ task_id }}",
    "extract": {
      "status": ["$.status"],
      "video_url": ["$.output.url"],
      "error": ["$.error.message", "$.message"]
    }
  },
  "status_map": {
    "queued": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed"
  },
  "capabilities": {"text_to_video": true}
}
```

## Constraints

- `auth` may be empty; otherwise it must reference `{{ api_key }}` at least once, with credentials only in `auth.headers` or
  `auth.query`. Ordinary request headers, body, and URL must not contain additional credentials.
- Video template variables include `base_url`, `api_key` (auth only), `model`, `prompt`, `duration`,
  `duration_seconds`, `aspect_ratio`, `resolution`, `generate_audio`, `seed`, `width`, `height`,
  `task_id`, `result_id`, and `inputs.<name>`. A whole-string single placeholder preserves its original type; null removes the containing field.
- Declare materials in `inputs`; `source` is `start_image`, `end_image`, `reference_images`, or
  `reference_audio_files`; `encoding` is `data_uri` or `base64`. Expand lists with `$each`; guard optional objects
  with `$when`. `capabilities` must match materials actually referenced in submit in both directions.
- `enum_maps` maps ArcReel values to provider values; `defaults` fills only optional ArcReel-side parameters.
- `extract` is a priority-ordered JSONPath array. Paths start at `$`, using only child segments and simple filters;
  no recursive descent, unions, stepped slices, or functions. For response fields containing JSON strings, use
  `{"path":"$.data","json_decode":true,"then":["$.id"]}`.
- `status_map` targets only `queued`, `running`, `succeeded`, `failed`. If success requires another retrieval,
  extract `result_id` in poll and add `result` to obtain `video_url` from `task_id` / `result_id`.
- Image definitions set `"media_type": "image"`; omission means video. Their variables are only `base_url`, `api_key` (auth only),
  `model`, `prompt`, `aspect_ratio`, `resolution`, `seed`, `width`, `height`, `task_id`, and `result_id`;
  `capabilities` must declare at least one of `text_to_image` / `image_to_image`, and may declare both. Image retrieval uses `image_url` (URL)
  or `image_b64` (inline base64, accepting bare strings and `data:image/...;base64,`), at least one, preferring URL if both exist;
  when `result` exists, put these in `result.extract`. Duration, audio, first/last frames, `usage`, and other video-only fields invalidate image definitions.
- Image materials accept only `source: "reference_images"`, with `data_uri` or `base64` encoding, expanded in submit using `$each`.
  Declaring `image_to_image` also requires positive `max_reference_images` and submit references to those images;
  if `text_to_image` is also declared, reference input cannot be required. Image-edit instructions are sent through `prompt`.

## Test inputs

`parameters.json` uses API field names, for example:

```json
{"model":"provider-model","prompt":"short test","duration_seconds":5,"aspect_ratio":"9:16","generate_audio":false}
```

Prefer references to saved providers in `credentials-ref.json`:

```json
{"provider_id":"custom-1"}
```

Material arguments map one-to-one to multipart fields: `--start-image` → `start_image`, `--end-image` →
`end_image`, `--reference-images` → `reference_images`, `--reference-audio-files` →
`reference_audio_files`. The last two accept repeated files; previews and connection tests use identical material arguments.
