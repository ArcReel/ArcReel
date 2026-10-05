import { startTransition, useEffect, type ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { ChevronLeft, Download, Library, Loader2, Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ROUTE_APP_ASSETS, ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS, episodeEditViewPath } from "@/app-routes";
import { UsageHeaderEntry } from "@/components/usage/UsageHeaderEntry";
import { Button, buttonVariants } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { isDemoProject } from "@/onboarding/demo-project";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import type { WorkspaceNotification } from "@/types";
import { episodeDisplayName } from "@/utils/episode-display";
import { AgentPanelToggle } from "./AgentPanelToggle";
import { ProjectMenu } from "./ProjectMenu";
import { ProjectStatusBar } from "./ProjectStatusBar";
import { useProjectExport } from "./useProjectExport";
import { WorkspaceNotifications } from "./WorkspaceNotificationsDrawer";

/**
 * 工作区顶栏，三段：
 * - 左：回到项目大厅 + 项目切换器
 * - 中：ProjectStatusBar（集进度与项目层的下一步；数据升级失败时是迁移重试）
 * - 右：通知 / 使用记录 / 导出 / 资产库 / 全局设置 / Agent 面板开关
 */
export function GlobalHeader() {
  const { t } = useTranslation(["dashboard", "common", "assets", "onboarding"]);
  const [location, setLocation] = useLocation();
  const { currentProjectData, currentProjectName } = useProjectsStore();
  const setUsagePanelOpen = useAppStore((s) => s.setUsagePanelOpen);
  const triggerScrollTo = useAppStore((s) => s.triggerScrollTo);
  const isConfigComplete = useConfigStatusStore((s) => s.isComplete);
  const fetchConfigStatus = useConfigStatusStore((s) => s.fetch);
  const demoMode = useDemoWorkbench();

  // 导出提示里的剪辑视图链接：在集页时指向当前集，否则指向播出顺序上的第一集；文案用集名。
  const routeEpisode = /\/episodes\/(\d+)/.exec(location)?.[1];
  const projectEpisodes = currentProjectData?.episodes ?? [];
  const editViewEpisodeId = routeEpisode !== undefined ? Number(routeEpisode) : projectEpisodes[0]?.episode;
  const editViewEpisode =
    editViewEpisodeId !== undefined && projectEpisodes.some((ep) => ep.episode === editViewEpisodeId)
      ? { episode: editViewEpisodeId, name: episodeDisplayName(projectEpisodes, editViewEpisodeId, t) }
      : null;
  const projectExport = useProjectExport({
    editViewEpisode,
    onOpenEditView: (episode) => {
      if (!currentProjectName) return;
      setLocation(`~${ROUTE_APP_PROJECTS}/${encodeURIComponent(currentProjectName)}${episodeEditViewPath(episode)}`);
    },
  });
  const exporting = projectExport.exporting !== null;

  // 演示项目在后端没有用量记录，入口整个不渲染。demoMode 在演示→真实切换时先于 store
  // 变为 false，currentProjectName 单独判一次兜住这一帧仍读到旧演示项目名的窗口。
  const usageProjectName =
    demoMode || !currentProjectName || isDemoProject(currentProjectName) ? null : currentProjectName;

  // 导出对话框打开期间切到别的项目或演示项目（如浏览器前进 / 后退复用同一路由实例）时随即关闭：
  // 对话框在打开时记下了当时的项目，留着会在选定范围后导出上一个项目；触发按钮已按 demoMode
  // 禁用，但已打开的对话框不受影响，仍可点击「导出」。
  const closeExport = projectExport.close;
  useEffect(() => {
    closeExport();
  }, [currentProjectName, demoMode, closeExport]);

  // 入口的数据随项目走：切到别的项目或演示项目时清空上一项目的用量，并收起悬浮层。
  useEffect(() => {
    void useUsageHeaderStore.getState().setProject(usageProjectName);
    if (usageProjectName === null) setUsagePanelOpen(false);
  }, [usageProjectName, setUsagePanelOpen]);

  useEffect(() => {
    void fetchConfigStatus();
  }, [fetchConfigStatus]);

  const handleNotificationNavigate = (notification: WorkspaceNotification) => {
    const target = notification.target;
    if (!target) return;
    startTransition(() => {
      setLocation(target.route);
    });
    triggerScrollTo({
      type: target.type,
      id: target.id,
      route: target.route,
      highlight_style: target.highlight_style ?? "flash",
      expires_at: Date.now() + 3000,
    });
  };

  return (
    <header className="@container/header grid h-12 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(min-content,1fr)] items-center gap-3 border-b border-border bg-background px-2">
      <div className="flex min-w-0 items-center gap-1">
        <Link href={`~${ROUTE_APP_PROJECTS}`} className={buttonVariants({ variant: "ghost", size: "sm" })}>
          <ChevronLeft aria-hidden data-icon="inline-start" />
          {t("dashboard:projects")}
        </Link>
        <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
        <ProjectMenu />
      </div>

      <div className="flex justify-center">
        {currentProjectName ? <ProjectStatusBar key={currentProjectName} projectName={currentProjectName} /> : null}
      </div>

      <div className="flex items-center justify-end gap-1">
        <WorkspaceNotifications onNavigate={handleNotificationNavigate} />
        {usageProjectName && <UsageHeaderEntry projectName={usageProjectName} />}
        <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-border" />
        <span className="flex" data-onboarding={ONBOARDING_ANCHORS.workbenchExport}>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={t("dashboard:export_project_zip")}
                  aria-busy={exporting}
                  disabled={!currentProjectName || exporting || demoMode}
                  onClick={() => currentProjectName && projectExport.open(currentProjectName)}
                />
              }
            >
              {exporting ? (
                <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
              ) : (
                <Download aria-hidden data-icon="inline-start" />
              )}
              {exporting ? t("dashboard:exporting_zip") : t("dashboard:export_zip")}
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {demoMode ? t("onboarding:demo_action_unavailable") : t("dashboard:export_project_zip")}
            </TooltipContent>
          </Tooltip>
        </span>
        <IconLink href={`~${ROUTE_APP_ASSETS}`} label={t("assets:library_title")}>
          <Library aria-hidden />
        </IconLink>
        <IconLink href={`~${ROUTE_APP_SETTINGS}`} label={t("dashboard:global_settings")}>
          <Settings aria-hidden />
          {!isConfigComplete && (
            <span
              role="img"
              aria-label={t("common:config_incomplete")}
              className="absolute top-1 right-1 size-2 rounded-full bg-warn"
            />
          )}
        </IconLink>
        <AgentPanelToggle />
      </div>
      {projectExport.element}
    </header>
  );
}

function IconLink({ href, label, children }: { href: string; label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link href={href} aria-label={label} className={buttonVariants({ variant: "ghost", size: "icon", className: "relative" })} />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
