import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { errMsg } from "@/utils/async";
import { episodeMoveCheck } from "@/utils/episode-order";

/**
 * Reorder broadcasting by moving an episode after `after` (null means first). Do not submit extracted-episode moves that violate source order; explain why;
 * the server rechecks source ranges after placement and its rejection is also displayed.
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
        await refreshAfterWrite(projectName, t);
      } catch (err) {
        useAppStore.getState().pushToast(t("episode_move_failed", { message: errMsg(err) }), "error");
      }
    },
    [projectName, t],
  );
}
