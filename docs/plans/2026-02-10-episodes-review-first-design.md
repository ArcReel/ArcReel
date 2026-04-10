# Episodes/Scenes Page "Review-First" Redesign (Confirmed)

## Goals
- Change the current "Episodes/Scenes" page from "edit-driven" to "review-driven".
- Without changing backend interfaces, elevate video playback and storyboard viewing to the primary first-screen task.
- Retain existing save, storyboard generation, video generation, upload, and expand-to-edit capabilities as secondary operations.

## Confirmed Decisions
- Review mode: single-item playback (no auto-playlist).
- Player position: in-page main player (fixed above the list).
- Trigger: click the video thumbnail on a scene card to start playback.
- Review layout: primary video view + secondary storyboard view shown simultaneously.
- Default behavior: no auto-play when entering the page; wait for manual click.
- List density: 5-column compact card grid.

## Current Problems (Misaligned Focus)
- In the current scene cards, video and storyboard are shown as small corner previews, inadequate for the review task.
- Page visual focus is on status, text, and edit buttons rather than "viewing generated results".
- Small embedded videos in the list interfere with browsing and switching when playing; review flow is not continuous.

## Design

### 1) Page Structure
- Within the `ProjectEpisodes` view, add a top-level main review area, positioned before the episode list.
- Main review area default empty state text: "Click any scene's video thumbnail to start reviewing."
- When the main review area is activated:
  - Left: 16:9 large video player (native browser controls).
  - Right: corresponding scene's large storyboard image + key info (scene ID, duration, status).
- Below: retain episode cards and scene grid, serving as a "scene selector".

### 2) Scene Card Strategy
- Retain existing card info and action buttons (save / storyboard / video / upload / expand-to-edit).
- Cards changed to 5-column compact grid to maintain high-density browsing efficiency.
- Clicking the video thumbnail in a card only updates the top main player; does not play within the card.
- The currently-reviewed card is highlighted (border/glow/label) for improved navigation.

### 3) Reference UI Trade-offs
- Adopt "dark main stage + compact card matrix" from the reference image as the visual baseline.
- Difference: add a fixed main review area to avoid relying solely on card browsing; maintain "review-first".

## Component and State Design

### Component Hierarchy (frontend)
- Page container: `ProjectEpisodes`
- Suggested new components:
  - `EpisodeReviewPanel`: top main review area (empty state / video state)
  - `SceneGrid`: 5-column scene card grid (can reuse existing rendering logic)
- Existing interfaces reused:
  - `resolveFileUrl(currentProjectName, path)` resolves video and storyboard URLs
  - `onGenerateStoryboard` / `onGenerateVideo` / `onSaveItem` / `onUploadStoryboard`

### New State
- `selectedReview: { scriptFile: string, itemId: string } | null`
  - Initial value: `null`
  - Written when user clicks a scene thumbnail that has a video
  - Main player queries current item and asset URLs based on this state

## Data Flow
1. User clicks the video thumbnail on a scene card.
2. Frontend checks whether the scene has `generated_assets.video_clip`.
3. If yes: set `selectedReview`, main review area loads and plays.
4. If no: toast notification "This scene has no playable video yet"; selected state does not change.
5. Main review area also shows the storyboard image (`generated_assets.storyboard_image`) and scene metadata.

## Error Handling and Resilience
- Stale selection: after switching projects or refreshing data, if the selected item no longer exists, automatically clear `selectedReview` and return to empty state.
- Video unavailable: show failure message on main player `onError`, and provide a "Regenerate Video" entry for the current item.
- Auto-refresh after regeneration: if the currently selected item is successfully regenerated, the player URL automatically uses the latest `video_clip`.
- Empty data fallback: clearly show distinct empty-state messages for no episodes, no scenes, or scenes without video.

## Testing Strategy
- File: `frontend/tests/*` (add or extend `workspace-page` related tests)
- Minimum regression coverage:
  - Entering the page shows the main review area in empty state with no auto-play.
  - After clicking a scene with a video thumbnail, the main player renders and binds the correct resource.
  - Clicking a scene without a video only shows an error notification; selected state does not change.
  - Currently selected card highlight is visible.
  - Selected state is cleared when switching projects.
  - Main player shows error state when loading fails.

## Implementation Scope
- Primary modification:
  - `frontend/src/react/pages/workspace-page.js`
- Style modifications (if needed):
  - `frontend/src/css/styles.css` or `frontend/src/css/app.css`
- Test modifications:
  - `frontend/tests/*workspace*` (additions following existing test structure)

## Acceptance Criteria
- After entering "Episodes/Scenes", there is a main review area at the top with an empty state and no auto-play.
- After clicking any scene with a video, a large player appears at the top and plays correctly.
- The main review area simultaneously shows the large storyboard image and key scene information.
- The 5-column compact scene grid is maintained below, with the current selection clearly highlighted.
- Existing generate/save/upload/expand-to-edit operations still work without regression.
