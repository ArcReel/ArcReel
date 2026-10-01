import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { episodeMoveCheck } from "@/utils/episode-order";

/**
 * 调整播出顺序：把一集移到 `after` 之后（null 为最前）。切出集之间违背源文顺序的移动不提交，提示原因；
 * 服务端按落位后的原文范围复核，拒绝时同样提示。
 */
export function useMoveEpisode(projectName: string | null) {
  const { t } = useTranslation("dashboard");
  return useCallback(
    async (episode: number, after: number | null) => {
      const project = useProjectsStore.getState().currentProjectData;
      if (!projectName || !project) return;
      const check = episodeMoveCheck(project.episodes, project.whole_source_files ?? [], episode, after);
      if (check === "noop") return;
      if (check === "locked") {
        useAppStore.getState().pushToast(t("episode_move_cut_locked"), "error");
        return;
      }
      try {
        await API.moveEpisode(projectName, episode, after);
        await useProjectsStore.getState().refreshProject(projectName);
      } catch (err) {
        useAppStore.getState().pushToast(t("episode_move_failed", { message: errMsg(err) }), "error");
      }
    },
    [projectName, t],
  );
}
