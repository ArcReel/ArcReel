## Why

The system settings page suffers from unclear categorization, inconsistent interactions (only some fields have clear buttons), and poor usability (the save button is at the bottom of the page and requires scrolling to see), resulting in a poor configuration experience for users. A comprehensive refactoring is needed to improve usability and consistency.

## What Changes

- **Multi-tab top navigation structure**: Split all configuration into four top navigation tabs alongside the existing "API Keys": **ArcReel Agent Configuration**, **AI Image/Video Generation Configuration**, **Advanced Configuration**, **API Keys**
- **Block-level save + unsaved change awareness**: Each tab has an independent save button; when any field within a tab is modified, the save button is highlighted and fixed at the bottom of the screen in a sticky manner, ensuring users can notice and complete the save without scrolling
- **Unified clear interaction**: Add clear buttons to all optional configuration fields (base_url, API keys, etc.) uniformly, eliminating the existing inconsistent interactions
- **Missing required configuration warnings**: When the ArcReel Agent API Key (Anthropic) or AI generation backend (AI Studio / Vertex AI, one of the two) is not configured, the system cannot function properly; clear warnings need to be shown at the settings entry point in the project lobby and on the settings page itself
- **Design standards**: Use the `/frontend-design` skill for UI design and the `/vercel-react-best-practices` skill for frontend development

## Capabilities

### New Capabilities

- `system-config-ui`: UI interaction specification for the system settings page, including tab navigation structure, tab-level save mechanism, unsaved change awareness design, unified clear button specification, and missing required configuration warnings

### Modified Capabilities

(No existing spec files need modification; this change only involves frontend UI layer changes and does not affect API contracts or data structures)

## Impact

- **Primary file**: `frontend/src/components/pages/SystemConfigPage.tsx` (requires significant refactoring)
- **Related files**: `frontend/src/components/pages/ProjectsPage.tsx` (settings entry warning badge), `frontend/src/components/layout/GlobalHeader.tsx` (settings entry warning badge)
- **Not affected**: Backend API, type definitions (`types/system.ts`), backend routes (`server/routers/system_config.py`)
- **Dependencies**: No new external dependencies; uses existing Tailwind CSS styling system
