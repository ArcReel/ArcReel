/**
 * 入队动作层：所有生成类入队操作的唯一入口。
 *
 * 每个动作内部固定封装三件事：
 * 1. 在**请求发出前**于 tasks-store 打乐观占用标记，请求失败即回滚，成功则用后端
 *    返回的 task_id 兑现（见 {@link submit}）——占用态因此从点击那一刻起就成立，
 *    调用方无须自备「请求在途」标记来覆盖网络往返窗口；
 * 2. 调用对应 API 入队端点；
 * 3. 弹提示：后端 deduped=true（同资源任务已在处理中，本次未新建）时统一
 *    弹 info 提示，否则沿用各操作原有的成功文案。
 *
 * 失败一律向上抛，由调用方决定错误提示。返回值统一归一化为 EnqueueResult，
 * 屏蔽各端点 task_id / task_ids 的形状差异。
 *
 * 组件禁止绕过本层直调入队类 API 方法（ESLint no-restricted-syntax 强制）。
 */
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import {
  useTasksStore,
  type ImageEditResourceKind,
  type OptimisticHandle,
  type ResourceKind,
} from "@/stores/tasks-store";

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

/** 资源粒度乐观标记的简写（请求发出前调用）。 */
function markResource(
  projectName: string,
  resourceKind: ResourceKind,
  resourceId: string,
  pendingTaskType: string,
): OptimisticHandle {
  return useTasksStore
    .getState()
    .beginOptimisticActive(projectName, resourceKind, resourceId, pendingTaskType);
}

/** scriptFile 粒度乐观标记的简写（请求发出前调用）。 */
function markScriptFile(projectName: string, taskType: string, scriptFile: string): OptimisticHandle {
  return useTasksStore.getState().beginOptimisticActiveForScriptFile(projectName, taskType, scriptFile);
}

/**
 * 入队请求的统一骨架：标记已在 `marks` 里打好，此处只负责发请求并按结果兑现——
 * 失败全部回滚后原样抛出，成功用 `taskIdsOf` 取出的 task_id 兑现（为空即回滚，
 * 后端没建任务行时标记永远等不到真实行）。
 *
 * 响应解析（`taskIdsOf`）与兑现同在 try 内：在途标记不被任何轮询写回清除，因此
 * 兑现前的任何异常路径都必须回滚，否则标记会残留到页面刷新为止。
 */
async function submit<T>(
  marks: readonly OptimisticHandle[],
  request: () => Promise<T>,
  taskIdsOf: (res: T) => string[],
): Promise<T> {
  try {
    const res = await request();
    const taskIds = taskIdsOf(res);
    for (const m of marks) m.settle(taskIds);
    return res;
  } catch (e) {
    for (const m of marks) m.rollback();
    throw e;
  }
}

function oneTaskId(res: { task_id: string }): string[] {
  return [res.task_id];
}

function manyTaskIds(res: { task_ids: string[] }): string[] {
  return res.task_ids;
}

export async function enqueueStoryboard(
  projectName: string,
  segmentId: string,
  prompt: string | Record<string, unknown>,
  scriptFile: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "storyboard", segmentId, "storyboard")],
    () => API.generateStoryboard(projectName, segmentId, prompt, scriptFile),
    oneTaskId,
  );
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
  const res = await submit(
    [markResource(projectName, "video", segmentId, "video")],
    () => API.generateVideo(projectName, segmentId, prompt, scriptFile, durationSeconds),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:video_task_submitted_toast", { id: segmentId }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueNarration(
  projectName: string,
  segmentId: string,
  scriptFile: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "tts", segmentId, "tts")],
    () => API.generateNarrationAudio(projectName, segmentId, scriptFile),
    oneTaskId,
  );
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
  const res = await submit(
    [markResource(projectName, "character", name, "character")],
    () => API.generateCharacter(projectName, name, prompt),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:character_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueCharacterVoiceSample(
  projectName: string,
  name: string,
  text: string,
  voice: string,
): Promise<EnqueueResult> {
  // 音色试听样本与该角色的设计图生成/编辑共用同一资源槽（kind "character"）：
  // 二者互斥可接受——试听样本本就该在角色卡其它生成任务空闲时才发起。
  const res = await submit(
    [markResource(projectName, "character", name, "voice_sample")],
    () => API.generateCharacterVoiceSample(projectName, name, text, voice),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:voice_sample_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueScene(
  projectName: string,
  name: string,
  prompt: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "scene", name, "scene")],
    () => API.generateProjectScene(projectName, name, prompt),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:scene_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueProp(
  projectName: string,
  name: string,
  prompt: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "prop", name, "prop")],
    () => API.generateProjectProp(projectName, name, prompt),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:prop_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueProduct(
  projectName: string,
  name: string,
  prompt: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "product", name, "product")],
    () => API.generateProjectProduct(projectName, name, prompt),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:product_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueImageEdit(
  projectName: string,
  params: {
    resourceType: ImageEditResourceKind;
    resourceId: string;
    instruction: string;
    scriptFile?: string | null;
  },
): Promise<EnqueueResult> {
  // image_edit 与生成任务共享同一资源槽位：kind 按被编辑资源类型归槽，
  // pendingTaskType 固定 image_edit，与 taskResourceKind 的归一化保持一致。
  const res = await submit(
    [markResource(projectName, params.resourceType, params.resourceId, "image_edit")],
    () => API.editImage(projectName, params),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, res.message);
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueGrid(
  projectName: string,
  episode: number,
  scriptFile: string,
  sceneIds?: string[],
): Promise<EnqueueResult> {
  // task_ids 可能为空数组（如 scene_ids 过滤后无匹配分组）：此时后端不产生任何任务行，
  // settle([]) 会把标记回滚掉，不留下永远等不到真实行的残留。
  const res = await submit(
    [markScriptFile(projectName, "grid", scriptFile)],
    () => API.generateGrid(projectName, episode, scriptFile, sceneIds),
    manyTaskIds,
  );
  notifyEnqueued(res.deduped, res.message);
  return { taskIds: res.task_ids, deduped: res.deduped };
}

export async function enqueueGridRegenerate(
  projectName: string,
  gridId: string,
  scriptFile: string | null,
): Promise<EnqueueResult> {
  const res = await submit(
    [
      markResource(projectName, "grid", gridId, "grid"),
      ...(scriptFile ? [markScriptFile(projectName, "grid", scriptFile)] : []),
    ],
    () => API.regenerateGrid(projectName, gridId),
    oneTaskId,
  );
  // 重生成入口成功时静默（面板内已有状态反馈），仅 deduped 时弹提示。
  notifyEnqueued(res.deduped, null);
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueReferenceVideoUnit(
  projectName: string,
  episode: number,
  unitId: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "reference_video", unitId, "reference_video")],
    () => API.generateReferenceVideoUnit(projectName, episode, unitId),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:reference_generate_queued"), "info");
  return { taskIds: [res.task_id], deduped: res.deduped };
}
