import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";

import { DefaultModelSettingsLink } from "@/components/shared/DefaultModelSettingsLink";
import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { TaskFailureDetails } from "@/components/shared/TaskFailureDetails";
import { Button } from "@/components/ui/button";
import { isAdScriptTask } from "@/hooks/useAdScriptEntry";
import { selectRecentTasks, useTasksStore } from "@/stores/tasks-store";
import { outputTruncationOf } from "@/utils/output-truncation";

import { latestEpisodeTextTaskFailure, type EpisodeTextTaskKind } from "./text-task-failure";

const FAILURE_KEYS: Record<EpisodeTextTaskKind, string> = {
  text_script_plan: "script_plan_failed",
  text_episode_script: "prompt_authoring_failed",
};

export function TextTaskFailureNote({
  projectName,
  episode,
  isAd = false,
  hasScript = true,
}: {
  projectName: string;
  episode: number;
  isAd?: boolean;
  hasScript?: boolean;
}) {
  const { t } = useTranslation(["dashboard", "common", "errors"]);
  const failure = useTasksStore(useShallow((s) => latestEpisodeTextTaskFailure(selectRecentTasks(s), projectName, episode)));
  const [dismissedTaskId, setDismissedTaskId] = useState<string | null>(null);
  if (failure === null || failure.task.task_id === dismissedTaskId) return null;
  const truncation = outputTruncationOf(failure.task);
  const reason =
    failure.task.error_code === "text_wait_timeout"
      ? t("errors:text_wait_timeout")
      : (failure.task.error_message ?? t("script_plan_failed_unknown"));
  const adScript =
    failure.kind === "text_episode_script" && isAd && (!hasScript || isAdScriptTask(failure.task));
  return (
    <div
      role="alert"
      className="mx-4 mt-2 flex items-start gap-2.5 rounded-xl border border-destructive/35 px-4 py-2.5 text-sm"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col items-start gap-1.5">
        <p className="m-0 text-destructive">{t(adScript ? "ad_script_failed" : FAILURE_KEYS[failure.kind], { reason })}</p>
        {failure.task.error_code === "text_default_model_not_set" ? <DefaultModelSettingsLink /> : null}
        {truncation ? <OutputTruncationHint truncation={truncation} /> : null}
        <TaskFailureDetails
          code={failure.task.error_code}
          params={failure.task.error_params}
          detail={failure.task.error_detail}
        />
      </div>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={t("common:close")}
        onClick={() => setDismissedTaskId(failure.task.task_id)}
      >
        <X aria-hidden />
      </Button>
    </div>
  );
}
