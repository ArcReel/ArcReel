// router.tsx — Route definitions for the studio layout

import { useEffect } from "react";
import { Route, Switch, Redirect, useParams } from "wouter";
import { StudioLayout } from "@/components/layout";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import { ProjectSettingsPage } from "@/components/pages/ProjectSettingsPage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ToastOverlay } from "@/components/layout/ToastOverlay";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAuthStore } from "@/stores/auth-store";

// ---------------------------------------------------------------------------
// AuthGuard — redirects to /login when not authenticated
// ---------------------------------------------------------------------------

function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-950 text-gray-500">
        Loading...
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Redirect to="/login" />;
  }

  return <>{children}</>;
}

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

    // Clear previous project's assistant state to ensure session isolation
    const assistantState = useAssistantStore.getState();
    assistantState.setSessions([]);
    assistantState.setCurrentSessionId(null);
    assistantState.setTurns([]);
    assistantState.setDraftTurn(null);
    assistantState.setSessionStatus(null);
    assistantState.setIsDraftSession(false);

    setProjectDetailLoading(true);
    API.getProject(projectName)
      .then((res) => {
        if (!cancelled) {
          setCurrentProject(projectName, res.project, res.scripts ?? {}, res.asset_fingerprints);
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
    <>
      <Switch>
        {/* Login page */}
        <Route path="/login" component={LoginPage} />

        {/* Root redirects to projects list */}
        <Route path="/">
          <Redirect to="/app/projects" />
        </Route>

        {/* /app and /app/ also redirect to projects list */}
        <Route path="/app">
          <Redirect to="/app/projects" />
        </Route>

        {/* Projects list */}
        <Route path="/app/projects">
          <AuthGuard>
            <ProjectsPage />
          </AuthGuard>
        </Route>

        {/* System settings */}
        <Route path="/app/settings">
          <AuthGuard>
            <SystemConfigPage />
          </AuthGuard>
        </Route>

        {/* Project settings — full-screen, must be before the nested workspace route */}
        <Route path="/app/projects/:projectName/settings">
          <AuthGuard>
            <ProjectSettingsPage />
          </AuthGuard>
        </Route>

        {/* Studio workspace (three-column layout) */}
        <Route path="/app/projects/:projectName" nest>
          <AuthGuard>
            <StudioWorkspace />
          </AuthGuard>
        </Route>

        {/* 404 */}
        <Route>
          <NotFoundPage />
        </Route>
      </Switch>
      <ToastOverlay />
    </>
  );
}
