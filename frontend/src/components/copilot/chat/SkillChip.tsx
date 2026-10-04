import { useTranslation } from "react-i18next";
import { WorkDetail, WorkRow, useSessionDone } from "./WorkRow";
import { SKILL_ICON } from "./work-label";

// ---------------------------------------------------------------------------
// SkillChip – Skill 调用的工序行：「/skill-name」加入参。展开查看日志中已有的参数与
// 结果；注入的 skill 全文不在日志里，没有参数与结果时不可展开。
// ---------------------------------------------------------------------------

interface SkillChipProps {
  name?: string;
  args?: string;
  /** Skill tool_use 还没有结果；独立的 skill_invocation 记录视为已完成。 */
  pending?: boolean;
  failed?: boolean;
  result?: string;
}

export function SkillChip({ name, args, pending = false, failed = false, result }: SkillChipProps) {
  const { t } = useTranslation("dashboard");
  const sessionDone = useSessionDone();
  const status = failed ? "error" : !pending ? "ok" : sessionDone ? "stopped" : "running";
  const input = args?.trim() ? args : "";
  const output = result?.trim() ? result : "";

  return (
    <WorkRow icon={SKILL_ICON} name={`/${name || t("skill_chip_unknown")}`} summary={args} status={status}>
      {(input || output) && (
        <>
          {input && <WorkDetail label={t("tool_call_input_label")} text={input} />}
          {output && <WorkDetail label={t("tool_call_result_label")} text={output} error={status === "error"} />}
        </>
      )}
    </WorkRow>
  );
}
