import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import type { RenderKind, RenderOptions, RenderSubmission } from "@/types";

export async function submitRender(
  projectName: string,
  timelineId: string,
  kind: RenderKind,
  options: RenderOptions,
): Promise<RenderSubmission> {
  const submission =
    kind === "final_cut"
      ? await API.renderFinalCut(projectName, timelineId, options)
      : await API.exportJianyingDraft(projectName, timelineId, { narration: options.narration });
  if (submission.deduped) {
    useAppStore.getState().pushToast(i18n.t("dashboard:enqueue_deduped_toast"), "info");
  }
  return submission;
}
