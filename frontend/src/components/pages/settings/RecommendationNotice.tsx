import { useState } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import type { RecommendationChange } from "@/types";
import { errMsg } from "@/utils/async";

export interface RecommendationNoticeProps {
  change?: RecommendationChange;
  /** provider id → 当前语言下的供应商名；缺键退回 id。 */
  providerNames: Record<string, string>;
  /** "provider/model" → 当前语言下的模型名；缺键退回 model id。 */
  modelNames: Record<string, string>;
  /**
   * 处理完（或推荐又变了）之后调用：上层重取配置与提示。`applied` 表示服务端已把推荐写成全局默认，
   * 上层要把它并入未保存修改，否则之后保存别的字段会把旧默认模型写回去。
   */
  onResolved: (change: RecommendationChange, applied: boolean) => Promise<void> | void;
}

function modelName(value: string, modelNames: Record<string, string>): string {
  return modelNames[value] ?? value.slice(value.indexOf("/") + 1);
}

/**
 * 推荐变化的提示：内置供应商在这个媒体类型下的推荐模型变了（或新给出了推荐），写在默认模型下方。
 * 只提示、不替用户切换（docs/adr/0103）：切换或保留当前都只处理这一条，之后不再提示，直到推荐再次变化。
 */
export function RecommendationNotice({ change, providerNames, modelNames, onResolved }: RecommendationNoticeProps) {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState(false);
  if (!change) return null;

  const provider = providerNames[change.provider_id] ?? change.provider_id;
  const model = modelName(change.recommended, modelNames);
  const message = change.current
    ? t("recommendation_changed", { provider, model, current: modelName(change.current, modelNames) })
    : t("recommendation_new", { provider, model });

  const resolve = async (apply: boolean) => {
    setPending(true);
    let applied = false;
    try {
      await API.acknowledgeRecommendation(change, apply);
      applied = apply;
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setPending(false);
    }
    await onResolved(change, applied);
  };

  return (
    <div role="status" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>{message}</span>
      <Button variant="outline" size="xs" disabled={pending} onClick={() => void resolve(true)}>
        {change.current ? t("recommendation_switch", { model }) : t("recommendation_set_default")}
      </Button>
      <Button variant="ghost" size="xs" disabled={pending} onClick={() => void resolve(false)}>
        {change.current ? t("recommendation_keep") : t("recommendation_dismiss")}
      </Button>
    </div>
  );
}
