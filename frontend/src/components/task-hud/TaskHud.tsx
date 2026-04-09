import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Image, Video, Check, X, Loader2, ChevronDown } from "lucide-react";
import { useAnchoredPopover } from "@/hooks/useAnchoredPopover";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
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
}: {
  task: TaskItem;
  isFading: boolean;
  expandedErrorId: string | null;
  onToggleError: (taskId: string) => void;
}) {
  const statusLabel: Record<TaskItem["status"], string> = {
    running: "生成中...",
    queued: "排队中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
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
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  tasks: TaskItem[];
}) {
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

  // 监听任务状态变化，为 succeeded 任务设置自动淡出
  useEffect(() => {
    const succeededTasks = tasks.filter(
      (t) =>
        t.status === "succeeded" &&
        !fadingIds.has(t.task_id) &&
        !hiddenIds.has(t.task_id),
    );

    for (const task of succeededTasks) {
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

    return () => {
      // 组件卸载时清理所有定时器
      for (const timer of timersRef.current.values()) {
        clearTimeout(timer);
      }
    };
  }, [tasks, fadingIds, hiddenIds]);

  const running = tasks.filter((t) => t.status === "running");
  const queued = tasks.filter((t) => t.status === "queued");
  const recent = tasks
    .filter((t) => t.status === "succeeded" || t.status === "failed")
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
            {running.length} 运行中
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
          />
        ))}
      </AnimatePresence>
      {visible.length === 0 && (
        <div className="px-3 py-2 text-xs text-gray-600">暂无任务</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TaskHud — 弹出面板，实时展示任务队列状态
// ---------------------------------------------------------------------------

export function TaskHud({ anchorRef }: { anchorRef: RefObject<HTMLElement | null> }) {
  const { taskHudOpen, setTaskHudOpen } = useAppStore();
  const { tasks, stats } = useTasksStore();
  const { panelRef, positionStyle } = useAnchoredPopover({
    open: taskHudOpen,
    anchorRef,
    onClose: () => setTaskHudOpen(false),
    sideOffset: 4,
  });

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
              排队{" "}
              <strong className="text-gray-200">{stats.queued}</strong>
            </span>
            <span>
              运行{" "}
              <strong className="text-indigo-400">{stats.running}</strong>
            </span>
            <span>
              完成{" "}
              <strong className="text-emerald-400">{stats.succeeded}</strong>
            </span>
            <span>
              失败{" "}
              <strong className="text-red-400">{stats.failed}</strong>
            </span>
          </div>

          {/* 双通道 */}
          <div className="max-h-80 divide-y divide-gray-800/50 overflow-y-auto">
            <ChannelSection title="图片通道" icon={Image} tasks={imageTasks} />
            <ChannelSection title="视频通道" icon={Video} tasks={videoTasks} />
          </div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
