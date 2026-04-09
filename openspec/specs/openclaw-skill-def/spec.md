## ADDED Requirements

### Requirement: Dynamic Rendering of Skill Definition File
The system SHALL provide a `GET /skill.md` endpoint that reads the `public/skill.md.template` template file, replaces the `{{BASE_URL}}` placeholder with the actual base URL accessed by the requester (inferred from the `Host` header and scheme), returns the rendered content, and requires no authentication.

#### Scenario: Access skill.md
- **WHEN** any client requests `GET /skill.md`
- **THEN** the system returns the rendered Skill definition file, with all `{{BASE_URL}}` replaced by the actual service address (e.g., `https://my-arcreel.example.com`)

#### Scenario: Different Deployment Addresses
- **WHEN** the user self-deploys at `http://192.168.1.100:1241` and accesses `/skill.md`
- **THEN** the API URLs in the returned file are `http://192.168.1.100:1241/api/v1/...`

### Requirement: Skill Workflow Description
skill.md SHALL describe the complete usage workflow: create project → save settings → multi-turn Agent conversation → view results.

#### Scenario: OpenClaw Reads the Workflow
- **WHEN** the OpenClaw Agent loads skill.md
- **THEN** it can obtain the complete API call sequence and parameter descriptions from it

### Requirement: Skill Tool Definitions
skill.md SHALL define the following core tools and their API endpoints, request/response formats:
- Create project (`POST /api/v1/projects`)
- Get/Update project settings (`GET/PATCH /api/v1/projects/{name}`)
- Agent conversation (`POST /api/v1/agent/chat`)
- Project list (`GET /api/v1/projects`)
- Project details (`GET /api/v1/projects/{name}`)

#### Scenario: Tool Definition Completeness
- **WHEN** the OpenClaw Agent parses the tool definitions in skill.md
- **THEN** each tool SHALL include endpoint path, HTTP method, request parameter descriptions, and response format examples

### Requirement: Authentication Description
skill.md SHALL explain the authentication method: users obtain an API Key (with `arc-` prefix) from the ArcReel settings page and pass it via `Authorization: Bearer <API_KEY>`.

#### Scenario: User Configures Authentication Per Instructions
- **WHEN** the user follows the authentication instructions in skill.md
- **THEN** they can successfully call all API endpoints defined in the Skill

### Requirement: OpenClaw Usage Guide Modal
The frontend project lobby page's top bar SHALL provide a 🦞 OpenClaw button that opens a usage guide Modal when clicked.

#### Scenario: Open the Guide Modal
- **WHEN** the user clicks the 🦞 OpenClaw button in the top bar
- **THEN** a Modal opens containing: a copyable prompt (with a dynamic skill.md URL), a 4-step usage guide, and a "Get API Token" button

#### Scenario: URL in Prompt Dynamically Adapts
- **WHEN** the user accesses from `http://localhost:1241` and opens the guide modal
- **THEN** the URL in the prompt is `http://localhost:1241/skill.md`

#### Scenario: Get API Token
- **WHEN** the user clicks the "Get API Token" button
- **THEN** it navigates to the API Key management page
