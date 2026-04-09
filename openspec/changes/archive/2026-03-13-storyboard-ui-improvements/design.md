## Context

SegmentCard is the core card component of the storyboard panel, rendered in TimelineCanvas's virtual scroll list. The current header area only shows storyboard ID, a read-only duration badge, and a character avatar stack. The storyboard's `duration_seconds` (4/6/8s) is fully supported in the backend data model and PATCH API, but the frontend provides no modification entry. The clue (`clues_in_segment` / `clues_in_scene`) fields also exist in the data model, but `SegmentCard` receives them as `_clues` and never renders them.

## Goals / Non-Goals

**Goals:**
- Let users directly switch storyboard duration (4/6/8s) in the card header, with the total episode duration updating accordingly
- Display associated clue image thumbnails in the card header, alongside the character avatar stack
- Add type tags to hover popovers uniformly to distinguish "Character" from "Location/Prop"

**Non-Goals:**
- Not modifying backend API or data model
- Not changing TimelineCanvas's virtual scrolling logic
- Not adding any new information to the SegmentCard content area (three columns)

## Decisions

### Decision 1: Duration Selector Uses Popover Rather Than Click-Cycle

**Choice**: Clicking the duration badge opens a Popover listing 4s / 6s / 8s buttons with the current value highlighted.

**Rationale**: Directly cycling (4→6→8→4) is unintuitive; users cannot see all options at once. Popover reuses the existing `Popover` component; implementation cost is low and consistent with other popup interaction styles in the project.

**Alternative**: Inline three-segment Segmented Control (always visible) — takes up horizontal space; may squeeze the ID badge and avatar area in the limited header width.

### Decision 2: Duration Change Passed Via Existing onUpdatePrompt Channel

**Choice**: Call `onUpdatePrompt(segmentId, "duration_seconds", newValue)`, reusing the complete chain of `StudioCanvasRouter` → `API.updateSegment` / `API.updateScene` → `refreshProject()`.

**Rationale**: No new props or callbacks needed; the backend PATCH interface already supports `duration_seconds`; total duration is re-aggregated from segments after `refreshProject()`.

### Decision 3: ClueStack as an Independent Component, Side-by-Side With AvatarStack

**Choice**: Create new `ClueStack.tsx` (in `frontend/src/components/ui/`); do not modify AvatarStack's generalization capabilities. SegmentCard header layout: AvatarStack (characters) on the left, vertical line separator, ClueStack (clues) on the right.

**Rationale**: Characters and clues have different semantics (characters have character_sheet; clues have clue_sheet; hover popover content differs); forcing a merge would increase AvatarStack's complexity. Copying the AvatarStack structural pattern (image + initial fallback + hover popover + overflow badge) has low cost and they don't interfere with each other.

**Shape**: Clue thumbnails use `rounded` (rounded square) rather than `rounded-full`, consistent with the image style in the Lorebook clue cards on the left.

### Decision 4: Unified Popover Type Tag Style

Character popover (AvatarPopover) adds a `Character` tag to the right of the name (indigo); clue popover shows `Location` (amber) or `Prop` (emerald) based on `Clue.type`. Both are small Badges; the popover content structure remains unchanged.

## Risks / Trade-offs

- **Total duration update depends on backend refresh**: After the duration change, it takes approximately 200-500ms to update the header total duration waiting for `refreshProject()` to complete. Since switching storyboard duration is an infrequent operation, optimistic updates are not implemented.
- **High rate of missing ClueStack images**: Early project clues typically don't have `clue_sheet`; the fallback is an initial letter color block. Functionality is complete but visual effect depends on whether the user has uploaded clue images.
