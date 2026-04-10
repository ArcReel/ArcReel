# Frontend Refactor Plan (AI Drama Management Dashboard)

## 1. Goals and Problems

### Current Problems
- Page entry points are scattered (projects, assistant, and usage on separate pages), lacking a unified "management dashboard" information architecture.
- The project page has a sidebar but overall visual hierarchy is weak, layout is unstable, and lacks a "productized dashboard" feel.
- The assistant workspace is accessed from the global top bar, which does not match the "call skill assistant at any time within a project" workflow.
- No landing page for user acquisition, which is unfavorable for SEO and registration conversion.

### Refactor Goals
- Establish a standard SaaS dashboard structure: `Landing -> App Dashboard -> Project Workspace`.
- Unify tech stack to `React + TailwindCSS + shadcn/ui style components`; assistant rendering continues to use `Streamdown`.
- Form a fixed secondary menu within projects: `Overview / Tasks / Clues / Episodes & Scenes`.
- Change assistant to a "global floating button", with a dedicated "session management" menu page.
- Usage statistics as an independent main menu entry, separate from project management.

## 2. Information Architecture (IA)

### Top-Level Routes
- `/`: SaaS Landing Page (acquisition page)
- `/app`: Main dashboard entry point (React)
- `/app?view=projects`: Project list
- `/app?view=usage`: Usage statistics
- `/app?view=assistant`: Conversation management

### Dashboard Main Menu (left sidebar)
- Drama Projects (Projects)
- Conversation Management (Assistant Sessions)
- Usage Statistics (Usage)

### Project Inner Menu (secondary)
- Overview
- Tasks
- Clues
- Episodes/Scenes

## 3. Page Design

### 3.1 Landing Page (`/`)
- Hero section: animated background, core value copy, primary CTA (enter dashboard / registration prompt).
- Acquisition section: WeChat public account QR code placement (visible above the fold or fixed card in second screen).
- Case studies section: showcase "novel -> drama video" representative examples (cover, duration, style tags).
- Footer section: product description, documentation entry, contact information.
- SEO: add title/description/OG base tags, structured semantic tags (`section`/`article`).

### 3.2 Dashboard (`/app`)
- Fixed left main menu + right content area.
- Lightweight global status bar at top (current project, quick search, user entry placeholder).
- Project list as cards; entering a project shows secondary menu content.

### 3.3 Assistant Floating Button
- Persistent in bottom-right corner, supports minimize/expand.
- Automatically associates with current project within project context (sends `project_name` with messages).
- Expands to a side chat panel; message streaming uses Streamdown.
- Separate "conversation management" page for viewing, switching, renaming, and deleting history sessions.

### 3.4 Usage Statistics
- Retain existing statistics capability, rebuilt with unified dashboard style.
- Supports filtering by project, type, status, date with pagination.

## 4. Technical Approach

### 4.1 Frontend Implementation Strategy (minimal intrusion)
- Backend FastAPI APIs unchanged; only new page routes added.
- React as the page rendering layer; API continues to reuse `webui/js/api.js`.
- Components use shadcn/ui semantic style (`Button/Card/Badge/Tabs/Sheet`); implement minimum set needed for project pages first.
- Tailwind continues as styling foundation, with unified design tokens (colors, spacing, border-radius, shadows).
- Streamdown loaded on demand in assistant message area to avoid large initial bundle.

### 4.2 Migration Strategy
- Frontend entry unified as a React single-page application; old pages no longer maintained as fallback paths.
- After migration, only new routes remain: `/`, `/app/projects`, `/app/projects/:name`, `/app/assistant`, `/app/usage`.

## 5. Phased Implementation

### Phase 1 (Completed)
- Added refactor plan document.
- Launched full React single-page application entry (`app.html + React Router-style path state`).
- Unified route support: `/`, `/app/projects`, `/app/projects/:name`, `/app/assistant`, `/app/usage`.
- Project page secondary menu (Overview/Tasks/Clues/Episodes & Scenes) displayed in React pages.
- Global floating assistant button + conversation management page (Streamdown streaming rendering).
- Usage statistics page migrated to React.

### Implementation Update (2026-02-07)
- All frontend page routes are now rendered by React; backend only handles API and static asset serving.
- Old static HTML pages and old page scripts have been removed; no compatibility entry points retained.

### Phase 2 (Future)
- Continue improving React project workspace editing capabilities (complete operations for characters/clues/episodes & scenes).
- Tasks page integrates executable actions (generate storyboard, generate video, batch retry, status tracking).
- Clues and episodes & scenes support full CRUD and version operations.

### Phase 3 (Future)
- Landing page SEO enhancements (structured data, case study detail pages, keyword layout).
- Real login/registration (OAuth or invite code) and growth funnel tracking.

## 6. Acceptance Criteria (Phase 1)
- Navigate from `/` to `/app` and complete page navigation.
- Left menu can switch between: Projects, Conversation Management, Usage Statistics.
- Within a project, secondary menu can navigate: Overview / Tasks / Clues / Episodes & Scenes.
- Floating assistant button opens a side panel with streaming message rendering.
- Usage statistics page displays correctly.
