## ADDED Requirements

### Requirement: Issue Download Token
The system SHALL provide a `POST /api/v1/projects/{project_name}/export/token` endpoint that issues a short-lived download token to authenticated users.

The token is a JWT (HS256), and the payload SHALL contain:
- `sub`: the current username
- `project`: the requested project name
- `purpose`: fixed value `"download"`
- `exp`: issue time + 300 seconds (5 minutes)

The endpoint SHALL return JSON: `{ "download_token": "<jwt>", "expires_in": 300 }`.

#### Scenario: Authenticated User Successfully Obtains a Download Token
- **WHEN** an authenticated user calls `POST /api/v1/projects/{name}/export/token` for an existing project
- **THEN** the system returns 200, with a response body containing the `download_token` string and `expires_in: 300`

#### Scenario: Unauthenticated User Requests Download Token
- **WHEN** a request without a valid Bearer JWT calls `POST /api/v1/projects/{name}/export/token`
- **THEN** the system returns 401

#### Scenario: Project Does Not Exist When Requesting Download Token
- **WHEN** an authenticated user calls `POST /api/v1/projects/{name}/export/token` for a non-existent project
- **THEN** the system returns 404

### Requirement: Export Endpoint Accepts Download Token Authentication
The export endpoint `GET /api/v1/projects/{name}/export` SHALL support authentication via the `download_token` query parameter, as an alternative to the Bearer JWT.

Validation rules:
- The token's `purpose` field MUST be `"download"`
- The token's `project` field MUST match the `{name}` in the URL
- The token MUST not be expired

When `download_token` is valid, the request does not need to carry an `Authorization` header.

#### Scenario: Export Using a Valid Download Token
- **WHEN** the request carries a valid `download_token` query parameter to access the export endpoint
- **THEN** the system returns the ZIP file normally without requiring an Authorization header

#### Scenario: Export Using an Expired Download Token
- **WHEN** the request carries an expired `download_token` query parameter to access the export endpoint
- **THEN** the system returns 401, with detail "Download link has expired, please export again"

#### Scenario: Export Using a Download Token With Mismatched Project
- **WHEN** the request carries a `download_token` (issued for project A) to access the export endpoint for project B
- **THEN** the system returns 403, with detail "Download token does not match the target project"

#### Scenario: Download Token Does Not Affect Existing Authentication Methods
- **WHEN** the request carries a valid Bearer JWT (without download_token) to access the export endpoint
- **THEN** the system returns the ZIP file normally (backward compatible)

### Requirement: Authentication Middleware Passes Through Download Token
The authentication middleware SHALL handle export endpoint requests specially: when a request contains a `download_token` query parameter, it SHALL delegate validation to the export endpoint itself, and the middleware SHALL not intercept it.

#### Scenario: Middleware Passes Through Export Requests With Download Token
- **WHEN** a request without an Authorization header but with a `download_token` query parameter accesses `/api/v1/projects/{name}/export`
- **THEN** the authentication middleware passes the request through without returning 401
