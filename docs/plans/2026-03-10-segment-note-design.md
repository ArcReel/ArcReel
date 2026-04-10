# Storyboard Note Feature Design

## Overview

Add a note area to the lower portion of the text column (first column) of storyboard cards, where users can write and view notes for each storyboard. Notes are for user reference only and do not participate in image/video generation.

## Data Layer

Add a new field to `NarrationSegment` and `DramaScene` models:

```python
note: Optional[str] = None
```

- Frontend type `script.ts` correspondingly adds `note?: string`
- `Optional` + `default=None` automatically handles old data compatibility, no migration needed
- Generation logic does not read this field, no changes needed

## API Layer

No new endpoints needed, reuse existing PATCH endpoints:

- Narration: `PATCH /api/v1/projects/{name}/segments/{segment_id}` — body contains `"note": "..."`
- Drama: `PATCH /api/v1/projects/{name}/scenes/{scene_id}` — updates contains `"note": "..."`

## Frontend UI

In the `TextColumn` component, add a note area below the original text/dialogue:

- Label "Note", styled consistently with the "Source Text" label (normal style, no special color)
- `textarea` occupies approximately half the text column space
- `placeholder`: "Add a note..."
- On blur (`onBlur`), if content has changed, call the save endpoint

## Affected Files

| File | Change |
|------|------|
| `lib/script_models.py` | Add `note` field to `NarrationSegment` / `DramaScene` |
| `frontend/src/types/script.ts` | Add `note?: string` to type |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | Render note area in `TextColumn` |
| `frontend/src/components/canvas/StudioCanvasRouter.tsx` | Pass note in save callback |
