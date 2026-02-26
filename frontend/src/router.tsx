// router.tsx — Route definitions for the studio layout

import { Route, Switch, Redirect } from "wouter";
import { StudioLayout } from "@/components/layout";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { ProjectsPage } from "@/components/pages/ProjectsPage";

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
// Top-level route tree
// ---------------------------------------------------------------------------

export function AppRoutes() {
  return (
    <Switch>
      {/* Root redirects to projects list */}
      <Route path="/">
        <Redirect to="/app/projects" />
      </Route>

      {/* Projects list */}
      <Route path="/app/projects" component={ProjectsPage} />

      {/* Studio workspace (three-column layout) */}
      <Route path="/app/projects/:projectName" nest>
        <StudioWorkspace />
      </Route>

      {/* 404 */}
      <Route>
        <div className="flex h-screen items-center justify-center bg-gray-950 text-gray-400">
          404 — 页面未找到
        </div>
      </Route>
    </Switch>
  );
}
