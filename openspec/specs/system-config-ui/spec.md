## ADDED Requirements

### Requirement: Four-Tab Top Navigation Structure

The settings page SHALL extend the top navigation tabs from the original `[config, api-keys]` to four tabs: **ArcReel Agent Configuration** (agent), **AI Image/Video Generation Configuration** (media), **Advanced Configuration** (advanced), and **API Keys** (api-keys).

Each tab's content:
- **ArcReel Agent Configuration**: Anthropic API Key, Base URL, and individual model selection fields
- **AI Image/Video Generation Configuration**: Gemini API Key, Base URL, backend selection, model selection, Vertex credentials, etc.
- **Advanced Configuration**: Rate limit (RPM), request interval, maximum concurrent worker count
- **API Keys**: Existing API Key management functionality (unchanged)

#### Scenario: User Opens System Settings Page
- **WHEN** the user navigates to `/app/settings`
- **THEN** the page SHALL display four top navigation tabs, with the first tab (ArcReel Agent Configuration) activated by default

#### Scenario: Tab Order
- **WHEN** the page renders
- **THEN** tab order SHALL be fixed as: ArcReel Agent Configuration → AI Image/Video Generation Configuration → Advanced Configuration → API Keys

---

### Requirement: Tab-Level Independent Save

Each configuration tab (agent / media / advanced) SHALL provide an independent save operation that saves all modified fields within that tab at once, without affecting other tabs.

#### Scenario: User Modifies Fields in a Tab and Saves
- **WHEN** the user modifies any field within a configuration tab and clicks that tab's save button
- **THEN** the system SHALL PATCH all modified fields within that tab; after a successful save the tab returns to the unmodified state

#### Scenario: Tab Saving State
- **WHEN** a tab save request is in progress
- **THEN** all inputs within the tab SHALL be disabled, and the save button SHALL display a loading state to prevent duplicate submissions

#### Scenario: Tab Save Failure
- **WHEN** the save request returns an error
- **THEN** the system SHALL display an error message near the save button, and field values SHALL retain the user's edited content

#### Scenario: Tab Has No Unsaved Changes
- **WHEN** all field values within the tab are identical to the currently saved values
- **THEN** the save button SHALL be in a disabled state

---

### Requirement: Unsaved Change Awareness — Sticky Save Footer + Tab Badge

When a configuration tab contains unsaved changes, that tab's save footer SHALL be fixed at the bottom of the viewport in a sticky manner, and a dot badge SHALL appear next to the tab label, ensuring users can always notice and trigger a save.

#### Scenario: Tab Contains Unsaved Changes
- **WHEN** the user modifies any field value in the current configuration tab
- **THEN** that tab's save footer SHALL become sticky and fixed at the bottom of the screen, the save button SHALL be highlighted (primary color), and a dot badge (●) SHALL appear next to the tab label

#### Scenario: Tab Has No Unsaved Changes
- **WHEN** all field values in the tab are the same as the saved values
- **THEN** the save footer SHALL render normally at the bottom of the tab content (non-sticky), and the save button SHALL be in a disabled state

#### Scenario: After Tab Save Succeeds
- **WHEN** the save request completes successfully
- **THEN** the sticky state SHALL be released, the save footer SHALL return to the bottom of the tab content, and the tab label badge SHALL disappear

#### Scenario: User Reverts Changes
- **WHEN** the user clicks the "Revert" button in the save footer
- **THEN** all field values in the tab SHALL revert to the last successfully saved values, and the sticky state SHALL be released

#### Scenario: User Switches to Another Tab (With Unsaved Changes)
- **WHEN** the user clicks another tab but the current tab has unsaved changes
- **THEN** the system SHALL allow the switch, and the dot badge on the original tab label SHALL continue to be displayed, reminding the user that tab has unsaved changes

---

### Requirement: Universal Clear Button for All Optional Fields

All non-required configuration fields (including base_url, API keys, and other optional fields) SHALL display a clear (×) button when they have a value. Clicking it SHALL immediately clear the field value and trigger the tab's unsaved state.

#### Scenario: User Clears a Field Value
- **WHEN** an optional field has a value and the user clicks the clear button
- **THEN** the field value SHALL immediately be cleared, the clear button SHALL disappear, and the tab enters the modified state (save footer becomes sticky)

#### Scenario: Field Is Empty
- **WHEN** an optional field value is empty
- **THEN** the clear button SHALL not be displayed

---

### Requirement: Global Entry Warning for Missing Required Configuration

When required system configuration is not fully set, all entry points leading to the settings page SHALL be marked with a red dot badge, reminding users to enter settings and complete configuration.

**Required configuration definition**: All three of the following must be satisfied for the system to function properly:
1. ArcReel Agent API Key (`anthropic_api_key.is_set`)
2. AI image generation backend credentials: if `image_backend = "aistudio"` then `gemini_api_key.is_set`; if `"vertex"` then `vertex_credentials.is_set`
3. AI video generation backend credentials: if `video_backend = "aistudio"` then `gemini_api_key.is_set`; if `"vertex"` then `vertex_credentials.is_set`

#### Scenario: Entering Project Lobby With Incomplete Required Configuration
- **WHEN** the user logs in and enters the project lobby, and required system configuration is incomplete
- **THEN** the settings icon button in the upper right of the project lobby SHALL display a red dot badge

#### Scenario: Inside a Project Workspace With Incomplete Required Configuration
- **WHEN** the user is in any project workspace, and required system configuration is incomplete
- **THEN** the settings icon button in the upper right of the global header SHALL display a red dot badge

#### Scenario: Required Configuration Is Complete
- **WHEN** all required system configuration is set
- **THEN** the settings icon button SHALL not display any badge, consistent with normal state

#### Scenario: After User Completes and Saves Configuration
- **WHEN** the user successfully saves the missing required fields on the settings page
- **THEN** the global entry badge SHALL disappear in real-time, without requiring a page refresh

---

### Requirement: Settings Page Internal Warning for Missing Required Configuration

When required system configuration is incomplete, the settings page SHALL display a warning banner above the tab navigation listing each missing item, with links to quickly jump to the corresponding tab.

#### Scenario: Anthropic API Key Not Configured
- **WHEN** the user enters the settings page and `anthropic_api_key.is_set === false`
- **THEN** the warning banner SHALL include an entry "ArcReel Agent API Key (Anthropic) not configured", with a link to the "ArcReel Agent Configuration" tab

#### Scenario: AI Image Backend Credentials Not Configured (AI Studio)
- **WHEN** `image_backend = "aistudio"` and `gemini_api_key.is_set === false`
- **THEN** the warning banner SHALL include an entry "AI Image API Key (Gemini AI Studio) not configured", with a link to the "AI Image/Video Generation Configuration" tab

#### Scenario: AI Image Backend Credentials Not Configured (Vertex)
- **WHEN** `image_backend = "vertex"` and `vertex_credentials.is_set === false`
- **THEN** the warning banner SHALL include an entry "AI Image Vertex AI credentials not uploaded", with a link to the "AI Image/Video Generation Configuration" tab

#### Scenario: AI Video Backend Credentials Not Configured (AI Studio)
- **WHEN** `video_backend = "aistudio"` and `gemini_api_key.is_set === false`
- **THEN** the warning banner SHALL include an entry "AI Video API Key (Gemini AI Studio) not configured", with a link to the "AI Image/Video Generation Configuration" tab

#### Scenario: AI Video Backend Credentials Not Configured (Vertex)
- **WHEN** `video_backend = "vertex"` and `vertex_credentials.is_set === false`
- **THEN** the warning banner SHALL include an entry "AI Video Vertex AI credentials not uploaded", with a link to the "AI Image/Video Generation Configuration" tab

#### Scenario: Deduplication of Identical Missing Reasons
- **WHEN** `image_backend` and `video_backend` use the same provider and that provider's credentials are both unconfigured
- **THEN** the warning banner SHALL merge them into one entry (e.g., "AI Image/Video API Key (Gemini AI Studio) not configured"), without redundant display

#### Scenario: All Required Configuration Complete
- **WHEN** all required system configuration is set
- **THEN** the settings page SHALL not display the warning banner

---

### Requirement: Tab Draft State Isolation

The page SHALL maintain independent draft state for each configuration tab. State between tabs is isolated, and switching tabs SHALL NOT reset unsaved changes in other tabs.

#### Scenario: Simultaneously Modified Fields in Multiple Tabs
- **WHEN** the user has made modifications in multiple tabs (none saved)
- **THEN** each tab with changes SHALL display a dot badge on its label, and each tab's draft state SHALL be independently preserved

#### Scenario: Initial Page Load
- **WHEN** configuration data finishes loading from the API
- **THEN** all tabs SHALL be in the no-unsaved-changes state, with save footers non-sticky and disabled
