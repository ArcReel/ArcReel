import { useState, useEffect, useRef } from "react";
import { ChevronLeft, Activity, Settings, DollarSign } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageStore } from "@/stores/usage-store";
import { TaskHud } from "@/components/task-hud/TaskHud";
import { UsageDrawer } from "./UsageDrawer";
import { API } from "@/api";

// ---------------------------------------------------------------------------
// Phase definitions
// ---------------------------------------------------------------------------

const PHASES = [
  { key: "characters", label: "角色/线索" },
  { key: "storyboard", label: "剧本分镜" },
  { key: "video", label: "视频合成" },
  { key: "compose", label: "后期处理" },
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
  const { currentProjectData, currentProjectName } = useProjectsStore();
  const { stats } = useTasksStore();
  const { taskHudOpen, setTaskHudOpen } = useAppStore();
  const assistantSessionStatus = useAssistantStore((s) => s.sessionStatus);
  const draftTurn = useAssistantStore((s) => s.draftTurn);
  const assistantToolActivitySuppressed = useAppStore(
    (s) => s.assistantToolActivitySuppressed
  );
  const setAssistantToolActivitySuppressed = useAppStore(
    (s) => s.setAssistantToolActivitySuppressed
  );
  const { stats: usageStats, setStats: setUsageStats } = useUsageStore();
  const [usageDrawerOpen, setUsageDrawerOpen] = useState(false);
  const usageAnchorRef = useRef<HTMLDivElement>(null);
  const taskHudAnchorRef = useRef<HTMLDivElement>(null);

  const currentPhase = currentProjectData?.status?.current_phase;
  const contentMode = currentProjectData?.content_mode;
  const runningCount = stats.running + stats.queued;
  const displayProjectTitle =
    currentProjectData?.title?.trim() || currentProjectName || "未选择项目";
  const assistantHasToolUse =
    draftTurn?.content.some((block) => block.type === "tool_use") ?? false;
  const showAssistantActivity =
    assistantSessionStatus === "running" &&
    assistantHasToolUse &&
    !assistantToolActivitySuppressed;

  // 加载费用统计数据
  useEffect(() => {
    API.getUsageStats(currentProjectName ? { projectName: currentProjectName } : {})
      .then((res) => {
        setUsageStats(res as {
          total_cost: number;
          image_count: number;
          video_count: number;
          failed_count: number;
          total_count: number;
        });
      })
      .catch(() => {});
  }, [currentProjectName, setUsageStats]);

  useEffect(() => {
    if (assistantSessionStatus !== "running") {
      setAssistantToolActivitySuppressed(false);
    }
  }, [assistantSessionStatus, setAssistantToolActivitySuppressed]);

  // Format content mode badge text
  const modeBadgeText =
    contentMode === "drama" ? "剧集动画 16:9" : "说书模式 9:16";

  // Format cost display
  const totalCost = usageStats?.total_cost ?? 0;
  const costText = `$${totalCost.toFixed(2)}`;

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-gray-800 bg-gray-900/80 px-4 backdrop-blur-sm">
      {/* ---- Left section ---- */}
      <div className="flex items-center gap-3">
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
            <DollarSign className="h-3.5 w-3.5" />
            <span>{costText}</span>
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

        {showAssistantActivity && (
          <div className="hidden items-center gap-2 rounded-full border border-amber-300/20 bg-amber-400/8 px-3 py-1 text-xs text-amber-100 shadow-[0_10px_30px_rgba(120,53,15,0.24)] lg:flex">
            <span className="h-2 w-2 rounded-full bg-amber-300 animate-pulse" />
            AI 正在调用工具并更新项目…
          </div>
        )}

        {/* Settings (placeholder) */}
        <button
          type="button"
          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
          title="设置"
          aria-label="设置"
        >
          <Settings className="h-4 w-4" />
        </button>

      </div>
    </header>
  );
}
