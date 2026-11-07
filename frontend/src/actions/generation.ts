/**
 * Enqueue action layer: sole entry for every generation enqueue operation.
 *
 * Each action encapsulates three fixed responsibilities:
 * 1. Mark optimistic occupancy in tasks-store **before requests**. Roll back failures, settle successes using backend
 *    returned task_id (see {@link submit}), making occupancy effective immediately on clicks;
 *    callers need no separate in-flight markers for network round trips. Batch entries with server-decided targets
 *    cannot know resources beforehand, so mark individually from response maps afterward (see {@link markResourcesByTaskId});
 * 2. Call corresponding API enqueue endpoints;
 * 3. Notify: backend deduped=true (same-resource tasks already processing, no new tasks created) always
 *    uses info notices; otherwise retain each operation's original success copy.
 *
 * All failures propagate for caller error notices. Normalize returns to EnqueueResult,
 * hiding endpoint task_id / task_ids shape differences.
 *
 * Components must never bypass this layer for direct enqueue API calls (enforced by ESLint no-restricted-syntax).
 */
import { API, derivativeResourceId } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type {
  AssetSheetBatchScope,
  AssetSheetBatchSubmitted,
  AuthorPromptsRequest,
  DraftDocType,
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
import { isTerminalStatus } from "@/types/task";
import { episodeItemLabel } from "@/utils/episode-display";

/** Toasts extend beyond episode pages: refer to entries as title · 5. */
function itemLabel(itemId: string): string {
  const { currentProjectData, currentScripts } = useProjectsStore.getState();
  return episodeItemLabel(itemId, currentScripts, currentProjectData?.episodes ?? [], i18n.t);
}

export interface EnqueueResult {
  taskIds: string[];
  deduped: boolean;
}

/**
 * Shared info notices for deduped; otherwise operation-specific success copy
 * (null successText means intentionally silent success, kept silent).
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

/** Resource-level optimistic-marker shorthand (call before requests). */
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

/** scriptFile-level optimistic-marker shorthand (call before requests). */
function markScriptFile(projectName: string, taskType: string, scriptFile: string): OptimisticHandle {
  return useTasksStore.getState().beginOptimisticActiveForScriptFile(projectName, taskType, scriptFile);
}

/**
 * Shared enqueue skeleton: `marks` already contains markers; send requests and settle results only.
 * Failures roll back all before unchanged propagation; successes settle task_id from `taskIdsOf` (empty rolls back,
 * because absent backend task rows can never satisfy markers).
 *
 * Response parsing (`taskIdsOf`) and settlement share try: polling never clears in-flight markers,
 * so every presettlement exception must roll back, preventing residues until page refresh.
 */
async function submit<T>(
  marks: readonly OptimisticHandle[],
  request: () => Promise<T>,
  taskIdsOf: (res: T, markIndex: number) => string[],
): Promise<T> {
  try {
    const res = await request();
    // Each marker gets its own rows: yielding waits for every owned task_id. Giving every marker entire batches
    // makes each resource await all rows, while latest-row snapshots may never show earlier rows again.
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

/**
 * Add individual resource markers from backend resource-id → task_id maps.
 *
 * For batches whose targets are server-decided: callers cannot know matched resources before requests, so mark
 * after responses and immediately claim each own row; each waits for its own persistence only. Never mark absent-map items
 * skipped by servers or failing enqueue.
 */
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
    () => API.generateStoryboard(projectName, segmentId, prompt, scriptFile),
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
    () => API.generateVideo(projectName, segmentId, prompt, scriptFile, durationSeconds),
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
  // Servers choose segments lacking narration but having sources, so marking cannot precede requests; mark per response map afterward.
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
    () => API.generateCharacter(projectName, name),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:character_task_submitted_toast", { name }));
  return { taskIds: [res.task_id], deduped: res.deduped };
}

/**
 * Submit character derivative asset images. Independent occupancy slots use compound `本体/衍生` IDs, never
 * obscuring base generation tasks. Backends recompute instructions from current registrations; omit prompt here.
 */
export async function enqueueCharacterDerivative(
  projectName: string,
  characterName: string,
  derivativeName: string,
): Promise<EnqueueResult> {
  const resourceId = derivativeResourceId(characterName, derivativeName);
  const res = await submit(
    [markResource(projectName, "character_derivative", resourceId, "character_derivative")],
    () => API.generateCharacterDerivative(projectName, characterName, derivativeName),
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
  // Voice-audition samples share asset-image generation/edit resource slots (kind "character");
  // exclusion is acceptable: samples should start only while other character-card generation is idle.
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
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "scene", name, "scene")],
    () => API.generateProjectScene(projectName, name),
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
    () => API.generateProjectProp(projectName, name),
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
    () => API.generateProjectProduct(projectName, name),
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
  // image_edit shares generation resource slots: bucket kind by edited resource types;
  // pendingTaskType stays image_edit, matching taskResourceKind normalization.
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
  // task_ids may be empty (all missing-only storyboard groups ready, or joint sheets awaiting grid splitting), producing no backend task rows;
  // settle([]) rolls markers back without residues waiting forever for real rows.
  const res = await submit(
    [markScriptFile(projectName, "grid", scriptFile)],
    () => API.generateGrid(projectName, episode, scriptFile, sceneIds),
    manyTaskIds,
  );
  // grid_id is server-generated and absent before requests; mark grids individually from response maps afterward;
  // scriptFile-level markers above cover round-trip windows.
  markResourcesByTaskId(projectName, "grid", "grid", res.task_ids_by_grid);
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
  // Regeneration successes stay silent (panels already show state), notifying deduped only.
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
    () => API.generateReferenceVideoUnit(projectName, episode, unitId, options),
    oneTaskId,
  );
  notifyEnqueued(res.deduped, i18n.t("dashboard:reference_generate_queued"), "info");
  return { taskIds: [res.task_id], deduped: res.deduped };
}

/**
 * Batch video generation: one all-or-nothing admission request, with servers evaluating all target units.
 *
 * All three outcomes mean successful evaluation, but only `admitted` creates tasks; `confirmation_required` and
 * `blocked` create none, so roll back all optimistic markers immediately. Callers show confirmations/gaps by outcome.
 * Omitted unit_ids (missing-only generation) means server-decided targets, unmarkable by frontend, so omit marks.
 */
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
    () => API.generateReferenceVideoBatch(projectName, episode, payload),
    (admission, index) => {
      if (admission.decision !== "admitted") return [];
      const taskId = admission.task_ids_by_unit[unitIds[index]];
      return taskId === undefined ? [] : [taskId];
    },
  );
  if (res.decision === "admitted") {
    notifyEnqueued(
      res.deduped,
      // No first-target enqueue means no created tasks; submitted-zero notices would conflict with interruption notices below.
      res.task_ids.length > 0
        ? i18n.t("dashboard:reference_batch_queued", { count: res.task_ids.length })
        : null,
      "info",
    );
    // Interrupted enqueue retains created tasks, so report created counts and missing targets together: success counts alone
    // imply whole batches run, leaving users later discovering unexplained omissions.
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

/**
 * Batch episode storyboard images/videos: servers choose targets (storyboards without usable artifacts);
 * mark occupancy individually from storyboard → task_id maps after responses.
 *
 * Failed whole-batch storyboard-video admission creates no tasks; never notify success, let callers state outcomes.
 */
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

/**
 * Batch asset-image generation: servers select scope targets; mark individual members from responses;
 * derivatives occupy compound `本体/衍生` character_derivative slots, independent of bases.
 * Deduped tasks share standard notices, otherwise report enqueued counts; never mark members lacking tasks.
 */
export async function enqueueAssetSheetBatch(
  projectName: string,
  scope: AssetSheetBatchScope,
): Promise<AssetSheetBatchSubmitted> {
  const res = await API.submitAssetSheetBatch(projectName, scope);
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

/** Task IDs for successfully created text-batch members. */
function memberTaskIds(batch: { members: ReadonlyArray<{ task_id?: string | null }> }): string[] {
  return batch.members.flatMap((member) => (member.task_id ? [member.task_id] : []));
}

/** Prompt-authoring occupancy: one text task per episode, matching server `episode-{N}` resource_id. */
export function promptAuthoringResourceId(episode: number): string {
  return `episode-${episode}`;
}

/**
 * Submit AI prompt authoring/rewriting. Explicit overwrite confirmation returns server 409, propagated unchanged;
 * callers read `diagnostic.prompt_overwrite`, confirm and retry tokens.
 */
export async function enqueuePromptAuthoring(
  projectName: string,
  episode: number,
  request: AuthorPromptsRequest,
): Promise<EnqueueResult> {
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
  if (!deduped) watchPromptAuthoringNoop(projectName, taskIds);
  return { taskIds, deduped };
}

/**
 * Prompt-authoring no-ops (no pending entries, no model call) explain that overwrite permits rewriting.
 * Entries close immediately on submission, so track task outcomes in enqueue layers. Stop after terminal tasks or lists no longer track
 * projects through switches/studio departures; returning never replays stale notices.
 */
function watchPromptAuthoringNoop(projectName: string, taskIds: readonly string[]): void {
  const pending = new Set(taskIds);
  if (pending.size === 0) return;
  const check = (state: ReturnType<typeof useTasksStore.getState>): void => {
    if (state.refreshScope?.projectName !== projectName) {
      unsubscribe();
      return;
    }
    for (const task of state.tasks) {
      if (!pending.has(task.task_id) || !isTerminalStatus(task.status)) continue;
      pending.delete(task.task_id);
      if (task.status === "succeeded" && task.result?.no_pending_authoring === true) {
        useAppStore.getState().pushToast(i18n.t("dashboard:prompt_authoring_nothing_pending"), "info");
      }
    }
    if (pending.size === 0) unsubscribe();
  };
  const unsubscribe = useTasksStore.subscribe(check);
  check(useTasksStore.getState());
}

/** Script-planning occupancy: one text task per episode, matching server `episode-{N}` resource_id. */
export function scriptPlanResourceId(episode: number): string {
  return `episode-${episode}`;
}

/**
 * Submit AI script planning, replacing whole existing episode plans/drafts after caller-owned confirmation;
 * invalid admission/ongoing planning errors propagate unchanged.
 */
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

/**
 * Submit ad/short-film script generation, sharing prompt-authoring task types/`episode-{N}` slots.
 * Whole regeneration requiring overwrite consent returns 409; errors propagate unchanged; callers inspect `diagnostic.script_overwrite`, confirm and retry tokens.
 */
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

/** Two episode-planning occupancy slots, matching servers: initial windows use first, later windows alternate. */
export const EPISODE_PLANNING_SLOTS = ["episode-planning", "episode-planning-next"] as const;

/**
 * Submit AI episode planning windowwise from current starts through whole-source ends. Ongoing planning/invalid admission
 * errors propagate unchanged. Additional instructions belong to this submission, never project persistence.
 */
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

/**
 * Start replanning from this episode, windowwise producing new candidates, sharing planning slots. Existing candidates/ongoing planning
 * errors propagate unchanged. Additional instructions persist with candidates.
 */
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

/** Continue interrupted candidate generation, sharing planning slots; server refusals propagate unchanged. */
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

/** AI draft-repair occupancy, one per draft, matching server `episode-{N}-{doc_type}` resource_id. */
export function draftRepairResourceId(episode: number, docType: DraftDocType): string {
  return `episode-${episode}-${docType}`;
}

/**
 * Submit AI draft repairs against saved `baseRevision`; changed drafts, existing repairs
 * and other server errors propagate unchanged.
 */
export async function enqueueDraftRepair(
  projectName: string,
  episode: number,
  docType: DraftDocType,
  baseRevision: string,
  instructions: string | null,
): Promise<EnqueueResult> {
  const res = await submit(
    [markResource(projectName, "text_draft_repair", draftRepairResourceId(episode, docType), "text_draft_repair")],
    () => API.repairEpisodeDraft(projectName, episode, docType, baseRevision, instructions),
    (response) => memberTaskIds(response.batch),
  );
  const taskIds = memberTaskIds(res.batch);
  const deduped = res.batch.members.some((member) => member.deduped === true);
  notifyEnqueued(deduped, i18n.t("dashboard:draft_repair_queued"), "info");
  return { taskIds, deduped };
}
