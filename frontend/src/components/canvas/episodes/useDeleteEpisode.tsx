import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeDeletionImpact } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

import { ImpactConfirmDialog, ImpactText } from "./ImpactConfirmDialog";

interface PendingDeletion {
  episode: number;
  name: string;
  impact: EpisodeDeletionImpact & { text: string };
  /** 确认时服务端的清单已变，这是更新后的清单。 */
  changed: boolean;
}

/**
 * 删除一集：先向服务端取丢失清单，确认框只呈现服务端成文的清单。没有产物、原文也能重建时用普通确认，
 * 否则按危险操作确认。确认时清单已变，换成新清单再确认一次，不删除。删除后这一集从列表里消失，不另行提示。
 * `guard` 包住确认删除这一步（如离开拦截）：放在确认之后，取消删除时未保存修改原样保留。
 * 被包住的动作返回是否删除成功，请求失败或需要再次确认时离开拦截保留修改。
 */
export function useDeleteEpisode(
  projectName: string,
  onDeleted?: (episode: number) => void,
  guard?: (proceed: () => Promise<boolean>) => void,
) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [pending, setPending] = useState<PendingDeletion | null>(null);
  const [busy, setBusy] = useState(false);

  const requestDelete = useCallback(
    async (episode: number) => {
      const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
      const name = episodeDisplayName(episodes, episode, t);
      try {
        const response = await API.deleteEpisode(projectName, episode);
        if (response.status === "confirmation_required") {
          setPending({ episode, name, impact: response.impact, changed: false });
        }
      } catch (err) {
        useAppStore.getState().pushToast(t("dashboard:episode_delete_failed", { message: errMsg(err) }), "error");
      }
    },
    [projectName, t],
  );

  const confirm = async (): Promise<boolean> => {
    if (pending === null) return false;
    setBusy(true);
    try {
      const response = await API.deleteEpisode(projectName, pending.episode, pending.impact.revision);
      if (response.status === "confirmation_required") {
        setPending({ ...pending, impact: response.impact, changed: true });
        return false;
      }
      setPending(null);
      onDeleted?.(pending.episode);
      await refreshAfterWrite(projectName, t);
      return true;
    } catch (err) {
      useAppStore.getState().pushToast(t("dashboard:episode_delete_failed", { message: errMsg(err) }), "error");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const dialog: ReactNode = (
    <ImpactConfirmDialog
      request={
        pending === null
          ? null
          : {
              title: t("dashboard:episode_delete_title", { name: pending.name }),
              body: (
                <ImpactText
                  text={pending.impact.text}
                  changedNotice={pending.changed ? t("dashboard:episode_delete_changed") : null}
                />
              ),
              confirmLabel: t("dashboard:episode_delete_confirm"),
              runningLabel: t("dashboard:episode_delete_running"),
              destructive: !pending.impact.recoverable,
            }
      }
      busy={busy}
      onConfirm={() => (guard ? guard(confirm) : void confirm())}
      onCancel={() => setPending(null)}
    />
  );

  return { requestDelete, dialog };
}
