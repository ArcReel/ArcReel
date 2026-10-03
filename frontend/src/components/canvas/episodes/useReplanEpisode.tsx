import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS, enqueueEpisodeReplan } from "@/actions/generation";
import { ConfirmDialog } from "@/components/legacy/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy } from "@/stores/tasks-store";
import type { ReplanPreview } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

interface PendingReplan {
  episode: number;
  preview: ReplanPreview;
  instruction: string;
}

/**
 * 「从这一集开始重新规划」：先向服务端取重新规划的范围，确认框写明采纳前现有分集不变、会被替换的集里
 * 哪些已开始制作，可附加要求；确认后发起生成，再以发起的集 ID 调用 `onStarted`（须传稳定引用）。
 */
export function useReplanEpisode(projectName: string, onStarted: (episode: number) => void) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [pending, setPending] = useState<PendingReplan | null>(null);
  const [busy, setBusy] = useState(false);

  const requestReplan = useCallback(
    async (episode: number) => {
      try {
        const preview = await API.previewEpisodeReplan(projectName, episode);
        setPending({ episode, preview, instruction: "" });
      } catch (err) {
        useAppStore.getState().pushToast(t("dashboard:replan_failed", { message: errMsg(err) }), "error");
      }
    },
    [projectName, t],
  );

  const confirm = async () => {
    if (pending === null) return;
    if (EPISODE_PLANNING_SLOTS.some((slot) => isResourceBusy("text_episode_plan", projectName, slot))) {
      useAppStore.getState().pushToast(t("dashboard:episode_planning_busy"), "error");
      return;
    }
    setBusy(true);
    try {
      await enqueueEpisodeReplan(projectName, pending.preview.episode, pending.instruction.trim() || null);
      setPending(null);
      onStarted(pending.preview.episode);
    } catch (err) {
      useAppStore.getState().pushToast(t("dashboard:replan_failed", { message: errMsg(err) }), "error");
    } finally {
      setBusy(false);
    }
  };

  let dialog: ReactNode = null;
  if (pending !== null) {
    const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
    const name = (episode: number) => episodeDisplayName(episodes, episode, t);
    const { preview } = pending;
    dialog = (
      <ConfirmDialog
        open
        title={t("dashboard:replan_start_title", { name: name(preview.episode) })}
        description={
          <>
            {preview.from_beginning ? (
              <span className="mb-2 block text-[var(--color-warm)]">{t("dashboard:replan_start_from_beginning")}</span>
            ) : null}
            <span className="block">{t("dashboard:replan_start_detail", { count: preview.replaced.length })}</span>
            {preview.started.length > 0 ? (
              <span className="mt-2 block">
                {t("dashboard:replan_start_started", {
                  names: preview.started.map(name).join(t("dashboard:replan_name_separator")),
                })}
              </span>
            ) : null}
            <label className="mt-3 block">
              <span className="mb-0.5 block text-[11px] text-text-3">{t("dashboard:guide_instruction_label")}</span>
              <input
                value={pending.instruction}
                onChange={(e) => setPending({ ...pending, instruction: e.target.value })}
                placeholder={t("dashboard:guide_instruction_placeholder")}
                className="focus-ring w-full rounded-md px-2 py-1 text-[12px]"
                style={{
                  background: "var(--color-surface-2)",
                  border: "1px solid var(--color-hairline)",
                  color: "var(--color-text)",
                }}
              />
            </label>
          </>
        }
        confirmLabel={t("dashboard:replan_start_confirm")}
        loading={busy}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      />
    );
  }

  return { requestReplan, dialog };
}
