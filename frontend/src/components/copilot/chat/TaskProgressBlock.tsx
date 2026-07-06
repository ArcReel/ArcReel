import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { useAssistantStore } from "@/stores/assistant-store";
import { TERMINAL_SESSION_STATUSES } from "./utils";

// ---------------------------------------------------------------------------
// TaskProgressBlock – 无锚点 tool_use 的后台任务进度行。
//
// 有锚点的 task 块由投影折叠进 SubagentCard（task_info），不经过本组件。
// ---------------------------------------------------------------------------

interface TaskProgressBlockProps {
  block: ContentBlock;
}

export function TaskProgressBlock({ block }: TaskProgressBlockProps) {
  const { t } = useTranslation("dashboard");
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);
  const sessionDone = sessionStatus != null && TERMINAL_SESSION_STATUSES.has(sessionStatus);

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
          <span>{t("task_progress_cancelled", { description })}</span>
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
          {status === "task_started" ? t("task_progress_started", { description }) : description}
          {tokens != null && ` ${t("subagent_tokens", { count: tokens })}`}
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
    const label = isCompleted
      ? t("task_progress_completed")
      : isFailed
        ? t("task_progress_failed")
        : t("task_progress_ended");
    return (
      <div
        className="my-1 flex items-center gap-1.5 text-[11.5px]"
        style={{ color }}
      >
        <span>{isCompleted ? "✓" : isFailed ? "✗" : "–"}</span>
        <span>
          {label}: {summary || description}
        </span>
      </div>
    );
  }

  return null;
}
