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
  /** The server's list changed during confirmation; this is the updated list. */
  changed: boolean;
}

/**
 * Delete an episode: fetch the loss list first and display only server-written content. Use ordinary confirmation if there are no artifacts and source can be reconstructed,
 * otherwise dangerous-action confirmation. If the list changes at confirmation, show the new list and confirm again without deleting. Deletion removes the episode from the list without another notice.
 * `guard` wraps confirmed deletion, for example a navigation guard; place it after confirmation so cancellation retains unsaved edits.
 * The wrapped action reports deletion success; request failures or renewed confirmation keep edits protected by the navigation guard.
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
