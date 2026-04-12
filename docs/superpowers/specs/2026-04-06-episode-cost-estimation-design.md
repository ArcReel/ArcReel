# Episode Cost Estimation Feature Design

## Overview

Add episode cost estimation functionality to ArcReel, displaying per-episode **estimated cost** (based on current model configuration) and **actual cost** (accumulated from historical API calls) in the Web UI. Supports three-level cost display from project overview down to individual segment cards.

## Requirements

- **Estimate**: calculated in real time when scripts change, based on segment count × current image/video model pricing
- **Actual**: accumulated cost of all successful API calls (including regenerations), precisely attributed per segment
- **Cost items**: estimates include storyboard images + video only; actuals additionally include character/clue generation costs at the project level
- **Currency**: all costs use the `Record<currency, amount>` structure, supporting mixed USD/CNY, displayed in original currency

## Data Model Changes

### New Field in ApiCall Table

| Field | Type | Description |
|-------|------|-------------|
| `segment_id` | `String(20), nullable, indexed` | Segment identifier (e.g., `E1S001`), parseable to extract episode number |

- New Alembic migration
- Passed in by `generation_tasks.py` at enqueue time, flowing through `UsageTracker.start_call()` → `UsageRepository`
- ApiCalls for character/clue generation do not set segment_id (`null`); distinguished by `call_type`
- No historical data backfill

## Backend API Design

### `GET /api/v1/projects/{project_name}/cost-estimate`

Returns estimated + actual costs for all episodes in the entire project at once.

**Request parameters**: none (automatically calculated from project's current scripts and model configuration)

**Response structure**:

```json
{
  "project_name": "my-project",
  "models": {
    "image": { "provider": "gemini-aistudio", "model": "gemini-3.1-flash-image-preview" },
    "video": { "provider": "gemini-aistudio", "model": "veo-3.1-lite-generate-preview" }
  },
  "episodes": [
    {
      "episode": 1,
      "title": "Opening",
      "segments": [
        {
          "segment_id": "E1S001",
          "duration_seconds": 6,
          "estimate": {
            "image": { "USD": 0.04 },
            "video": { "USD": 0.35 }
          },
          "actual": {
            "image": { "USD": 0.08 },
            "video": { "USD": 0.35 }
          }
        }
      ],
      "totals": {
        "estimate": {
          "image": { "USD": 0.40 },
          "video": { "USD": 3.50 }
        },
        "actual": {
          "image": { "USD": 0.48 },
          "video": { "USD": 3.50 }
        }
      }
    }
  ],
  "project_totals": {
    "estimate": {
      "image": { "USD": 1.20 },
      "video": { "USD": 10.50 }
    },
    "actual": {
      "image": { "USD": 1.08, "CNY": 1.20 },
      "video": { "USD": 10.50 },
      "character_and_clue": { "USD": 0.45 }
    }
  }
}
```

### Cost Type `CostBreakdown`

All cost values unified as `Record<currency, amount>` mappings:

```python
# Single currency
{"USD": 0.04}
# Mixed currency (regeneration used a different provider)
{"USD": 0.04, "CNY": 1.20}
```

### Calculation Logic

**Estimate:**
1. Read each episode's script → iterate through segments
2. Resolve current image/video model + parameters (resolution, audio, duration) via ConfigResolver
3. Call CostCalculator to compute image + video cost for each segment

**Actual:**
1. Query all successful ApiCall records from UsageRepository by `project_name` + `segment_id`
2. Group by segment_id + call_type + currency and accumulate costs (including accumulated regeneration costs)
3. At project level: additionally query image records with `segment_id IS NULL` (character + clue image generation both use `call_type=image`; merge into `character_and_clue`)

### New Service Layer

`server/services/cost_estimation.py`: orchestrates ConfigResolver + CostCalculator + UsageRepository + ProjectManager

### New Route

`server/routers/cost_estimation.py`: mounted at `/api/v1/projects/{project_name}/cost-estimate`

## Frontend Design

### Data Layer

**API call**: add `getCostEstimate(projectName)` method in `frontend/src/api.ts`.

**Store**: add `costEstimate` state field in `projects-store.ts`, refreshed on project load/script change (debounce 500ms).

### Three-Level UI Display

#### 1. Project Overview (OverviewCanvas)

Add a project total cost summary bar above the episode list area:

- **Estimated** (yellow total): storyboard + video, broken down by type
- **Actual** (green total): storyboard + video + characters + clues, broken down by type
- Mixed currency shown on same line: `Storyboard $0.20 + ¥4.00`

Each row in the episode list adds estimated/actual cost columns:
- Broken down by type (storyboard / video) + total
- Episodes not yet generated show gray "— Not generated —"
- Total label color matches storyboard/video label color (gray); only the amount numbers are highlighted

#### 2. Timeline Top (TimelineCanvas)

Add a single-line cost bar below the episode header:
- Estimated | Actual separated by a vertical bar
- Format: `Estimate Storyboard $0.40 Video $3.50 Total $3.90 | Actual Storyboard $0.48 Video $3.50 Total $3.98`

#### 3. Segment Card (SegmentCard)

Display inline after segment_id and duration in the header row:
- Separated from segment_id/duration by `|`
- Format: `Estimate Storyboard $0.04 Video $0.35 | Actual Storyboard $0.04 Video $0.35`
- Use `—` as placeholder for ungenerated items
- No total shown (single segment has only two items, no need to summarize)

### Color Semantics

| Element | Color |
|---------|-------|
| Labels (Storyboard/Video/Total/Estimate/Actual) | `#71717a` (gray) |
| Individual cost amounts | `#d4d4d8` (light gray) |
| Estimated total amount | `#fbbf24` (yellow) |
| Actual total amount | `#34d399` (green) |
| Ungenerated placeholder | `#52525b` (dark gray) |

### Real-time Updates

- Debounce 500ms request to backend to recalculate estimates on script changes (segment add/remove, duration modification)
- Trigger cost data refresh via project event SSE when a generation task completes

## Impact Scope

### Backend

| File | Change |
|------|--------|
| `lib/db/models/api_call.py` | Add `segment_id` field |
| `lib/usage_tracker.py` | Add `segment_id` parameter to `start_call()` |
| `lib/db/repositories/usage_repo.py` | Pass `segment_id` in `start_call()`; add new per-segment aggregate query methods |
| `server/services/generation_tasks.py` | Pass `segment_id` at enqueue time |
| `server/services/cost_estimation.py` | **New**: cost estimation service |
| `server/routers/cost_estimation.py` | **New**: API route |
| `server/app.py` | Register new route |
| Alembic migration | **New**: add `segment_id` field |

### Frontend

| File | Change |
|------|--------|
| `frontend/src/api.ts` | Add `getCostEstimate()` |
| `frontend/src/types/cost.ts` | **New**: cost estimation type definitions |
| `frontend/src/stores/projects-store.ts` | Add `costEstimate` state |
| `frontend/src/components/canvas/OverviewCanvas.tsx` | Add cost summary bar + episode list cost columns |
| `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` | Add episode cost bar |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | Inline cost display in header |

## Testing Plan

- `test_cost_estimation_service.py`: estimate calculation logic (single currency, mixed currency, no script, empty segment)
- `test_cost_estimation_router.py`: API endpoint (normal response, project not found, no script)
- `test_usage_repo.py`: add tests for new per-segment_id aggregate query
- Existing `test_usage_tracker.py`: update to adapt to new start_call signature
