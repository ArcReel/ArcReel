import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { WorkDetail, WorkRow, useSessionDone, useToolLabel } from "./WorkRow";
import { stringifyResult } from "./utils";
import { toolStatus } from "./work-label";

// ---------------------------------------------------------------------------
// ToolCallWithResult: one tool call with its result as a workflow row, showing a localized name and short summary;
// expand into Arguments and Results sections.
//
// Skill and Agent/Task tool_use bypass this component, using SkillChip and SubagentCard respectively;
// see ContentBlockRenderer dispatch.
// ---------------------------------------------------------------------------

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
