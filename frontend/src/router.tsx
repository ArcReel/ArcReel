// router.tsx — Route definitions for the studio layout

import { Route, Switch } from "wouter";
import { StudioLayout } from "@/components/layout";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";

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
    <StudioLayout>
      <StudioCanvasRouter />
    </StudioLayout>
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
