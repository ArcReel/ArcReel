/**
 * Enqueue output actions: final rendering and Jianying draft export. Jianying drafts use only the narration version in options.
 *
 * Render-lane tasks occupy no resource slot and do not enter the usage overlay, so do not mark optimistic occupancy here.
 * The output dialog polls progress by returned task_id. With deduped=true, an existing task already handles this artifact;
 * show the shared info notice and let callers track the returned task_id normally. Always propagate failures for caller feedback.
 */
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
