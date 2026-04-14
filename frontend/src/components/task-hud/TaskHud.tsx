import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { voidPromise } from "@/utils/async";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Image, Video, Check, X, Loader2, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAnchoredPopover } from "@/hooks/useAnchoredPopover";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { API } from "@/api";
import type { TaskItem } from "@/types";
import { UI_LAYERS } from "@/utils/ui-layers";
import { POPOVER_BG } from "@/components/ui/Popover";

// ---------------------------------------------------------------------------
// Task status icon — visual indicator per task state
// ---------------------------------------------------------------------------

function TaskStatusIcon({ status }: { status: TaskItem["status"] }) {
  switch (status) {
    case "running":
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400" />;
    case "queued":
      return <div className="h-2 w-2 rounded-full bg-gray-500" />;
    case "succeeded":
      return <Check className="h-3.5 w-3.5 text-emerald-400" />;
    case "failed":
      return <X className="h-3.5 w-3.5 text-red-400" />;
    case "cancelled":
      return <X className="h-3.5 w-3.5 text-gray-400" />;
  }
}

// ---------------------------------------------------------------------------
// RunningProgressBar — 运行中任务的动态进度条
// ---------------------------------------------------------------------------

function RunningProgressBar() {
  return (
    <div className="relative mt-1 h-0.5 w-full overflow-hidden rounded-full bg-gray-800">
      <motion.div
        className="absolute inset-y-0 left-0 w-1/3 rounded-full bg-gradient-to-r from-indigo-500 via-indigo-400 to-indigo-500"
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
// TaskRow — 单个任务条目（含完成高亮、失败展开、运行进度条）
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

  const statusColor: Record<TaskItem["status"], string> = {
    running: "text-indigo-400",
    queued: "text-gray-500",
    succeeded: "text-emerald-400",
    failed: "text-red-400",
    cancelled: "text-gray-400",
  };

  // 根据状态确定行背景样式
  const rowBg =
    task.status === "failed"
      ? "bg-red-500/10"
      : task.status === "succeeded" && !isFading
        ? "bg-emerald-500/10"
        : "";

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
      {/* 主行内容 */}
      <div
        className={`flex items-center gap-2 px-3 py-1.5 text-sm ${rowBg} ${
          hasError ? "cursor-pointer hover:bg-red-500/15" : ""
        }`}
        onClick={hasError ? () => onToggleError(task.task_id) : undefined}
      >
        <TaskStatusIcon status={task.status} />
        <span className="font-mono text-xs text-gray-400">
          {task.resource_id}
        </span>
        <span className="flex-1 truncate text-gray-300">{task.task_type}</span>
        <span className={`text-xs ${statusColor[task.status]}`}>
          {statusLabel[task.status]}
        </span>
        {task.status === "queued" && onCancel && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onCancel(task.task_id);
            }}
            className="ml-1 rounded px-1 py-0.5 text-xs text-gray-500 hover:bg-gray-700 hover:text-gray-300"
            title={t("cancel_task")}
            aria-label={t("cancel_this_task")}
          >
            {t("cancel_btn")}
          </button>
        )}
        {task.status === "cancelled" && task.cancelled_by === "cascade" && (
          <span className="ml-1 text-xs text-gray-500">{t("cascade_label")}</span>
        )}
        {hasError && (
          <ChevronDown
            className={`h-3 w-3 text-gray-500 transition-transform ${
              isErrorExpanded ? "rotate-180" : ""
            }`}
          />
        )}
      </div>

      {/* 运行中任务的进度条 */}
      {task.status === "running" && (
        <div className="px-3 pb-1">
          <RunningProgressBar />
        </div>
      )}

      {/* 失败任务的错误详情展开区域 */}
      <AnimatePresence>
        {hasError && isErrorExpanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="mx-3 mb-1.5 rounded bg-red-500/5 px-2 py-1.5 text-xs text-red-300/80">
              {task.error_message}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// ChannelSection — 按图片/视频通道分组，含自动淡出逻辑
// ---------------------------------------------------------------------------

function ChannelSection({
  title,
  icon: Icon,
  tasks,
  onCancel,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  tasks: TaskItem[];
  onCancel?: (taskId: string) => void;
}) {
  const { t } = useTranslation("dashboard");
  // 跟踪正在淡出的任务 ID
  const [fadingIds, setFadingIds] = useState<Set<string>>(new Set());
  // 跟踪已完全淡出（应隐藏）的任务 ID
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(new Set());
  // 保存定时器引用以便清理
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  // 失败任务错误详情展开状态
  const [expandedErrorId, setExpandedErrorId] = useState<string | null>(null);

  const toggleError = useCallback((taskId: string) => {
    setExpandedErrorId((prev) => (prev === taskId ? null : taskId));
  }, []);

  // 监听任务状态变化，为 succeeded/cancelled 任务设置自动淡出
  useEffect(() => {
    const autoFadeTasks = tasks.filter(
      (t) =>
        (t.status === "succeeded" || t.status === "cancelled") &&
        !fadingIds.has(t.task_id) &&
        !hiddenIds.has(t.task_id),
    );

    for (const task of autoFadeTasks) {
      if (timersRef.current.has(task.task_id)) continue;

      // 3 秒后开始淡出动画
      const fadeTimer = setTimeout(() => {
        setFadingIds((prev) => new Set(prev).add(task.task_id));

        // 淡出动画完成后（400ms）标记为隐藏
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
      // 组件卸载时清理所有定时器
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
    };
  }, [tasks, fadingIds, hiddenIds]);

  const running = tasks.filter((t) => t.status === "running");
  const queued = tasks.filter((t) => t.status === "queued");
  const recent = tasks
    .filter((t) => t.status === "succeeded" || t.status === "failed" || t.status === "cancelled")
    .filter((t) => !hiddenIds.has(t.task_id))
    .slice(0, 5);

  const visible = [...running, ...queued, ...recent];

  return (
    <div>
      <div className="flex items-center gap-2 px-3 py-2 text-xs font-semibold text-gray-400">
        <Icon className="h-3.5 w-3.5" />
        {title}
        {running.length > 0 && (
          <span className="ml-auto text-indigo-400">
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
        <div className="px-3 py-2 text-xs text-gray-600">{t("no_tasks")}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TaskHud — 弹出面板，实时展示任务队列状态
// ---------------------------------------------------------------------------

export function TaskHud({ anchorRef }: { anchorRef: RefObject<HTMLElement | null> }) {
  const { t } = useTranslation("dashboard");
  const { taskHudOpen, setTaskHudOpen } = useAppStore();
  const { tasks, stats } = useTasksStore();
  const { panelRef, positionStyle } = useAnchoredPopover({
    open: taskHudOpen,
    anchorRef,
    onClose: () => setTaskHudOpen(false),
    sideOffset: 4,
  });

  const [cancelConfirm, setCancelConfirm] = useState<{
    taskId?: string;
    preview?: { task: { task_id: string; task_type: string; resource_id: string }; cascaded: { task_id: string; task_type: string; resource_id: string }[] };
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
    const queuedTask = tasks.find((t) => t.status === "queued");
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

  // Escape 键关闭确认面板
  useEffect(() => {
    if (!cancelConfirm) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setCancelConfirm(null);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [cancelConfirm]);

  const imageTasks = tasks.filter((t) => t.media_type === "image");
  const videoTasks = tasks.filter((t) => t.media_type === "video");

  if (typeof document === "undefined") return null;

  return createPortal(
    <AnimatePresence>
      {taskHudOpen && (
        <motion.div
          ref={panelRef}
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.15 }}
          className={`fixed w-80 isolate rounded-lg border border-gray-800 shadow-xl ${UI_LAYERS.workspacePopover}`}
          style={{
            ...positionStyle,
            backgroundColor: POPOVER_BG,
          }}
        >
          {/* 统计栏 */}
          <div className="flex gap-3 border-b border-gray-800 px-3 py-2 text-xs text-gray-400">
            <span>
              {t("queued_label")}{" "}
              <strong className="text-gray-200">{stats.queued}</strong>
            </span>
            <span>
              {t("running_label")}{" "}
              <strong className="text-indigo-400">{stats.running}</strong>
            </span>
            <span>
              {t("completed_label")}{" "}
              <strong className="text-emerald-400">{stats.succeeded}</strong>
            </span>
            <span>
              {t("failed_label")}{" "}
              <strong className="text-red-400">{stats.failed}</strong>
            </span>
            {stats.cancelled > 0 && (
              <span>
                {t("cancelled_label")}{" "}
                <strong className="text-gray-400">{stats.cancelled}</strong>
              </span>
            )}
            {stats.queued > 0 && (
              <button
                onClick={voidPromise(handleCancelAll)}
                className="ml-auto text-xs text-gray-500 hover:text-red-400"
                aria-label={t("cancel_all_queued_aria")}
              >
                {t("cancel_all")}
              </button>
            )}
          </div>

          {/* 双通道 */}
          <div className="max-h-80 divide-y divide-gray-800/50 overflow-y-auto">
            <ChannelSection title={t("image_channel")} icon={Image} tasks={imageTasks} onCancel={voidPromise(handleCancelSingle)} />
            <ChannelSection title={t("video_channel")} icon={Video} tasks={videoTasks} onCancel={voidPromise(handleCancelSingle)} />
          </div>

          {/* 取消确认面板 */}
          {cancelConfirm && (
            <div className="border-t border-gray-800 px-3 py-2" role="alertdialog" aria-label={t("cancel_confirm_aria")}>
              <p className="text-xs text-gray-300">
                {cancelConfirm.preview
                  ? cancelConfirm.preview.cascaded.length > 0
                    ? t("cancel_cascade_msg", { count: cancelConfirm.preview.cascaded.length })
                    : t("cancel_single_confirm")
                  : t("cancel_all_confirm", { count: cancelConfirm.allCount })}
              </p>
              {cancelConfirm.preview && cancelConfirm.preview.cascaded.length > 0 && (
                <ul className="mt-1 max-h-20 overflow-y-auto text-xs text-gray-500">
                  {cancelConfirm.preview.cascaded.map((t) => (
                    <li key={t.task_id}>
                      {t.task_type} / {t.resource_id}
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-2 flex gap-2">
                <button
                  onClick={voidPromise(confirmCancel)}
                  disabled={cancelling}
                  className="rounded bg-red-600/80 px-2 py-0.5 text-xs text-white hover:bg-red-600 disabled:opacity-50"
                >
                  {cancelling ? t("cancelling") : t("confirm_cancel")}
                </button>
                <button
                  onClick={() => setCancelConfirm(null)}
                  className="rounded px-2 py-0.5 text-xs text-gray-400 hover:bg-gray-700"
                >
                  {t("go_back")}
                </button>
              </div>
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
