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
        <div
          className="my-1 flex items-center gap-1.5 text-[11.5px]"
          style={{ color: "var(--color-text-4)" }}
        >
          <span>–</span>
          <span>{description} (已取消)</span>
        </div>
      );
    }

    const tokens = status === "task_progress" ? block.usage?.total_tokens : undefined;
    return (
      <div
        className="my-1 flex items-center gap-1.5 text-[11.5px]"
        style={{ color: "var(--color-text-3)" }}
      >
        <span
          className="inline-block h-3 w-3 animate-spin rounded-full border-t-transparent"
          style={{
            borderTop: "1px solid transparent",
            border: "1px solid var(--color-accent)",
            borderTopColor: "transparent",
          }}
        />
        <span>
          {status === "task_started" ? `子任务开始: ${description}` : description}
          {tokens != null && ` (tokens: ${tokens})`}
        </span>
      </div>
    );
  }

  if (status === "task_notification") {
    const isCompleted = taskStatus === "completed";
    const isFailed = taskStatus === "failed";
    const color = isFailed
      ? "var(--color-danger)"
      : isCompleted
        ? "var(--color-good)"
        : "var(--color-text-3)";
    return (
      <div
        className="my-1 flex items-center gap-1.5 text-[11.5px]"
        style={{ color }}
      >
        <span>{isCompleted ? "✓" : isFailed ? "✗" : "–"}</span>
        <span>
          子任务{isCompleted ? "完成" : isFailed ? "失败" : "结束"}: {summary || description}
        </span>
      </div>
    );
  }

  return null;
}
