## Why

OpenClaw is the hottest open-source AI Agent platform in 2026 (GitHub 247k+ stars), supporting capability extensions through AgentSkills. By integrating OpenClaw Skills for ArcReel, users can call ArcReel's project creation, script generation, storyboard production, video generation, and other capabilities through natural language conversation, lowering the barrier to use and expanding customer acquisition channels.

## What Changes

- Add API Key authentication mode: on top of the existing OAuth2 authentication, add `Authorization: Bearer <API_KEY>` authentication, reusing existing API endpoints
- Add API Key management functionality: the frontend provides a Token generation page; the backend provides CRUD interfaces
- Add synchronous Agent conversation endpoint: existing assistant API is SSE streaming-based; a synchronous request-response interface needs to be provided for OpenClaw to call
- Write an OpenClaw AgentSkill definition file (`skill.md`) in the Zopia format describing available tools and calling methods

## Capabilities

### New Capabilities

- `api-key-auth`: API Key generation, management, and Bearer Token authentication mechanism, as a supplementary authentication mode to the existing authentication system
- `sync-agent-chat`: Synchronous Agent conversation endpoint, wrapping the existing SSE streaming assistant into a request-response mode
- `openclaw-skill-def`: Skill definition file complying with the OpenClaw AgentSkill specification, describing the workflow and available APIs

### Modified Capabilities

(No existing capability requirement definitions need modification; only the authentication middleware layer needs to be compatible with the new API Key mode)

## Impact

- **Authentication layer**: `get_current_user` in `server/routers/auth.py` needs to be compatible with API Key authentication
- **Database**: Add `ApiKey` ORM model and migration
- **Backend routes**: Add API Key management routes and synchronous Agent conversation route
- **Frontend**: Add API Key management page (in the settings area)
- **Project root**: Add `skill.md` definition file for OpenClaw to read
