## ADDED Requirements

### Requirement: Per-Entity Version Tracking
The system MUST maintain an independent version number for each entity (key format `entity_type:entity_id`), replacing the global mediaRevision counter. All media asset consumers (SegmentCard, CharacterCard, ClueCard, OverviewCanvas, AssetSidebar, AvatarStack, VersionTimeMachine) must migrate to the new mechanism.

#### Scenario: Single Storyboard Image Generation Completes
- **WHEN** the SSE event reports storyboard_ready for segment "seg_001"
- **THEN** only the version number for `segment:seg_001` increments; other entities' version numbers remain unchanged

#### Scenario: Single Video Generation Completes
- **WHEN** the SSE event reports video_ready for segment "seg_003"
- **THEN** only the version number for `segment:seg_003` increments; other entities' version numbers remain unchanged

#### Scenario: Character Design Image Generation Completes
- **WHEN** the SSE event reports updated for character "Zhang San"
- **THEN** only the version number for `character:Zhang San` increments; other entities' version numbers remain unchanged

#### Scenario: Clue Design Image Generation Completes
- **WHEN** the SSE event reports updated for clue "Weapon"
- **THEN** only the version number for `clue:Weapon` increments; other entities' version numbers remain unchanged

#### Scenario: Project Metadata Update
- **WHEN** the SSE event reports updated for the project
- **THEN** the version number for `project:project` increments

### Requirement: Directly Construct Version Key From SSE Events
The system MUST directly construct the version key from the `entity_type` and `entity_id` fields in SSE change events, without needing to derive file paths.

#### Scenario: storyboard_ready Event
- **WHEN** a change event is received with `entity_type: "segment"`, `entity_id: "seg_005"`, `action: "storyboard_ready"`
- **THEN** the version number for key `segment:seg_005` is incremented

#### Scenario: character updated Event
- **WHEN** a change event is received with `entity_type: "character"`, `entity_id: "Zhang San"`, `action: "updated"`
- **THEN** the version number for key `character:Zhang San` is incremented

#### Scenario: clue updated Event
- **WHEN** a change event is received with `entity_type: "clue"`, `entity_id: "Weapon"`, `action: "updated"`
- **THEN** the version number for key `clue:Weapon` is incremented

### Requirement: Each Component Subscribes Precisely
Each media consumer component MUST subscribe only to the version numbers of entities relevant to it.

#### Scenario: SegmentCard Precise Subscription
- **WHEN** the storyboard image for segment "seg_001" finishes generating
- **THEN** only the SegmentCard for segment "seg_001" triggers media URL changes and re-rendering; other SegmentCards are not affected

#### Scenario: CharacterCard Precise Subscription
- **WHEN** the design image for character "Zhang San" finishes generating
- **THEN** only "Zhang San"'s CharacterCard, the corresponding AvatarStack avatar, and the AssetSidebar entry trigger re-rendering; other characters are not affected

#### Scenario: ClueCard Precise Subscription
- **WHEN** the design image for clue "Weapon" finishes generating
- **THEN** only "Weapon"'s ClueCard and AssetSidebar entry trigger re-rendering

#### Scenario: OverviewCanvas Precise Subscription
- **WHEN** the project style image is updated
- **THEN** only the style image in OverviewCanvas triggers re-rendering

#### Scenario: VersionTimeMachine Precise Subscription
- **WHEN** a new version of a resource finishes generating
- **THEN** VersionTimeMachine subscribes to the entity key corresponding to the currently displayed resource and re-fetches the version list only when that entity changes

### Requirement: Full Invalidation Fallback
When the specific changed entity cannot be determined (e.g., the task polling channel), the system MUST retain full cache invalidation as a fallback mechanism.

#### Scenario: Task Completed But No SSE Event
- **WHEN** useProjectAssetSync detects that a task changed from non-succeeded to succeeded
- **THEN** the full invalidation method is called, incrementing the version numbers of all tracked entities uniformly

### Requirement: Asset Re-Generation
When assets are regenerated, the cache invalidation mechanism MUST trigger correctly.

#### Scenario: Storyboard Image Re-Generated
- **WHEN** the user regenerates the storyboard image for segment "seg_001" (the file path is unchanged but the content is updated)
- **THEN** the Worker sends a storyboard_ready event, the frontend increments the version number for `segment:seg_001`, and the browser loads the new content

#### Scenario: Character Design Image Re-Generated
- **WHEN** the user regenerates the design image for character "Zhang San"
- **THEN** the Worker sends a character updated event, the frontend increments the version number for `character:Zhang San`, and the browser loads the new content
