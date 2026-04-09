# batch-notification-aggregation Specification

## Purpose
TBD - created by archiving change timeline-perf-and-notifications. Update Purpose after archive.
## Requirements
### Requirement: Aggregate Notifications for Same-Type Changes
When an SSE change batch contains multiple changes of the same type, the system MUST aggregate them into a single notification rather than displaying just one of them.

#### Scenario: Batch Add Characters
- **WHEN** the Agent adds 3 characters in batch (Zhang San, Li Si, Wang Wu), and the SSE change batch contains 3 `character:created` changes
- **THEN** the system displays one aggregated toast notification: "Added 3 characters: Zhang San, Li Si, Wang Wu"

#### Scenario: Batch Add Clues
- **WHEN** the Agent adds 2 clues in batch (Weapon, Diary), and the SSE change batch contains 2 `clue:created` changes
- **THEN** the system displays one aggregated toast notification: "Added 2 clues: Weapon, Diary"

#### Scenario: Single Change Retains Original Format
- **WHEN** the SSE change batch contains only 1 `character:created` change
- **THEN** the notification text retains the current behavior format (e.g., "Character 'Zhang San' created")

### Requirement: Grouped Display for Different Change Types
Different types of changes MUST be displayed in groups, with each group independently generating one notification.

#### Scenario: Mixed-Type Changes
- **WHEN** an SSE batch contains both 2 `character:created` changes and 1 `episode:created` change
- **THEN** the system generates two toast notifications: one about characters and one about the episode

### Requirement: Workspace Notification Aggregation
Workspace notifications (persistent notifications, not toasts) MUST also be displayed in aggregated form, navigating to the location of the first change in the group.

#### Scenario: Workspace Notification for Batch Character Creation
- **WHEN** the Agent adds 3 characters in batch and the source is not "webui"
- **THEN** one workspace notification is generated with aggregated text; clicking it navigates to the first character

### Requirement: Long List Truncation
When the number of same-type changes exceeds a threshold, the notification text MUST be truncated to maintain readability.

#### Scenario: More Than 5 Same-Type Changes
- **WHEN** an SSE batch contains 8 `segment:updated` changes
- **THEN** the notification text is truncated, e.g., "Updated 8 storyboards: seg_001, seg_002... etc."
