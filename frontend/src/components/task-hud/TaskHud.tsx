import { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Image, Video, Check, X, Loader2 } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

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
// TaskRow — single task entry with animation
// ---------------------------------------------------------------------------

function TaskRow({ task }: { task: TaskItem }) {
  const statusLabel: Record<TaskItem["status"], string> = {
    running: "生成中...",
    queued: "排队中",
    succeeded: "已完成",
    failed: "失败",
  };

  const statusColor: Record<TaskItem["status"], string> = {
    running: "text-indigo-400",
    queued: "text-gray-500",
    succeeded: "text-emerald-400",
    failed: "text-red-400",
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.2 }}
      className={`flex items-center gap-2 px-3 py-1.5 text-sm ${
        task.status === "failed" ? "bg-red-500/5" : ""
      }`}
    >
      <TaskStatusIcon status={task.status} />
      <span className="font-mono text-xs text-gray-400">
        {task.resource_id}
      </span>
      <span className="flex-1 truncate text-gray-300">{task.task_type}</span>
      <span className={`text-xs ${statusColor[task.status]}`}>
        {statusLabel[task.status]}
      </span>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// ChannelSection — groups tasks under image or video channel
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
  const running = tasks.filter((t) => t.status === "running");
  const queued = tasks.filter((t) => t.status === "queued");
  const recent = tasks
    .filter((t) => t.status === "succeeded" || t.status === "failed")
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
          <TaskRow key={task.task_id} task={task} />
        ))}
      </AnimatePresence>
      {visible.length === 0 && (
        <div className="px-3 py-2 text-xs text-gray-600">暂无任务</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TaskHud — popover panel showing real-time task queue status
// ---------------------------------------------------------------------------

export function TaskHud() {
  const { taskHudOpen, setTaskHudOpen } = useAppStore();
  const { tasks, stats } = useTasksStore();
  const panelRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!taskHudOpen) return;
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setTaskHudOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [taskHudOpen, setTaskHudOpen]);

  const imageTasks = tasks.filter((t) => t.media_type === "image");
  const videoTasks = tasks.filter((t) => t.media_type === "video");

  return (
    <AnimatePresence>
      {taskHudOpen && (
        <motion.div
          ref={panelRef}
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.15 }}
          className="absolute right-0 top-full z-50 mt-1 w-80 rounded-lg border border-gray-800 bg-gray-900 shadow-xl"
        >
          {/* Stats bar */}
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

          {/* Dual channel */}
          <div className="max-h-80 divide-y divide-gray-800/50 overflow-y-auto">
            <ChannelSection title="图片通道" icon={Image} tasks={imageTasks} />
            <ChannelSection title="视频通道" icon={Video} tasks={videoTasks} />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
