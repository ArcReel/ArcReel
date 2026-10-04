import { useCallback, useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { EPISODE_PLANNING_SLOTS, enqueueEpisodeReplan } from "@/actions/generation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy } from "@/stores/tasks-store";
import type { ReplanPreview } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { episodeDisplayName } from "@/utils/episode-display";

import { ImpactConfirmDialog, type ImpactConfirmRequest } from "./ImpactConfirmDialog";

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
  const instructionId = useId();

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

  let request: ImpactConfirmRequest | null = null;
  if (pending !== null) {
    const episodes = useProjectsStore.getState().currentProjectData?.episodes ?? [];
    const name = (episode: number) => episodeDisplayName(episodes, episode, t);
    const { preview } = pending;
    request = {
      title: t("dashboard:replan_start_title", { name: name(preview.episode) }),
      body: (
        <div className="flex flex-col gap-2">
          {preview.from_beginning ? <p className="text-warn">{t("dashboard:replan_start_from_beginning")}</p> : null}
          <p>{t("dashboard:replan_start_detail", { count: preview.replaced.length })}</p>
          {preview.started.length > 0 ? (
            <p>
              {t("dashboard:replan_start_started", {
                names: preview.started.map(name).join(t("dashboard:replan_name_separator")),
              })}
            </p>
          ) : null}
          <div className="mt-2 flex flex-col gap-1.5">
            <Label htmlFor={instructionId}>{t("dashboard:guide_instruction_label")}</Label>
            <Input
              id={instructionId}
              value={pending.instruction}
              disabled={busy}
              onChange={(e) => setPending({ ...pending, instruction: e.target.value })}
              placeholder={t("dashboard:guide_instruction_placeholder")}
            />
          </div>
        </div>
      ),
      confirmLabel: t("dashboard:replan_start_confirm"),
      destructive: false,
      interactiveBody: true,
    };
  }
  const dialog: ReactNode = (
    <ImpactConfirmDialog
      request={request}
      busy={busy}
      onConfirm={() => void confirm()}
      onCancel={() => setPending(null)}
    />
  );

  return { requestReplan, dialog };
}
