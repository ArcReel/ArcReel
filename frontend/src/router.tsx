// router.tsx — Route definitions for the studio layout

import { useEffect, useRef } from "react";
import { Route, Switch, Redirect, useParams } from "wouter";
import { useTranslation } from "react-i18next";
import { StudioLayout } from "@/components/layout";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { LoginPage } from "@/components/pages/LoginPage";
import { NotFoundPage } from "@/components/pages/NotFoundPage";
import { ToastOverlay } from "@/components/layout/ToastOverlay";
import { LazyBoundary } from "@/components/shared/LazyBoundary";
import { LoadingState } from "@/components/shared/LoadingState";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useTrackReturnTo } from "@/components/shared/page-shell/return-to";
import {
  buildDemoProjectData,
  buildDemoScripts,
  DEMO_PROJECT_NAME,
  isDemoProject,
} from "@/onboarding/demo-project";
import { setApiReadOnly } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAuthStore } from "@/stores/auth-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { errMsg } from "@/utils/async";
import { lazyNamed } from "@/utils/lazy-component";
import {
  ROUTE_APP,
  ROUTE_APP_ASSETS,
  ROUTE_APP_PROJECTS,
  ROUTE_APP_SETTINGS,
  WORKSPACE_ROUTE_SETTINGS,
} from "@/app-routes";

// Route-split noninitial pages; lobby/workspaces/login are landing pages loaded with entries,
// while global settings with Recharts, project settings and libraries load on entry.
const SystemConfigPage = lazyNamed(() => import("@/components/pages/SystemConfigPage"), "SystemConfigPage");
const ProjectSettingsPage = lazyNamed(
  () => import("@/components/pages/ProjectSettingsPage"),
  "ProjectSettingsPage",
);
const AssetLibraryPage = lazyNamed(() => import("@/components/pages/AssetLibraryPage"), "AssetLibraryPage");
// Onboarding driver.js loads separately alongside pages, producing no initial DOM or placeholders.
const OnboardingTour = lazyNamed(() => import("@/onboarding/OnboardingTour"), "OnboardingTour");

/** Record last visited app pages for settings/library Back destinations. */
function ReturnToTracker() {
  useTrackReturnTo();
  return null;
}

/**
 * Prefetch settings while onboarding runs: crossing from lobbies requires anchors within bounded waits;
 * fetching chunks only at navigation would consume those waits.
 */
function OnboardingChunkPreloader() {
  const active = useOnboardingStore((s) => s.active);
  useEffect(() => {
    if (active) SystemConfigPage.preload();
  }, [active]);
  return null;
}

// ---------------------------------------------------------------------------
// ConfigStatusLoader centrally fetches configuration completeness after login.
// ---------------------------------------------------------------------------

/**
 * Single configuration-completeness loader (red dots/required-setting notices), mounted at route roots across navigation.
 * Singleton initialization covers home/settings/projects, without relying on particular page mounts
 * fetching. Initial failures such as unready backends retry with backoff for a bounded number of attempts, without manual refresh.
 */
function ConfigStatusLoader() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  // react-doctor-disable-next-line react-doctor/effect-needs-cleanup -- async tick stores backoff timer IDs in effect-local variables; returned cleanup clearTimeout/sets cancelled
  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      await useConfigStatusStore.getState().fetch();
      if (cancelled) return;
      if (!useConfigStatusStore.getState().initialized && attempts < 5) {
        attempts += 1;
        timer = setTimeout(() => void tick(), 800 * attempts);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isAuthenticated]);

  return null;
}

// ---------------------------------------------------------------------------
// AuthGuard — redirects to /login when not authenticated
// ---------------------------------------------------------------------------

function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();

  if (isLoading) {
    return <LoadingState variant="screen" />;
  }

  if (!isAuthenticated) {
    // `~` targets top-level /login from AuthGuard nested routes
    // such as /app/projects/:projectName; relative paths append nested bases,
    // requiring absolute navigation for real login routes.
    // Retain complete window.location URLs; nested useLocation is relative,
    // enabling postlogin returns.
    const from = window.location.pathname + window.location.search + window.location.hash;
    return <Redirect to={`~/login?from=${encodeURIComponent(from)}`} />;
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
  const { t } = useTranslation("onboarding");
  // Ref-expose t for initial-load onError so language changes do not rebuild t/effect dependencies
  // and reload whole real projects; effects intentionally exclude t below.
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  // Project lifecycle clears old Agent state, then loads by project types.
  // Exclude `t`: language changes reinject demo constants only, never rerun real-project
  // loads clearing stores, flashing empty states or deleting Agent session state.
  useEffect(() => {
    if (!projectName) return;

    const assistantState = useAssistantStore.getState();
    assistantState.setSessions([]);
    assistantState.setCurrentSessionId(null);
    assistantState.resetTimeline();
    assistantState.setSessionStatus(null);
    assistantState.setIsDraftSession(false);

    // Demo projects do not exist on disk; constants avoid requests and lock writes throughout
    // lifecycles, with data injected by following effects.
    if (isDemoProject(projectName)) {
      setApiReadOnly(true);
      setProjectDetailLoading(false);
      return () => {
        setApiReadOnly(false);
        setCurrentProject(null, null);
      };
    }

    // Real-project entry always unlocks prior demo gates.
    setApiReadOnly(false);
    setProjectDetailLoading(true);
    // Persist project names first with empty data: refreshProject permits writes only for matching
    // currentProjectName, otherwise discarding noncurrent responses (projects-store.ts
    // isCurrentProject comments). This also rotates cancellation domains, invalidating prior requests;
    // initial loads share domains with other refreshProject callers.
    setCurrentProject(projectName, null);
    void useProjectsStore
      .getState()
      .refreshProject(projectName, {
        onError: (err) =>
          useAppStore.getState().pushToast(tRef.current("dashboard:project_load_failed", { message: errMsg(err) }), "error"),
      })
      .then((result) => {
        // "cancelled" means unsynchronized after project/domain changes; successors own
        // loading settlement, leaving shared state untouched here.
        if (result === "cancelled") return;
        setProjectDetailLoading(false);
      });

    return () => {
      setCurrentProject(null, null);
    };
  }, [projectName, setCurrentProject, setProjectDetailLoading]);

  // Demo constants follow UI languages; new `t` identities reinject only demos.
  useEffect(() => {
    if (!projectName || !isDemoProject(projectName)) return;
    setCurrentProject(projectName, buildDemoProjectData(t), buildDemoScripts(t));
  }, [projectName, setCurrentProject, t]);

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
  // Leave guards wrap every route, asking before in-app navigation with unsaved edits.
  return (
    <LeaveGuardProvider>
      <ConfigStatusLoader />
      <ReturnToTracker />
      <OnboardingChunkPreloader />
      <LazyBoundary variant="none">
        <OnboardingTour />
      </LazyBoundary>
      {/* Lazy pages show full-page loading/final failures; workspace canvases have local boundaries and never fall back here. */}
      <LazyBoundary variant="screen">
        <Switch>
          {/* Login page */}
          <Route path="/login" component={LoginPage} />

          {/* Root redirects to projects list */}
          <Route path="/">
            <Redirect to="/app/projects" />
          </Route>

          {/* /app and /app/ also redirect to projects list */}
          <Route path={ROUTE_APP}>
            <Redirect to={ROUTE_APP_PROJECTS} />
          </Route>

          {/* Projects list */}
          <Route path={ROUTE_APP_PROJECTS}>
            <AuthGuard>
              <ProjectsPage />
            </AuthGuard>
          </Route>

          {/* System settings */}
          <Route path={ROUTE_APP_SETTINGS}>
            <AuthGuard>
              <SystemConfigPage />
            </AuthGuard>
          </Route>

          {/* Asset library */}
          <Route path={ROUTE_APP_ASSETS}>
            <AuthGuard>
              <AssetLibraryPage />
            </AuthGuard>
          </Route>

          {/* Demo projects lack usable project settings because backends have none; direct URLs/bookmarks/external links
              bypass GlobalHeader redirects, so guard routes again, pointing to globals.
              Place before general project-setting routes, since wouter matches declaration order. */}
          <Route path={`${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}/${WORKSPACE_ROUTE_SETTINGS}`}>
            <Redirect to={ROUTE_APP_SETTINGS} />
          </Route>

          {/* Project settings — full-screen, must be before the nested workspace route */}
          <Route path={`${ROUTE_APP_PROJECTS}/:projectName/${WORKSPACE_ROUTE_SETTINGS}`}>
            <AuthGuard>
              <ProjectSettingsPage />
            </AuthGuard>
          </Route>

          {/* Studio workspace (three-column layout); inner canvas Switch fallbacks show unknown-subpath empty states. */}
          <Route path={`${ROUTE_APP_PROJECTS}/:projectName`} nest>
            <AuthGuard>
              <StudioWorkspace />
            </AuthGuard>
          </Route>

          {/* 404 */}
          <Route>
            <NotFoundPage />
          </Route>
        </Switch>
      </LazyBoundary>
      <ToastOverlay />
    </LeaveGuardProvider>
  );
}
