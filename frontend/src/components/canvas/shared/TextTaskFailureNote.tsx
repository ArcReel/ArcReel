import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ChevronRight, X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";

import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { isAdScriptTask } from "@/hooks/useAdScriptEntry";
import { useTasksStore } from "@/stores/tasks-store";
import { outputTruncationOf } from "@/utils/output-truncation";

import { latestEpisodeTextTaskFailure, type EpisodeTextTaskKind } from "./text-task-failure";

const FAILURE_KEYS: Record<EpisodeTextTaskKind, string> = {
  text_script_plan: "script_plan_failed",
  text_episode_script: "prompt_authoring_failed",
  text_draft_repair: "draft_repair_failed",
};

function paramsText(params: Record<string, unknown> | undefined): string {
  return Object.entries(params ?? {})
    .map(([key, value]) => `${key}=${typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}`)
    .join(", ");
}

/**
 * Reason/recovery for the latest failed episode text task: AI planning, prompt authoring, repair or ad/short-film generation.
 * Shown atop the episode page across confirmation, timeline and source views; after dismissal, return only on the next failure.
 *
 * Full ad/short-film generation shares the script-text task with prompt authoring. Without a formal script it can only be full generation;
 * otherwise distinguish through task markers such as full regeneration or contract failure.
 *
 * Summaries show only backend UI-language reasons; codes, params and raw server text belong in expandable Details. If localization fails
 * for external/unknown errors, omit the raw-text field because the original already serves as summary, leaving no details.
 */
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
  const { t } = useTranslation(["dashboard", "common"]);
  const failure = useTasksStore(useShallow((s) => latestEpisodeTextTaskFailure(s.tasks, projectName, episode)));
  const [dismissedTaskId, setDismissedTaskId] = useState<string | null>(null);
  if (failure === null || failure.task.task_id === dismissedTaskId) return null;
  const truncation = outputTruncationOf(failure.task);
  const reason = failure.task.error_message ?? t("script_plan_failed_unknown");
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
        {truncation ? <OutputTruncationHint truncation={truncation} /> : null}
        {failure.task.error_detail ? (
          <Collapsible render={<div className="flex w-full min-w-0 flex-col gap-1" />}>
            <CollapsibleTrigger render={<Button variant="ghost" size="xs" />}>
              {t("text_task_failure_details")}
              <ChevronRight
                data-icon="inline-end"
                aria-hidden
                className="transition-transform group-aria-expanded/button:rotate-90"
              />
            </CollapsibleTrigger>
            <CollapsibleContent>
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-subtle-foreground">
                <dt className="text-muted-foreground">{t("text_task_failure_code")}</dt>
                <dd className="m-0 min-w-0 font-mono break-all">{failure.task.error_code}</dd>
                <dt className="text-muted-foreground">{t("text_task_failure_params")}</dt>
                <dd className="m-0 min-w-0 font-mono break-all">{paramsText(failure.task.error_params)}</dd>
                <dt className="text-muted-foreground">{t("text_task_failure_server_text")}</dt>
                <dd className="m-0 min-w-0 font-mono break-all whitespace-pre-wrap">{failure.task.error_detail}</dd>
              </dl>
            </CollapsibleContent>
          </Collapsible>
        ) : null}
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
