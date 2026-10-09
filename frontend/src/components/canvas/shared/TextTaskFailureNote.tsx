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
 * 本集最近一次文本任务（AI 规划脚本、编写提示词、AI 修复、广告/短片 AI 生成脚本）失败时的原因与出路，
 * 挂在集页顶部，内容确认页、时间线与集原文页都看得到。关掉后，直到下一次失败才再出现。
 *
 * 广告/短片的整份生成与提示词编写共用脚本文本任务：本集还没有正式脚本时，这类任务只可能是整份生成；
 * 有正式脚本时按任务自身的标记（整份重做、违约失败）区分。
 *
 * 摘要只显示后端按界面语言渲染的原因；问题码、参数与服务端原文收进可展开的「详情」。后端没能把原因
 * 本地化时（外部或未知错误）不带原文字段，原文本身就是摘要，也就没有详情。
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
