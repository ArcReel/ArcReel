import { API, derivativeResourceId } from "@/api";
import { generationOptions, generationArgs } from "@/stores/run-settings-store";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useAssetSheetBatchStore } from "@/stores/asset-sheet-batch-store";
import { useProjectsStore } from "@/stores/projects-store";
import type {
  AssetSheetBatchScope,
  AssetSheetBatchSubmitted,
  AuthorPromptsRequest,
  PlanningGap,
  PlanScriptRequest,
  GenerateAdScriptRequest,
  ReferenceBatchAdmission,
  ReferenceBatchGenerateRequest,
  ReferenceGenerationRequestOptions,
  StoryboardBatchKind,
  StoryboardBatchSubmitted,
} from "@/types";
import {
  useTasksStore,
  type ImageEditResourceKind,
  type OptimisticHandle,
  type ResourceKind,
} from "@/stores/tasks-store";
import { episodeItemLabel } from "@/utils/episode-display";

function itemLabel(itemId: string): string {
  const { currentProjectData, currentScripts } = useProjectsStore.getState();
  return episodeItemLabel(itemId, currentScripts, currentProjectData?.episodes ?? [], i18n.t);
}

export interface EnqueueResult {
  taskIds: string[];
  deduped: boolean;
}

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

function markScriptFile(projectName: string, taskType: string, scriptFile: string): OptimisticHandle {
  return useTasksStore.getState().beginOptimisticActiveForScriptFile(projectName, taskType, scriptFile);
}

async function submit<T>(
  marks: readonly OptimisticHandle[],
  request: () => Promise<T>,
  taskIdsOf: (res: T, markIndex: number) => string[],
): Promise<T> {
  try {
    const res = await request();
    marks.forEach((m, index) => m.settle(taskIdsOf(res, index)));
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

function markResourcesByTaskId(
  projectName: string,
  resourceKind: ResourceKind,
  pendingTaskType: string,
  taskIdsByResource: Record<string, string>,
): void {
  for (const [resourceId, taskId] of Object.entries(taskIdsByResource)) {
    markResource(projectName, resourceKind, resourceId, pendingTaskType).settle([taskId]);
  }
}

export async function enqueueStoryboard(
  projectName: string,
  segmentId: string,
  prompt: string | Record<string, unknown>,
  scriptFile: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "storyboard", segmentId, "storyboard")],
    () => API.generateStoryboard(projectName, segmentId, prompt, scriptFile, ...generationArgs(projectName, "image")),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:storyboard_task_submitted_toast", { id: itemLabel(segmentId) }));
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
    () => API.generateVideo(projectName, segmentId, prompt, scriptFile, durationSeconds, ...generationArgs(projectName, "video")),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:video_task_submitted_toast", { id: itemLabel(segmentId) }));
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
  notifyEnqueued(res.deduped, i18n.t("dashboard:narration_task_submitted_toast", { id: itemLabel(segmentId) }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueEpisodeNarration(
  projectName: string,
  scriptFile: string,
): Promise<EnqueueResult> {
  const res = await API.generateEpisodeNarrationAudio(projectName, scriptFile);
  markResourcesByTaskId(projectName, "tts", "tts", res.task_ids_by_segment);
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
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "character", name, "character")],
    () => API.generateCharacter(projectName, name, ...generationArgs(projectName, "image")),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:character_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueCharacterDerivative(
  projectName: string,
  characterName: string,
  derivativeName: string,
): Promise<EnqueueResult> {
  const resourceId = derivativeResourceId(characterName, derivativeName);
  const res = await submit(
    [markResource(projectName, "character_derivative", resourceId, "character_derivative")],
    () => API.generateCharacterDerivative(projectName, characterName, derivativeName, ...generationArgs(projectName, "image")),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, res.message);
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueCharacterVoiceSample(
  projectName: string,
  name: string,
  text: string,
  voice: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "voice_sample", name, "voice_sample")],
    () => API.generateCharacterVoiceSample(projectName, name, text, voice),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:voice_sample_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueScene(
  projectName: string,
  name: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "scene", name, "scene")],
    () => API.generateProjectScene(projectName, name, ...generationArgs(projectName, "image")),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:scene_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueProp(
  projectName: string,
  name: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "prop", name, "prop")],
    () => API.generateProjectProp(projectName, name, ...generationArgs(projectName, "image")),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:prop_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueProduct(
  projectName: string,
  name: string,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "product", name, "product")],
    () => API.generateProjectProduct(projectName, name, ...generationArgs(projectName, "image")),
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
  const res = await submit(
    [markScriptFile(projectName, "grid", scriptFile)],
    () => API.generateGrid(projectName, episode, scriptFile, sceneIds),
    manyTaskIds,
  );
  markResourcesByTaskId(projectName, "grid", "grid", res.task_ids_by_grid);
  if (res.blocked?.length) useAppStore.getState().pushToast(res.message, "error");
  else notifyEnqueued(res.deduped, res.message);
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
  notifyEnqueued(res.deduped, null);
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueReferenceVideoUnit(
  projectName: string,
  episode: number,
  unitId: string,
  options: ReferenceGenerationRequestOptions = {},
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "reference_video", unitId, "reference_video")],
    () => API.generateReferenceVideoUnit(projectName, episode, unitId, generationOptions(projectName, "video") ? { ...options, generation_options: generationOptions(projectName, "video") } : options),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:reference_generate_queued"), "info");
  return { taskIds: [res.task_id], deduped: res.deduped };
}

export async function enqueueReferenceVideoBatch(
  projectName: string,
  episode: number,
  payload: ReferenceBatchGenerateRequest,
): Promise<ReferenceBatchAdmission> {
  const unitIds = payload.unit_ids ?? [];
  const marks = unitIds.map((unitId) =>
    markResource(projectName, "reference_video", unitId, "reference_video"),
  );
  const res = await submit(
    marks,
    () => API.generateReferenceVideoBatch(projectName, episode, generationOptions(projectName, "video") ? { ...payload, generation_options: generationOptions(projectName, "video") } : payload),
    (admission, index) => {
      if (admission.decision !== "admitted") return [];
      const taskId = admission.task_ids_by_unit[unitIds[index]];
      return taskId === undefined ? [] : [taskId];
    },
  );
  if (res.decision === "admitted") {
    notifyEnqueued(
      res.deduped,
      res.task_ids.length > 0
        ? i18n.t("dashboard:reference_batch_queued", { count: res.task_ids.length })
        : null,
      "info",
    );
    const refused = res.units.flatMap((unit) => unit.problems
      .filter((problem) => problem.code === "video_reference_images_exceeded")
      .map((problem) => `${unit.unit_id} · ${problem.message ?? problem.detail}`));
    if (refused.length) useAppStore.getState().pushToast(refused.join("\n"), "error");
    if (res.enqueue_failures.length > 0) {
      useAppStore
        .getState()
        .pushToast(
          i18n.t("dashboard:reference_batch_enqueue_interrupted", {
            count: res.enqueue_failures.length,
          }),
          "error",
        );
    }
  }
  return res;
}

export async function enqueueStoryboardBatch(
  projectName: string,
  episode: number,
  kind: StoryboardBatchKind,
): Promise<StoryboardBatchSubmitted> {
  const res = await API.submitStoryboardBatch(projectName, episode, kind);
  if (res.admission && res.admission.decision !== "admitted") return res;
  const taskType = kind === "storyboards" ? "storyboard" : "video";
  markResourcesByTaskId(projectName, taskType, taskType, res.task_ids_by_unit);
  const queued = Object.keys(res.task_ids_by_unit).length;
  notifyEnqueued(
    false,
    queued > 0 ? i18n.t(`dashboard:storyboard_batch_submitted.${kind}`, { count: queued }) : null,
  );
  if (res.enqueue_failures.length > 0) {
    useAppStore
      .getState()
      .pushToast(
        i18n.t("dashboard:storyboard_batch_enqueue_failed", { count: res.enqueue_failures.length }),
        "warning",
      );
  }
  return res;
}

export async function enqueueAssetSheetBatch(
  projectName: string,
  scope: AssetSheetBatchScope,
): Promise<AssetSheetBatchSubmitted> {
  const res = await API.submitAssetSheetBatch(projectName, generationOptions(projectName, "image") ? { ...scope, generation_options: generationOptions(projectName, "image") } : scope);
  const members = res.members.filter((member) => member.status !== "blocked");
  if (members.length > 0) {
    useAssetSheetBatchStore.getState().track({ batchId: res.batch_id, projectName, members });
  }
  let queued = 0;
  for (const member of res.members) {
    if (!member.task_id) continue;
    queued += 1;
    const [kind, id]: [ResourceKind, string] = member.derivative
      ? ["character_derivative", derivativeResourceId(member.name, member.derivative)]
      : [member.asset_type, member.name];
    markResource(projectName, kind, id, kind).settle([member.task_id]);
  }
  notifyEnqueued(
    res.members.some((member) => member.deduped),
    queued > 0 ? i18n.t("assets:sheet_batch_submitted", { count: queued }) : null,
  );
  return res;
}

function memberTaskIds(batch: { members: ReadonlyArray<{ task_id?: string | null }> }): string[] {
  return batch.members.flatMap((member) => (member.task_id ? [member.task_id] : []));
}

export function promptAuthoringResourceId(episode: number): string {
  return `episode-${episode}`;
}

export async function enqueuePromptAuthoring(
  projectName: string,
  episode: number,
  request: AuthorPromptsRequest,
): Promise<EnqueueResult> {
  const scope = useTasksStore.getState().refreshScope;
  const res = await submit(
    [markResource(projectName, "text_episode_script", promptAuthoringResourceId(episode), "text_episode_script")],
    () => API.authorPrompts(projectName, episode, request),
    (response) => memberTaskIds(response.batch),
  );
  const taskIds = memberTaskIds(res.batch);
  const deduped = res.batch.members.some((member) => member.deduped === true);
  notifyEnqueued(
    deduped,
    i18n.t(request.rewrite ? "dashboard:prompt_authoring_rewrite_queued" : "dashboard:prompt_authoring_queued"),
    "info",
  );
  if (!deduped && scope?.projectName === projectName && useTasksStore.getState().refreshScope === scope) {
    watchPromptAuthoringNoop(projectName, taskIds);
  }
  return { taskIds, deduped };
}

function watchPromptAuthoringNoop(projectName: string, taskIds: readonly string[]): void {
  if (taskIds.length === 0) return;
  const store = useTasksStore.getState();
  const pending = new Map([...new Set(taskIds)].map((id) => [id, store.retainTask(projectName, id)]));
  const check = (state: ReturnType<typeof useTasksStore.getState>): void => {
    if (state.refreshScope !== store.refreshScope) {
      unsubscribe();
      for (const release of pending.values()) release();
      return;
    }
    const finished: Array<() => void> = [];
    for (const [id, release] of pending) {
      const status = state.tracked.get(id)?.status;
      if (status !== "resolved" && status !== "missing") continue;
      pending.delete(id);
      finished.push(release);
      const task = state.rowsById.get(id);
      if (status === "resolved" && task?.status === "succeeded" && task.result?.no_pending_authoring === true) {
        useAppStore.getState().pushToast(i18n.t("dashboard:prompt_authoring_nothing_pending"), "info");
      }
    }
    if (pending.size === 0) unsubscribe();
    for (const release of finished) release();
  };
  const unsubscribe = useTasksStore.subscribe(check);
  check(useTasksStore.getState());
}

export function scriptPlanResourceId(episode: number): string {
  return `episode-${episode}`;
}

export async function enqueueScriptPlan(
  projectName: string,
  episode: number,
  request: PlanScriptRequest,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "text_script_plan", scriptPlanResourceId(episode), "text_script_plan")],
    () => API.planScript(projectName, episode, request),
    (response) => memberTaskIds(response.batch),
  );
  const taskIds = memberTaskIds(res.batch);
  const deduped = res.batch.members.some((member) => member.deduped === true);
  notifyEnqueued(deduped, null);
  return { taskIds, deduped };
}

export async function enqueueAdScript(
  projectName: string,
  episode: number,
  request: GenerateAdScriptRequest,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "text_episode_script", promptAuthoringResourceId(episode), "text_episode_script")],
    () => API.generateAdScript(projectName, episode, request),
    (response) => memberTaskIds(response.batch),
  );
  const taskIds = memberTaskIds(res.batch);
  const deduped = res.batch.members.some((member) => member.deduped === true);
  notifyEnqueued(deduped, i18n.t("dashboard:ad_script_queued"), "info");
  return { taskIds, deduped };
}

export const EPISODE_PLANNING_SLOTS = ["episode-planning", "episode-planning-next"] as const;

export async function enqueueEpisodePlanning(
  projectName: string,
  instructions: string | null,
  gap: PlanningGap | null = null,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "text_episode_plan", EPISODE_PLANNING_SLOTS[0], "text_episode_plan")],
    () => API.planEpisodes(projectName, instructions, gap),
    (response) => memberTaskIds(response.batch),
  );
  const taskIds = memberTaskIds(res.batch);
  const deduped = res.batch.members.some((member) => member.deduped === true);
  notifyEnqueued(deduped, null);
  return { taskIds, deduped };
}

export async function enqueueEpisodeReplan(
  projectName: string,
  episode: number,
  instructions: string | null,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "text_episode_plan", EPISODE_PLANNING_SLOTS[0], "text_episode_plan")],
    () => API.startEpisodeReplan(projectName, episode, instructions),
    (response) => memberTaskIds(response.batch),
  );
  const taskIds = memberTaskIds(res.batch);
  const deduped = res.batch.members.some((member) => member.deduped === true);
  notifyEnqueued(deduped, null);
  return { taskIds, deduped };
}

export async function enqueueEpisodeReplanContinue(projectName: string, candidateId: string): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "text_episode_plan", EPISODE_PLANNING_SLOTS[0], "text_episode_plan")],
    () => API.continueEpisodeReplan(projectName, candidateId),
    (response) => memberTaskIds(response.batch),
  );
  const taskIds = memberTaskIds(res.batch);
  const deduped = res.batch.members.some((member) => member.deduped === true);
  notifyEnqueued(deduped, null);
  return { taskIds, deduped };
}
