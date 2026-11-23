import { useTranslation } from "react-i18next";

import { InlineWarning } from "@/components/shared/InlineWarning";
import type { ModelLifecycleNotice as Notice, ModelReferenceLayer } from "@/types/system";

export type ModelLifecycleNotices = Record<string, Notice>;

export interface ModelLifecycleNoticeProps {
  value: string;
  notices?: ModelLifecycleNotices;
  layer?: ModelReferenceLayer;
  modelNames?: Record<string, string>;
  onSwitch: (target: string) => void;
}

function messageKey(notice: Notice, hasSuccessor: boolean): string {
  if (notice.status === "unavailable") return "model_lifecycle_unavailable";
  const base =
    notice.status === "retiring"
      ? "model_lifecycle_retiring"
      : notice.shutdown_date
        ? "model_lifecycle_discontinued"
        : "model_lifecycle_discontinued_undated";
  return hasSuccessor ? base : `${base}_no_successor`;
}

export function ModelLifecycleNotice({ value, notices, layer = "default", modelNames, onSwitch }: ModelLifecycleNoticeProps) {
  const { t } = useTranslation("templates");
  const notice = value ? notices?.[value] : undefined;
  if (!notice) return null;

  const target = notice.switch_targets[layer] ?? null;
  const targetName = target ? (modelNames?.[target] ?? target.slice(target.indexOf("/") + 1)) : null;
  const successor = targetName ?? notice.successor_name ?? notice.successor;
  return (
    <InlineWarning
      message={t(messageKey(notice, !!successor), { date: notice.shutdown_date ?? "", successor: successor ?? "" })}
      action={target ? { label: t("model_lifecycle_switch", { model: targetName }), onClick: () => onSwitch(target) } : undefined}
    />
  );
}
