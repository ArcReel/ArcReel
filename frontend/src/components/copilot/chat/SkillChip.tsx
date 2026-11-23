import { useTranslation } from "react-i18next";
import { WorkDetail, WorkRow, useSessionDone } from "./WorkRow";
import { SKILL_ICON } from "./work-label";


interface SkillChipProps {
  name?: string;
  args?: string;
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
