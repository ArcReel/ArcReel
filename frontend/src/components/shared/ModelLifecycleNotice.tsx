import { useTranslation } from "react-i18next";

import { InlineWarning } from "@/components/shared/InlineWarning";
import type { ModelLifecycleNotice as Notice, ModelReferenceLayer } from "@/types/system";

/** "provider/model" → retirement notices from candidate-endpoint `lifecycle`. */
export type ModelLifecycleNotices = Record<string, Notice>;

export interface ModelLifecycleNoticeProps {
  /** Current reference values (provider/model); empty values (follow defaults/automatic selection) show no notices. */
  value: string;
  notices?: ModelLifecycleNotices;
  /** Reference locations determine switch candidates: task-type buckets use their buckets, others use default layers. */
  layer?: ModelReferenceLayer;
  /** "provider/model" → model names in current locales; missing keys fall back to model IDs. */
  modelNames?: Record<string, string>;
  /** One-click switching changes only this reference. */
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

/**
 * Retirement notices at references: retiring/retired selected models show retirement dates and official successors below selects; offer one-click switching
 * when successors share providers and are candidates here. Missing selected models without lifecycle data show Unavailable. Notify only, never switch for users
 * (docs/adr/0103), without dialogs.
 */
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
