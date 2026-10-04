import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { WorkDetail, WorkRow, useSessionDone } from "./WorkRow";
import { BACKGROUND_TASK_ICON, type WorkStatus } from "./work-label";

// ---------------------------------------------------------------------------
// TaskProgressBlock – 没有锚点 tool_use 的后台任务，显示为一行工序，就地更新状态。
//
// 有锚点的 task 块由投影折叠进 SubagentCard（task_info），不经过本组件。
// ---------------------------------------------------------------------------

function taskStatus(block: ContentBlock, sessionDone: boolean): WorkStatus | null {
  switch (block.status) {
    case "task_started":
    case "task_progress":
      return sessionDone ? "stopped" : "running";
    case "task_notification":
      if (block.task_status === "failed") return "error";
      return block.task_status === "completed" ? "ok" : "stopped";
    default:
      return null;
  }
}

export function TaskProgressBlock({ block }: { block: ContentBlock }) {
  const { t } = useTranslation("dashboard");
  const sessionDone = useSessionDone();
  const status = taskStatus(block, sessionDone);
  if (!status) return null;

  const text = (status === "running" || status === "stopped" ? block.description : block.summary || block.description) ?? "";
  const tokens = status === "running" ? block.usage?.total_tokens : undefined;
  const summary = [text, tokens != null ? t("subagent_tokens", { count: tokens }) : ""].filter(Boolean).join(" · ");
  const input = block.description?.trim() ? block.description : "";
  const output = block.summary?.trim() ? block.summary : "";

  return (
    <WorkRow icon={BACKGROUND_TASK_ICON} name={t("work_background_task")} summary={summary} status={status}>
      {(input || output) && (
        <>
          {input && <WorkDetail label={t("tool_call_input_label")} text={input} />}
          {output && <WorkDetail label={t("tool_call_result_label")} text={output} error={status === "error"} />}
        </>
      )}
    </WorkRow>
  );
}
