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

/** Trigger a browser download via a hidden <a> element, avoiding blank tabs from window.open */
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
  { key: "setup", label: "Setup" },
  { key: "worldbuilding", label: "World" },
  { key: "scripting", label: "Scripting" },
  { key: "production", label: "Production" },
  { key: "completed", label: "Done" },
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
    <nav className="flex items-center gap-1" aria-label="Workflow stages">
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
    currentProjectData?.title?.trim() || currentProjectName || "No project selected";
  const unreadNotificationCount = workspaceNotifications.filter((item) => !item.read).length;

  // Load usage stats (auto-refresh on task completion)
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
    contentMode === "drama" ? "Drama 16:9" : "Narration 9:16";

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
      useAppStore.getState().pushToast("JianYing draft export started. Unzip the downloaded ZIP into the JianYing drafts folder.", "success");
    } catch (err) {
      useAppStore.getState().pushToast(`JianYing draft export failed: ${(err as Error).message}`, "error");
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
          `Project ZIP download started. Export contains ${diagnosticCount} diagnostics.`,
          "warning",
        );
      } else {
        useAppStore.getState().pushToast("Project ZIP download started", "success");
      }
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`Export failed: ${(err as Error).message}`, "error");
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
          aria-label="Back to projects"
        >
          <ChevronLeft className="h-4 w-4" />
          <span className="hidden sm:inline">Projects</span>
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
            title={`Workspace notifications: ${workspaceNotifications.length}`}
            aria-label="Open notification center"
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
            title={`Total project cost: ${costText}`}
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
            title={`Task status: ${stats.running} running, ${stats.queued} queued`}
            aria-label="Toggle task panel"
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
            title="Export current project ZIP"
            aria-label="Export project ZIP"
          >
            {exportingProject ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            <span className="hidden lg:inline">
              {exportingProject ? "Exporting..." : "Export ZIP"}
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
          title="Settings"
          aria-label="Settings"
        >
          <Settings className="h-4 w-4" />
          {!isConfigComplete && !currentProjectName && (
            <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-rose-500" aria-label="Configuration incomplete" />
          )}
        </button>

      </div>

      {exportDiagnostics !== null && (
        <ArchiveDiagnosticsDialog
          title="Export Diagnostics"
          description="Export completed pre-check and generated ZIP. The following issues were detected in the export package."
          sections={[
            { key: "blocking", title: "Blocking issues", tone: "border-red-400/25 bg-red-500/10 text-red-100", items: exportDiagnostics.blocking },
            { key: "auto_fixed", title: "Auto-fixed", tone: "border-indigo-400/25 bg-indigo-500/10 text-indigo-100", items: exportDiagnostics.auto_fixed },
            { key: "warnings", title: "Warnings", tone: "border-amber-400/25 bg-amber-500/10 text-amber-100", items: exportDiagnostics.warnings },
          ]}
          onClose={() => setExportDiagnostics(null)}
        />
      )}
    </header>
  );
}
