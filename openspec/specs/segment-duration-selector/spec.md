## ADDED Requirements

### Requirement: Interactive Storyboard Duration Switching

The duration display element in the SegmentCard header SHALL support user click to open a selector, allowing switching between three options — 4s, 6s, and 8s. After selection, the new value SHALL be written to the backend via the `onUpdatePrompt` callback, and the episode total duration SHALL be refreshed after saving completes.

#### Scenario: Click Duration Badge to Open Selector

- **WHEN** the user clicks the duration badge in the SegmentCard header (e.g., "4s")
- **THEN** a Popover opens listing "4s", "6s", and "8s" buttons, with the current value highlighted

#### Scenario: Select New Duration and Save

- **WHEN** the user clicks a duration option in the popup selector (e.g., "6s")
- **THEN** the Popover closes, the duration badge immediately displays the new value "6s", and `onUpdatePrompt(segmentId, "duration_seconds", 6)` is triggered to save to the backend

#### Scenario: Cancel Selection

- **WHEN** the user clicks outside the Popover
- **THEN** the Popover closes, and the duration badge retains its original value unchanged

#### Scenario: Read-Only When No onUpdatePrompt

- **WHEN** SegmentCard is not provided the `onUpdatePrompt` prop (read-only mode)
- **THEN** the duration badge is not clickable and its appearance is consistent with the read-only state (no hover effect)

### Requirement: Episode Total Duration Auto-Update

The total duration displayed in the TimelineCanvas header SHALL automatically update whenever any storyboard duration changes and the project data is refreshed, without any additional action required.

#### Scenario: Total Duration Updates After Storyboard Duration Change

- **WHEN** the user modifies a storyboard duration, the backend saves successfully, and `refreshProject()` completes
- **THEN** the total duration displayed in the TimelineCanvas header is re-calculated by summing `duration_seconds` across all storyboards, reflecting the latest value
