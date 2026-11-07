import { startTransition, useEffect, type ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { ChevronLeft, Download, Library, Loader2, Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ROUTE_APP_ASSETS, ROUTE_APP_PROJECTS, ROUTE_APP_SETTINGS, episodeEditViewPath } from "@/app-routes";
import { UsageHeaderEntry } from "@/components/usage/UsageHeaderEntry";
import { Button, buttonVariants } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ONBOARDING_ANCHORS } from "@/onboarding/anchors";
import { DemoReadOnlyBadge } from "@/onboarding/DemoReadOnlyBadge";
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
 * Workspace headers in three sections:
 * - Left: project lobby + switcher (demos add Demo · Readonly badges)
 * - Center: ProjectStatusBar (episode progress/project next steps, or migration retries after upgrade failures)
 * - Right: notifications / usage / export / library / global settings / Agent-panel toggle
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

  // Export editing-view links target current episodes on episode pages, otherwise first broadcast-order episodes; use titles in copy.
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

  // Demos lack backend usage. demoMode becomes false before stores during demo→real switches;
  // separately check currentProjectName while it still holds old demos.
  const usageProjectName =
    demoMode || !currentProjectName || isDemoProject(currentProjectName) ? null : currentProjectName;

  // Project/demo switches while export dialogs open close them immediately, including reused history-route instances:
  // dialogs captured old projects and would export them after scope selection. Disabled triggers follow demoMode,
  // but existing dialogs otherwise remain clickable.
  const closeExport = projectExport.close;
  useEffect(() => {
    closeExport();
  }, [currentProjectName, demoMode, closeExport]);

  // Project-bound usage clears old data/closes overlays on project/demo switches.
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
        {demoMode && <DemoReadOnlyBadge />}
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
          <Link
            href={href}
            aria-label={label}
            // eslint-disable-next-line shadcn/require-static-classes -- Link uses buttonVariants ghost/icon styling; rendering Button as a link adds role="button" through Base UI, so render={<Button />} is unsuitable; TooltipTrigger is unstyled (eslint.config.js UNSTYLED_PRIMITIVES)
            className={buttonVariants({ variant: "ghost", size: "icon", className: "relative" })}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}
