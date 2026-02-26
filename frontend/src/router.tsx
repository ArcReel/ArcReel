// router.tsx — Route definitions for the studio layout

import { useEffect } from "react";
import { Route, Switch, Redirect, useParams } from "wouter";
import { StudioLayout } from "@/components/layout";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";

// ---------------------------------------------------------------------------
// StudioWorkspace — loads project data and renders three-column layout
// ---------------------------------------------------------------------------

function StudioWorkspace() {
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName ?? null;
  const { setCurrentProject, setProjectDetailLoading } = useProjectsStore();

  useEffect(() => {
    if (!projectName) return;
    let cancelled = false;

    setProjectDetailLoading(true);
    API.getProject(projectName)
      .then((res) => {
        if (!cancelled) {
          setCurrentProject(projectName, res.project, res.scripts ?? {});
        }
      })
      .catch(() => {
        // Still set the project name so the UI shows something
        if (!cancelled) {
          setCurrentProject(projectName, null);
        }
      })
      .finally(() => {
        if (!cancelled) setProjectDetailLoading(false);
      });

    return () => {
      cancelled = true;
      setCurrentProject(null, null);
    };
  }, [projectName, setCurrentProject, setProjectDetailLoading]);

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
