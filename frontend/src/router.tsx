// router.tsx — Route definitions for the studio layout

import { useEffect } from "react";
import { Route, Switch, Redirect, useParams } from "wouter";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { StudioLayout } from "@/components/layout";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import { ProjectSettingsPage } from "@/components/pages/ProjectSettingsPage";
import { AssetLibraryPage } from "@/components/pages/AssetLibraryPage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ToastOverlay } from "@/components/layout/ToastOverlay";
import { OnboardingTour } from "@/onboarding/OnboardingTour";
import {
  buildDemoProjectData,
  buildDemoScripts,
  DEMO_PROJECT_NAME,
  isDemoProject,
} from "@/onboarding/demo-project";
import { API, setApiReadOnly } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAuthStore } from "@/stores/auth-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import {
  ROUTE_APP,
  ROUTE_APP_ASSETS,
  ROUTE_APP_PROJECTS,
  ROUTE_APP_SETTINGS,
  WORKSPACE_ROUTE_SETTINGS,
} from "@/app-routes";

// ---------------------------------------------------------------------------
// ConfigStatusLoader — 登录后集中拉取一次配置完整性状态
// ---------------------------------------------------------------------------

/**
 * 配置完整性（红点 / 必需设置提醒）的单点加载器，始终挂载在路由根，跨页面导航存活。
 * 单例 store 一次初始化即覆盖所有落地页（首页 / 设置 / 项目），不再依赖某个具体页面
 * 是否在 mount 时拉取。首次失败（如后端尚未就绪）时带界次数退避重试，无需手动刷新页面。
 */
function ConfigStatusLoader() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

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
  const { t } = useTranslation("common");

  if (isLoading) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex h-screen items-center justify-center gap-2 bg-bg text-[13px] text-text-4"
      >
        <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" />
        <span>{t("loading")}</span>
      </div>
    );
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

// ---------------------------------------------------------------------------
// StudioWorkspace — loads project data and renders three-column layout
// ---------------------------------------------------------------------------

function StudioWorkspace() {
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName ?? null;
  const { setCurrentProject, setProjectDetailLoading } = useProjectsStore();
  const { t } = useTranslation("onboarding");

  // 项目生命周期：清空上一个项目的 assistant 状态，再按项目类型取数据。
  // 依赖里不含 `t` —— 界面语言变化只该重灌演示常量（见下一个 effect），不该让真实项目
  // 整条加载重跑（会先清空 store 闪一次空态，还会连带清掉助手会话状态）。
  useEffect(() => {
    if (!projectName) return;

    const assistantState = useAssistantStore.getState();
    assistantState.setSessions([]);
    assistantState.setCurrentSessionId(null);
    assistantState.resetTimeline();
    assistantState.setSessionStatus(null);
    assistantState.setIsDraftSession(false);

    // 引导演示项目在磁盘上并不存在：数据直接来自前端常量，不发请求，并在这段生命周期内
    // 锁死写请求。数据本身由下一个 effect 灌入。
    if (isDemoProject(projectName)) {
      setApiReadOnly(true);
      setProjectDetailLoading(false);
      return () => {
        setApiReadOnly(false);
        setCurrentProject(null, null);
      };
    }

    // 进真实项目一律解锁，不让上一次演示的闸门残留下来
    setApiReadOnly(false);
    setProjectDetailLoading(true);
    const controller = new AbortController();
    API.getProject(projectName, { signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setCurrentProject(projectName, res.project, res.scripts ?? {}, res.asset_fingerprints);
      })
      .catch(() => {
        // Still set the project name so the UI shows something
        if (controller.signal.aborted) return;
        setCurrentProject(projectName, null);
      })
      .finally(() => {
        // 已被接管方作废时不动共享状态，否则会踩到新一轮加载
        if (!controller.signal.aborted) setProjectDetailLoading(false);
      });

    return () => {
      controller.abort();
      setCurrentProject(null, null);
    };
  }, [projectName, setCurrentProject, setProjectDetailLoading]);

  // 演示数据随界面语言走：`t` 换身份时重灌一遍常量，只影响演示项目
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
  return (
    <>
      <ConfigStatusLoader />
      <OnboardingTour />
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

        {/* 演示项目没有可用的项目级设置（后端不存在该项目）——地址栏直达、书签或外部链接
            都可能绕开 GlobalHeader 里已做的重定向，这里在路由层再挡一次，指向全局设置。
            必须排在下面通用的项目设置路由之前，wouter 按声明顺序匹配。 */}
        <Route path={`${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}/${WORKSPACE_ROUTE_SETTINGS}`}>
          <Redirect to={ROUTE_APP_SETTINGS} />
        </Route>

        {/* Project settings — full-screen, must be before the nested workspace route */}
        <Route path={`${ROUTE_APP_PROJECTS}/:projectName/${WORKSPACE_ROUTE_SETTINGS}`}>
          <AuthGuard>
            <ProjectSettingsPage />
          </AuthGuard>
        </Route>

        {/* Studio workspace (three-column layout) */}
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
      <ToastOverlay />
    </>
  );
}
