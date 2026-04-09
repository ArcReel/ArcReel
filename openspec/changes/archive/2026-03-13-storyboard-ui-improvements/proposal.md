## Why

The storyboard panel's SegmentCard has two information gaps: storyboard duration (4/6/8s) is currently read-only with no way to modify it directly in the UI, forcing users to use other means to adjust it; the associated clue (scene/prop) fields exist in the data model but have never been displayed in the card header, resulting in incomplete creative context.

## What Changes

- **DurationBadge → DurationSelector**: The storyboard duration label changes from read-only to interactive; clicking it opens a Popover to select 4s / 6s / 8s; after selection, the new value is written to the backend via the existing `onUpdatePrompt` channel; the total episode duration in the episode header is automatically updated when data refreshes.
- **New ClueStack component**: The right side of the SegmentCard header displays associated clue thumbnails (rounded square, consistent with the Lorebook image style on the left); hovering shows a popover with clue name, image, and type tag (Location / Prop).
- **Character popover adds "Character" tag**: AvatarPopover adds a `Character` type tag next to the character name, unified in style with the clue popover, for easy distinction.

## Capabilities

### New Capabilities

- `segment-duration-selector`: The storyboard duration in the SegmentCard header can be switched via a popup selector (4/6/8s), with the total episode duration display updating accordingly.
- `clue-stack-display`: The SegmentCard header displays associated clue image thumbnail stacks; the hover popover shows the name, image, and type tag (Location/Prop); the character popover also adds a "Character" type tag.

### Modified Capabilities

(No existing specs need modification)

## Impact

- Pure frontend change; does not involve backend API or data model changes
- Modified files: `frontend/src/components/canvas/timeline/SegmentCard.tsx`, `frontend/src/components/ui/AvatarStack.tsx`
- New files: `frontend/src/components/ui/ClueStack.tsx`
- Backend PATCH `/projects/{name}/segments/{segment_id}` already supports the `duration_seconds` field; no changes needed
