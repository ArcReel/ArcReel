// router.tsx — Route definitions for the new studio layout
// This file coexists with the legacy router (src/react/router.js) during migration.
// A new main.tsx entry point (Task 1.5) will mount <AppRoutes />.

import { Route, Switch } from "wouter";

// ---------------------------------------------------------------------------
// Placeholder components — will be replaced with real components in Phase 1+
// ---------------------------------------------------------------------------

function PlaceholderPage({ name }: { name: string }) {
  return <div className="p-8 text-gray-400">{name} — Coming in Phase 1+</div>;
}

function LandingPagePlaceholder() {
  return <PlaceholderPage name="Landing Page" />;
}

function ProjectsPagePlaceholder() {
  return <PlaceholderPage name="Projects Page" />;
}

// ---------------------------------------------------------------------------
// StudioWorkspace — three-column layout with nested routes
// ---------------------------------------------------------------------------

function StudioWorkspace() {
  return (
    <div className="flex h-screen bg-gray-950 text-gray-100">
      {/* Left sidebar: asset navigation */}
      <aside className="w-[15%] min-w-50 border-r border-gray-800 p-4">
        <PlaceholderPage name="Asset Sidebar" />
      </aside>

      {/* Center canvas: switches based on nested route */}
      <main className="flex-1 overflow-auto p-4">
        <Switch>
          <Route path="/">
            <PlaceholderPage name="Overview Canvas" />
          </Route>
          <Route path="/lorebook">
            <PlaceholderPage name="Lorebook Gallery" />
          </Route>
          <Route path="/episodes/:episodeId">
            {(params) => (
              <PlaceholderPage
                name={`Timeline: Episode ${params.episodeId}`}
              />
            )}
          </Route>
        </Switch>
      </main>

      {/* Right panel: AI co-pilot */}
      <aside className="w-[40%] min-w-90 border-l border-gray-800 p-4">
        <PlaceholderPage name="Agent Co-pilot" />
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Backward-compatibility pages
// ---------------------------------------------------------------------------

function AssistantPagePlaceholder() {
  return <PlaceholderPage name="Assistant Page" />;
}

function TasksPagePlaceholder() {
  return <PlaceholderPage name="Tasks Page" />;
}

function UsagePagePlaceholder() {
  return <PlaceholderPage name="Usage Page" />;
}

// ---------------------------------------------------------------------------
// Top-level route tree
// ---------------------------------------------------------------------------

export function AppRoutes() {
  return (
    <Switch>
      <Route path="/" component={LandingPagePlaceholder} />
      <Route path="/app/projects" component={ProjectsPagePlaceholder} />
      <Route path="/app/projects/:projectName" nest>
        <StudioWorkspace />
      </Route>
      <Route path="/app/assistant" component={AssistantPagePlaceholder} />
      <Route path="/app/tasks" component={TasksPagePlaceholder} />
      <Route path="/app/usage" component={UsagePagePlaceholder} />
      <Route>
        <div className="p-8 text-gray-400">404 — Page not found</div>
      </Route>
    </Switch>
  );
}
