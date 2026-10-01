/**
 * 出片入队动作：成片渲染与剪映草稿导出。
 *
 * render 车道任务不占用任何资源槽，也不进用量悬浮层，因此这里不打乐观占用标记；
 * 进度由出片对话框按返回的 task_id 轮询。deduped=true（同一产物已有任务在处理）时
 * 弹统一 info 提示，调用方照常跟踪返回的 task_id。失败一律向上抛，由调用方提示。
 */
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import type { RenderKind, RenderSubmission } from "@/types";

export async function submitRender(
  projectName: string,
  timelineId: string,
  kind: RenderKind,
): Promise<RenderSubmission> {
  const submission =
    kind === "final_cut"
      ? await API.renderFinalCut(projectName, timelineId)
      : await API.exportJianyingDraft(projectName, timelineId);
  if (submission.deduped) {
    useAppStore.getState().pushToast(i18n.t("dashboard:enqueue_deduped_toast"), "info");
  }
  return submission;
}
