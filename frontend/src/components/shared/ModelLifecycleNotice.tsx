import { useTranslation } from "react-i18next";

import { InlineWarning } from "@/components/shared/InlineWarning";
import type { ModelLifecycleNotice as Notice, ModelReferenceLayer } from "@/types/system";

/** "provider/model" → 下线提示，取自候选接口的 `lifecycle`。 */
export type ModelLifecycleNotices = Record<string, Notice>;

export interface ModelLifecycleNoticeProps {
  /** 这一处引用的当前值（provider/model）；留空（跟随默认、自动选择）不提示。 */
  value: string;
  notices?: ModelLifecycleNotices;
  /** 这一处引用所在的位置，决定可切换的目标：任务类型桶按桶取，其余取默认层。 */
  layer?: ModelReferenceLayer;
  /** "provider/model" → 当前语言下的模型名；缺键退回 model id。 */
  modelNames?: Record<string, string>;
  /** 一键切换：只改这一处引用。 */
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
 * 引用处的下线提示：选中的模型即将下线或已下线时，在下拉下方写明下线日期与官方后继；后继在同一供应商下、
 * 且是这一处的候选时附一键切换。选中的模型已不在清单里、也查不到生命周期时，提示不可用。只提示、不替用户切换
 * （docs/adr/0103），也没有弹窗。
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
