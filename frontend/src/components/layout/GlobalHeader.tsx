import { startTransition, useState, useEffect, useRef } from "react";
import { useLocation } from "wouter";
import { ChevronLeft, Activity, Settings, Bell, Download, Loader2 } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageStore, type UsageStats } from "@/stores/usage-store";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { UsageDrawer } from "./UsageDrawer";
import { WorkspaceNotificationsDrawer } from "./WorkspaceNotificationsDrawer";
import { ExportScopeDialog } from "./ExportScopeDialog";

import { API } from "@/api";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
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

// ---------------------------------------------------------------------------
// Phase definitions
// ---------------------------------------------------------------------------

const PHASES = [
  { key: "setup", label: "准备中" },
  { key: "worldbuilding", label: "世界观" },
  { key: "scripting", label: "剧本创作" },
  { key: "production", label: "制作中" },
  { key: "completed", label: "已完成" },
] as const;

type PhaseKey = (typeof PHASES)[number]["key"];

// ---------------------------------------------------------------------------
// PhaseStepper — horizontal workflow indicator
// ---------------------------------------------------------------------------

function PhaseStepper({
  currentPhase,
}: {
  currentPhase: string | undefined;
}) {
  const currentIdx = PHASES.findIndex((p) => p.key === currentPhase);

  return (
    <nav className="flex items-center gap-1" aria-label="工作流阶段">
      {PHASES.map((phase, idx) => {
        const isCompleted = currentIdx > idx;
        const isCurrent = currentIdx === idx;

        // Determine colors
        let circleClass =
          "flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold shrink-0 transition-colors";
        let labelClass = "text-xs whitespace-nowrap transition-colors";

        if (isCompleted) {
          circleClass += " bg-emerald-600 text-white";
          labelClass += " text-emerald-400";
        } else if (isCurrent) {
          circleClass += " bg-indigo-600 text-white";
          labelClass += " text-indigo-300 font-medium";
        } else {
          circleClass += " bg-gray-700 text-gray-400";
          labelClass += " text-gray-500";
        }

        return (
          <div key={phase.key} className="flex items-center gap-1">
            {/* Connector line (before each step except the first) */}
            {idx > 0 && (
              <div
                className={`h-px w-4 shrink-0 ${
                  isCompleted ? "bg-emerald-600" : "bg-gray-700"
                }`}
              />
            )}

            {/* Step circle + label */}
            <div className="flex items-center gap-1.5">
              <span className={circleClass}>{idx + 1}</span>
              <span className={labelClass}>{phase.label}</span>
            </div>
          </div>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// GlobalHeader
// ---------------------------------------------------------------------------

interface GlobalHeaderProps {
  onNavigateBack?: () => void;
}

export function GlobalHeader({ onNavigateBack }: GlobalHeaderProps) {
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
  const contentMode = currentProjectData?.content_mode;
  const runningCount = stats.running + stats.queued;
  const displayProjectTitle =
    currentProjectData?.title?.trim() || currentProjectName || "未选择项目";
  const unreadNotificationCount = workspaceNotifications.filter((item) => !item.read).length;

  // 加载费用统计数据（任务完成时自动刷新）
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


  // Format content mode badge text
  const modeBadgeText =
    contentMode === "drama" ? "剧集动画 16:9" : "说书模式 9:16";

  // Format cost display – show multi-currency summary
  const costByCurrency = usageStats?.cost_by_currency ?? {};
  const costText = Object.entries(costByCurrency)
    .filter(([, v]) => v > 0)
    .map(([currency, amount]) => `${currency === "CNY" ? "¥" : "$"}${amount.toFixed(2)}`)
    .join(" + ") || "$0.00";

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

  const handleJianyingExport = async (episode: number, draftPath: string, jianyingVersion: string) => {
    if (!currentProjectName || jianyingExporting) return;

    setJianyingExporting(true);
    try {
      const { download_token } = await API.requestExportToken(currentProjectName, "current");
      const url = API.getJianyingDraftDownloadUrl(
        currentProjectName, episode, draftPath, download_token, jianyingVersion,
      );
      triggerBrowserDownload(url);
      setExportDialogOpen(false);
      useAppStore.getState().pushToast("剪映草稿导出已开始，请将下载的 ZIP 解压到剪映草稿目录中", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`剪映草稿导出失败: ${(err as Error).message}`, "error");
    } finally {
      setJianyingExporting(false);
    }
  };

  const handleExportProject = async (scope: "current" | "full") => {
    if (!currentProjectName || exportingProject) return;

    setExportDialogOpen(false);
    setExportingProject(true);
    try {
      const { download_token, diagnostics } = await API.requestExportToken(currentProjectName, scope);
      const url = API.getExportDownloadUrl(currentProjectName, download_token, scope);
      triggerBrowserDownload(url);
      const diagnosticCount =
        diagnostics.blocking.length + diagnostics.auto_fixed.length + diagnostics.warnings.length;
      if (diagnosticCount > 0) {
        setExportDiagnostics(diagnostics);
        useAppStore.getState().pushToast(
          `项目 ZIP 已开始下载，导出包包含 ${diagnosticCount} 条诊断`,
          "warning",
        );
      } else {
        useAppStore.getState().pushToast("项目 ZIP 已开始下载", "success");
      }
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`导出失败: ${(err as Error).message}`, "error");
    } finally {
      setExportingProject(false);
    }
  };

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-gray-800 bg-gray-900/80 px-4 backdrop-blur-sm">
      {/* ---- Left section ---- */}
      <div className="flex items-center gap-3">
        {/* Logo */}
        <img src="/android-chrome-192x192.png" alt="ArcReel" className="h-5 w-5" />

        {/* Back to projects */}
        <button
          type="button"
          onClick={onNavigateBack}
          className="flex items-center gap-1 text-sm text-gray-400 transition-colors hover:text-gray-200"
          aria-label="返回项目大厅"
        >
          <ChevronLeft className="h-4 w-4" />
          <span className="hidden sm:inline">项目大厅</span>
        </button>

        {/* Divider */}
        <div className="h-4 w-px bg-gray-700" />

        {/* Project name */}
        <span className="max-w-48 truncate text-sm font-medium text-gray-200">
          {displayProjectTitle}
        </span>

        {/* Content mode badge */}
        {contentMode && (
          <span className="rounded-full bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
            {modeBadgeText}
          </span>
        )}
      </div>

      {/* ---- Center section ---- */}
      <div className="hidden md:flex">
        <PhaseStepper currentPhase={currentPhase} />
      </div>

      {/* ---- Right section ---- */}
      <div className="flex items-center gap-3">
        <div className="relative" ref={notificationAnchorRef}>
          <button
            type="button"
            onClick={() => setNotificationDrawerOpen(!notificationDrawerOpen)}
            className={`relative flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors ${
              notificationDrawerOpen
                ? "bg-amber-500/20 text-amber-200"
                : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            }`}
            title={`会话通知: ${workspaceNotifications.length} 条`}
            aria-label="打开通知中心"
          >
            <Bell className="h-3.5 w-3.5" />
            {unreadNotificationCount > 0 && (
              <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-400 px-1 text-[10px] font-bold text-slate-950">
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
            className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors ${
              usageDrawerOpen
                ? "bg-indigo-500/20 text-indigo-400"
                : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            }`}
            title={`项目总花费: ${costText}`}
          >
            <span className="font-mono">{costText}</span>
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
            className={`relative rounded-md p-1.5 transition-colors ${
              taskHudOpen
                ? "bg-indigo-500/20 text-indigo-400"
                : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            }`}
            title={`任务状态: ${stats.running} 运行中, ${stats.queued} 排队中`}
            aria-label="切换任务面板"
          >
            <Activity
              className={`h-4 w-4 ${runningCount > 0 ? "animate-pulse" : ""}`}
            />
            {/* Running task count badge */}
            {runningCount > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-indigo-500 px-1 text-[10px] font-bold text-white">
                {runningCount}
              </span>
            )}
          </button>
          <TaskHud anchorRef={taskHudAnchorRef} />
        </div>


        <div className="relative" ref={exportAnchorRef}>
          <button
            type="button"
            onClick={() => setExportDialogOpen(!exportDialogOpen)}
            disabled={!currentProjectName || exportingProject}
            className="inline-flex items-center gap-1 rounded-md border border-gray-700 px-2 py-1 text-xs text-gray-300 transition-colors hover:border-gray-500 hover:bg-gray-800 hover:text-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
            title="导出当前项目 ZIP"
            aria-label="导出当前项目 ZIP"
          >
            {exportingProject ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            <span className="hidden lg:inline">
              {exportingProject ? "导出中..." : "导出 ZIP"}
            </span>
          </button>
          <ExportScopeDialog
            open={exportDialogOpen}
            onClose={() => setExportDialogOpen(false)}
            onSelect={(scope) => { if (scope !== "jianying-draft") void handleExportProject(scope); }}
            anchorRef={exportAnchorRef}
            episodes={currentProjectData?.episodes ?? []}
            onJianyingExport={handleJianyingExport}
            jianyingExporting={jianyingExporting}
          />
        </div>

        {/* Settings (placeholder) */}
        <button
          type="button"
          onClick={() => setLocation(
            currentProjectName
              ? `~/app/projects/${encodeURIComponent(currentProjectName)}/settings`
              : "~/app/settings"
          )}
          className="relative rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
          title="设置"
          aria-label="设置"
        >
          <Settings className="h-4 w-4" />
          {!isConfigComplete && !currentProjectName && (
            <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-rose-500" aria-label="配置不完整" />
          )}
        </button>

      </div>

      {exportDiagnostics !== null && (
        <ArchiveDiagnosticsDialog
          title="导出诊断"
          description="导出已完成预检查并生成 ZIP。以下问题在导出包中被检测到。"
          sections={[
            { key: "blocking", title: "阻断问题", tone: "border-red-400/25 bg-red-500/10 text-red-100", items: exportDiagnostics.blocking },
            { key: "auto_fixed", title: "已自动修复", tone: "border-indigo-400/25 bg-indigo-500/10 text-indigo-100", items: exportDiagnostics.auto_fixed },
            { key: "warnings", title: "警告", tone: "border-amber-400/25 bg-amber-500/10 text-amber-100", items: exportDiagnostics.warnings },
          ]}
          onClose={() => setExportDiagnostics(null)}
        />
      )}
    </header>
  );
}
