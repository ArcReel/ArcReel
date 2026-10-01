import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeDeletionImpact } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

interface PendingDeletion {
  episode: number;
  name: string;
  impact: EpisodeDeletionImpact & { text: string };
  /** 确认时服务端的清单已变，这是更新后的清单。 */
  changed: boolean;
}

/**
 * 删除一集：先向服务端取丢失清单，确认框只呈现服务端成文的清单。没有产物、原文也能重建时用普通确认，
 * 否则按危险操作确认。确认时清单已变，换成新清单再确认一次，不删除。
 */
export function useDeleteEpisode(projectName: string, onDeleted?: (episode: number) => void) {
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

  const confirm = async () => {
    if (pending === null) return;
    setBusy(true);
    try {
      const response = await API.deleteEpisode(projectName, pending.episode, pending.impact.revision);
      if (response.status === "confirmation_required") {
        setPending({ ...pending, impact: response.impact, changed: true });
        return;
      }
      setPending(null);
      onDeleted?.(pending.episode);
      await useProjectsStore.getState().refreshProject(projectName);
      useAppStore.getState().pushToast(t("dashboard:episode_delete_done", { name: pending.name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(t("dashboard:episode_delete_failed", { message: errMsg(err) }), "error");
    } finally {
      setBusy(false);
    }
  };

  const dialog: ReactNode =
    pending === null ? null : (
      <ConfirmDialog
        open
        tone={pending.impact.recoverable ? "default" : "danger"}
        title={t("dashboard:episode_delete_title", { name: pending.name })}
        description={
          <>
            {pending.changed ? (
              <span className="mb-2 block text-[var(--color-warm)]">{t("dashboard:episode_delete_changed")}</span>
            ) : null}
            <span className="block whitespace-pre-line">{pending.impact.text}</span>
          </>
        }
        confirmLabel={t("dashboard:episode_delete_confirm")}
        loadingLabel={t("dashboard:episode_delete_running")}
        loading={busy}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      />
    );

  return { requestDelete, dialog };
}
