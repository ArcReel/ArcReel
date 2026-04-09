## ADDED Requirements

### Requirement: API Key Generation
The system SHALL provide an API Key creation interface that generates keys in the format `arc-` + 32 random characters, returns the full key (visible only at creation time), and stores only the SHA-256 hash in the database.

#### Scenario: Successfully Create an API Key
- **WHEN** an authenticated user calls `POST /api/v1/api-keys` with a `name` parameter
- **THEN** the system returns a response containing the full `key`, `name`, `key_prefix`, `created_at`, `expires_at`, with status code 201

#### Scenario: Duplicate Name at Creation
- **WHEN** an authenticated user creates an API Key with the same name as an existing key
- **THEN** the system returns a 409 error

### Requirement: API Key List Query
The system SHALL provide an API Key list query interface that returns metadata for all keys (without the full key).

#### Scenario: Query API Key List
- **WHEN** an authenticated user calls `GET /api/v1/api-keys`
- **THEN** the system returns `id`, `name`, `key_prefix`, `created_at`, `expires_at`, `last_used_at` for all keys

### Requirement: API Key Deletion (Revocation)
The system SHALL provide an API Key deletion interface that immediately invalidates the key.

#### Scenario: Successfully Delete an API Key
- **WHEN** an authenticated user calls `DELETE /api/v1/api-keys/{key_id}`
- **THEN** the system deletes that key record, and subsequent requests using that key return 401

#### Scenario: Delete a Non-Existent Key
- **WHEN** an authenticated user deletes a non-existent key_id
- **THEN** the system returns 404

### Requirement: Bearer Token Authentication Routing
The system SHALL determine the authentication mode in `_verify_and_get_payload` based on the token prefix: tokens starting with `arc-` go through the API Key validation path; otherwise they go through the JWT validation path.

#### Scenario: API Key Authentication Succeeds
- **WHEN** a request carries `Authorization: Bearer arc-xxxxx` and that key exists in the database and has not expired
- **THEN** the system returns `{"sub": "apikey:<key_name>", "via": "apikey"}` payload and updates `last_used_at`

#### Scenario: API Key Has Expired
- **WHEN** a request carries a validly formatted API Key but it has exceeded `expires_at`
- **THEN** the system returns 401

#### Scenario: API Key Does Not Exist
- **WHEN** a request carries an `arc-` prefixed token but the hash does not match any database record
- **THEN** the system returns 401

#### Scenario: JWT Authentication Is Not Affected
- **WHEN** a request carries a Bearer token not starting with `arc-`
- **THEN** the system processes it through the original JWT validation flow

### Requirement: API Key Caching
The system SHALL use an in-memory cache (LRU, TTL 5 minutes) for API Key query results to reduce database queries.

#### Scenario: Cache Hit
- **WHEN** the same API Key makes multiple requests within 5 minutes
- **THEN** only the first request triggers a database query; subsequent ones are read from the cache

#### Scenario: Cache Invalidation After Key Deletion
- **WHEN** an API Key is deleted
- **THEN** that key's cache entry SHALL be immediately cleared
