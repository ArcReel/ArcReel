import type { ContentBlock } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";

interface TaskProgressBlockProps {
  block: ContentBlock;
}

const TERMINAL_SESSION = new Set(["completed", "error", "interrupted"]);

export function TaskProgressBlock({ block }: TaskProgressBlockProps) {
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);
  const sessionDone = sessionStatus != null && TERMINAL_SESSION.has(sessionStatus);

  const status = block.status;
  const description = block.description || "";
  const summary = block.summary || "";
  const taskStatus = block.task_status;

  if (status === "task_started" || status === "task_progress") {
    // When session is no longer running, show cancelled state instead of spinner
    if (sessionDone) {
      return (
        <div className="my-1 flex items-center gap-1.5 text-xs text-slate-500">
          <span>{"\u2013"}</span>
          <span>{description} (Đã hủy)</span>
        </div>
      );
    }

    const tokens = status === "task_progress" ? block.usage?.total_tokens : undefined;
    return (
      <div className="my-1 flex items-center gap-1.5 text-xs text-slate-400">
        <span className="inline-block h-3 w-3 animate-spin rounded-full border border-slate-500 border-t-transparent" />
        <span>
          {status === "task_started" ? `Nhiệm vụ con bắt đầu: ${description}` : description}
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
          Nhiệm vụ con {isCompleted ? "hoàn thành" : isFailed ? "thất bại" : "kết thúc"}: {summary || description}
        </span>
      </div>
    );
  }

  return null;
}
