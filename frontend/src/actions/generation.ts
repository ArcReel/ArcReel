/**
 * 入队动作层：所有生成类入队操作的唯一入口。
 *
 * 每个动作内部固定封装三件事：
 * 1. 调用对应 API 入队端点；
 * 2. 成功后在 tasks-store 打乐观占用标记（入队成功到 SSE 轮询把真实任务行
 *    写进 store 之间有 ~3s 空窗，期间同资源的其它入口会误判为空闲）；
 * 3. 弹提示：后端 deduped=true（同资源任务已在处理中，本次未新建）时统一
 *    弹 info 提示，否则沿用各操作原有的成功文案。
 *
 * 失败一律向上抛，由调用方决定错误提示与回滚副作用。返回值统一归一化为
 * EnqueueResult，屏蔽各端点 task_id / task_ids 的形状差异。
 *
 * 组件禁止绕过本层直调入队类 API 方法（ESLint no-restricted-syntax 强制）。
 */
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";

export interface EnqueueResult {
  taskIds: string[];
  deduped: boolean;
}

/**
 * deduped 时弹统一 info 提示；否则弹该操作自己的成功文案
 * （successText 为 null 表示该操作成功时本就静默，维持静默）。
 */
function notifyEnqueued(
  deduped: boolean,
  successText: string | null,
  successTone: "success" | "info" = "success",
): void {
  const { pushToast } = useAppStore.getState();
  if (deduped) {
    pushToast(i18n.t("dashboard:enqueue_deduped_toast"), "info");
  } else if (successText !== null) {
    pushToast(successText, successTone);
  }
}

export async function enqueueStoryboard(
  projectName: string,
  segmentId: string,
  prompt: string | Record<string, unknown>,
  scriptFile: string,
): Promise<EnqueueResult> {
  const res = await API.generateStoryboard(projectName, segmentId, prompt, scriptFile);
  useTasksStore.getState().markOptimisticActive(projectName, "storyboard", segmentId, "storyboard");
  notifyEnqueued(res.deduped, i18n.t("dashboard:storyboard_task_submitted_toast", { id: segmentId }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueVideo(
  projectName: string,
  segmentId: string,
  prompt: string | Record<string, unknown>,
  scriptFile: string,
  durationSeconds?: number,
): Promise<EnqueueResult> {
  const res = await API.generateVideo(projectName, segmentId, prompt, scriptFile, durationSeconds);
  useTasksStore.getState().markOptimisticActive(projectName, "video", segmentId, "video");
  notifyEnqueued(res.deduped, i18n.t("dashboard:video_task_submitted_toast", { id: segmentId }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueNarration(
  projectName: string,
  segmentId: string,
  scriptFile: string,
): Promise<EnqueueResult> {
  const res = await API.generateNarrationAudio(projectName, segmentId, scriptFile);
  useTasksStore.getState().markOptimisticActive(projectName, "tts", segmentId, "tts");
  notifyEnqueued(res.deduped, i18n.t("dashboard:narration_task_submitted_toast", { id: segmentId }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueEpisodeNarration(
  projectName: string,
  scriptFile: string,
): Promise<EnqueueResult> {
  const res = await API.generateEpisodeNarrationAudio(projectName, scriptFile);
  // 批量响应不含各段 segment_id，无法逐段打乐观标记；批量入口本身没有
  // 逐段占用消费方，空窗期由 SSE 轮询写回真实任务行兜住。
  notifyEnqueued(
    res.deduped,
    res.task_ids.length > 0
      ? i18n.t("dashboard:narration_batch_submitted_toast", { count: res.task_ids.length })
      : i18n.t("dashboard:narration_batch_none_missing_toast"),
  );
  return { taskIds: res.task_ids, deduped: res.deduped };
}

export async function enqueueCharacter(
  projectName: string,
  name: string,
  prompt: string,
): Promise<EnqueueResult> {
  const res = await API.generateCharacter(projectName, name, prompt);
  useTasksStore.getState().markOptimisticActive(projectName, "character", name, "character");
  notifyEnqueued(res.deduped, i18n.t("dashboard:character_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueScene(
  projectName: string,
  name: string,
  prompt: string,
): Promise<EnqueueResult> {
  const res = await API.generateProjectScene(projectName, name, prompt);
  useTasksStore.getState().markOptimisticActive(projectName, "scene", name, "scene");
  notifyEnqueued(res.deduped, i18n.t("dashboard:scene_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueProp(
  projectName: string,
  name: string,
  prompt: string,
): Promise<EnqueueResult> {
  const res = await API.generateProjectProp(projectName, name, prompt);
  useTasksStore.getState().markOptimisticActive(projectName, "prop", name, "prop");
  notifyEnqueued(res.deduped, i18n.t("dashboard:prop_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueProduct(
  projectName: string,
  name: string,
  prompt: string,
): Promise<EnqueueResult> {
  const res = await API.generateProjectProduct(projectName, name, prompt);
  useTasksStore.getState().markOptimisticActive(projectName, "product", name, "product");
  notifyEnqueued(res.deduped, i18n.t("dashboard:product_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueImageEdit(
  projectName: string,
  params: {
    resourceType: "character" | "scene" | "prop" | "product" | "storyboard";
    resourceId: string;
    instruction: string;
    scriptFile?: string | null;
  },
): Promise<EnqueueResult> {
  const res = await API.editImage(projectName, params);
  // image_edit 与生成任务共享同一资源槽位：kind 按被编辑资源类型归槽，
  // pendingTaskType 固定 image_edit，与 taskResourceKind 的归一化保持一致。
  useTasksStore.getState().markOptimisticActive(projectName, params.resourceType, params.resourceId, "image_edit");
  notifyEnqueued(res.deduped, res.message);
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueGrid(
  projectName: string,
  episode: number,
  scriptFile: string,
  sceneIds?: string[],
): Promise<EnqueueResult> {
  const res = await API.generateGrid(projectName, episode, scriptFile, sceneIds);
  // task_ids 可能为空数组（如 scene_ids 过滤后无匹配分组）：此时后端不会产生
  // 任何任务行，乐观标记将永远等不到真实任务落库来解除，需在打标前排除。
  if (res.task_ids.length > 0) {
    useTasksStore.getState().markOptimisticActiveForScriptFile(projectName, "grid", scriptFile);
  }
  notifyEnqueued(res.deduped, res.message);
  return { taskIds: res.task_ids, deduped: res.deduped };
}

export async function enqueueGridRegenerate(
  projectName: string,
  gridId: string,
  scriptFile: string | null,
): Promise<EnqueueResult> {
  const res = await API.regenerateGrid(projectName, gridId);
  if (scriptFile) {
    useTasksStore.getState().markOptimisticActiveForScriptFile(projectName, "grid", scriptFile);
  }
  // 重生成入口成功时静默（面板内已有状态反馈），仅 deduped 时弹提示。
  notifyEnqueued(res.deduped, null);
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueReferenceVideoUnit(
  projectName: string,
  episode: number,
  unitId: string,
): Promise<EnqueueResult> {
  const res = await API.generateReferenceVideoUnit(projectName, episode, unitId);
  useTasksStore.getState().markOptimisticActive(projectName, "reference_video", unitId, "reference_video");
  notifyEnqueued(res.deduped, i18n.t("dashboard:reference_generate_queued"), "info");
  return { taskIds: [res.task_id], deduped: res.deduped };
}
