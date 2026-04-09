## MODIFIED Requirements

### Requirement: Synchronous Agent Chat Endpoint
The system SHALL provide a `POST /api/v1/agent/chat` synchronous endpoint that accepts user messages (including optional image attachments) and returns the complete Agent reply.

#### Scenario: New Session Conversation
- **WHEN** an authenticated user calls `POST /api/v1/agent/chat`, providing `project_name` and `message`, without passing `session_id`
- **THEN** the system creates a new session, executes the Agent conversation, and returns `session_id`, `reply` (complete text), and `status: "completed"`

#### Scenario: Reuse Existing Session
- **WHEN** an authenticated user calls the endpoint with a valid `session_id`
- **THEN** the system continues the conversation within that session context and returns the reply

#### Scenario: Project Not Found
- **WHEN** the provided `project_name` does not correspond to an existing project
- **THEN** the system returns 404

#### Scenario: Response Timeout
- **WHEN** Agent processing exceeds 120 seconds
- **THEN** the system returns the partially collected response with `status` set to `"timeout"`

#### Scenario: Send Message with Image Attachments
- **WHEN** an authenticated user calls `POST /api/v1/assistant/sessions/{id}/messages` with a request body containing `content` (text) and `images` (up to 5 base64 image objects)
- **THEN** the system combines the text and images into a multimodal message and passes it to the Agent, which can perceive image content and respond accordingly
