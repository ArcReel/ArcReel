import { startTransition, useState, useEffect, useRef } from "react";
import { errMsg, voidPromise } from "@/utils/async";
import { useLocation } from "wouter";
import { ChevronLeft, Activity, Settings, Bell, Download, Loader2, Package } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageStore, type UsageStats } from "@/stores/usage-store";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { UsageDrawer } from "./UsageDrawer";
import { WorkspaceNotificationsDrawer } from "./WorkspaceNotificationsDrawer";
import { ExportScopeDialog } from "./ExportScopeDialog";
import { ProjectMenu } from "./ProjectMenu";
import { PhaseStepper } from "./PhaseStepper";

import { API } from "@/api";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { rememberAssetLibraryReturnTo } from "@/components/pages/AssetLibraryPage";
import type { ExportDiagnostics, WorkspaceNotification } from "@/types";

/** 通过隐藏 <a> 触发浏览器下载，避免 window.open 产生空白标签页 */
function triggerBrowserDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

interface GlobalHeaderProps {
  onNavigateBack?: () => void;
}

/**
 * 工作台顶栏（48px，玻璃面板）。三段式 grid：
 * - 左：返回按钮 + ProjectMenu（项目切换菜单）
 * - 中：PhaseStepper（5 阶段胶囊）
 * - 右：通知 / 费用 / 任务雷达 / 导出 / 资产库 / 设置
 */
export function GlobalHeader({ onNavigateBack }: GlobalHeaderProps) {
  const { t } = useTranslation();
  const [, setLocation] = useLocation();
  const { currentProjectData, currentProjectName } = useProjectsStore();
  const { stats } = useTasksStore();
  const { taskHudOpen, setTaskHudOpen, triggerScrollTo, markWorkspaceNotificationRead } =
    useAppStore();
  const { stats: usageStats, setStats: setUsageStats } = useUsageStore();
  const [usageDrawerOpen, setUsageDrawerOpen] = useState(false);
  const [notificationDrawerOpen, setNotificationDrawerOpen] = useState(false);
  const [exportingProject, setExportingProject] = useState(false);
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [jianyingExporting, setJianyingExporting] = useState(false);
  const [exportDiagnostics, setExportDiagnostics] = useState<ExportDiagnostics | null>(null);
  const usageAnchorRef = useRef<HTMLDivElement>(null);
  const notificationAnchorRef = useRef<HTMLDivElement>(null);
  const taskHudAnchorRef = useRef<HTMLDivElement>(null);
  const exportAnchorRef = useRef<HTMLDivElement>(null);
  const isConfigComplete = useConfigStatusStore((s) => s.isComplete);
  const fetchConfigStatus = useConfigStatusStore((s) => s.fetch);
  const workspaceNotifications = useAppStore((s) => s.workspaceNotifications);

  const currentPhase = currentProjectData?.status?.current_phase;
  const runningCount = stats.running + stats.queued;
  const unreadNotificationCount = workspaceNotifications.filter((item) => !item.read).length;

  const completedTaskCount = stats.succeeded + stats.failed;
  useEffect(() => {
    API.getUsageStats(currentProjectName ? { projectName: currentProjectName } : {})
      .then((res) => {
        setUsageStats(res as unknown as UsageStats);
      })
      .catch(() => {});
  }, [currentProjectName, completedTaskCount, setUsageStats]);

  useEffect(() => {
    void fetchConfigStatus();
  }, [fetchConfigStatus]);

  // Format cost display – show multi-currency summary
  const costByCurrency = usageStats?.cost_by_currency ?? {};
  const costEntries = Object.entries(costByCurrency).filter(([, v]) => v > 0);
  const cnyCost = costEntries.find(([c]) => c === "CNY")?.[1] ?? 0;
  const usdCost = costEntries.find(([c]) => c === "USD")?.[1] ?? 0;
  const otherCosts = costEntries.filter(([c]) => c !== "CNY" && c !== "USD");
  const costTooltip =
    costEntries.length > 0
      ? costEntries
          .map(([currency, amount]) => `${currency === "CNY" ? "¥" : "$"}${amount.toFixed(2)}`)
          .join(" + ")
      : "$0.00";

  const handleNotificationNavigate = (notification: WorkspaceNotification) => {
    if (!notification.target) return;
    const target = notification.target;

    markWorkspaceNotificationRead(notification.id);
    setNotificationDrawerOpen(false);
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

  const handleJianyingExport = async (
    episode: number,
    draftPath: string,
    jianyingVersion: string,
  ) => {
    if (!currentProjectName || jianyingExporting) return;

    setJianyingExporting(true);
    try {
      const { download_token } = await API.requestExportToken(currentProjectName, "current");
      const url = API.getJianyingDraftDownloadUrl(
        currentProjectName,
        episode,
        draftPath,
        download_token,
        jianyingVersion,
      );
      triggerBrowserDownload(url);
      setExportDialogOpen(false);
      useAppStore.getState().pushToast(t("dashboard:jianying_export_started"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushNotification(
          t("dashboard:jianying_export_failed", { message: errMsg(err) }),
          "error",
        );
    } finally {
      setJianyingExporting(false);
    }
  };

  const handleExportProject = async (scope: "current" | "full") => {
    if (!currentProjectName || exportingProject) return;

    setExportDialogOpen(false);
    setExportingProject(true);
    try {
      const { download_token, diagnostics } = await API.requestExportToken(
        currentProjectName,
        scope,
      );
      const url = API.getExportDownloadUrl(currentProjectName, download_token, scope);
      triggerBrowserDownload(url);
      const diagnosticCount =
        diagnostics.blocking.length + diagnostics.auto_fixed.length + diagnostics.warnings.length;
      if (diagnosticCount > 0) {
        setExportDiagnostics(diagnostics);
        useAppStore.getState().pushToast(
          t("dashboard:project_zip_download_started_with_diagnostics", { count: diagnosticCount }),
          "warning",
        );
      } else {
        useAppStore.getState().pushToast(t("dashboard:project_zip_download_started"), "success");
      }
    } catch (err) {
      useAppStore
        .getState()
        .pushNotification(t("dashboard:export_failed", { message: errMsg(err) }), "error");
    } finally {
      setExportingProject(false);
    }
  };

  return (
    <>
      <header
        className="grid h-12 shrink-0 items-center px-4"
        style={{
          gridTemplateColumns: "minmax(0, 256px) 1fr auto",
          gap: 14,
          background:
            "linear-gradient(180deg, oklch(0.21 0.011 265 / 0.85), oklch(0.19 0.010 265 / 0.75))",
          backdropFilter: "blur(16px) saturate(1.1)",
          WebkitBackdropFilter: "blur(16px) saturate(1.1)",
          borderBottom: "1px solid var(--color-hairline)",
          boxShadow: "0 1px 0 0 oklch(1 0 0 / 0.02) inset",
          position: "relative",
          zIndex: 20,
        }}
      >
        {/* ---- Left: back + project menu ---- */}
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            onClick={onNavigateBack}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors focus-ring"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "var(--color-text)")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "var(--color-text-3)")}
            aria-label={t("dashboard:projects")}
          >
            <ChevronLeft className="h-4 w-4" />
            <span className="hidden sm:inline">{t("dashboard:projects")}</span>
          </button>
          <div
            aria-hidden="true"
            className="h-4 w-px"
            style={{ background: "var(--color-hairline)" }}
          />
          <ProjectMenu />
        </div>

        {/* ---- Center: phase stepper ---- */}
        <div className="hidden justify-self-center md:flex">
          <PhaseStepper currentPhase={currentPhase} />
        </div>

        {/* ---- Right: actions ---- */}
        <div className="flex items-center gap-1">
          <div className="relative" ref={notificationAnchorRef}>
            <button
              type="button"
              onClick={() => setNotificationDrawerOpen(!notificationDrawerOpen)}
              className="relative grid h-[30px] w-[30px] place-items-center rounded-md transition-colors focus-ring"
              style={{
                color: notificationDrawerOpen
                  ? "var(--color-accent-2)"
                  : "var(--color-text-3)",
                background: notificationDrawerOpen
                  ? "var(--color-accent-dim)"
                  : "transparent",
              }}
              onMouseEnter={(e) => {
                if (!notificationDrawerOpen)
                  e.currentTarget.style.background = "oklch(0.28 0.012 265 / 0.6)";
              }}
              onMouseLeave={(e) => {
                if (!notificationDrawerOpen) e.currentTarget.style.background = "transparent";
              }}
              title={t("dashboard:notification_tooltip", { count: workspaceNotifications.length })}
              aria-label={t("dashboard:open_notification_center")}
            >
              <Bell className="h-3.5 w-3.5" />
              {unreadNotificationCount > 0 && (
                <span
                  className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold"
                  style={{
                    background: "var(--color-warn)",
                    color: "oklch(0.14 0 0)",
                  }}
                >
                  {unreadNotificationCount > 9 ? "9+" : unreadNotificationCount}
                </span>
              )}
            </button>
            <WorkspaceNotificationsDrawer
              open={notificationDrawerOpen}
              onClose={() => setNotificationDrawerOpen(false)}
              anchorRef={notificationAnchorRef}
              onNavigate={handleNotificationNavigate}
            />
          </div>

          {/* Cost badge + UsageDrawer */}
          <div className="relative" ref={usageAnchorRef}>
            <button
              type="button"
              onClick={() => setUsageDrawerOpen(!usageDrawerOpen)}
              className="inline-flex items-center gap-2 rounded-md px-2.5 py-[5px] text-[11.5px] transition-colors focus-ring"
              style={{
                background: usageDrawerOpen
                  ? "var(--color-accent-dim)"
                  : "oklch(0.22 0.011 265 / 0.5)",
                border: "1px solid var(--color-hairline-soft)",
                color: "var(--color-text-2)",
              }}
              title={t("dashboard:cost_tooltip", { cost: costTooltip })}
            >
              {cnyCost > 0 && (
                <span className="num" style={{ color: "var(--color-text-4)" }}>
                  ¥{cnyCost.toFixed(2)}
                </span>
              )}
              {cnyCost > 0 && usdCost > 0 && (
                <span
                  aria-hidden="true"
                  style={{
                    width: 2,
                    height: 10,
                    borderRadius: 1,
                    background: "var(--color-hairline)",
                  }}
                />
              )}
              {(usdCost > 0 || cnyCost === 0) && (
                <span
                  className="num font-medium"
                  style={{ color: "var(--color-text-2)" }}
                >
                  ${usdCost.toFixed(2)}
                </span>
              )}
              {otherCosts.length > 0 && (
                <span className="num" style={{ color: "var(--color-text-4)" }}>
                  +{otherCosts.length}
                </span>
              )}
            </button>
            <UsageDrawer
              open={usageDrawerOpen}
              onClose={() => setUsageDrawerOpen(false)}
              projectName={currentProjectName}
              anchorRef={usageAnchorRef}
            />
          </div>

          {/* Task radar + TaskHud popover */}
          <div className="relative" ref={taskHudAnchorRef}>
            <button
              type="button"
              onClick={() => setTaskHudOpen(!taskHudOpen)}
              className="relative grid h-[30px] w-[30px] place-items-center rounded-md transition-colors focus-ring"
              style={{
                color: taskHudOpen ? "var(--color-accent-2)" : "var(--color-text-3)",
                background: taskHudOpen ? "var(--color-accent-dim)" : "transparent",
              }}
              onMouseEnter={(e) => {
                if (!taskHudOpen)
                  e.currentTarget.style.background = "oklch(0.28 0.012 265 / 0.6)";
              }}
              onMouseLeave={(e) => {
                if (!taskHudOpen) e.currentTarget.style.background = "transparent";
              }}
              title={t("dashboard:task_status_tooltip", {
                running: stats.running,
                queued: stats.queued,
              })}
              aria-label={t("dashboard:toggle_task_panel")}
            >
              <Activity className={`h-4 w-4 ${runningCount > 0 ? "animate-shot-pulse" : ""}`} />
              {runningCount > 0 && (
                <span
                  className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold"
                  style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}
                >
                  {runningCount}
                </span>
              )}
            </button>
            <TaskHud anchorRef={taskHudAnchorRef} />
          </div>

          <div
            aria-hidden="true"
            className="mx-1 h-[18px] w-px"
            style={{ background: "var(--color-hairline)" }}
          />

          {/* Export — accent CTA */}
          <div className="relative" ref={exportAnchorRef}>
            <button
              type="button"
              onClick={() => setExportDialogOpen(!exportDialogOpen)}
              disabled={!currentProjectName || exportingProject}
              className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors focus-ring disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                background:
                  "linear-gradient(180deg, oklch(0.82 0.09 295), oklch(0.72 0.09 295))",
                color: "oklch(0.15 0 0)",
                boxShadow:
                  "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 0 0 1px oklch(0.55 0.10 295 / 0.4), 0 4px 14px -6px var(--color-accent-glow)",
              }}
              title={t("dashboard:export_project_zip")}
              aria-label={t("dashboard:export_project_zip")}
            >
              {exportingProject ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              <span className="hidden lg:inline">
                {exportingProject ? t("dashboard:exporting_zip") : t("dashboard:export_zip")}
              </span>
            </button>
            <ExportScopeDialog
              open={exportDialogOpen}
              onClose={() => setExportDialogOpen(false)}
              onSelect={(scope) => {
                if (scope !== "jianying-draft") void handleExportProject(scope);
              }}
              anchorRef={exportAnchorRef}
              episodes={currentProjectData?.episodes ?? []}
              onJianyingExport={voidPromise(handleJianyingExport)}
              jianyingExporting={jianyingExporting}
            />
          </div>

          {/* Asset library */}
          <button
            type="button"
            onClick={() => {
              rememberAssetLibraryReturnTo(window.location.pathname);
              setLocation("~/app/assets");
            }}
            className="grid h-[30px] w-[30px] place-items-center rounded-md transition-colors focus-ring"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "oklch(0.28 0.012 265 / 0.6)";
              e.currentTarget.style.color = "var(--color-text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--color-text-3)";
            }}
            title={t("assets:library_title")}
            aria-label={t("assets:library_title")}
          >
            <Package className="h-4 w-4" />
          </button>

          {/* Settings */}
          <button
            type="button"
            onClick={() =>
              setLocation(
                currentProjectName
                  ? `~/app/projects/${encodeURIComponent(currentProjectName)}/settings`
                  : "~/app/settings",
              )
            }
            className="relative grid h-[30px] w-[30px] place-items-center rounded-md transition-colors focus-ring"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "oklch(0.28 0.012 265 / 0.6)";
              e.currentTarget.style.color = "var(--color-text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--color-text-3)";
            }}
            title={t("settings")}
            aria-label={t("settings")}
          >
            <Settings className="h-4 w-4" />
            {!isConfigComplete && !currentProjectName && (
              <span
                className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full"
                style={{ background: "var(--color-danger)" }}
                aria-label={t("dashboard:config_incomplete")}
              />
            )}
          </button>
        </div>
      </header>

      {exportDiagnostics !== null && (
        <ArchiveDiagnosticsDialog
          title={t("dashboard:export_diagnostics_title")}
          description={t("dashboard:export_diagnostics_description")}
          sections={[
            {
              key: "blocking",
              title: t("dashboard:diagnostics_blocking"),
              severity: "blocking",
              items: exportDiagnostics.blocking,
            },
            {
              key: "auto_fixed",
              title: t("dashboard:diagnostics_auto_fixed"),
              severity: "auto_fixed",
              items: exportDiagnostics.auto_fixed,
            },
            {
              key: "warnings",
              title: t("dashboard:diagnostics_warnings"),
              severity: "warnings",
              items: exportDiagnostics.warnings,
            },
          ]}
          onClose={() => setExportDiagnostics(null)}
        />
      )}
    </>
  );
}
