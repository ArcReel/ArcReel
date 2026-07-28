import { useCallback, useState, type RefObject } from "react";
import { activateOnEnterSpace } from "@/utils/a11y";
import { voidPromise } from "@/utils/async";
import { motion, AnimatePresence } from "framer-motion";
import {
  Image,
  Video,
  AudioLines,
  Check,
  X,
  Loader2,
  ChevronDown,
  Activity,
  AlertTriangle,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useAppStore } from "@/stores/app-store";
import { isTerminalStatus, useTasksStore } from "@/stores/tasks-store";
import { API } from "@/api";
import type { TaskItem } from "@/types";
import { GlassPopover } from "@/components/ui/GlassPopover";

// ---------------------------------------------------------------------------
// Theme tokens — v3 cool oklch + accent purple
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<TaskItem["status"], string> = {
  running: "var(--color-accent-2)",
  queued: "var(--color-text-4)",
  cancelling: "var(--color-text-3)",
  succeeded: "var(--color-good)",
  failed: "oklch(0.72 0.18 25)",
  cancelled: "var(--color-text-3)",
};

// ---------------------------------------------------------------------------
// Status filter
// ---------------------------------------------------------------------------

type TaskFilter = "all" | "active" | "failed" | "done";

const FILTER_STATUSES: Record<Exclude<TaskFilter, "all">, readonly TaskItem["status"][]> = {
  active: ["running", "cancelling", "queued"],
  failed: ["failed"],
  done: ["succeeded", "cancelled"],
};

function matchesFilter(task: TaskItem, filter: TaskFilter): boolean {
  return filter === "all" || FILTER_STATUSES[filter].includes(task.status);
}

/**
 * 后端 `_localize_task` 已把 `result.warnings` 渲染成当前语言的字符串数组，
 * 这里只做形态收窄：`result` 是 `Record<string, unknown>`，非字符串条目一律丢弃，
 * 避免旧任务行里残留的 `{key, params}` 结构被当成文本渲染成 `[object Object]`。
 */
function taskWarnings(task: TaskItem): string[] {
  const warnings = task.result?.warnings;
  if (!Array.isArray(warnings)) return [];
  return warnings.filter((warning): warning is string => typeof warning === "string");
}

// ---------------------------------------------------------------------------
// Task status icon
// ---------------------------------------------------------------------------

function TaskStatusIcon({ status }: { status: TaskItem["status"] }) {
  switch (status) {
    case "running":
      return (
        <Loader2
          className="h-3.5 w-3.5 animate-spin"
          style={{ color: STATUS_COLORS.running }}
        />
      );
    case "queued":
      return (
        <span
          aria-hidden
          className="h-2 w-2 rounded-full"
          style={{
            background: STATUS_COLORS.queued,
            boxShadow: "0 0 4px oklch(1 0 0 / 0.1)",
          }}
        />
      );
    case "cancelling":
      return (
        <Loader2
          className="h-3.5 w-3.5 animate-spin"
          style={{ color: STATUS_COLORS.cancelling }}
        />
      );
    case "succeeded":
      return <Check className="h-3.5 w-3.5" style={{ color: STATUS_COLORS.succeeded }} />;
    case "failed":
      return <X className="h-3.5 w-3.5" style={{ color: STATUS_COLORS.failed }} />;
    case "cancelled":
      return <X className="h-3.5 w-3.5" style={{ color: STATUS_COLORS.cancelled }} />;
  }
}

// ---------------------------------------------------------------------------
// RunningProgressBar
// ---------------------------------------------------------------------------

function RunningProgressBar() {
  return (
    <div
      className="relative mt-1 h-0.5 w-full overflow-hidden rounded-full"
      style={{ background: "oklch(0.16 0.010 265 / 0.7)" }}
    >
      <motion.div
        className="absolute inset-y-0 left-0 w-1/3 rounded-full"
        style={{
          background:
            "linear-gradient(90deg, var(--color-accent-soft), var(--color-accent), var(--color-accent-soft))",
          boxShadow: "0 0 6px var(--color-accent-glow)",
        }}
        animate={{ x: ["0%", "200%"] }}
        transition={{
          duration: 1.5,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// TaskRow
// ---------------------------------------------------------------------------

function TaskRow({
  task,
  expandedTaskId,
  onToggleDetail,
  onCancel,
}: {
  task: TaskItem;
  expandedTaskId: string | null;
  onToggleDetail: (taskId: string) => void;
  onCancel?: (taskId: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const statusLabel: Record<TaskItem["status"], string> = {
    running: t("generating_status"),
    queued: t("queued_status"),
    cancelling: t("cancelling_status"),
    succeeded: t("completed_status"),
    failed: t("failed_status"),
    cancelled: t("cancelled_status"),
  };

  const warnings = task.status === "succeeded" ? taskWarnings(task) : [];
  const hasWarnings = warnings.length > 0;
  const hasError = Boolean(task.status === "failed" && task.error_message);
  // 失败原因与生成警示走同一套展开交互：一行最多只有其中一种详情。
  const isExpandable = hasError || hasWarnings;
  const isExpanded = expandedTaskId === task.task_id;

  const rowBg = hasError
    ? "oklch(0.30 0.10 25 / 0.18)"
    : hasWarnings
      ? "oklch(0.35 0.10 70 / 0.12)"
      : task.status === "succeeded"
        ? "oklch(0.30 0.10 155 / 0.12)"
        : "transparent";
  const rowHoverBg = hasError
    ? "oklch(0.30 0.10 25 / 0.28)"
    : "oklch(0.35 0.10 70 / 0.22)";

  return (
    <motion.div
      layout
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.2 }}
      className="overflow-hidden"
    >
      <div
        className={`flex items-center gap-2 px-3 py-1.5 text-[12px] ${
          isExpandable ? "cursor-pointer" : ""
        }`}
        style={{ background: rowBg, transition: "background-color .12s ease" }}
        role={isExpandable ? "button" : undefined}
        tabIndex={isExpandable ? 0 : undefined}
        aria-expanded={isExpandable ? isExpanded : undefined}
        aria-controls={isExpandable ? `task-detail-${task.task_id}` : undefined}
        onClick={isExpandable ? () => onToggleDetail(task.task_id) : undefined}
        onKeyDown={
          isExpandable ? activateOnEnterSpace(() => onToggleDetail(task.task_id)) : undefined
        }
        onMouseEnter={(e) => {
          if (isExpandable) e.currentTarget.style.background = rowHoverBg;
        }}
        onMouseLeave={(e) => {
          if (isExpandable) e.currentTarget.style.background = rowBg;
        }}
      >
        <TaskStatusIcon status={task.status} />
        <span
          className="num text-[10.5px]"
          style={{ color: "var(--color-text-3)" }}
        >
          {task.resource_id}
        </span>
        <span
          className="flex-1 truncate"
          style={{ color: "var(--color-text-2)" }}
        >
          {t(`task_type_${task.task_type}`, { defaultValue: task.task_type })}
        </span>
        <span
          className="text-[10.5px]"
          style={{ color: STATUS_COLORS[task.status] }}
        >
          {statusLabel[task.status]}
        </span>
        {task.status === "queued" && onCancel && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onCancel(task.task_id);
            }}
            className="focus-ring ml-1 rounded px-1 py-0.5 text-[10.5px] transition-colors"
            style={{ color: "var(--color-text-4)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "oklch(0.72 0.18 25)";
              e.currentTarget.style.background = "oklch(0.30 0.10 25 / 0.18)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-4)";
              e.currentTarget.style.background = "transparent";
            }}
            title={t("cancel_task")}
            aria-label={t("cancel_this_task")}
          >
            {t("cancel_btn")}
          </button>
        )}
        {task.status === "running" && onCancel && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onCancel(task.task_id);
            }}
            className="focus-ring ml-1 inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[10.5px] transition-colors"
            style={{ color: "var(--color-text-4)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "oklch(0.72 0.18 25)";
              e.currentTarget.style.background = "oklch(0.30 0.10 25 / 0.18)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "var(--color-text-4)";
              e.currentTarget.style.background = "transparent";
            }}
            title={t("cancel_running_warning")}
            aria-label={t("cancel_this_task")}
          >
            <AlertTriangle className="h-3 w-3" aria-hidden />
            {t("cancel_btn")}
          </button>
        )}
        {task.status === "cancelling" && (
          <button
            type="button"
            disabled
            className="ml-1 inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-[10.5px] opacity-60"
            style={{ color: "var(--color-text-4)" }}
            title={t("cancelling_status")}
            aria-label={t("cancelling_status")}
          >
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("cancelling_status")}
          </button>
        )}
        {task.status === "cancelled" && task.cancelled_by === "cascade" && (
          <span
            className="ml-1 text-[10.5px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("cascade_label")}
          </span>
        )}
        {hasWarnings && (
          <span
            className="inline-flex items-center gap-0.5 text-[10.5px]"
            style={{ color: "var(--color-warn)" }}
            title={t("task_warnings_hint")}
          >
            <AlertTriangle className="h-3 w-3" aria-hidden />
            <span className="num">{warnings.length}</span>
            <span className="sr-only">{t("task_warnings_count", { count: warnings.length })}</span>
          </span>
        )}
        {isExpandable && (
          <ChevronDown
            className={`h-3 w-3 transition-transform ${isExpanded ? "rotate-180" : ""}`}
            style={{ color: "var(--color-text-4)" }}
            aria-hidden
          />
        )}
      </div>

      {(task.status === "running" || task.status === "cancelling") && (
        <div className="px-3 pb-1">
          <RunningProgressBar />
        </div>
      )}

      <AnimatePresence>
        {isExpandable && isExpanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            {hasError ? (
              <div
                id={`task-detail-${task.task_id}`}
                className="mx-3 mb-1.5 rounded px-2 py-1.5 text-[10.5px]"
                style={{
                  background: "oklch(0.30 0.10 25 / 0.10)",
                  color: "oklch(0.85 0.10 25)",
                  border: "1px solid oklch(0.45 0.18 25 / 0.30)",
                }}
              >
                {task.error_message}
              </div>
            ) : (
              <ul
                id={`task-detail-${task.task_id}`}
                className="mx-3 mb-1.5 space-y-1 rounded px-2 py-1.5 text-[10.5px]"
                style={{
                  background: "oklch(0.35 0.10 70 / 0.10)",
                  color: "oklch(0.86 0.09 70)",
                  border: "1px solid oklch(0.50 0.12 70 / 0.30)",
                }}
              >
                {warnings.map((warning, index) => (
                  <li key={`${task.task_id}-warning-${index}`}>{warning}</li>
                ))}
              </ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// ChannelSection
// ---------------------------------------------------------------------------

function ChannelSection({
  title,
  icon: Icon,
  tasks,
  filter,
  onCancel,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  tasks: TaskItem[];
  filter: TaskFilter;
  onCancel?: (taskId: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);

  const toggleDetail = useCallback((taskId: string) => {
    setExpandedTaskId((prev) => (prev === taskId ? null : taskId));
  }, []);

  // cancelling 是 running 的延伸中间态：worker 在响应 CancelledError 期间任务仍占用
  // provider 槽位。归入 running 桶展示，让用户看到带 spinner 的"Cancelling…"按钮
  // 而不是直接消失。
  const running = tasks.filter(
    (task) => task.status === "running" || task.status === "cancelling",
  );
  const queued = tasks.filter((task) => task.status === "queued");
  // 终态任务不再自动消失也不截断：展示窗口由 API 分页与容器滚动兜底。API 已按
  // updated_at 倒序返回，这里的过滤保序，因此终态段天然是时间倒序。
  const recent = tasks.filter((task) => isTerminalStatus(task.status));

  const visible = [...running, ...queued, ...recent].filter((task) =>
    matchesFilter(task, filter),
  );

  return (
    <div>
      <div
        className="flex items-center gap-2 px-3 py-2 text-[10.5px] font-bold uppercase"
        style={{
          color: "var(--color-text-4)",
          letterSpacing: "0.8px",
          background: "oklch(0.18 0.010 265 / 0.6)",
        }}
      >
        <Icon className="h-3.5 w-3.5" style={{ color: "var(--color-text-3)" }} />
        {title}
        {running.length > 0 && (
          <span
            className="num ml-auto rounded px-1.5 py-px text-[10px]"
            style={{
              color: "var(--color-accent-2)",
              background: "var(--color-accent-dim)",
              border: "1px solid var(--color-accent-soft)",
              letterSpacing: 0,
              textTransform: "none",
            }}
          >
            {t("running_count", { count: running.length })}
          </span>
        )}
      </div>
      <AnimatePresence>
        {visible.map((task) => (
          <TaskRow
            key={task.task_id}
            task={task}
            expandedTaskId={expandedTaskId}
            onToggleDetail={toggleDetail}
            onCancel={onCancel}
          />
        ))}
      </AnimatePresence>
      {visible.length === 0 && (
        <div
          className="px-3 py-2 text-[11px] italic"
          style={{ color: "var(--color-text-4)" }}
        >
          {filter === "all" ? t("no_tasks") : t("no_tasks_in_filter")}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter pills
// ---------------------------------------------------------------------------

/**
 * 状态过滤 pill。计数取自已加载的任务列表而非 `/tasks/stats`：它标的是「点进去能看到
 * 几条」，与上方统计条的项目级累计口径不同，用当前列表口径才不会点开即落空。
 */
function FilterPills({
  tasks,
  value,
  onChange,
}: {
  tasks: TaskItem[];
  value: TaskFilter;
  onChange: (filter: TaskFilter) => void;
}) {
  const { t } = useTranslation("dashboard");

  const options: { key: TaskFilter; label: string; count: number; accent: string }[] = [
    {
      key: "all",
      label: t("task_filter_all"),
      count: tasks.length,
      accent: "var(--color-text-2)",
    },
    {
      key: "active",
      label: t("task_filter_active"),
      count: tasks.filter((task) => matchesFilter(task, "active")).length,
      accent: STATUS_COLORS.running,
    },
    {
      key: "failed",
      label: t("task_filter_failed"),
      count: tasks.filter((task) => matchesFilter(task, "failed")).length,
      accent: STATUS_COLORS.failed,
    },
    {
      key: "done",
      label: t("task_filter_done"),
      count: tasks.filter((task) => matchesFilter(task, "done")).length,
      accent: STATUS_COLORS.succeeded,
    },
  ];

  return (
    <div
      className="flex items-center gap-1.5 px-4 py-2"
      role="group"
      aria-label={t("task_filter_group_aria")}
      style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
    >
      {options.map((option) => {
        const active = value === option.key;
        return (
          <button
            key={option.key}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.key)}
            className="focus-ring inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] transition-colors"
            style={{
              color: active ? "var(--color-accent-2)" : "var(--color-text-3)",
              background: active ? "var(--color-accent-dim)" : "transparent",
              border: `1px solid ${active ? "var(--color-accent-soft)" : "var(--color-hairline-soft)"}`,
            }}
            onMouseEnter={(e) => {
              if (!active) e.currentTarget.style.color = "var(--color-text-2)";
            }}
            onMouseLeave={(e) => {
              if (!active) e.currentTarget.style.color = "var(--color-text-3)";
            }}
          >
            {option.label}
            <span
              className="num"
              style={{
                // 计数只在非零时取状态色：它是"这里有东西值得看"的信号，
                // 全部为零时着色只是装饰。
                color: option.count > 0 ? option.accent : "var(--color-text-4)",
                fontWeight: 600,
              }}
            >
              {option.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat pill
// ---------------------------------------------------------------------------

function StatPill({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span style={{ color: "var(--color-text-4)" }}>{label}</span>
      <span className="num" style={{ color, fontWeight: 600 }}>
        {value}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// TaskHud
// ---------------------------------------------------------------------------

export function TaskHud({ anchorRef }: { anchorRef: RefObject<HTMLElement | null> }) {
  const { t } = useTranslation("dashboard");
  const { taskHudOpen, setTaskHudOpen } = useAppStore();
  const { tasks, stats } = useTasksStore();

  const [cancelConfirm, setCancelConfirm] = useState<{
    taskId?: string;
    preview?: {
      task: { task_id: string; task_type: string; resource_id: string };
      cascaded: { task_id: string; task_type: string; resource_id: string }[];
    };
    allCount?: number;
    projectName?: string;
  } | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [filter, setFilter] = useState<TaskFilter>("all");

  const handleCancelSingle = useCallback(async (taskId: string) => {
    try {
      const preview = await API.cancelPreview(taskId);
      setCancelConfirm({ taskId, preview });
    } catch {
      // task no longer queued
    }
  }, []);

  const handleCancelAll = useCallback(async () => {
    const queuedTask = tasks.find((task) => task.status === "queued");
    if (!queuedTask) return;
    const projectName = queuedTask.project_name;
    try {
      const { queued_count } = await API.cancelAllPreview(projectName);
      setCancelConfirm({ allCount: queued_count, projectName });
    } catch {
      // no queued tasks
    }
  }, [tasks]);

  const confirmCancel = useCallback(async () => {
    if (!cancelConfirm) return;
    setCancelling(true);
    try {
      if (cancelConfirm.taskId) {
        await API.cancelTask(cancelConfirm.taskId);
      } else if (cancelConfirm.projectName) {
        await API.cancelAllQueued(cancelConfirm.projectName);
      }
    } finally {
      setCancelling(false);
      setCancelConfirm(null);
    }
  }, [cancelConfirm]);

  useEscapeClose(() => setCancelConfirm(null), Boolean(cancelConfirm));

  const imageTasks = tasks.filter((task) => task.media_type === "image");
  const videoTasks = tasks.filter((task) => task.media_type === "video");
  const audioTasks = tasks.filter((task) => task.media_type === "audio");

  return (
    <GlassPopover
      open={taskHudOpen}
      onClose={() => setTaskHudOpen(false)}
      anchorRef={anchorRef}
      sideOffset={6}
      width="w-[22rem]"
    >
      <motion.div
        initial={{ opacity: 0, y: -6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.15 }}
      >
        {/* Header */}
        <div
          className="relative flex items-center gap-2 px-4 py-3"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
              border: "1px solid var(--color-accent-soft)",
              color: "var(--color-accent-2)",
              boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
            }}
          >
            <Activity className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div
              className="display-serif text-[14px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {t("task_hud_title")}
            </div>
            <div
              className="num text-[10px] uppercase"
              style={{
                color: "var(--color-text-4)",
                letterSpacing: "1.2px",
              }}
            >
              {t("task_hud_subtitle")}
            </div>
          </div>
        </div>

        {/* Stats bar */}
        <div
          className="flex items-center gap-3 px-4 py-2 text-[11px]"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <StatPill
            label={t("queued_label")}
            value={stats.queued}
            color="var(--color-text)"
          />
          <StatPill
            label={t("running_label")}
            value={stats.running}
            color={STATUS_COLORS.running}
          />
          <StatPill
            label={t("completed_label")}
            value={stats.succeeded}
            color={STATUS_COLORS.succeeded}
          />
          <StatPill
            label={t("failed_label")}
            value={stats.failed}
            color={STATUS_COLORS.failed}
          />
          {stats.cancelled > 0 && (
            <StatPill
              label={t("cancelled_label")}
              value={stats.cancelled}
              color={STATUS_COLORS.cancelled}
            />
          )}
          {stats.queued > 0 && (
            <button
              type="button"
              onClick={voidPromise(handleCancelAll)}
              className="focus-ring ml-auto rounded px-1.5 py-0.5 text-[10.5px] transition-colors"
              style={{ color: "var(--color-text-4)" }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "oklch(0.72 0.18 25)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-text-4)";
              }}
              aria-label={t("cancel_all_queued_aria")}
            >
              {t("cancel_all")}
            </button>
          )}
        </div>

        {/* Status filter */}
        <FilterPills tasks={tasks} value={filter} onChange={setFilter} />

        {/* Channels */}
        <div
          className="max-h-80 overflow-y-auto"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <ChannelSection
            title={t("image_channel")}
            icon={Image}
            tasks={imageTasks}
            filter={filter}
            onCancel={voidPromise(handleCancelSingle)}
          />
          <div
            className="h-px"
            style={{ background: "var(--color-hairline-soft)" }}
          />
          <ChannelSection
            title={t("video_channel")}
            icon={Video}
            tasks={videoTasks}
            filter={filter}
            onCancel={voidPromise(handleCancelSingle)}
          />
          {audioTasks.length > 0 && (
            <>
              <div
                className="h-px"
                style={{ background: "var(--color-hairline-soft)" }}
              />
              <ChannelSection
                title={t("audio_channel")}
                icon={AudioLines}
                tasks={audioTasks}
                filter={filter}
                onCancel={voidPromise(handleCancelSingle)}
              />
            </>
          )}
        </div>

        {/* Cancel confirmation */}
        {cancelConfirm && (
          <div
            className="px-4 py-3"
            role="alertdialog"
            aria-label={t("cancel_confirm_aria")}
            style={{ background: "oklch(0.16 0.010 265 / 0.5)" }}
          >
            <p
              className="text-[12px]"
              style={{ color: "var(--color-text-2)" }}
            >
              {cancelConfirm.preview
                ? cancelConfirm.preview.cascaded.length > 0
                  ? t("cancel_cascade_msg", {
                      count: cancelConfirm.preview.cascaded.length,
                    })
                  : t("cancel_single_confirm")
                : t("cancel_all_confirm", { count: cancelConfirm.allCount })}
            </p>
            {cancelConfirm.preview &&
              cancelConfirm.preview.cascaded.length > 0 && (
                <ul
                  className="num mt-1.5 max-h-20 overflow-y-auto text-[10.5px]"
                  style={{ color: "var(--color-text-4)" }}
                >
                  {cancelConfirm.preview.cascaded.map((task) => (
                    <li key={task.task_id}>
                      {t(`task_type_${task.task_type}`, {
                        defaultValue: task.task_type,
                      })}{" "}
                      / {task.resource_id}
                    </li>
                  ))}
                </ul>
              )}
            <div className="mt-2.5 flex gap-2">
              <button
                type="button"
                onClick={voidPromise(confirmCancel)}
                disabled={cancelling}
                className="focus-ring rounded px-2.5 py-1 text-[11px] font-medium transition-transform disabled:opacity-50"
                style={{
                  color: "oklch(0.98 0 0)",
                  background:
                    "linear-gradient(135deg, oklch(0.55 0.20 25), oklch(0.45 0.18 25))",
                  boxShadow:
                    "inset 0 1px 0 oklch(1 0 0 / 0.18), 0 4px 14px -4px oklch(0.40 0.18 25 / 0.5)",
                }}
              >
                {cancelling ? t("cancelling") : t("confirm_cancel")}
              </button>
              <button
                type="button"
                onClick={() => setCancelConfirm(null)}
                className="focus-ring rounded px-2.5 py-1 text-[11px] transition-colors"
                style={{
                  color: "var(--color-text-3)",
                  border: "1px solid var(--color-hairline)",
                  background: "oklch(0.22 0.011 265 / 0.5)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = "var(--color-text)";
                  e.currentTarget.style.background = "oklch(0.26 0.013 265 / 0.7)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = "var(--color-text-3)";
                  e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
                }}
              >
                {t("go_back")}
              </button>
            </div>
          </div>
        )}
      </motion.div>
    </GlassPopover>
  );
}
