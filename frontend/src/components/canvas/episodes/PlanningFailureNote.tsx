import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";

import { DefaultModelSettingsLink } from "@/components/shared/DefaultModelSettingsLink";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { TaskFailureDetails } from "@/components/shared/TaskFailureDetails";

import type { PlanningFailure } from "./episode-planning-model";

export function PlanningFailureNote({ failure }: { failure: PlanningFailure }) {
  const { t } = useTranslation(["dashboard", "errors"]);
  const reason =
    failure.code === "text_wait_timeout" ? t("errors:text_wait_timeout") : failure.message.trim();
  const settings = failure.code === "text_default_model_not_set";
  if (!reason && !settings && !failure.truncated) return null;
  return (
    <div role="alert" className="flex flex-col gap-1.5 rounded-md bg-warn/10 p-2 text-xs">
      {reason ? (
        <p className="flex gap-1.5 text-subtle-foreground">
          <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <span className="min-w-0 wrap-break-word">{t("episode_planning_failed", { reason })}</span>
        </p>
      ) : null}
      {settings ? <DefaultModelSettingsLink /> : null}
      {failure.truncated ? <OutputTruncationHint truncation={failure.truncated} /> : null}
      <TaskFailureDetails code={failure.code} params={failure.params} detail={failure.detail} />
    </div>
  );
}
