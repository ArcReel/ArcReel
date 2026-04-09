## 1. Storyboard Duration Selector (DurationSelector)

- [x] 1.1 Refactor `DurationBadge` in `SegmentCard.tsx` into `DurationSelector`: clickable when `onUpdatePrompt` is present, opens Popover on click; retains read-only appearance when `onUpdatePrompt` is absent
- [x] 1.2 Render 4s / 6s / 8s three option buttons in the Popover, with the current value highlighted
- [x] 1.3 After selecting a new value, call `onUpdatePrompt(segmentId, "duration_seconds", newValue)` and close the Popover
- [x] 1.4 When clicking outside the Popover, close the Popover without changing the duration value

## 2. ClueStack Component

- [x] 2.1 Create `frontend/src/components/ui/ClueStack.tsx`, implementing clue thumbnail stacking display following the AvatarStack structure
- [x] 2.2 Clue image shape uses rounded square (`rounded`), dimensions consistent with character avatar (`h-7 w-7`), stacking offset uses `-space-x-2`
- [x] 2.3 When no `clue_sheet` exists, display an initial letter color block (rounded square), with color determined by a hash of the name
- [x] 2.4 Show `+n` overflow badge when there are more than 4 clues
- [x] 2.5 ClueStack is not rendered when the storyboard has no associated clues

## 3. Clue Hover Popover (CluePopover)

- [x] 3.1 Implement `CluePopover` inside `ClueStack.tsx`: clue image on the left (icon placeholder if no image); name + type tag + description summary on the right
- [x] 3.2 Show "Location" tag (amber color) when `type === "location"`; show "Prop" tag (emerald color) when `type === "prop"`
- [x] 3.3 Popover layout, size, and layer are consistent with AvatarPopover

## 4. Add Type Tag to Character Popover

- [x] 4.1 Modify `AvatarPopover` in `AvatarStack.tsx`: add a "Character" tag (indigo color) next to the character name, unified in style with the clue popover tag

## 5. SegmentCard Header Integration

- [x] 5.1 In `SegmentCard.tsx`, obtain associated clue names (read from `clues_in_segment` / `clues_in_scene`, same pattern as `getCharacterNames`)
- [x] 5.2 Rename `_clues` parameter to `clues`; render `ClueStack` in the header; right side header layout is AvatarStack (left) + vertical line + ClueStack (right), separated by a vertical line (`border-l border-gray-700`)
- [x] 5.3 Replace `DurationBadge` with `DurationSelector`, connecting to the `onUpdatePrompt` callback

## 6. Validation

- [x] 6.1 Run `pnpm test` to confirm all tests pass
- [x] 6.2 Run `pnpm typecheck` to confirm no TypeScript type errors
