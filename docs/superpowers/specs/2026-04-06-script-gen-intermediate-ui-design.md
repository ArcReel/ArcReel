# Script Generation Intermediate Product UI Display and Event Notification

## Background

Both narration and drama animation modes have a prerequisite step before generating the JSON script:

- **narration mode**: `split-narration-segments` subagent generates `step1_segments.md` (segment split table)
- **drama mode**: `normalize-drama-script` subagent generates `step1_normalized_script.md` (normalized script table)

These intermediate products are completely invisible to users — no UI display in the frontend, no event notifications, and the sidebar does not show episodes that only have step1. Users cannot view split/normalized results and cannot perceive this process.

## Goals

1. Users can view and edit step1 intermediate products in the Web UI
2. Automatic user notification and navigation when step1 generation completes
3. Episodes with only step1 (no final JSON script yet) are visible in the sidebar

## Design

### Part 1: Backend Changes

#### 1.1 StatusCalculator Fix

`_load_episode_script()` currently only detects `step1_segments.md`; needs to also support drama mode based on `content_mode`:

- `content_mode === "narration"` → detect `step1_segments.md`
- `content_mode === "drama"` → detect `step1_normalized_script.md`

When a step1 file is detected in either mode, return the `"segmented"` status.

#### 1.2 New `draft` Event Type

Add two new events in `ProjectEventService`:

| entity_type | action | Trigger |
|-------------|--------|---------|
| `draft` | `created` | step1 file first generated (PUT endpoint detects file does not exist → creates) |
| `draft` | `updated` | step1 file edited and updated (PUT endpoint detects file already exists → updates) |

Event data includes a `focus` field to drive frontend auto-navigation:

```python
focus = {
    "pane": "episode",
    "episode": episode_num,
    "tab": "preprocessing"  # new field specifying the activated Tab
}
```

The event's `label` field varies by content_mode:
- narration: `"Episode N Segment Split"`
- drama: `"Episode N Normalized Script"`

#### 1.3 Drafts API Cleanup

Remove step2/step3 file mappings in `server/routers/files.py`, keeping only step1:

```python
# narration mode
STEP_FILES = {1: "step1_segments.md"}

# drama mode
STEP_FILES = {1: "step1_normalized_script.md"}
```

The API endpoint `GET/PUT/DELETE /drafts/{episode}/step1` internally determines which file to read/write based on `content_mode` in `project.json`. The frontend uniformly calls step1 without needing to know the filename difference.

#### 1.4 Event Trigger Integration

The drafts PUT endpoint calls `ProjectEventService` to emit a `draft:created` or `draft:updated` event after successful saving. The subagent naturally triggers the event chain when saving files via the existing drafts API.

### Part 2: Frontend Changes

#### 2.1 Sidebar (AssetSidebar)

Episode list rendering logic change:

- Render episodes with `status === "segmented"` normally (currently only `"generated"` and episodes with script_file are rendered)
- Style: gray status dot (`text-gray-500`) + "Preprocessing" label on the right (indigo small badge: `text-indigo-400 bg-indigo-950`)
- Click navigates to `/episodes/{N}`, same behavior as normal episodes

Episodes with neither step1 nor JSON script do not appear in the list.

#### 2.2 TimelineCanvas Tab Transformation

Add a tab bar below the title area with two tabs: "Preprocessing" and "Script Timeline."

Tab visibility and activation rules:

| Status | Tab Bar | Default Active |
|--------|---------|----------------|
| Only step1, no script | Shown, "Script Timeline" tab disabled | Preprocessing |
| Both step1 and script | Shown, both clickable | Script Timeline |
| Only script, no step1 | Tab bar hidden | — (retain existing behavior) |

Tab styles:
- Active: `text-indigo-400`, 2px bottom `border-indigo-500`
- Inactive: `text-gray-500`, 2px bottom `transparent`
- Disabled: `text-gray-700`, `cursor-not-allowed`

#### 2.3 Preprocessing Tab Content Component (New)

Create new `PreprocessingView` component, following the view/edit toggle pattern of `SourceFileViewer`:

**View mode (default):**
- Top status bar: left side shows completion status + timestamp, right side "Edit" button
- Main area: Markdown rendering, converting step1's Markdown tables to HTML tables
- Status label shows different text based on content_mode:
  - narration: "Segment Split Completed"
  - drama: "Normalized Script Completed"

**Edit mode:**
- Enter by clicking the "Edit" button
- textarea text editor (`font-mono`, referencing SourceFileViewer styles)
- Top buttons change to "Save" + "Cancel"
- Save calls `PUT /api/v1/projects/{name}/drafts/{episode}/step1`
- After successful save, automatically exits edit mode; backend emits `draft:updated` event

#### 2.4 Event Handling and Auto-Navigation

Add `draft` event handling in the `useProjectEventsSSE` hook:

**Toast notifications:**
- `draft:created`: important notification (`important: true`), pop up Toast
  - narration: "Episode N Segment Split Complete · XX segments · ~XXs"
  - drama: "Episode N Normalized Script Complete · XX scenes · ~XXs"
- `draft:updated`: non-important notification

**Auto-navigation:**
- After receiving `draft:created` event, based on `focus` field:
  1. Navigate to `/episodes/{N}` (if not on that page)
  2. Activate "Preprocessing" tab
- Trigger project data reload (refresh sidebar episode list to make new episodes visible)

**Event priority:**
- Add `"draft:created": 6` to `CHANGE_PRIORITY` (after episode events, before storyboard_ready)

## Affected Files

### Backend
- `lib/status_calculator.py` — fix step1 detection for drama mode
- `server/routers/files.py` — remove step2/step3 mappings, integrate event emission
- `server/services/project_events.py` — add draft event type and label generation

### Frontend
- `frontend/src/components/layout/AssetSidebar.tsx` — sidebar supports segmented status
- `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` — add tab bar
- `frontend/src/components/canvas/timeline/PreprocessingView.tsx` — **new**, preprocessing content component
- `frontend/src/hooks/useProjectEventsSSE.ts` — add draft event handling
- `frontend/src/types/workspace.ts` — add draft event type definitions
- `frontend/src/utils/project-changes.ts` — add notification text for draft events
- `frontend/src/api.ts` — existing draft API, no changes needed

### Tests
- `tests/test_status_calculator.py` — add drama mode step1 detection test cases
- `tests/test_files_router.py` — update drafts API tests (remove step2/step3)
