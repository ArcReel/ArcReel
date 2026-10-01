import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";

import { EPISODE_PLANNING_SLOTS, enqueueEpisodePlanning } from "@/actions/generation";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";

/**
 * 「规划这段未切分的原文」：以这段原文的结尾为终点直接提交 AI 分集规划，不替换任何集，新集按源文位置插入。
 * 分集规划进行中时不可用。
 */
export function PlanGapButton({ sourceFile, end }: { sourceFile: string; end: number }) {
  const { t } = useTranslation("dashboard");
  const projectName = useProjectsStore((s) => s.currentProjectName);
  const activePlanning = useActiveResourceIds("text_episode_plan", projectName ?? "");
  const planning = EPISODE_PLANNING_SLOTS.some((slot) => activePlanning.has(slot));
  const [submitting, setSubmitting] = useState(false);
  if (!projectName) return null;

  const submit = async () => {
    if (EPISODE_PLANNING_SLOTS.some((slot) => isResourceBusy("text_episode_plan", projectName, slot))) {
      useAppStore.getState().pushToast(t("episode_planning_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueEpisodePlanning(projectName, null, { source_file: sourceFile, end });
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <button
      type="button"
      className={GHOST_BTN_CLS}
      disabled={planning || submitting}
      title={planning ? t("episode_planning_busy") : undefined}
      onClick={() => void submit()}
    >
      <Sparkles className="h-3.5 w-3.5" aria-hidden />
      {t("episode_plan_gap")}
    </button>
  );
}
