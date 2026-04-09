## ADDED Requirements

### Requirement: Virtual Scroll Rendering
TimelineCanvas MUST use virtual scrolling technology to render the SegmentCard list, mounting only SegmentCards near the viewport into the DOM.

#### Scenario: Initial Load With Many Storyboards
- **WHEN** the user opens an episode containing 50 storyboards
- **THEN** only the viewport-visible SegmentCards plus the overscan count (approximately 8-12) are rendered in the DOM, not all 50

#### Scenario: Scrolling Through the Timeline
- **WHEN** the user scrolls down the timeline
- **THEN** SegmentCards entering the viewport range are rendered, and those leaving the viewport range are unmounted

### Requirement: Dynamic Height Support
Virtual scrolling MUST support dynamic heights for SegmentCards, including height changes caused by expand/collapse state.

#### Scenario: Expand/Collapse a Card
- **WHEN** the user expands a SegmentCard causing its height to change
- **THEN** the virtual scroll list correctly adjusts the positions of subsequent items, with no jumps or overlaps

#### Scenario: Estimated Height Differs From Actual Height
- **WHEN** the actual rendered height of a SegmentCard differs from the estimated value
- **THEN** the virtualizer automatically corrects via measureElement, keeping scroll position smooth

### Requirement: Image Lazy Loading
`<img>` tags in the viewport MUST use the browser's native lazy loading attribute.

#### Scenario: Images in the Overscan Area
- **WHEN** a SegmentCard is in the overscan area (rendered but not yet in the visible viewport)
- **THEN** its `<img>` tag has the `loading="lazy"` attribute, and the browser defers loading until it approaches the visible area

### Requirement: Scroll-to-Target Compatibility
Agent- or system-triggered scroll targeting (scrollTarget) MUST work correctly in the virtual scroll environment.

#### Scenario: Agent Triggers Scroll to a Storyboard Not in DOM
- **WHEN** scrollTarget points to a segment ID that is not currently in the DOM
- **THEN** the system scrolls to the target position via virtualizer.scrollToIndex, and the target SegmentCard is rendered and visible

#### Scenario: Highlight After Scroll-to-Target
- **WHEN** scroll targeting has reached the target segment
- **THEN** the target SegmentCard executes a flash highlight animation, consistent with current behavior
