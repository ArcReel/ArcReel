import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft,
  ChevronRight,
  Clock,
  Loader2,
  Save,
  Scissors,
  Sparkles,
} from "lucide-react";
import { UnitList } from "./UnitList";
import { UnitRail } from "./UnitRail";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { ScriptPreviewPanel } from "./ScriptPreviewPanel";
import { deriveUnitStatus } from "./unit-status";
import { EpisodeHeader } from "./EpisodeHeader";
import { ReferenceDurationConfirmDialog } from "./ReferenceDurationConfirmDialog";
import { ReferenceBatchAdmissionDialog } from "./ReferenceBatchAdmissionDialog";
import { referenceBatchOutcome } from "./batch-outcome";
import { NarrationDeliveryChoice } from "@/components/shared/NarrationDeliveryChoice";
import { computeVoiceLegacyNotice, VoiceLegacyBanner } from "./VoiceLegacyBanner";
import { useReferenceDurationGate } from "@/hooks/useReferenceDurationGate";
import { ReferenceScriptPlanPreviewPanel } from "@/components/canvas/reference/ReferenceScriptPlanPreviewPanel";
import { API } from "@/api";
import {
  enqueueNarration,
  enqueueReferenceVideoBatch,
  enqueueReferenceVideoUnit,
} from "@/actions/generation";
import {
  useReferenceVideoStore,
  referenceVideoCacheKey,
} from "@/stores/reference-video-store";
import {
  isResourceBusy,
  useActiveResourceIds,
  useLatestTasksByResource,
} from "@/stores/tasks-store";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { errMsg } from "@/utils/async";
import {
  buildMentionLookup,
  extractMentions,
  lineSpeechMarks,
  splitScriptLines,
} from "@/utils/reference-mentions";
import type {
  ReferenceBatchAdmission,
  ReferenceRequestOptions,
  ReferenceVideoUnit,
  UnitStatus,
} from "@/types";

export interface ReferenceVideoCanvasProps {
  projectName: string;
  episode: number;
  episodeTitle?: string;
  onSaveTitle?: (next: string) => Promise<void>;
  canEditTitle?: boolean;
  /** prompt_authoring 剧本（scripts/episode_N.json）是否已生成——决定默认 tab（镜像 GridImageToVideoCanvas 的 hasScript 判定）。 */
  hasScript?: boolean;
  /** ad 参考生视频一阶段产出，不展示 script_plan 脚本规划页。 */
  showPreprocess?: boolean;
  /** unit 时长为自由正整数，不用供应商档位作为编排限制。 */
  freeDuration?: boolean;
  /**
   * unit 时长下拉的档位，来自模型能力声明（已按参考图约束与分辨率收窄）。供正文里带
   * `@[名称]` 引用的 unit 使用；能力不可解析时为 undefined——此时不渲染下拉，只读展示当前
   * 秒数，不编造档位。
   */
  durationOptions?: number[];
  /**
   * 同一模型能力下、不叠加参考图约束的档位（仍按分辨率收窄）。供正文里没有可解析引用的
   * unit 使用——参考图约束按 unit 生效，不能因同集内其它 unit 带图就收窄这类 unit 的可选档位。
   */
  durationOptionsNoReference?: number[];
  /** 上游旁白工作流给出的请求事实；不在画布内探测或推断 TTS 状态。 */
  requestOptions?: ReferenceRequestOptions;
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);

/**
 * 画布层自记的按 unit 占用位（不产生任务行、进不了 tasks-store 占用集的那些写入路径）。
 *
 * `ids` 供渲染，`ref` 供提交时刻新鲜读：state 要等 render 冲刷才可见，而「点击发生在状态
 * 已变、渲染未到」的窗口正是提交时复核要挡的（与 isUnitBusy 的新鲜读同理）。
 */
function useUnitFlagSet() {
  const [ids, setIds] = useState<Set<string>>(() => new Set());
  const ref = useRef<Set<string>>(new Set());
  const set = useCallback((unitId: string, on: boolean) => {
    const next = new Set(ref.current);
    if (on) next.add(unitId);
    else next.delete(unitId);
    ref.current = next;
    setIds(next);
  }, []);
  return useMemo(() => ({ ids, ref, set }), [ids, set]);
}

// 容器宽度断点（px，对应设计稿的响应式行为）。
//   < LIST_RAIL_BREAKPOINT — 左侧 UnitList 收成 56px rail（带 flyout 触发）
//   < STACK_PREVIEW_BREAKPOINT — 中右合栏，预览叠成 sub-tab
const LIST_RAIL_BREAKPOINT = 1100;
const STACK_PREVIEW_BREAKPOINT = 880;
// 三栏布局下右栏宽度——主区更宽时给预览更大的呼吸空间。
const PREVIEW_COL_NARROW = 320;
const PREVIEW_COL_WIDE = 360;
const WIDE_BREAKPOINT = 1280;

// Compound key avoids cross-project draft bleed: E{ep}U{n} repeats across projects.
function draftKey(projectName: string, episode: number, unitId: string): string {
  return `${projectName}::${episode}::${unitId}`;
}

function toastError(e: unknown, format?: (msg: string) => string): void {
  const msg = errMsg(e);
  useAppStore.getState().pushToast(format ? format(msg) : msg, "error");
}

/**
 * 提交时刻的占用复核：按钮渲染期捕获的占用态未必是最新的（批量循环、Agent 入队、
 * SSE 落库都可能在渲染之后、点击之前占用同一 unit），故一律用 getState() 新鲜读。
 * 入队动作层在请求发出前就打乐观标记，因此同一 tick 内的连点也会被这一读拦下。
 */
function isUnitBusy(projectName: string, unitId: string): boolean {
  return isResourceBusy("reference_video", projectName, unitId);
}

function unitNarrationText(unit: ReferenceVideoUnit | null): string {
  if (!unit) return "";
  const narration: string[] = [];
  let hasCharacterSpeech = false;
  for (const line of splitScriptLines(unit.text)) {
    for (const mark of lineSpeechMarks(line)) {
      if (mark.speaker) {
        hasCharacterSpeech = true;
      } else {
        narration.push(mark.text.trim());
      }
    }
  }
  return hasCharacterSpeech ? "" : narration.join("\n");
}

export function ReferenceVideoCanvas({
  projectName,
  episode,
  episodeTitle,
  onSaveTitle,
  canEditTitle,
  hasScript = true,
  showPreprocess = true,
  freeDuration = false,
  durationOptions,
  durationOptionsNoReference,
  requestOptions,
}: ReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");
  const [narrationDelivery, setNarrationDelivery] = useState<"post_production" | "use_tts">(
    requestOptions?.narration_delivery ?? "post_production",
  );
  const effectiveRequestOptions = useMemo<ReferenceRequestOptions>(
    () =>
      requestOptions || narrationDelivery !== "post_production"
        ? { ...requestOptions, narration_delivery: narrationDelivery }
        : {},
    [narrationDelivery, requestOptions],
  );

  const loadUnits = useReferenceVideoStore((s) => s.loadUnits);
  const addUnit = useReferenceVideoStore((s) => s.addUnit);
  const patchUnit = useReferenceVideoStore((s) => s.patchUnit);
  const select = useReferenceVideoStore((s) => s.select);

  const units =
    useReferenceVideoStore((s) => s.unitsByEpisode[referenceVideoCacheKey(projectName, episode)]) ??
    (EMPTY_UNITS as ReferenceVideoUnit[]);
  const selectedUnitId = useReferenceVideoStore((s) => s.selectedUnitId);
  const error = useReferenceVideoStore((s) => s.error);
  const loading = useReferenceVideoStore((s) => s.loading);
  const project = useProjectsStore((s) => s.currentProjectData);
  // schema v6 起各 bucket 共用名称空间，每个名字只会声明一次。
  const mentionLookup = useMemo(() => buildMentionLookup(project), [project]);

  const voiceLegacyNotice = useMemo(
    () => computeVoiceLegacyNotice(units, project?.characters ?? {}, project?.character_voice_binding),
    [units, project],
  );
  // 关闭 = 「已确认到该角色当前这一版声音」，故写回该角色自己的 voice_updated_at 而非
  // 本机当前时间：两侧都由后端戳出，比较不受客户端时钟偏差影响（时钟落后会让关闭永不生效），
  // 也不受 ISO 格式差异影响。下一次声音更新使 voice_updated_at 前移，横幅自然重新出现。
  const handleDismissVoiceLegacyNotice = useCallback(async () => {
    // 提交时刻新鲜读：横幅渲染后声音可能又被更新，须确认到最新那一版。
    const characters = useProjectsStore.getState().currentProjectData?.characters ?? {};
    try {
      await Promise.all(
        voiceLegacyNotice.characterNames.map((name) => {
          const acknowledgedAt = characters[name]?.voice_updated_at;
          if (!acknowledgedAt) return Promise.resolve();
          return API.updateCharacter(projectName, name, { voice_notice_dismissed_at: acknowledgedAt });
        }),
      );
      // refreshProject 失败时 resolve "failed" 而非 reject，须传 onError，否则 PATCH 已成功
      // 但本地 store 未同步时会静默吞掉，横幅带着旧数据留在页面上却不提示用户。
      await useProjectsStore.getState().refreshProject(projectName, {
        onError: (err) => toastError(err, (msg) => t("voice_legacy_banner_dismiss_failed", { error: msg })),
      });
    } catch (e) {
      // 静默失败会让横幅原样留在页面上而用户以为已关闭，必须可见。
      toastError(e, (msg) => t("voice_legacy_banner_dismiss_failed", { error: msg }));
    }
  }, [projectName, t, voiceLegacyNotice.characterNames]);

  // Drafts persist across unit switches; entry is dropped when text matches server value.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [durationDrafts, setDurationDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  // resource（=unit）→ 最新任务行。「最新行胜出」下沉到 store selector：
  // store 不保证 tasks 顺序（SSE 原位 upsert），重试的新行不被旧失败行盖住。
  const tasksByUnit = useLatestTasksByResource(projectName, "reference_video");

  // 参考生视频任务完成时经项目事件 SSE 自增，驱动本 effect 重拉分组展示成片。
  const unitsRevision = useAppStore((s) => s.referenceVideoUnitsRevision);

  useEffect(() => {
    // prompt_authoring 剧本未生成时 /episodes/{episode}/units 后端会 404（无脚本可拆单元）；
    // hasScript 转 true 后本 effect 随依赖变化重跑，补上首次拉取。
    if (!hasScript) return;
    void loadUnits(projectName, episode);
  }, [loadUnits, projectName, episode, hasScript, unitsRevision]);

  const selected = useMemo(
    () => units.find((u) => u.unit_id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );
  const selectedDurationKey = selected
    ? draftKey(projectName, episode, selected.unit_id)
    : null;
  const selectedDurationValue = selectedDurationKey
    ? (durationDrafts[selectedDurationKey] ?? String(selected?.duration_seconds ?? ""))
    : "";

  // 参考图约束按 unit 而非按集生效（同 lib.reference_video.request_projection 的
  // ReferenceUnitRequestProjector 按可用参考图定 r2v / i2v 的判据）：正文里解析不出已登记
  // 引用的 unit 用不叠加该约束的档位，否则同集内其它 unit 带图会连带把它的可选档位收窄到
  // 一个它本不受限的子集。
  const selectedHasReference = useMemo(
    () =>
      selected
        ? extractMentions(selected.text).some((name) => Boolean(mentionLookup[name]))
        : false,
    [selected, mentionLookup],
  );
  const effectiveDurationOptions = selectedHasReference ? durationOptions : durationOptionsNoReference;

  // selectedUnitId is a global singleton; validate against current episode's units.
  useEffect(() => {
    if (units.length > 0 && !selected) {
      select(units[0].unit_id);
    }
  }, [units, selected, select]);

  // 乐观占用来自 tasks-store：入队动作层在请求发出前打标、失败回滚，真实任务行落库后
  // 让位，故「请求发出 → 任务行落库」全程都被覆盖，画布无须自备请求在途标记。
  const busyUnitIds = useActiveResourceIds("reference_video", projectName);
  const ttsBusyUnitIds = useActiveResourceIds("tts", projectName);

  // 成片上传、版本恢复与时长保存都不产生任务行，进不了 tasks-store 占用集，故在画布层按 unit 记录。
  // 存在这里而非 UnitPreviewPanel 内：该面板有窄屏 sub-tab 与宽屏右栏两处挂载点，切换子页
  // 或跨越 STACK_PREVIEW_BREAKPOINT 都会卸载它（在途请求不会因此取消），且它随选中项切换
  // 复用，面板内的单个布尔量还会把 A 的占用态串到 B 上。
  const uploading = useUnitFlagSet();
  const restoring = useUnitFlagSet();
  const durationSaving = useUnitFlagSet();

  const setUploading = uploading.set;
  const handleRestoringChange = restoring.set;
  const setDurationSaving = durationSaving.set;

  /** 该 unit 是否被任一写入路径占用：生成（tasks-store 占用集）、成片上传、版本恢复或时长保存。 */
  const isUnitLocked = useCallback(
    (unitId: string) =>
      isUnitBusy(projectName, unitId) ||
      uploading.ref.current.has(unitId) ||
      restoring.ref.current.has(unitId) ||
      durationSaving.ref.current.has(unitId),
    [projectName, uploading.ref, restoring.ref, durationSaving.ref],
  );

  const statusMap = useMemo<Record<string, UnitStatus>>(() => {
    const map: Record<string, UnitStatus> = {};
    for (const u of units) {
      map[u.unit_id] = deriveUnitStatus({
        hasClip: Boolean(u.generated_assets.video_clip),
        queueRow: tasksByUnit.get(u.unit_id),
        busy: busyUnitIds.has(u.unit_id),
        uploading: uploading.ids.has(u.unit_id),
        // 本画布提供成片上传入口：上传后单元已有可播放资产，历史失败不再覆盖 ready
        // （与 timeline/grid 画布用 toast 提示失败的语义对齐）。
        supportsManualUpload: true,
      });
    }
    return map;
  }, [units, tasksByUnit, busyUnitIds, uploading.ids]);

  // 独立于 statusMap 传给 UnitPreviewPanel：重试（旧失败行在）与重新生成（旧成功行在）
  // 两条路径上 queueRow 始终非空，statusMap 的乐观分支不生效，仅看 status 会在入队到
  // 任务行落库之间的窗口内漏禁用生成按钮。
  const selectedBusy = !!(selected && busyUnitIds.has(selected.unit_id));
  const selectedCancelling = !!(selected && tasksByUnit.get(selected.unit_id)?.status === "cancelling");

  const failureMessage = useMemo(() => {
    if (!selected) return null;
    if (statusMap[selected.unit_id] !== "failed") return null;
    return tasksByUnit.get(selected.unit_id)?.error_message ?? null;
  }, [selected, statusMap, tasksByUnit]);

  const dirtyMap = useMemo<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    for (const u of units) {
      const v = drafts[draftKey(projectName, episode, u.unit_id)];
      if (v !== undefined && v !== u.text) map[u.unit_id] = true;
    }
    return map;
  }, [units, drafts, projectName, episode]);

  const handleAdd = useCallback(async () => {
    try {
      await addUnit(projectName, episode, { prompt: "" });
    } catch (e) {
      toastError(e);
    }
  }, [addUnit, projectName, episode]);

  const [stackTab, setStackTab] = useState<"editor" | "preview">("editor");

  // 时长取档闸门：申请秒数与请求时长基准不一致时先确认，取消则一个都不入队
  const isUnitGenerationBlocked = useCallback(
    (unitId: string) =>
      Boolean(
        useReferenceVideoStore
          .getState()
          .unitsByEpisode[referenceVideoCacheKey(projectName, episode)]?.find((u) => u.unit_id === unitId)
          ?.needs_replan,
      ),
    [projectName, episode],
  );
  /** 单元入口的复核：无占用且规划状态可生成。 */
  const canEnqueueUnit = useCallback(
    (unitId: string) => !isUnitLocked(unitId) && !isUnitGenerationBlocked(unitId),
    [isUnitLocked, isUnitGenerationBlocked],
  );

  /**
   * 批量入口的复核：只问「这个 unit 还缺成片吗」。
   *
   * needs_replan、在途任务这类问题不在这里过滤——它们正是服务端准入要逐条报告、并据此
   * 让整批零任务入队的缺口；在浏览器里先摘掉，服务端看到的就只剩健康子集，会照常建任务，
   * 用户既看不到缺口也失去了全有或全无的保证。
   *
   * 已有成片的单元不同：它已经不是「缺成片」的目标。任务完成后该 unit 不再 busy，而队列
   * 去重只看 queued/running/cancelling，确认弹窗停留期间完成的单元若原样提交，会再跑一次
   * 生成、重复计费并覆盖刚出的成片。实时读 store 而非渲染期 units 快照。
   *
   * 本地写入（成片上传、版本恢复、时长保存）服务端看不见，也即将改写该 unit，同样排除。
   */
  const canEnqueueBatchUnit = useCallback(
    (unitId: string) => {
      // 只排除本画布自己的写入（服务端看不到它们）：生成占用与 needs_replan 交给准入去报告。
      const hasLocalWrite =
        uploading.ref.current.has(unitId) ||
        restoring.ref.current.has(unitId) ||
        durationSaving.ref.current.has(unitId);
      if (hasLocalWrite) return false;
      const fresh = useReferenceVideoStore
        .getState()
        .unitsByEpisode[referenceVideoCacheKey(projectName, episode)]?.find((u) => u.unit_id === unitId);
      return !fresh?.generated_assets?.video_clip;
    },
    [projectName, episode, uploading.ref, restoring.ref, durationSaving.ref],
  );

  const durationGate = useReferenceDurationGate({
    projectName,
    episode,
    requestOptions: effectiveRequestOptions,
  });
  /** 整批准入判定的未决结论（需确认 / 受阻）；admitted 由 toast 反馈，不进这里。 */
  const [batchAdmission, setBatchAdmission] = useState<ReferenceBatchAdmission | null>(null);

  const enqueue = useCallback(
    async (
      unitId: string,
      confirmedRequestDuration: number | undefined,
      options: ReferenceRequestOptions,
    ) => {
      // 提交前用 getState() 新鲜读复核：按钮渲染期捕获的占用态未必是最新的
      // （批量循环、Agent 入队、SSE 落库都可能在渲染之后、点击之前占用同一 unit）；
      // 时长确认弹窗打开期间同样会经过这段窗口，故复核落在入队这一刻。
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      if (isUnitGenerationBlocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_needs_replan"), "error");
        return;
      }
      try {
        // 乐观打标（请求发出前）、失败回滚与 queued/deduped 提示都在动作层内完成
        await enqueueReferenceVideoUnit(projectName, episode, unitId, {
          ...options,
          ...(confirmedRequestDuration == null
            ? {}
            : { confirmed_request_duration_seconds: confirmedRequestDuration }),
        });
      } catch (e) {
        toastError(e, (msg) => t("reference_generate_request_failed", { error: msg }));
      }
    },
    [projectName, episode, isUnitLocked, isUnitGenerationBlocked, t],
  );

  /**
   * 串行 enqueue —— 让前端依次触发后端 dedup 检查；后端实际仍按 worker 并发跑。
   *
   * 每次 POST 前都用入口的判定复核一遍：循环里每个请求之间都是一段等待窗口，靠后的
   * 单元可能在此期间由别处生成完成，只在循环开始前过滤一次拦不住它。
   *
   * 单元入口专用：批量入口走服务端的全有或全无准入，一次请求评估全部目标。
   */
  const makeEnqueueSerially = useCallback(
    (canEnqueue: (unitId: string) => boolean, options: ReferenceRequestOptions) =>
      async (unitIds: string[], confirmedDurations: ReadonlyMap<string, number>) => {
      for (const id of unitIds) {
        if (!canEnqueue(id)) continue;
        await enqueue(id, confirmedDurations.get(id), options);
      }
    },
    [enqueue],
  );

  const handleGenerate = useCallback(
    async (unitId: string) => {
      setStackTab("preview");
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      if (isUnitGenerationBlocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_needs_replan"), "error");
        return;
      }
      await durationGate.run(
        [unitId],
        makeEnqueueSerially(canEnqueueUnit, effectiveRequestOptions),
        canEnqueueUnit,
      );
    },
    [
      durationGate,
      makeEnqueueSerially,
      isUnitLocked,
      isUnitGenerationBlocked,
      canEnqueueUnit,
      effectiveRequestOptions,
      t,
    ],
  );

  const handleUploadVideo = useCallback(
    async (unitId: string, file: File) => {
      // 上传与生成回写同一个成片文件，故与生成入口同一套占用判定：文件选择对话框
      // 打开期间同一 unit 可能已被占用，按钮渲染期的禁用态挡不住这段窗口。
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      setUploading(unitId, true);
      try {
        try {
          const result = await API.uploadReferenceUnitVideo(projectName, episode, unitId, file);
          useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
          useAppStore.getState().pushToast(t("media_upload_success", { id: unitId }), "success");
        } catch (e) {
          toastError(e, (msg) => t("media_upload_failed", { message: msg }));
          return;
        }
        // 上传已成功落盘：刷新失败单独提示，不误报为上传失败（SSE/重进页面兜底最终一致）
        try {
          await loadUnits(projectName, episode);
        } catch (e) {
          toastError(e, (msg) => t("media_refresh_failed", { message: msg }));
        }
      } finally {
        setUploading(unitId, false);
      }
    },
    [projectName, episode, loadUnits, isUnitLocked, setUploading, t],
  );

  const handleUnitsRefresh = useCallback(
    () => loadUnits(projectName, episode),
    [loadUnits, projectName, episode],
  );

  const handleGenerateNarration = useCallback(
    async (unitId: string) => {
      const scriptFile = useProjectsStore
        .getState()
        .currentProjectData?.episodes?.find((item) => item.episode === episode)?.script_file;
      if (!scriptFile) {
        useAppStore.getState().pushToast(t("timeline_script_not_ready"), "error");
        return;
      }
      try {
        await enqueueNarration(projectName, unitId, scriptFile);
      } catch (error) {
        toastError(error, (message) => t("generate_narration_failed", { message }));
      }
    },
    [episode, projectName, t],
  );
  const onGenerateNarrationVoid = useCallback(
    (unitId: string) => {
      void handleGenerateNarration(unitId);
    },
    [handleGenerateNarration],
  );

  // 批量生成的作用对象：全部尚无成片的 unit（含 needs_replan、在途、失败重试）。按钮禁用须与
  // 它同一口径——只看当前选中 unit 是否在跑、与作用对象无关的判定会脱节：选中项空闲时按钮会在
  // 没有任何待生成 unit 的情况下仍可点击，选中项在跑时又会挡住其余 unit 的批量生成。
  const batchTargets = useMemo(
    () => units.filter((u) => statusMap[u.unit_id] !== "ready"),
    [units, statusMap],
  );

  /**
   * 一次请求走服务端的全有或全无准入，{@link referenceBatchOutcome} 的五种结局都是评估
   * 成功。只有整批干净入队那一种由动作层那句提示收尾，其余四种都留下结论面板：档位待拍板、
   * 准入受阻，以及没排上队列的是哪几个、各自为什么。
   */
  const runBatch = useCallback(
    async (unitIds: string[], confirmedDurations?: Record<string, number>) => {
      try {
        const admission = await enqueueReferenceVideoBatch(projectName, episode, {
          unit_ids: unitIds,
          // 旁白交付方式随请求走，与单元入口同一个选择：不带上它，整批会按服务端
          // 默认的「后期配音」准入，用户在画布上选的「使用当前 TTS」被静默丢弃。
          narration_delivery: narrationDelivery,
          ...(confirmedDurations ? { confirmed_request_durations: confirmedDurations } : {}),
        });
        setBatchAdmission(referenceBatchOutcome(admission) === "queued" ? null : admission);
      } catch (e) {
        setBatchAdmission(null);
        toastError(e, (msg) => t("reference_batch_request_failed", { error: msg }));
      }
    },
    [projectName, episode, narrationDelivery, t],
  );

  const handleBatchGenerate = useCallback(async () => {
    if (batchTargets.length === 0) {
      useAppStore.getState().pushToast(t("reference_batch_nothing_to_do"), "info");
      return;
    }
    setStackTab("preview");
    // 实时复核而非用渲染期快照：其它入口（单元按钮、Agent 入队、SSE 落库）可能已占用
    // 同一 unit。命中即跳过，不当作错误提示——批量入口的语义是「把还能生成的都排上」，
    // 逐个报错只会刷屏。
    const targets = batchTargets.map((u) => u.unit_id).filter(canEnqueueBatchUnit);
    if (targets.length === 0) {
      useAppStore.getState().pushToast(t("reference_batch_nothing_to_do"), "info");
      return;
    }
    await runBatch(targets);
  }, [batchTargets, canEnqueueBatchUnit, runBatch, t]);

  /**
   * 聚合确认后重发同一端点完成入队：档位按 tier 摊回各 unit，目标集合仍是本轮全部
   * 目标（无需确认的单元也在其中，否则它们永远排不上）。
   *
   * 提交时刻再复核一次：弹窗停留期间（用户思考时长，可以很长）清单里的单元可能已被
   * 别处生成完成——按冻结清单原样重发会重复计费并覆盖刚出的成片。
   */
  const handleBatchConfirm = useCallback(() => {
    const admission = batchAdmission;
    setBatchAdmission(null);
    const tiers = admission?.confirmation?.tiers ?? [];
    if (!admission || tiers.length === 0) return;
    const durationByUnit = new Map<string, number>();
    for (const tier of tiers) {
      // 档位没解析出来的组没有可确认的值：略过后重发，服务端会照旧把它算成缺口，
      // 而不是收到一个空档位当成用户已拍板。
      if (tier.request_duration_seconds == null) continue;
      for (const unitId of tier.unit_ids) durationByUnit.set(unitId, tier.request_duration_seconds);
    }
    const targets = Array.from(
      new Set([...admission.units.map((u) => u.unit_id), ...durationByUnit.keys()]),
    ).filter(canEnqueueBatchUnit);
    if (targets.length === 0) {
      // 弹窗此刻已关闭，不给反馈的话界面只是静静地什么也没发生。
      useAppStore.getState().pushToast(t("reference_batch_nothing_to_do"), "info");
      return;
    }
    const confirmed: Record<string, number> = {};
    for (const unitId of targets) {
      const duration = durationByUnit.get(unitId);
      if (duration != null) confirmed[unitId] = duration;
    }
    void runBatch(targets, confirmed);
  }, [batchAdmission, canEnqueueBatchUnit, runBatch, t]);

  const onAdd = useCallback(() => void handleAdd(), [handleAdd]);

  // 时长与正文分开提交：时长不是文本的一部分，改档位立即落盘，不牵连未保存的正文草稿。
  const handleDurationChange = useCallback(
    async (unitId: string, seconds: number): Promise<boolean> => {
      // 渲染期的禁用态未必最新（SSE / Agent 入队可能刚占用），提交时刻再复核一次
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return false;
      }
      setDurationSaving(unitId, true);
      try {
        await patchUnit(projectName, episode, unitId, { duration_seconds: seconds });
        // 参考生视频按申请秒数计价，SSE 的 unit 失效负责最终同步列表；费用面板仍需
        // 在本地写成功时主动刷新，给当前浏览器即时反馈。
        useCostStore.getState().debouncedFetch(projectName);
        return true;
      } catch (e) {
        toastError(e);
        return false;
      } finally {
        setDurationSaving(unitId, false);
      }
    },
    [patchUnit, projectName, episode, isUnitLocked, setDurationSaving, t],
  );

  const clearDurationDraft = useCallback((key: string, expected: string) => {
    setDurationDrafts((current) => {
      if (current[key] !== expected) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }, []);

  const handleFreeDurationChange = useCallback(
    (unitId: string, value: string) => {
      const key = draftKey(projectName, episode, unitId);
      setDurationDrafts((current) => ({ ...current, [key]: value }));
    },
    [projectName, episode],
  );

  const commitFreeDuration = useCallback(
    (unitId: string, rawValue: string) => {
      const key = draftKey(projectName, episode, unitId);
      if (!(key in durationDrafts)) return;
      const seconds = Number(rawValue);
      const fresh = useReferenceVideoStore
        .getState()
        .unitsByEpisode[referenceVideoCacheKey(projectName, episode)]?.find(
          (unit) => unit.unit_id === unitId,
        );
      const confirmsDurationMarker = Boolean(fresh?.needs_replan);
      if (
        !Number.isInteger(seconds) ||
        seconds < 1 ||
        seconds > 300 ||
        !fresh ||
        (fresh.duration_seconds === seconds && !confirmsDurationMarker)
      ) {
        clearDurationDraft(key, rawValue);
        return;
      }
      void handleDurationChange(unitId, seconds).then((saved) => {
        if (saved) clearDurationDraft(key, rawValue);
      });
    },
    [projectName, episode, durationDrafts, handleDurationChange, clearDurationDraft],
  );
  const onGenerateVoid = useCallback((id: string) => void handleGenerate(id), [handleGenerate]);

  const handlePromptChange = useCallback(
    (next: string) => {
      if (!selected) return;
      const key = draftKey(projectName, episode, selected.unit_id);
      const baseText = selected.text;
      setDrafts((d) => {
        if (next === baseText) {
          if (!(key in d)) return d;
          const copy = { ...d };
          delete copy[key];
          return copy;
        }
        return { ...d, [key]: next };
      });
    },
    [selected, projectName, episode],
  );

  const currentText = useMemo(() => {
    if (!selected) return "";
    return drafts[draftKey(projectName, episode, selected.unit_id)] ?? selected.text;
  }, [selected, drafts, projectName, episode]);

  const isDirty = !!(selected && dirtyMap[selected.unit_id]);

  // 编辑器列内的两种视图：写文稿 / 看解析结果。解析预览是只读派生视图，与正文同一份
  // 文本，故共用编辑器列的空间而非再占一栏（右栏留给成片预览）。
  const [editorView, setEditorView] = useState<"script" | "parse">("script");

  const hasAnyDurationDraft = units.some((unit) => {
    const raw = durationDrafts[draftKey(projectName, episode, unit.unit_id)];
    if (raw === undefined) return false;
    const seconds = Number(raw);
    return (
      !Number.isInteger(seconds) ||
      seconds < 1 ||
      seconds > 300 ||
      seconds !== unit.duration_seconds ||
      Boolean(unit.needs_replan)
    );
  });
  const hasAnyDraft = Object.keys(drafts).length > 0 || hasAnyDurationDraft;

  // 草稿已落盘 → 丢弃本地草稿。若这期间用户又敲了字（草稿值已变），保留新草稿不动，
  // 否则落盘响应回来时会把用户刚输入的内容抹掉。
  const clearFlushedDraft = useCallback((key: string, flushed: string) => {
    setDrafts((d) => {
      if (d[key] !== flushed) return d;
      const copy = { ...d };
      delete copy[key];
      return copy;
    });
  }, []);

  const handleSave = useCallback(async () => {
    if (!selected) return;
    const unitId = selected.unit_id;
    const key = draftKey(projectName, episode, unitId);
    const draftText = drafts[key];
    if (draftText === undefined || draftText === selected.text) return;
    setSaving(true);
    try {
      await patchUnit(projectName, episode, unitId, { prompt: draftText });
      clearFlushedDraft(key, draftText);
    } catch (e) {
      toastError(e);
    } finally {
      setSaving(false);
    }
  }, [selected, drafts, patchUnit, projectName, episode, clearFlushedDraft]);

  // Reset tab to units on project/episode change (render-time derived-state pattern).
  // 初始值按 hasScript 走 GridImageToVideoCanvas 同款判定：prompt_authoring 剧本未生成时（仅 segmented）
  // units 面板无脚本可读、请求会 404，应先落到内容确认。
  const [tab, setTab] = useState<"units" | "preproc">(
    hasScript || !showPreprocess ? "units" : "preproc",
  );
  const [lastEpisode, setLastEpisode] = useState(episode);
  const [lastProject, setLastProject] = useState(projectName);
  if (lastEpisode !== episode || lastProject !== projectName) {
    setLastEpisode(episode);
    setLastProject(projectName);
    setTab(hasScript || !showPreprocess ? "units" : "preproc");
  }

  useEffect(() => {
    // 剧本生成完成后（hasScript 由 false 变 true）自动切到 units，同一 episode 内组件不 remount。
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 镜像 GridImageToVideoCanvas 同款效果
    if (hasScript || !showPreprocess) setTab("units");
  }, [hasScript, showPreprocess]);

  // 通知回跳：收到 reference_unit scroll target 时切到 units tab 并选中对应 unit
  // （镜像 ShotSplitView 的选择式回跳）。units 异步加载，靠依赖变化重试到命中或过期。
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  useEffect(() => {
    if (scrollTarget?.type !== "reference_unit") return;
    const requestId = scrollTarget.request_id;
    if (units.some((u) => u.unit_id === scrollTarget.id)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 订阅通知 store，触发后切 tab + 选中
      setTab("units");
      select(scrollTarget.id);
      clearScrollTarget(requestId);
      return;
    }
    // units 加载中：等待，不安排过期清理——否则慢网/冷启动下 loadUnits 尚未返回就
    // 到期，target 会被提前清除，units 到达也无法再选中目标 unit。
    if (loading) return;
    // 加载完成仍未命中：挂一个到 expires_at 的一次性兜底清理，避免此后 units/loading
    // 都不再变化时 effect 不再重跑、过期 target 永久残留 store。units 若晚到会触发
    // 依赖变化、重跑本 effect 并清掉该定时器。
    const remaining = scrollTarget.expires_at - Date.now();
    if (remaining <= 0) {
      clearScrollTarget(requestId);
      return;
    }
    const timer = setTimeout(() => clearScrollTarget(requestId), remaining);
    return () => clearTimeout(timer);
  }, [scrollTarget, units, loading, select, clearScrollTarget]);

  const preprocStatus: "loading" | "error" | "empty" | "ready" = loading
    ? "loading"
    : error
      ? "error"
      : units.length === 0
        ? "empty"
        : "ready";
  const preprocDot: Record<typeof preprocStatus, string> = {
    loading: "bg-gray-500",
    error: "bg-red-500",
    empty: "bg-gray-500",
    ready: "bg-emerald-500",
  };

  useEffect(() => {
    if (!hasAnyDraft) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasAnyDraft]);

  const workbenchRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(1200);
  useLayoutEffect(() => {
    if (!workbenchRef.current) return;
    const el = workbenchRef.current;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = e.contentRect.width;
        // jsdom 下 contentRect.width 恒为 0；同像素值不重复 setState 避免亚像素抖动。
        if (w > 0) setContainerWidth((prev) => (prev === w ? prev : w));
      }
    });
    ro.observe(el);
    const initial = el.getBoundingClientRect().width;
    if (initial > 0) setContainerWidth(initial);
    return () => ro.disconnect();
  }, []);
  const listMode: "rail" | "full" = containerWidth < LIST_RAIL_BREAKPOINT ? "rail" : "full";
  const stackPreview = containerWidth < STACK_PREVIEW_BREAKPOINT;
  const listColW = listMode === "rail" ? 56 : 320;
  const previewColW = containerWidth < WIDE_BREAKPOINT ? PREVIEW_COL_NARROW : PREVIEW_COL_WIDE;
  const gridCols = stackPreview
    ? `${listColW}px minmax(0, 1fr)`
    : `${listColW}px minmax(0, 1fr) ${previewColW}px`;
  const [listFlyoutOpen, setListFlyoutOpen] = useState(false);

  const segCost = useCostStore((s) =>
    selected ? s._segmentIndex.get(selected.unit_id) : undefined,
  );
  const estimatedCost = segCost?.estimate.video;
  const displayedEstimatedCost = narrationDelivery === "use_tts" ? undefined : estimatedCost;
  const actualCost = segCost?.actual.video;
  const narrationEstimatedCost = segCost?.estimate.audio;
  const selectedNarrationText = unitNarrationText(selected);

  const selectedIndex = selected ? units.findIndex((u) => u.unit_id === selected.unit_id) : -1;
  const goPrev = useCallback(() => {
    if (selectedIndex <= 0) return;
    select(units[selectedIndex - 1].unit_id);
  }, [select, units, selectedIndex]);
  const goNext = useCallback(() => {
    if (selectedIndex < 0 || selectedIndex >= units.length - 1) return;
    select(units[selectedIndex + 1].unit_id);
  }, [select, units, selectedIndex]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <EpisodeHeader
        episode={episode}
        title={episodeTitle ?? `E${episode}`}
        units={units}
        onSaveTitle={onSaveTitle}
        canEditTitle={canEditTitle}
      />

      {/* Tabs + request-local generation controls */}
      <div className="flex items-center gap-0.5 border-b border-[var(--color-hairline)] bg-[oklch(0.19_0.012_250_/_0.5)] px-5">
        <div role="tablist" aria-label={t("reference_main_tab_aria")} className="flex items-center gap-0.5">
          {showPreprocess && <button
            type="button"
            role="tab"
            aria-selected={tab === "preproc"}
            onClick={() => setTab("preproc")}
            className={`focus-ring relative inline-flex items-center gap-1.5 px-3.5 py-2.5 text-[12.5px] font-medium ${
              tab === "preproc" ? "text-[var(--color-text)]" : "text-[var(--color-text-3)]"
            }`}
          >
            <span>{t("reference_tab_preprocess")}</span>
            {preprocStatus === "loading" ? (
              <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-4)]" aria-hidden="true" />
            ) : (
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 rounded-full ${preprocDot[preprocStatus]}`}
              />
            )}
            {tab === "preproc" && (
              <span
                aria-hidden="true"
                className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
              />
            )}
          </button>}
          <button
            type="button"
            role="tab"
            aria-selected={tab === "units"}
            onClick={() => setTab("units")}
            className={`focus-ring relative px-3.5 py-2.5 text-[12.5px] font-medium ${
              tab === "units" ? "text-[var(--color-text)]" : "text-[var(--color-text-3)]"
            }`}
          >
            {t("reference_tab_units")}
            {tab === "units" && (
              <span
                aria-hidden="true"
                className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
              />
            )}
          </button>
        </div>
        <span className="flex-1" />
        {tab === "units" && (
          <>
            <NarrationDeliveryChoice
              value={narrationDelivery}
              onChange={setNarrationDelivery}
              compact
            />
            <button
              type="button"
              onClick={() => void handleBatchGenerate()}
              disabled={batchTargets.length === 0}
              className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] px-2.5 py-1 text-[11.5px] text-[var(--color-text-2)] transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t("reference_batch_generate")}</span>
            </button>
          </>
        )}
      </div>

      {tab === "units" && voiceLegacyNotice.count > 0 && (
        <VoiceLegacyBanner
          message={t("voice_legacy_banner_message", { count: voiceLegacyNotice.count })}
          dismissLabel={t("voice_legacy_banner_dismiss")}
          onDismiss={() => void handleDismissVoiceLegacyNotice()}
        />
      )}

      {error && tab === "units" && (
        <p
          role="alert"
          className="border-b border-[var(--color-hairline-soft)] bg-red-500/10 px-5 py-2 text-xs text-red-400"
        >
          {error}
        </p>
      )}

      {tab === "preproc" ? (
        <div className="min-h-0 flex-1 overflow-auto bg-[oklch(0.18_0.011_250_/_0.25)]">
          <div className="mx-auto w-full max-w-3xl px-6 py-5">
            <ReferenceScriptPlanPreviewPanel
              key={`${projectName}:${episode}`}
              projectName={projectName}
              episode={episode}
              lookup={mentionLookup}
            />
          </div>
        </div>
      ) : (
        <div
          ref={workbenchRef}
          className="relative min-h-0 flex-1 overflow-hidden bg-[oklch(0.18_0.011_250_/_0.25)]"
        >
          <div className="grid h-full min-h-0" style={{ gridTemplateColumns: gridCols }}>
            {/* 左：UnitList / UnitRail */}
            {listMode === "full" ? (
              <UnitList
                units={units}
                selectedId={selectedUnitId}
                onSelect={select}
                onAdd={onAdd}
                dirtyMap={dirtyMap}
                statusMap={statusMap}
              />
            ) : (
              <UnitRail
                units={units}
                selectedId={selectedUnitId}
                onSelect={select}
                onExpand={() => setListFlyoutOpen(true)}
                dirtyMap={dirtyMap}
                statusMap={statusMap}
              />
            )}

            {/* 中：UnitHeader + Editor / Preview（stackPreview 时叠 sub-tab） */}
            <div className="flex min-h-0 flex-col overflow-hidden bg-[radial-gradient(ellipse_at_top,oklch(0.20_0.012_270_/_0.35),oklch(0.17_0.010_265_/_0.2))]">
              {selected ? (
                <>
                  <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-hairline-soft)] px-4 py-2.5">
                    <span
                      translate="no"
                      className="rounded px-2.5 py-1 font-mono text-xs font-bold tracking-wider text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_2px_6px_-2px_var(--color-accent-glow)]"
                    >
                      {selected.unit_id}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-2 py-0.5 text-[11.5px] text-[var(--color-text-2)]">
                      <Clock className="h-3 w-3" aria-hidden="true" />
                      {freeDuration ? (
                        <input
                          type="number"
                          min={1}
                          max={300}
                          step={1}
                          aria-label={t("duration_selector_aria")}
                          value={selectedDurationValue}
                          disabled={isUnitLocked(selected.unit_id)}
                          onChange={(e) =>
                            handleFreeDurationChange(selected.unit_id, e.currentTarget.value)
                          }
                          onBlur={(e) =>
                            commitFreeDuration(selected.unit_id, e.currentTarget.value)
                          }
                          onKeyDown={(e) => {
                            if (e.key === "Enter") e.currentTarget.blur();
                          }}
                          className="focus-ring w-14 bg-transparent font-mono tabular-nums text-[var(--color-text-2)] disabled:cursor-not-allowed disabled:opacity-60"
                        />
                      ) : effectiveDurationOptions && effectiveDurationOptions.length > 0 ? (
                        <select
                          aria-label={t("duration_selector_aria")}
                          value={selected.duration_seconds}
                          disabled={isUnitLocked(selected.unit_id)}
                          title={
                            isUnitLocked(selected.unit_id) ? t("duration_locked_generating") : undefined
                          }
                          onChange={(e) =>
                            void handleDurationChange(selected.unit_id, Number(e.target.value))
                          }
                          className="focus-ring cursor-pointer bg-transparent font-mono tabular-nums text-[var(--color-text-2)] disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {/* 已保存的越界值（换模型后档位收窄）留一项，避免下拉把它静默改写成别的秒数 */}
                          {(effectiveDurationOptions.includes(selected.duration_seconds)
                            ? effectiveDurationOptions
                            : [...effectiveDurationOptions, selected.duration_seconds].sort(
                                (a, b) => a - b,
                              )
                          ).map((seconds) => (
                            <option key={seconds} value={seconds}>
                              {t("duration_seconds_value_text", { value: seconds })}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="font-mono tabular-nums" title={t("duration_no_options")}>
                          {selected.duration_seconds}s
                        </span>
                      )}
                    </span>
                    <span className="flex-1" />
                    {selectedIndex >= 0 && (
                      <span className="font-mono text-[10.5px] tabular-nums text-[var(--color-text-4)]">
                        {selectedIndex + 1} / {units.length}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={goPrev}
                      disabled={selectedIndex <= 0}
                      aria-label={t("reference_unit_prev")}
                      className="focus-ring inline-grid h-6 w-6 place-items-center rounded border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-2)] hover:bg-[oklch(0.26_0.013_265_/_0.7)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={goNext}
                      disabled={selectedIndex < 0 || selectedIndex >= units.length - 1}
                      aria-label={t("reference_unit_next")}
                      className="focus-ring inline-grid h-6 w-6 place-items-center rounded border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-2)] hover:bg-[oklch(0.26_0.013_265_/_0.7)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>

                  {selected.needs_replan && (
                    <p role="alert" className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-300">
                      {t("reference_needs_replan")}
                    </p>
                  )}

                  {stackPreview && (
                    <div
                      role="tablist"
                      aria-label={t("reference_tab_aria")}
                      className="flex items-center gap-0 border-b border-[var(--color-hairline)] bg-[oklch(0.19_0.012_250_/_0.4)] px-5"
                    >
                      <button
                        type="button"
                        role="tab"
                        aria-selected={stackTab === "editor"}
                        onClick={() => setStackTab("editor")}
                        className={`focus-ring relative inline-flex items-center gap-1.5 px-3.5 py-2.5 text-[12.5px] font-medium ${
                          stackTab === "editor"
                            ? "text-[var(--color-text)]"
                            : "text-[var(--color-text-3)]"
                        }`}
                      >
                        <Scissors className="h-3 w-3" aria-hidden="true" />
                        <span>{t("reference_tab_editor")}</span>
                        {isDirty && (
                          <span
                            aria-label={t("reference_tab_dirty_aria")}
                            className="h-1.5 w-1.5 rounded-full bg-amber-400"
                          />
                        )}
                        {stackTab === "editor" && (
                          <span
                            aria-hidden="true"
                            className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
                          />
                        )}
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={stackTab === "preview"}
                        onClick={() => setStackTab("preview")}
                        className={`focus-ring relative inline-flex items-center gap-1.5 px-3.5 py-2.5 text-[12.5px] font-medium ${
                          stackTab === "preview"
                            ? "text-[var(--color-text)]"
                            : "text-[var(--color-text-3)]"
                        }`}
                      >
                        <span>{t("reference_tab_preview")}</span>
                        {statusMap[selected.unit_id] === "running" && (
                          <span className="h-1.5 w-1.5 rounded-full bg-amber-400 motion-safe:animate-pulse" />
                        )}
                        {statusMap[selected.unit_id] === "ready" && (
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        )}
                        {statusMap[selected.unit_id] === "failed" && (
                          <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
                        )}
                        {stackTab === "preview" && (
                          <span
                            aria-hidden="true"
                            className="absolute -bottom-px left-2.5 right-2.5 h-0.5 rounded bg-[var(--color-accent)]"
                          />
                        )}
                      </button>
                    </div>
                  )}

                  <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                    {(!stackPreview || stackTab === "editor") && (
                      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                        <div
                          role="tablist"
                          aria-label={t("reference_editor_view_aria")}
                          className="flex items-center gap-1 px-3 pt-2.5"
                        >
                          {(["script", "parse"] as const).map((view) => (
                            <button
                              key={view}
                              type="button"
                              role="tab"
                              id={`reference-editor-view-tab-${view}`}
                              aria-selected={editorView === view}
                              aria-controls={`reference-editor-view-panel-${view}`}
                              // 未选中的 tab 退出 Tab 序列，左右方向键在两者间移动：
                              // tablist 的键盘约定是「Tab 进出控件组、方向键在组内切换」。
                              tabIndex={editorView === view ? 0 : -1}
                              onKeyDown={(e) => {
                                if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                                e.preventDefault();
                                const next = view === "script" ? "parse" : "script";
                                setEditorView(next);
                                document.getElementById(`reference-editor-view-tab-${next}`)?.focus();
                              }}
                              onClick={() => setEditorView(view)}
                              className={`focus-ring rounded-md border px-2.5 py-1 text-[11.5px] font-medium transition-colors ${
                                editorView === view
                                  ? "border-[var(--color-accent)]/50 bg-[var(--color-accent-soft)] text-[var(--color-text)]"
                                  : "border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-3)] hover:text-[var(--color-text-2)]"
                              }`}
                            >
                              {view === "script"
                                ? t("reference_editor_view_script")
                                : t("reference_editor_view_parse")}
                            </button>
                          ))}
                        </div>
                        {editorView === "script" ? (
                          <div
                            id="reference-editor-view-panel-script"
                            role="tabpanel"
                            aria-labelledby="reference-editor-view-tab-script"
                            className="flex min-h-0 flex-1 flex-col overflow-hidden p-3"
                          >
                            <ReferenceVideoCard
                              key={selected.unit_id}
                              unit={selected}
                              projectName={projectName}
                              episode={episode}
                              value={currentText}
                              onChange={handlePromptChange}
                            />
                          </div>
                        ) : (
                          <div
                            id="reference-editor-view-panel-parse"
                            role="tabpanel"
                            aria-labelledby="reference-editor-view-tab-parse"
                            // 解析预览是只读的，面板内没有可聚焦后代：滚动容器兼作焦点目标，
                            // 键盘用户切到这个 tab 后才能用 PageDown / 方向键读到折线以下的内容
                            // （WAI tabs：tabpanel 无可聚焦内容时自身取 tabindex="0"）。
                            tabIndex={0}
                            className="flex min-h-0 flex-1 flex-col overflow-y-auto"
                          >
                            <ScriptPreviewPanel
                              key={selected.unit_id}
                              projectName={projectName}
                              episode={episode}
                              text={currentText}
                              lookup={mentionLookup}
                            />
                          </div>
                        )}
                        {/* Editor bottom bar */}
                        <div className="flex flex-shrink-0 items-center gap-2 border-t border-[var(--color-hairline-soft)] bg-[oklch(0.18_0.010_265_/_0.5)] px-3.5 py-2">
                          <span
                            className={`inline-flex items-center gap-1.5 text-[11px] ${
                              isDirty ? "text-amber-300" : "text-[var(--color-text-4)]"
                            }`}
                          >
                            {isDirty ? (
                              <>
                                <span
                                  aria-hidden="true"
                                  className="h-1.5 w-1.5 rounded-full bg-amber-400"
                                />
                                {t("reference_unsaved")}
                              </>
                            ) : (
                              <>
                                <span
                                  aria-hidden="true"
                                  className="h-1.5 w-1.5 rounded-full bg-emerald-400"
                                />
                                {t("reference_synced")}
                              </>
                            )}
                          </span>
                          <span className="flex-1" />
                          <button
                            type="button"
                            onClick={() => void handleSave()}
                            disabled={!isDirty || saving}
                            className={`focus-ring inline-flex min-w-[80px] items-center justify-center gap-1.5 rounded-md px-3 py-1 text-xs font-semibold ${
                              isDirty
                                ? "text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))] shadow-[inset_0_1px_0_oklch(1_0_0_/_0.3),0_4px_12px_-4px_var(--color-accent-glow)]"
                                : "border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] text-[var(--color-text-4)]"
                            } disabled:cursor-not-allowed`}
                          >
                            {saving ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                            ) : (
                              <Save className="h-3.5 w-3.5" aria-hidden="true" />
                            )}
                            {saving ? t("common:saving") : t("common:save")}
                          </button>
                        </div>
                      </div>
                    )}
                    {stackPreview && stackTab === "preview" && (
                      <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
                        <UnitPreviewPanel
                          unit={selected}
                          projectName={projectName}
                          status={statusMap[selected.unit_id]}
                          errorMessage={failureMessage}
                          busy={selectedBusy}
                          cancelling={selectedCancelling}
                          estimatedCost={displayedEstimatedCost}
                          actualCost={actualCost}
                          narrationText={selectedNarrationText}
                          narrationGenerating={ttsBusyUnitIds.has(selected.unit_id)}
                          narrationEstimatedCost={narrationEstimatedCost}
                          onGenerateNarration={onGenerateNarrationVoid}
                          onGenerate={onGenerateVoid}
                          generationBlocked={Boolean(selected.needs_replan)}
                          onUploadVideo={handleUploadVideo}
                          uploadingVideo={uploading.ids.has(selected.unit_id)}
                          restoring={restoring.ids.has(selected.unit_id)}
                          onRestoringChange={handleRestoringChange}
                          checkBusy={isUnitLocked}
                          onRestored={handleUnitsRefresh}
                        />
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="flex flex-1 items-center justify-center text-xs text-[var(--color-text-4)]">
                  {t("reference_canvas_empty")}
                </div>
              )}
            </div>

            {/* 右：UnitPreviewPanel（仅大屏） */}
            {!stackPreview && (
              <div className="flex min-h-0 flex-col overflow-hidden border-l border-[var(--color-hairline)] bg-[linear-gradient(180deg,oklch(0.19_0.011_265_/_0.5),oklch(0.17_0.010_265_/_0.35))]">
                <UnitPreviewPanel
                  unit={selected}
                  projectName={projectName}
                  status={selected ? statusMap[selected.unit_id] : undefined}
                  errorMessage={failureMessage}
                  busy={selectedBusy}
                  cancelling={selectedCancelling}
                  estimatedCost={displayedEstimatedCost}
                  actualCost={actualCost}
                  narrationText={selectedNarrationText}
                  narrationGenerating={selected ? ttsBusyUnitIds.has(selected.unit_id) : false}
                  narrationEstimatedCost={narrationEstimatedCost}
                  onGenerateNarration={onGenerateNarrationVoid}
                  onGenerate={onGenerateVoid}
                  generationBlocked={Boolean(selected?.needs_replan)}
                  onUploadVideo={handleUploadVideo}
                  uploadingVideo={selected ? uploading.ids.has(selected.unit_id) : false}
                  restoring={selected ? restoring.ids.has(selected.unit_id) : false}
                  onRestoringChange={handleRestoringChange}
                  checkBusy={isUnitLocked}
                  onRestored={handleUnitsRefresh}
                />
              </div>
            )}
          </div>

          {/* 折叠态下的展开抽屉 */}
          {listFlyoutOpen && (
            <>
              <button
                type="button"
                aria-label={t("common:close")}
                onClick={() => setListFlyoutOpen(false)}
                className="absolute inset-0 z-30 bg-black/40 backdrop-blur-[2px]"
              />
              <div
                className="absolute bottom-0 left-0 top-0 z-40 w-[320px] shadow-[8px_0_24px_-8px_oklch(0_0_0_/_0.6)]"
              >
                <UnitList
                  units={units}
                  selectedId={selectedUnitId}
                  onSelect={(id) => {
                    select(id);
                    setListFlyoutOpen(false);
                  }}
                  onAdd={onAdd}
                  dirtyMap={dirtyMap}
                  statusMap={statusMap}
                />
              </div>
            </>
          )}
        </div>
      )}

      <ReferenceDurationConfirmDialog {...durationGate.dialogProps} />
      <ReferenceBatchAdmissionDialog
        admission={batchAdmission}
        onConfirm={handleBatchConfirm}
        onClose={() => setBatchAdmission(null)}
      />
    </div>
  );
}
