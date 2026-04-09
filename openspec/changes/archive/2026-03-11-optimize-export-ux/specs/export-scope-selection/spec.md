## ADDED Requirements

### Requirement: Export Endpoint Supports scope Parameter
The export endpoint `GET /api/v1/projects/{name}/export` SHALL accept a `scope` query parameter with values `full` or `current`, defaulting to `full`.

- `scope=full`: packages all files in the project directory (existing behavior)
- `scope=current`: skips historical resource files under the `versions/` directory, retaining only the trimmed `versions/versions.json`

#### Scenario: Default scope Is full
- **WHEN** the export request does not carry a scope parameter
- **THEN** the system packages in `full` mode, and the ZIP contains all historical files under the `versions/` directory

#### Scenario: scope=full Exports All Data
- **WHEN** the export request carries `scope=full`
- **THEN** the ZIP contains all files in the project directory (including complete `versions/` content), consistent with existing behavior

#### Scenario: scope=current Skips Historical Version Files
- **WHEN** the export request carries `scope=current`
- **THEN** the ZIP does not contain any files under `versions/storyboards/`, `versions/videos/`, `versions/characters/`, or `versions/clues/`

#### Scenario: Invalid scope Value
- **WHEN** the export request carries `scope=invalid`
- **THEN** the system returns 422, indicating that scope must be `full` or `current`

### Requirement: Current-Version-Only Export Retains Trimmed Version Metadata
When `scope=current`, the ZIP SHALL contain the `versions/versions.json` file, but its content is trimmed: the `versions` array for each resource retains only the one record corresponding to `current_version`.

The trimmed `versions.json` retains the following metadata:
- `current_version` number
- The current version's `prompt` (generation prompt)
- The current version's `created_at` (creation time)
- The current version's `version` number

#### Scenario: Trimmed versions.json Contains Only Current Version Record
- **WHEN** project storyboard E1S01 has 3 versions (current_version=3), and is exported with `scope=current`
- **THEN** the `storyboards.E1S01.versions` array in `versions/versions.json` in the ZIP contains only version 3's record, with `current_version` still set to 3

#### Scenario: Trimmed versions.json Retains Generation Prompt
- **WHEN** exporting with `scope=current` and the current version has prompt metadata
- **THEN** the `prompt` field of the current version record in the trimmed `versions/versions.json` is retained

### Requirement: Export Manifest Marks scope
The `arcreel-export.json` manifest file SHALL contain a `scope` field with value `"full"` or `"current"`, reflecting the actual export scope.

#### Scenario: full Export Manifest scope Is full
- **WHEN** exporting with `scope=full`
- **THEN** the `scope` field in `arcreel-export.json` has value `"full"`

#### Scenario: current Export Manifest scope Is current
- **WHEN** exporting with `scope=current`
- **THEN** the `scope` field in `arcreel-export.json` has value `"current"`

### Requirement: Frontend Export Interaction Supports Scope Selection
The frontend SHALL display a selection dialog after the user clicks the export button, providing two export options:

- **Current Version Only** (recommended): Marked as the recommended option, noting it excludes version history and is smaller in size
- **All Data**: Notes it includes the complete version history

After the user makes a selection, the frontend SHALL:
1. Call `POST /api/v1/projects/{name}/export/token` to obtain a download token
2. Construct the download URL: `/api/v1/projects/{name}/export?download_token=xxx&scope=yyy`
3. Trigger the browser's native download via `window.open` or an `<a>` tag

#### Scenario: User Selects Current Version Only Export
- **WHEN** the user clicks the export button and selects "Current Version Only"
- **THEN** the browser initiates a native download request with `scope=current`, visible in the browser's download manager

#### Scenario: User Selects All Data Export
- **WHEN** the user clicks the export button and selects "All Data"
- **THEN** the browser initiates a native download request with `scope=full`

#### Scenario: User Can Switch Pages During Export
- **WHEN** the user triggers the export download and then switches to another page
- **THEN** the download is not interrupted, as it is taken over by the browser's native download manager

#### Scenario: Download Token Acquisition Fails
- **WHEN** the request to obtain a download token fails (network error or authentication expired)
- **THEN** the frontend displays an error toast notification and does not trigger a download
