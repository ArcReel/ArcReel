import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, X } from "lucide-react";
import { useShallow } from "zustand/react/shallow";

import { OutputTruncationHint } from "@/components/shared/OutputTruncationHint";
import { useTasksStore } from "@/stores/tasks-store";
import { outputTruncationOf } from "@/utils/output-truncation";

import { latestEpisodeTextTaskFailure, type EpisodeTextTaskKind } from "./text-task-failure";

const FAILURE_KEYS: Record<EpisodeTextTaskKind, string> = {
  text_script_plan: "script_plan_failed",
  text_episode_script: "prompt_authoring_failed",
  text_draft_repair: "draft_repair_failed",
};

/**
 * 本集最近一次文本任务（AI 规划脚本、编写提示词、AI 修复）失败时的原因与出路，挂在集页顶部，
 * 内容确认页、时间线与集原文页都看得到。关掉后，直到下一次失败才再出现。
 */
export function TextTaskFailureNote({ projectName, episode }: { projectName: string; episode: number }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const failure = useTasksStore(useShallow((s) => latestEpisodeTextTaskFailure(s.tasks, projectName, episode)));
  const [dismissedTaskId, setDismissedTaskId] = useState<string | null>(null);
  if (failure === null || failure.task.task_id === dismissedTaskId) return null;
  const truncation = outputTruncationOf(failure.task);
  const reason = failure.task.error_message ?? t("script_plan_failed_unknown");
  return (
    <div
      role="alert"
      className="mx-4 mt-2 flex items-start gap-2.5 rounded-xl border border-red-500/35 px-4 py-2.5 text-[12.5px]"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-300" aria-hidden />
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className="m-0 text-red-300">{t(FAILURE_KEYS[failure.kind], { reason })}</p>
        {truncation ? <OutputTruncationHint truncation={truncation} /> : null}
      </div>
      <button
        type="button"
        aria-label={t("common:close")}
        onClick={() => setDismissedTaskId(failure.task.task_id)}
        className="focus-ring shrink-0 rounded p-0.5 text-text-4 hover:text-text-2"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}
