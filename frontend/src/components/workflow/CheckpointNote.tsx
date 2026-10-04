import { useTranslation } from "react-i18next";
import { UnitTag } from "./UnitTag";

/**
 * 哪些单元的请求供应商已经收单。
 *
 * 它自己占一行，不折进任务状态词里：已收单意味着重试可能重复计费，而任务状态词回答的是
 * 另一个问题（这次尝试跑到哪了）。同一步上多个任务已收单时只说一次、列出单元；
 * 没有已收单的任务时不出现——没有这条事实就不摆一句空话。
 */
export function CheckpointNote({ unitIds }: { unitIds: string[] }) {
  const { t } = useTranslation("workflow");
  if (unitIds.length === 0) return null;
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      {unitIds.map((unitId) => (
        <UnitTag key={unitId} unitId={unitId} />
      ))}
      <span>{t("checkpoint_submitted")}</span>
    </p>
  );
}
