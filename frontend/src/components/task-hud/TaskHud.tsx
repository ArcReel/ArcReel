import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { activateOnEnterSpace } from "@/utils/a11y";
import { voidPromise } from "@/utils/async";
import { motion, AnimatePresence } from "framer-motion";
import { Image, Video, Check, X, Loader2, ChevronDown, Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useEscapeClose } from "@/hooks/useEscapeClose";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { API } from "@/api";
import type { TaskItem } from "@/types";
import { Popover } from "@/components/ui/Popover";

// ---------------------------------------------------------------------------
// Theme tokens — v3 cool oklch + accent purple
// ---------------------------------------------------------------------------

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

const STATUS_COLORS: Record<TaskItem["status"], string> = {
  running: "var(--color-accent-2)",
  queued: "var(--color-text-4)",
  succeeded: "var(--color-good)",
  failed: "oklch(0.72 0.18 25)",
  cancelled: "var(--color-text-3)",
};

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
  isFading,
  expandedErrorId,
  onToggleError,
  onCancel,
}: {
  task: TaskItem;
  isFading: boolean;
  expandedErrorId: string | null;
  onToggleError: (taskId: string) => void;
  onCancel?: (taskId: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const statusLabel: Record<TaskItem["status"], string> = {
    running: t("generating_status"),
    queued: t("queued_status"),
    succeeded: t("completed_status"),
    failed: t("failed_status"),
    cancelled: t("cancelled_status"),
  };

  const rowBg =
    task.status === "failed"
      ? "oklch(0.30 0.10 25 / 0.18)"
      : task.status === "succeeded" && !isFading
        ? "oklch(0.30 0.10 155 / 0.12)"
        : "transparent";

  const isErrorExpanded = expandedErrorId === task.task_id;
  const hasError = task.status === "failed" && task.error_message;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, height: 0 }}
      animate={{
        opacity: isFading ? 0 : 1,
        height: isFading ? 0 : "auto",
      }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: isFading ? 0.4 : 0.2 }}
      className="overflow-hidden"
    >
      <div
        className={`flex items-center gap-2 px-3 py-1.5 text-[12px] ${
          hasError ? "cursor-pointer" : ""
        }`}
        style={{ background: rowBg, transition: "background-color .12s ease" }}
        role={hasError ? "button" : undefined}
        tabIndex={hasError ? 0 : undefined}
        onClick={hasError ? () => onToggleError(task.task_id) : undefined}
        onKeyDown={hasError ? activateOnEnterSpace(() => onToggleError(task.task_id)) : undefined}
        onMouseEnter={(e) => {
          if (hasError)
            e.currentTarget.style.background = "oklch(0.30 0.10 25 / 0.28)";
        }}
        onMouseLeave={(e) => {
          if (hasError) e.currentTarget.style.background = rowBg;
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
          {task.task_type}
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
        {task.status === "cancelled" && task.cancelled_by === "cascade" && (
          <span
            className="ml-1 text-[10.5px]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("cascade_label")}
          </span>
        )}
        {hasError && (
          <ChevronDown
            className={`h-3 w-3 transition-transform ${isErrorExpanded ? "rotate-180" : ""}`}
            style={{ color: "var(--color-text-4)" }}
          />
        )}
      </div>

      {task.status === "running" && (
        <div className="px-3 pb-1">
          <RunningProgressBar />
        </div>
      )}

      <AnimatePresence>
        {hasError && isErrorExpanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div
              className="mx-3 mb-1.5 rounded px-2 py-1.5 text-[10.5px]"
              style={{
                background: "oklch(0.30 0.10 25 / 0.10)",
                color: "oklch(0.85 0.10 25)",
                border: "1px solid oklch(0.45 0.18 25 / 0.30)",
              }}
            >
              {task.error_message}
            </div>
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
  onCancel,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  tasks: TaskItem[];
  onCancel?: (taskId: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  const [fadingIds, setFadingIds] = useState<Set<string>>(new Set());
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(new Set());
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const [expandedErrorId, setExpandedErrorId] = useState<string | null>(null);

  const toggleError = useCallback((taskId: string) => {
    setExpandedErrorId((prev) => (prev === taskId ? null : taskId));
  }, []);

  useEffect(() => {
    const autoFadeTasks = tasks.filter(
      (task) =>
        (task.status === "succeeded" || task.status === "cancelled") &&
        !fadingIds.has(task.task_id) &&
        !hiddenIds.has(task.task_id),
    );

    for (const task of autoFadeTasks) {
      if (timersRef.current.has(task.task_id)) continue;

      const fadeTimer = setTimeout(() => {
        setFadingIds((prev) => new Set(prev).add(task.task_id));

        const hideTimer = setTimeout(() => {
          setHiddenIds((prev) => new Set(prev).add(task.task_id));
          timersRef.current.delete(task.task_id);
        }, 400);

        timersRef.current.set(task.task_id + "_hide", hideTimer);
      }, 3000);

      timersRef.current.set(task.task_id, fadeTimer);
    }

    const timers = timersRef.current;
    return () => {
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
    };
  }, [tasks, fadingIds, hiddenIds]);

  const running = tasks.filter((task) => task.status === "running");
  const queued = tasks.filter((task) => task.status === "queued");
  const recent = tasks
    .filter(
      (task) =>
        task.status === "succeeded" ||
        task.status === "failed" ||
        task.status === "cancelled",
    )
    .filter((task) => !hiddenIds.has(task.task_id))
    .slice(0, 5);

  const visible = [...running, ...queued, ...recent];

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
            isFading={fadingIds.has(task.task_id)}
            expandedErrorId={expandedErrorId}
            onToggleError={toggleError}
            onCancel={onCancel}
          />
        ))}
      </AnimatePresence>
      {visible.length === 0 && (
        <div
          className="px-3 py-2 text-[11px] italic"
          style={{ color: "var(--color-text-4)" }}
        >
          {t("no_tasks")}
        </div>
      )}
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

  return (
    <Popover
      open={taskHudOpen}
      onClose={() => setTaskHudOpen(false)}
      anchorRef={anchorRef}
      sideOffset={6}
      width="w-[22rem]"
      backgroundColor="transparent"
      className="overflow-hidden rounded-xl"
      style={{
        background: PANEL_BG,
        border: "1px solid var(--color-hairline)",
        backdropFilter: "blur(14px)",
        WebkitBackdropFilter: "blur(14px)",
        boxShadow:
          "0 24px 60px -12px oklch(0 0 0 / 0.55), inset 0 1px 0 oklch(1 0 0 / 0.05)",
      }}
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
            className="pointer-events-none absolute inset-x-0 top-0 h-px"
            style={{
              background:
                "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
            }}
          />
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

        {/* Channels */}
        <div
          className="max-h-80 overflow-y-auto"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <ChannelSection
            title={t("image_channel")}
            icon={Image}
            tasks={imageTasks}
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
            onCancel={voidPromise(handleCancelSingle)}
          />
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
                      {task.task_type} / {task.resource_id}
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
    </Popover>
  );
}
