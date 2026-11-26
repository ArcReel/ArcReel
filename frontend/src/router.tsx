
import { useEffect, useRef } from "react";
import { Route, Switch, Redirect, useLocation, useParams } from "wouter";
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
import {
  shouldLoadEpisodeScript,
  normalizeScriptFile,
  useProjectsStore,
} from "@/stores/projects-store";
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

const SystemConfigPage = lazyNamed(() => import("@/components/pages/SystemConfigPage"), "SystemConfigPage");
const ProjectSettingsPage = lazyNamed(
  () => import("@/components/pages/ProjectSettingsPage"),
  "ProjectSettingsPage",
);
const AssetLibraryPage = lazyNamed(() => import("@/components/pages/AssetLibraryPage"), "AssetLibraryPage");
const OnboardingTour = lazyNamed(() => import("@/onboarding/OnboardingTour"), "OnboardingTour");

function ReturnToTracker() {
  useTrackReturnTo();
  return null;
}

function OnboardingChunkPreloader() {
  const active = useOnboardingStore((s) => s.active);
  useEffect(() => {
    if (active) SystemConfigPage.preload();
  }, [active]);
  return null;
}


function ConfigStatusLoader() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  // react-doctor-disable-next-line react-doctor/effect-needs-cleanup -- 退避计时器在异步 tick 里设置，id 存在 effect 局部变量，返回的 cleanup 统一 clearTimeout 并置 cancelled
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


function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();

  if (isLoading) {
    return <LoadingState variant="screen" />;
  }

  if (!isAuthenticated) {
    // 用 `~` 前缀跳到顶层 /login：AuthGuard 可能渲染在 nest 嵌套路由内
    // （/app/projects/:projectName），此时相对路径会被拼到嵌套 base 之后，
    // 必须用绝对路径才能落到真正的 /login。
    // 带上完整原始 URL（取 window.location，nest 内 useLocation 只是相对路径），
    // 登录成功后据此回跳。
    const from = window.location.pathname + window.location.search + window.location.hash;
    return <Redirect to={`~/login?from=${encodeURIComponent(from)}`} />;
  }

  return <>{children}</>;
}


function parseWorkspaceEpisode(location: string): number | null {
  const match = /^\/episodes\/(\d+)(?:\/|$)/.exec(location);
  if (!match) return null;
  const num = Number(match[1]);
  return Number.isInteger(num) && num > 0 ? num : null;
}

function StudioWorkspace() {
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName ?? null;
  const [location] = useLocation();
  const routeEpisode = parseWorkspaceEpisode(location);
  const routeEpisodeRef = useRef(routeEpisode);
  useEffect(() => {
    routeEpisodeRef.current = routeEpisode;
  }, [routeEpisode]);
  const { setCurrentProject, setProjectDetailLoading } = useProjectsStore();
  const currentProjectData = useProjectsStore((s) => s.currentProjectName === projectName ? s.currentProjectData : null);
  const { t } = useTranslation("onboarding");
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  useEffect(() => {
    if (!projectName) return;

    const assistantState = useAssistantStore.getState();
    assistantState.setSessions([]);
    assistantState.setCurrentSessionId(null);
    assistantState.resetTimeline();
    assistantState.setSessionStatus(null);
    assistantState.setIsDraftSession(false);

    if (isDemoProject(projectName)) {
      setApiReadOnly(true);
      setProjectDetailLoading(false);
      return () => {
        setApiReadOnly(false);
        setCurrentProject(null, null);
      };
    }

    let disposed = false;
    setApiReadOnly(false);
    setProjectDetailLoading(true);
    setCurrentProject(projectName, null);
    const initialEpisode = routeEpisodeRef.current;
    if (initialEpisode !== null) {
      void useProjectsStore
        .getState()
        .loadEpisodeScript(projectName, initialEpisode, `episode_${initialEpisode}.json`, {
          speculative: true,
        });
    }
    void useProjectsStore
      .getState()
      .refreshProject(projectName, {
        onError: (err) =>
          useAppStore.getState().pushToast(tRef.current("dashboard:project_load_failed", { message: errMsg(err) }), "error"),
      })
      .then(() => {
        if (!disposed) setProjectDetailLoading(false);
      });

    return () => {
      disposed = true;
      setCurrentProject(null, null);
    };
  }, [projectName, setCurrentProject, setProjectDetailLoading]);

  useEffect(() => {
    if (!projectName || isDemoProject(projectName)) return;
    const store = useProjectsStore.getState();
    if (routeEpisode === null) {
      if (store.activeEpisodeScript.episode !== null || Object.keys(store.currentScripts).length > 0) {
        store.clearActiveEpisodeScript();
      }
      return;
    }
    if (!currentProjectData) {
      void store.loadEpisodeScript(projectName, routeEpisode, `episode_${routeEpisode}.json`, {
        speculative: true,
      });
      return;
    }
    const epMeta = currentProjectData.episodes.find((entry) => entry.episode === routeEpisode);
    const boundFile = normalizeScriptFile(epMeta?.script_file);
    if (!epMeta || !shouldLoadEpisodeScript(epMeta) || !boundFile) {
      if (
        store.activeEpisodeScript.episode !== routeEpisode ||
        store.activeEpisodeScript.status !== "missing" ||
        Object.keys(store.currentScripts).length > 0
      ) {
        store.clearActiveEpisodeScript();
      }
      return;
    }
    void store.loadEpisodeScript(projectName, routeEpisode, boundFile, {
      onError: (err) =>
        useAppStore.getState().pushToast(tRef.current("dashboard:project_load_failed", { message: errMsg(err) }), "error"),
    });
  }, [projectName, routeEpisode, currentProjectData]);

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


export function AppRoutes() {
  return (
    <LeaveGuardProvider>
      <ConfigStatusLoader />
      <ReturnToTracker />
      <OnboardingChunkPreloader />
      <LazyBoundary variant="none">
        <OnboardingTour />
      </LazyBoundary>
      <LazyBoundary variant="screen">
        <Switch>
          <Route path="/login" component={LoginPage} />

          <Route path="/">
            <Redirect to="/app/projects" />
          </Route>

          <Route path={ROUTE_APP}>
            <Redirect to={ROUTE_APP_PROJECTS} />
          </Route>

          <Route path={ROUTE_APP_PROJECTS}>
            <AuthGuard>
              <ProjectsPage />
            </AuthGuard>
          </Route>

          <Route path={ROUTE_APP_SETTINGS}>
            <AuthGuard>
              <SystemConfigPage />
            </AuthGuard>
          </Route>

          <Route path={ROUTE_APP_ASSETS}>
            <AuthGuard>
              <AssetLibraryPage />
            </AuthGuard>
          </Route>

          {/* 演示项目没有可用的项目级设置（后端不存在该项目）——地址栏直达、书签或外部链接
              都可能绕开 GlobalHeader 里已做的重定向，这里在路由层再挡一次，指向全局设置。
              必须排在下面通用的项目设置路由之前，wouter 按声明顺序匹配。 */}
          <Route path={`${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}/${WORKSPACE_ROUTE_SETTINGS}`}>
            <Redirect to={ROUTE_APP_SETTINGS} />
          </Route>

          <Route path={`${ROUTE_APP_PROJECTS}/:projectName/${WORKSPACE_ROUTE_SETTINGS}`}>
            <AuthGuard>
              <ProjectSettingsPage />
            </AuthGuard>
          </Route>

          <Route path={`${ROUTE_APP_PROJECTS}/:projectName`} nest>
            <AuthGuard>
              <StudioWorkspace />
            </AuthGuard>
          </Route>

          <Route>
            <NotFoundPage />
          </Route>
        </Switch>
      </LazyBoundary>
      <ToastOverlay />
    </LeaveGuardProvider>
  );
}
