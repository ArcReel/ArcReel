import { useState } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import type { RecommendationChange } from "@/types";
import { errMsg } from "@/utils/async";

export interface RecommendationNoticeProps {
  change?: RecommendationChange;
  providerNames: Record<string, string>;
  modelNames: Record<string, string>;
  onResolved: (change: RecommendationChange, applied: boolean) => Promise<void> | void;
}

function modelName(value: string, modelNames: Record<string, string>): string {
  return modelNames[value] ?? value.slice(value.indexOf("/") + 1);
}

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
