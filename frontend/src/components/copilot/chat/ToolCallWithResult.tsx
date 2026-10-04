import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { WorkDetail, WorkRow, useSessionDone, useToolLabel } from "./WorkRow";
import { toolStatus } from "./work-label";

// ---------------------------------------------------------------------------
// ToolCallWithResult – 一次工具调用连同它的结果，显示为一行工序：本地化名称加一句摘要，
// 展开后分「参数」「结果」两段。
//
// Skill 与 Agent/Task tool_use 不经过本组件（分别由 SkillChip 与 SubagentCard 渲染，
// 见 ContentBlockRenderer 分发）。
// ---------------------------------------------------------------------------

export function stringifyResult(value: unknown): string {
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

export function ToolCallWithResult({ block }: { block: ContentBlock }) {
  const { t } = useTranslation("dashboard");
  const sessionDone = useSessionDone();
  const { name, summary, icon } = useToolLabel(block.name ?? "", block.input);
  const status = toolStatus(block, sessionDone);

  const input = block.input && Object.keys(block.input).length > 0 ? JSON.stringify(block.input, null, 2) : "";
  const result = stringifyResult(block.result);

  return (
    <WorkRow icon={icon} name={name || t("work_tool_fallback")} summary={summary} status={status}>
      {(input || result) && (
        <>
          {input && <WorkDetail label={t("tool_call_input_label")} text={input} />}
          {result && <WorkDetail label={t("tool_call_result_label")} text={result} error={status === "error"} />}
        </>
      )}
    </WorkRow>
  );
}
