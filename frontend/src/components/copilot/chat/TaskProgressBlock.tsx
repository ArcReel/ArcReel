import type { ContentBlock } from "@/types";

interface TaskProgressBlockProps {
  block: ContentBlock;
}

export function TaskProgressBlock({ block }: TaskProgressBlockProps) {
  const status = block.status;
  const description = block.description || "";
  const summary = block.summary || "";
  const taskStatus = block.task_status;

  if (status === "task_started") {
    return (
      <div className="my-1 flex items-center gap-1.5 text-xs text-slate-400">
        <span className="inline-block h-3 w-3 animate-spin rounded-full border border-slate-500 border-t-transparent" />
        <span>子任务开始: {description}</span>
      </div>
    );
  }

  if (status === "task_progress") {
    const tokens = block.usage?.total_tokens;
    return (
      <div className="my-1 flex items-center gap-1.5 text-xs text-slate-400">
        <span className="inline-block h-3 w-3 animate-spin rounded-full border border-slate-500 border-t-transparent" />
        <span>
          {description}
          {tokens != null && ` (tokens: ${tokens})`}
        </span>
      </div>
    );
  }

  if (status === "task_notification") {
    const isCompleted = taskStatus === "completed";
    const isFailed = taskStatus === "failed";
    return (
      <div
        className={`my-1 flex items-center gap-1.5 text-xs ${
          isFailed ? "text-red-400" : isCompleted ? "text-green-400" : "text-slate-400"
        }`}
      >
        <span>{isCompleted ? "\u2713" : isFailed ? "\u2717" : "\u2013"}</span>
        <span>
          子任务{isCompleted ? "完成" : isFailed ? "失败" : "结束"}: {summary || description}
        </span>
      </div>
    );
  }

  return null;
}
