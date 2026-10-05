import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import { useEditUnit, type EditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { RetainedEditUnit, useRetainWhile } from "@/components/shared/edit-unit/RetainedEditUnit";
import { useConfirmLeave, useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useStaysInEpisodeView } from "@/components/canvas/episode-page/EpisodeViewScope";
import { UnitList } from "./UnitList";
import { UnitRail } from "./UnitRail";
import { UnitPreviewPanel } from "./UnitPreviewPanel";
import { ReferenceVideoCard } from "./ReferenceVideoCard";
import { ScriptPreviewPanel } from "./ScriptPreviewPanel";
import { STATUS_CONF, deriveUnitStatus } from "./unit-status";
import { tierProblemText } from "./unit-tier-problem";
import { ReferenceSplitAlert } from "./ReferenceSplitAlert";
import { ReferenceDurationConfirmDialog } from "./ReferenceDurationConfirmDialog";
import { ReferenceBatchAdmissionDialog } from "./ReferenceBatchAdmissionDialog";
import { referenceBatchOutcome } from "./batch-outcome";
import { AdScriptButton, AdScriptProgress } from "@/components/canvas/shared/AdScriptDialog";
import { BatchFillButton, useBatchGap } from "@/components/canvas/episode-page/BatchFillButton";
import { EpisodeHeaderActions } from "@/components/canvas/episode-page/EpisodeHeaderActions";
import type { EpisodeCanvasContext } from "@/components/canvas/episode-page/EpisodePage";
import { NoScriptBlankState } from "@/components/canvas/shared/StartBlankScriptButton";
import { computeVoiceLegacyNotice, VoiceLegacyBanner } from "./VoiceLegacyBanner";
import { useReferenceDurationGate } from "@/hooks/useReferenceDurationGate";
import { ReferenceScriptPlanPreviewPanel } from "@/components/canvas/reference/ReferenceScriptPlanPreviewPanel";
import { PromptAuthoringDraftPanel, usePromptAuthoringDraft } from "./PromptAuthoringDraftPanel";
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
import { PromptAuthoringButton } from "@/components/canvas/shared/PromptAuthoringButton";
import {
  buildMentionLookup,
  lineSpeechMarks,
  splitScriptLines,
} from "@/utils/reference-mentions";
import type {
  ReferenceBatchAdmission,
  ReferenceUnitCapabilityMap,
  ReferenceVideoUnit,
  UnitStatus,
} from "@/types";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { stepAnchor } from "@/utils/move-anchor";

export interface ReferenceVideoCanvasProps extends EpisodeCanvasContext {
  projectName: string;
  episode: number;
  /** prompt_authoring 剧本（scripts/episode_N.json）是否已生成：没有时单元列表无脚本可读，不拉取。 */
  hasScript?: boolean;
  /** ad 参考生视频一阶段产出，不展示 script_plan 脚本规划页。 */
  showPreprocess?: boolean;
  /** unit 时长为自由正整数，不用供应商档位作为编排限制。 */
  freeDuration?: boolean;
  /**
   * 画布根部的能力请求明确答复视频模型无法解析（400/422）。逐单元的桶、档位与端点固定
   * 标志不从这里来：它们随单元列表由服务端按可用参考图逐单元给出（`unitCapabilitiesByEpisode`）。
   */
  videoModelUnresolved?: boolean;
  /** 剧本规划档位（能力端点的 `duration_constraints.planning`）：内容确认页上端点固定的单元按它选时长、判越档。 */
  planDurationOptions?: number[];
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);
const EMPTY_CAPABILITIES: ReferenceUnitCapabilityMap = Object.freeze({});

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

/**
 * 选中单元的正文编辑单元。按 unit_id 作 key 挂载：切换单元前由离开拦截询问，
 * 外部移除时由 RetainedEditUnit 保留可见编辑器，放弃或保存后才跟随真实列表。
 */
function UnitPromptEdit({
  unit,
  onSave,
  allowNavigation,
  children,
}: {
  unit: ReferenceVideoUnit;
  onSave: (unitId: string, prompt: string) => Promise<string>;
  allowNavigation: (to: string) => boolean;
  children: (edit: EditUnit<string>) => ReactNode;
}) {
  const { t } = useTranslation("dashboard");
  const unitId = unit.unit_id;
  const save = useCallback((prompt: string) => onSave(unitId, prompt), [onSave, unitId]);
  const edit = useEditUnit({
    source: unit.text,
    save,
    allowNavigation,
    leaveTitle: t("reference_unit_leave_title", { id: itemIdWithinEpisode(unitId) }),
  });
  return <>{children(edit)}</>;
}

/** 自由时长的合法范围（秒），与服务端校验一致。 */
const FREE_DURATION_MAX = 300;

/**
 * 自由时长输入：失焦或回车时提交一次，中间输入的数字不落盘。
 * 尚未提交的值登记到离开拦截，并与正文一起参与外部移除保留；非法值在提交时回到已保存的时长。
 */
function FreeDurationInput({
  unit,
  disabled,
  externalChangeShown,
  onCommit,
}: {
  unit: ReferenceVideoUnit;
  disabled: boolean;
  /** 正文的提示条已在说明外部移除，这里不再重复。 */
  externalChangeShown: boolean;
  onCommit: (seconds: number) => Promise<boolean>;
}) {
  const { t } = useTranslation("dashboard");
  const [draft, setDraft] = useState<string | null>(null);
  // 失焦触发的提交在途：离开拦截据此等它落定再判断，避免「放弃修改」放走已发出的提交或「保存」重复提交
  const [committing, setCommitting] = useState(false);
  const seconds = Number(draft);
  const valid = Number.isInteger(seconds) && seconds >= 1 && seconds <= FREE_DURATION_MAX;
  // 与已保存值相同的显式提交也有意义：它确认了只差时长的重新规划标记
  const dirty =
    draft !== null && (!valid || seconds !== unit.duration_seconds || Boolean(unit.needs_replan));

  const discard = useCallback(() => setDraft(null), []);
  const commit = useCallback(async () => {
    if (draft === null) return true;
    if (!dirty || !valid) {
      setDraft(null);
      return true;
    }
    setCommitting(true);
    try {
      const saved = await onCommit(seconds);
      // 提交期间又改过的值留着，等下一次提交
      if (saved) setDraft((current) => (current === draft ? null : current));
      return saved;
    } finally {
      setCommitting(false);
    }
  }, [draft, dirty, valid, seconds, onCommit]);

  const discarding = useLeaveGuard({ dirty, saving: committing, save: commit, discard });
  const retention = useRetainWhile((dirty || committing) && !discarding);
  const notice = dirty && !externalChangeShown ? retention?.message : undefined;

  return (
    <>
      <Input
        type="number"
        min={1}
        max={FREE_DURATION_MAX}
        step={1}
        aria-label={t("duration_selector_aria")}
        value={draft ?? String(unit.duration_seconds)}
        disabled={disabled}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        className="h-7 w-20"
      />
      {notice ? <span role="status" className="text-subtle-foreground">{notice}</span> : null}
    </>
  );
}

/** 单元头部的图标按钮：可访问名称同时作为悬停提示。 */
function HeaderIconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Button variant="ghost" size="icon-sm" aria-label={label} disabled={disabled} onClick={onClick} />}
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

export function ReferenceVideoCanvas({
  projectName,
  episode,
  view,
  onViewChange,
  hasScript = true,
  showPreprocess = true,
  freeDuration = false,
  videoModelUnresolved,
  planDurationOptions,
}: ReferenceVideoCanvasProps) {
  const { t } = useTranslation("dashboard");

  const loadUnits = useReferenceVideoStore((s) => s.loadUnits);
  const addUnit = useReferenceVideoStore((s) => s.addUnit);
  const patchUnit = useReferenceVideoStore((s) => s.patchUnit);
  const deleteUnit = useReferenceVideoStore((s) => s.deleteUnit);
  const moveUnit = useReferenceVideoStore((s) => s.moveUnit);
  const select = useReferenceVideoStore((s) => s.select);

  const units =
    useReferenceVideoStore((s) => s.unitsByEpisode[referenceVideoCacheKey(projectName, episode)]) ??
    (EMPTY_UNITS as ReferenceVideoUnit[]);
  const unitCapabilities =
    useReferenceVideoStore(
      (s) => s.unitCapabilitiesByEpisode[referenceVideoCacheKey(projectName, episode)],
    ) ?? EMPTY_CAPABILITIES;
  const selectedUnitId = useReferenceVideoStore((s) => s.selectedUnitId);
  const error = useReferenceVideoStore((s) => s.error);
  const loading = useReferenceVideoStore((s) => s.loading);
  const project = useProjectsStore((s) => s.currentProjectData);
  // schema v6 起各 bucket 共用名称空间，每个名字只会声明一次。
  const mentionLookup = useMemo(() => buildMentionLookup(project), [project]);
  // 提示词编写草稿：待修复草稿在视频单元页取代工作台呈现，Agent 的可编辑草稿只在工作台上方提示。
  const { view: promptDraft, refresh: refreshPromptDraft } = usePromptAuthoringDraft(projectName, episode);

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

  // 单元落哪个桶、可选哪些档位，由服务端按此刻可用的参考图逐单元判定（与执行侧
  // ReferenceUnitRequestProjector 同一判据），随单元列表与写入响应到达；画布不按正文里
  // 「名字已登记」自判——登记了资产却缺图的引用不算带图，执行会落 i2v，画布就按 i2v 取档。
  // 结论尚未到达时控件降级为只读，不编造档位。
  const selectedCapability = selected ? (unitCapabilities[selected.unit_id] ?? null) : null;
  const effectiveDurationOptions = selectedCapability?.allowed_durations ?? undefined;
  const selectedDurationEndpointFixed = selectedCapability?.duration_endpoint_fixed ?? false;
  const selectedTierProblem =
    selectedCapability?.problem != null
      ? tierProblemText(t, selectedCapability.problem, selectedCapability.hydrated_capability)
      : null;
  const selectedSplit = selectedCapability != null && selectedCapability.problems.length > 0 ? selectedCapability : null;

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
  // 存在这里而非 UnitPreviewPanel 内：该面板随选中项切换复用，面板内的单个布尔量会把 A 的
  // 占用态串到 B 上；没有选中项时它也会卸载，在途请求不会因此取消。
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
        hasClip: Boolean(u.generated_assets?.video_clip),
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

  const failureMessage = useMemo(() => {
    if (!selected) return null;
    if (statusMap[selected.unit_id] !== "failed") return null;
    return tasksByUnit.get(selected.unit_id)?.error_message ?? null;
  }, [selected, statusMap, tasksByUnit]);

  // 切换选中单元会卸载当前单元的编辑单元：有未保存的正文或时长时先询问
  const confirmLeave = useConfirmLeave();
  const selectUnit = useCallback(
    (unitId: string) => {
      confirmLeave(() => select(unitId), { saveLabel: t("common:save_and_switch") });
    },
    [confirmLeave, select, t],
  );

  // afterUnitId 缺省时追加到末尾；新单元不继承同号旧单元的产物与版本历史。
  // store 新增后选中新单元，同样先经离开拦截。
  const handleAdd = useCallback(
    (afterUnitId?: string) => {
      confirmLeave(
        () => {
          void addUnit(projectName, episode, {
            prompt: "",
            ...(afterUnitId !== undefined ? { after_unit_id: afterUnitId } : {}),
          }).catch((e: unknown) => toastError(e));
        },
        { saveLabel: t("common:save_and_switch") },
      );
    },
    [confirmLeave, addUnit, projectName, episode, t],
  );

  // 改序不弹确认；请求在途时丢弃后续操作，避免基于过期顺序计算锚点。
  const [movingUnit, setMovingUnit] = useState(false);
  const handleMove = useCallback(async (unitId: string, afterUnitId: string | null) => {
    if (movingUnit) return;
    setMovingUnit(true);
    try {
      await moveUnit(projectName, episode, unitId, afterUnitId);
    } catch (e) {
      toastError(e);
    } finally {
      setMovingUnit(false);
    }
  }, [moveUnit, projectName, episode, movingUnit]);

  // 移除比其他写入多挡一类占用：在跑的配音任务同样指向该单元（与时间线分镜的移除守卫一致）。
  const isUnitRemovalBlocked = useCallback(
    (unitId: string) => isUnitLocked(unitId) || ttsBusyUnitIds.has(unitId),
    [isUnitLocked, ttsBusyUnitIds],
  );
  const [removeUnitId, setRemoveUnitId] = useState<string | null>(null);
  const [removingUnit, setRemovingUnit] = useState(false);
  const openRemoveDialog = useCallback(
    (unitId: string) => {
      // 渲染期的禁用态未必最新，打开确认前再复核一次占用
      if (isUnitRemovalBlocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      setRemoveUnitId(unitId);
    },
    [isUnitRemovalBlocked, t],
  );
  // 返回是否移除成功：离开拦截据此决定是否丢弃未保存修改
  const handleRemoveUnit = useCallback(async () => {
    if (!removeUnitId || removingUnit) return false;
    // 询问未保存修改期间单元可能已被占用，提交时刻再复核
    if (isUnitRemovalBlocked(removeUnitId)) {
      useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
      return false;
    }
    setRemovingUnit(true);
    try {
      await deleteUnit(projectName, episode, removeUnitId);
      setRemoveUnitId(null);
      return true;
    } catch (e) {
      toastError(e);
      return false;
    } finally {
      setRemovingUnit(false);
    }
  }, [deleteUnit, projectName, episode, removeUnitId, removingUnit, isUnitRemovalBlocked, t]);

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
   * 去重只看 queued/running，确认弹窗停留期间完成的单元若原样提交，会再跑一次
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

  const durationGate = useReferenceDurationGate({ projectName, episode });
  /** 整批准入判定的未决结论（需确认 / 受阻）；admitted 由 toast 反馈，不进这里。 */
  const [batchAdmission, setBatchAdmission] = useState<ReferenceBatchAdmission | null>(null);

  const enqueue = useCallback(
    async (unitId: string, confirmedRequestDuration: number | undefined) => {
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
        await enqueueReferenceVideoUnit(
          projectName,
          episode,
          unitId,
          confirmedRequestDuration == null ? {} : { confirmed_request_duration_seconds: confirmedRequestDuration },
        );
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
    (canEnqueue: (unitId: string) => boolean) =>
      async (unitIds: string[], confirmedDurations: ReadonlyMap<string, number>) => {
      for (const id of unitIds) {
        if (!canEnqueue(id)) continue;
        await enqueue(id, confirmedDurations.get(id));
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
        makeEnqueueSerially(canEnqueueUnit),
        canEnqueueUnit,
      );
    },
    [
      durationGate,
      makeEnqueueSerially,
      isUnitLocked,
      isUnitGenerationBlocked,
      canEnqueueUnit,
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
  // 后期配音项目不生成旁白配音：收起生成入口，已有配音照常试听。
  const onGenerateNarration = project?.narration_delivery === "use_tts" ? onGenerateNarrationVoid : undefined;

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
          ...(confirmedDurations ? { confirmed_request_durations: confirmedDurations } : {}),
        });
        setBatchAdmission(referenceBatchOutcome(admission) === "queued" ? null : admission);
      } catch (e) {
        setBatchAdmission(null);
        toastError(e, (msg) => t("reference_batch_request_failed", { error: msg }));
      }
    },
    [projectName, episode, t],
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

  const onAdd = useCallback(() => handleAdd(), [handleAdd]);

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

  const onGenerateVoid = useCallback((id: string) => void handleGenerate(id), [handleGenerate]);

  const handlePromptSave = useCallback(
    async (unitId: string, prompt: string) => {
      const saved = await patchUnit(projectName, episode, unitId, { prompt });
      return saved.text;
    },
    [patchUnit, projectName, episode],
  );
  const allowNavigation = useStaysInEpisodeView();

  // 编辑器列内的两种视图：写文稿 / 看解析结果。解析预览是只读派生视图，与正文同一份
  // 文本，故共用编辑器列的空间而非再占一栏（右栏留给成片预览）。
  const [editorView, setEditorView] = useState<"script" | "parse">("script");

  const videoGap = useBatchGap(projectName, episode, "videos", "reference_video");

  // 通知回跳：收到 reference_unit scroll target 时切到视频单元视图并选中对应 unit
  // （镜像 ShotSplitView 的选择式回跳）。units 异步加载，靠依赖变化重试到命中或过期。
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  useEffect(() => {
    if (scrollTarget?.type !== "reference_unit") return;
    const requestId = scrollTarget.request_id;
    if (units.some((u) => u.unit_id === scrollTarget.id)) {
      const unitId = scrollTarget.id;
      // 应用内链接要求打开该单元的预览时，窄屏下把预览子页签切到前台。
      const start = useAppStore.getState().playbackStart;
      const openPreview = start?.resource_type === "reference_videos" && start.resource_id === unitId;
      // 切视图与选中合成一次离开拦截：分两次请求时，拦截只保留后一次，放行后会停在原视图
      const focusUnit = () => {
        onViewChange("board", { replace: true });
        select(unitId);
        if (openPreview) setStackTab("preview");
      };
      // 已在视频单元视图且就是当前单元：不卸载任何编辑单元，不必询问
      if (view === "board" && unitId === selectedUnitId) focusUnit();
      else confirmLeave(focusUnit, { saveLabel: t("common:save_and_switch") });
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
  }, [scrollTarget, units, loading, view, selectedUnitId, select, clearScrollTarget, onViewChange, confirmLeave, t]);

  const [listSheetOpen, setListSheetOpen] = useState(false);

  const segCost = useCostStore((s) =>
    selected ? s._segmentIndex.get(selected.unit_id) : undefined,
  );
  const estimatedCost = segCost?.estimate.video;
  const actualCost = segCost?.actual.video;
  const narrationEstimatedCost = segCost?.estimate.audio;
  const selectedNarrationText = unitNarrationText(selected);

  const selectedIndex = selected ? units.findIndex((u) => u.unit_id === selected.unit_id) : -1;
  const goPrev = useCallback(() => {
    if (selectedIndex <= 0) return;
    selectUnit(units[selectedIndex - 1].unit_id);
  }, [selectUnit, units, selectedIndex]);
  const goNext = useCallback(() => {
    if (selectedIndex < 0 || selectedIndex >= units.length - 1) return;
    selectUnit(units[selectedIndex + 1].unit_id);
  }, [selectUnit, units, selectedIndex]);
  const moveStep = useCallback(
    (direction: "earlier" | "later") => {
      const afterId = stepAnchor(units.map((u) => u.unit_id), selectedIndex, direction);
      if (afterId !== undefined) void handleMove(units[selectedIndex].unit_id, afterId);
    },
    [handleMove, units, selectedIndex],
  );

  const unitHeaderLocked = selected ? isUnitLocked(selected.unit_id) : false;
  const notices =
    view === "board" &&
    (voiceLegacyNotice.count > 0 ||
      promptDraft?.editable_by === "agent" ||
      (hasScript && !showPreprocess) ||
      Boolean(error));

  const renderDurationControl = (unit: ReferenceVideoUnit, edit: EditUnit<string>) => {
    if (freeDuration && !selectedDurationEndpointFixed) {
      return (
        <FreeDurationInput
          key={unit.unit_id}
          unit={unit}
          disabled={unitHeaderLocked}
          externalChangeShown={edit.dirty}
          onCommit={(seconds) => handleDurationChange(unit.unit_id, seconds)}
        />
      );
    }
    if (selectedTierProblem) {
      return <span className="font-medium text-warn">{selectedTierProblem.label}</span>;
    }
    if (!selectedDurationEndpointFixed && effectiveDurationOptions && effectiveDurationOptions.length > 0) {
      // 已保存的越界值（换模型后档位收窄）留一项，避免下拉把它静默改写成别的秒数
      const options = effectiveDurationOptions.includes(unit.duration_seconds)
        ? effectiveDurationOptions
        : [...effectiveDurationOptions, unit.duration_seconds].sort((a, b) => a - b);
      return (
        <Select
          value={unit.duration_seconds}
          disabled={unitHeaderLocked}
          onValueChange={(seconds: number | null) => {
            if (seconds !== null) void handleDurationChange(unit.unit_id, seconds);
          }}
        >
          <SelectTrigger size="sm" aria-label={t("duration_selector_aria")}>
            <SelectValue>{(seconds: number) => t("duration_seconds_value_text", { value: seconds })}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {options.map((seconds) => (
              <SelectItem key={seconds} value={seconds}>
                {t("duration_seconds_value_text", { value: seconds })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    }
    return (
      <span className="font-mono tabular-nums">
        {t("duration_seconds_value_text", { value: unit.duration_seconds })}
        <span className="sr-only">
          {t(selectedDurationEndpointFixed ? "duration_not_driven_notice" : "duration_no_options")}
        </span>
      </span>
    );
  };

  const renderPreview = (edit: EditUnit<string> | null) => (
    <UnitPreviewPanel
      unit={selected}
      projectName={projectName}
      status={selected ? statusMap[selected.unit_id] : undefined}
      errorMessage={failureMessage}
      busy={selectedBusy}
      estimatedCost={estimatedCost}
      actualCost={actualCost}
      narrationText={selectedNarrationText}
      narrationGenerating={selected ? ttsBusyUnitIds.has(selected.unit_id) : false}
      narrationEstimatedCost={narrationEstimatedCost}
      onGenerateNarration={onGenerateNarration && edit ? (id) => void edit.saveAndGenerate(() => onGenerateNarration(id)) : onGenerateNarration}
      // 正文有未保存修改时先保存再生成，生成用的是服务端上的正文
      onGenerate={edit ? (id) => void edit.saveAndGenerate(() => handleGenerate(id)) : onGenerateVoid}
      saveFirst={edit?.dirty}
      saving={edit?.status === "saving"}
      generationBlocked={Boolean(selected?.needs_replan)}
      onUploadVideo={handleUploadVideo}
      uploadingVideo={selected ? uploading.ids.has(selected.unit_id) : false}
      restoring={selected ? restoring.ids.has(selected.unit_id) : false}
      onRestoringChange={handleRestoringChange}
      checkBusy={isUnitLocked}
      onRestored={handleUnitsRefresh}
    />
  );

  const renderSelectedUnit = (unit: ReferenceVideoUnit, edit: EditUnit<string>) => {
    const status = statusMap[unit.unit_id];
    return (
      <>
        {/* 中栏上方：单元头部、单元提示与窄屏下的编辑 / 预览切换 */}
        <div className="col-start-2 row-start-1 flex min-w-0 flex-col border-b border-border">
          <div className="flex flex-wrap items-center gap-2 px-4 py-2">
            <span
              translate="no"
              className="rounded-sm bg-primary px-2 py-0.5 font-mono text-xs font-semibold text-primary-foreground"
            >
              {itemIdWithinEpisode(unit.unit_id)}
            </span>
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock aria-hidden className="size-3.5" />
              {renderDurationControl(unit, edit)}
            </span>
            {selectedTierProblem && (
              <span role="alert" className="text-xs text-subtle-foreground">
                {selectedTierProblem.hint}
              </span>
            )}
            <span className="flex-1" />
            {selectedIndex >= 0 && (
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {selectedIndex + 1} / {units.length}
              </span>
            )}
            <div className="flex items-center">
              <HeaderIconButton
                label={t("reference_unit_move_earlier")}
                disabled={movingUnit || selectedIndex <= 0}
                onClick={() => moveStep("earlier")}
              >
                <ChevronUp aria-hidden />
              </HeaderIconButton>
              <HeaderIconButton
                label={t("reference_unit_move_later")}
                disabled={movingUnit || selectedIndex < 0 || selectedIndex >= units.length - 1}
                onClick={() => moveStep("later")}
              >
                <ChevronDown aria-hidden />
              </HeaderIconButton>
              <HeaderIconButton label={t("reference_unit_prev")} disabled={selectedIndex <= 0} onClick={goPrev}>
                <ChevronLeft aria-hidden />
              </HeaderIconButton>
              <HeaderIconButton
                label={t("reference_unit_next")}
                disabled={selectedIndex < 0 || selectedIndex >= units.length - 1}
                onClick={goNext}
              >
                <ChevronRight aria-hidden />
              </HeaderIconButton>
              <HeaderIconButton label={t("reference_unit_insert_after")} onClick={() => handleAdd(unit.unit_id)}>
                <Plus aria-hidden />
              </HeaderIconButton>
              <HeaderIconButton
                label={t("reference_unit_remove")}
                disabled={isUnitRemovalBlocked(unit.unit_id)}
                onClick={() => openRemoveDialog(unit.unit_id)}
              >
                <Trash2 aria-hidden />
              </HeaderIconButton>
            </div>
          </div>

          {unit.needs_replan && (
            <p
              role="alert"
              className="flex items-start gap-2 border-t border-warn/30 bg-warn/10 px-4 py-2 text-xs text-subtle-foreground"
            >
              <AlertTriangle aria-hidden className="mt-px size-3.5 shrink-0 text-warn" />
              {t("reference_needs_replan")}
            </p>
          )}

          {selectedSplit && (
            <ReferenceSplitAlert
              capability={selectedSplit}
              className="border-t border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive"
            />
          )}

          <Tabs
            value={stackTab}
            onValueChange={(next: "editor" | "preview") => setStackTab(next)}
            className="mx-4 mb-2 @4xl/canvas:hidden"
          >
            <TabsList aria-label={t("reference_tab_aria")}>
              <TabsTrigger value="editor">
                {t("reference_tab_editor")}
                {edit.dirty && (
                  <>
                    <span aria-hidden className="size-1.5 rounded-full bg-warn" />
                    <span className="sr-only">{t("reference_tab_dirty_aria")}</span>
                  </>
                )}
              </TabsTrigger>
              <TabsTrigger value="preview">
                {t("reference_tab_preview")}
                {status && status !== "pending" && (
                  <span aria-hidden className={`size-1.5 rounded-full ${STATUS_CONF[status].dotClass}`} />
                )}
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        {/* 中栏：文稿编辑器与解析预览。窄屏下与预览栏占同一格，由上方的切换决定显示哪一个 */}
        <div
          data-inactive={stackTab !== "editor" || undefined}
          className="col-start-2 row-start-2 flex min-h-0 min-w-0 flex-col data-inactive:hidden @4xl/canvas:data-inactive:flex"
        >
          <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
          <Tabs
            value={editorView}
            onValueChange={(next: "script" | "parse") => setEditorView(next)}
            className="flex-1"
          >
            <TabsList aria-label={t("reference_editor_view_aria")}>
              <TabsTrigger value="script">{t("reference_editor_view_script")}</TabsTrigger>
              <TabsTrigger value="parse">{t("reference_editor_view_parse")}</TabsTrigger>
            </TabsList>
            <TabsContent value="script" className="flex flex-col">
              <ReferenceVideoCard
                unit={unit}
                projectName={projectName}
                episode={episode}
                value={edit.value}
                onChange={edit.setValue}
                beforePreview={edit.save}
                dirty={edit.dirty}
                saving={edit.status === "saving"}
              />
            </TabsContent>
            <TabsContent value="parse">
              <ScriptPreviewPanel
                projectName={projectName}
                episode={episode}
                text={edit.value}
                lookup={mentionLookup}
              />
            </TabsContent>
          </Tabs>
          </div>
          <UnsavedChangesBar unit={edit} className="mx-3 mb-3 shrink-0" />
        </div>

        {/* 右栏：成片预览。窄屏下叠进中栏，由编辑 / 预览切换显示 */}
        <div
          data-inactive={stackTab !== "preview" || undefined}
          className="col-start-2 row-start-2 flex min-h-0 min-w-0 flex-col border-border data-inactive:hidden @4xl/canvas:col-start-3 @4xl/canvas:row-start-1 @4xl/canvas:row-end-3 @4xl/canvas:border-l @4xl/canvas:data-inactive:flex"
        >
          {renderPreview(edit)}
        </div>
      </>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {view === "board" && (
        <EpisodeHeaderActions>
          {/* 没有预处理的参考画布只用于广告/短片：有正式脚本时可整份重新生成。 */}
          {hasScript && !showPreprocess && (
            <AdScriptButton projectName={projectName} episode={episode} regenerate />
          )}
          {hasScript && (
            <PromptAuthoringButton
              projectName={projectName}
              episode={episode}
              scope={selectedUnitId ? "current" : "pending"}
              currentEntryId={selectedUnitId}
            />
          )}
          <BatchFillButton
            kind="videos"
            count={videoGap}
            units
            disabled={batchTargets.length === 0}
            onClick={() => void handleBatchGenerate()}
          />
        </EpisodeHeaderActions>
      )}

      {/* 工作台上方的提示：限高并自带滚动，提示再多也不把编辑器与预览挤到高度下限以下 */}
      {notices && (
        <section
          aria-label={t("reference_notices_aria")}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 提示超出限高时只能在这里滚动，键盘须能聚焦
          tabIndex={0}
          className="focus-ring relative max-h-[30dvh] shrink-0 overflow-y-auto border-b border-border"
        >
          {voiceLegacyNotice.count > 0 && (
            <VoiceLegacyBanner
              message={t("voice_legacy_banner_message", { count: voiceLegacyNotice.count })}
              dismissLabel={t("voice_legacy_banner_dismiss")}
              onDismiss={() => void handleDismissVoiceLegacyNotice()}
            />
          )}

          {promptDraft?.editable_by === "agent" && (
            <div className="px-5 py-2">
              <PromptAuthoringDraftPanel
                key={`${projectName}:${episode}`}
                projectName={projectName}
                episode={episode}
                view={promptDraft}
                onSettled={refreshPromptDraft}
              />
            </div>
          )}

          {hasScript && !showPreprocess && (
            <AdScriptProgress projectName={projectName} episode={episode} noScript={false} className="mx-5 my-2" />
          )}

          {error && (
            <p role="alert" className="bg-destructive/10 px-5 py-2 text-xs text-destructive">
              {error}
            </p>
          )}
        </section>
      )}

      <RetainedEditUnit identity={promptDraft?.editable_by === "user" ? "draft" : "workbench"} message={t("reference_prompt_draft_replaced")} value={view === "plan" ? (
        <div className="relative min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-6 py-5">
            <ReferenceScriptPlanPreviewPanel
              key={`${projectName}:${episode}`}
              projectName={projectName}
              episode={episode}
              lookup={mentionLookup}
              videoModelUnresolved={videoModelUnresolved}
              planningDurations={planDurationOptions}
              onOpenTimeline={() => onViewChange("board")}
            />
          </div>
        </div>
      ) : promptDraft?.editable_by === "user" ? (
        <div className="relative min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-6 py-5">
            <PromptAuthoringDraftPanel
              key={`${projectName}:${episode}`}
              projectName={projectName}
              episode={episode}
              view={promptDraft}
              onSettled={refreshPromptDraft}
            />
          </div>
        </div>
      ) : (
        // 工作台按画布宽度分档，列宽固定：
        //   窄于 4xl：单元图标栏 + 中栏，预览叠进中栏；4xl 起预览单独成栏；
        //   6xl 起图标栏换成完整列表；7xl 起预览栏加宽。
        <div className="grid min-h-0 flex-1 grid-cols-[3.5rem_minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] @4xl/canvas:grid-cols-[3.5rem_minmax(0,1fr)_20rem] @6xl/canvas:grid-cols-[20rem_minmax(0,1fr)_20rem] @7xl/canvas:grid-cols-[20rem_minmax(0,1fr)_22.5rem]">
          <div className="col-start-1 row-start-1 row-end-3 flex min-h-0 @6xl/canvas:hidden">
            <UnitRail
              units={units}
              selectedId={selectedUnitId}
              onSelect={selectUnit}
              onExpand={() => setListSheetOpen(true)}
              statusMap={statusMap}
              className="min-w-0 flex-1"
            />
          </div>
          <div className="col-start-1 row-start-1 row-end-3 hidden min-h-0 @6xl/canvas:flex">
            <UnitList
              units={units}
              selectedId={selectedUnitId}
              onSelect={selectUnit}
              onAdd={onAdd}
              onMove={handleMove}
              statusMap={statusMap}
              className="min-w-0 flex-1"
            />
          </div>

          <RetainedEditUnit identity={selected?.unit_id ?? "missing"} value={selected ? (
            <UnitPromptEdit
              key={selected.unit_id}
              unit={selected}
              onSave={handlePromptSave}
              allowNavigation={allowNavigation}
            >
              {(edit) => renderSelectedUnit(selected, edit)}
            </UnitPromptEdit>
          ) : (
            <>
              <div className="col-start-2 row-start-1 row-end-3 flex min-h-0 flex-col">
                {!hasScript && !showPreprocess ? (
                  // 广告/短片没有脚本规划：没有正式脚本时直接从空白开始。
                  <NoScriptBlankState projectName={projectName} episode={episode} className="flex-1 text-xs" />
                ) : hasScript && units.length === 0 ? (
                  <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-sm text-muted-foreground">
                    <p>{t("reference_canvas_empty")}</p>
                    <Button onClick={onAdd}>
                      <Plus aria-hidden data-icon="inline-start" />
                      {t("reference_unit_add_first")}
                    </Button>
                  </div>
                ) : (
                  <p className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
                    {t("reference_canvas_empty")}
                  </p>
                )}
              </div>
              <div className="col-start-3 row-start-1 row-end-3 hidden min-h-0 flex-col border-l border-border @4xl/canvas:flex">
                {renderPreview(null)}
              </div>
            </>
          )} message={t("reference_unit_externally_removed")}>
            {(detail) => detail}
          </RetainedEditUnit>
        </div>
      )}>
        {(content) => content}
      </RetainedEditUnit>

      {/* 图标栏展开的完整列表：搜索、新增与排序 */}
      <Sheet open={listSheetOpen} onOpenChange={setListSheetOpen}>
        <SheetContent
          side="left"
          showCloseButton={false}
          className="data-[side=left]:w-80"
        >
          <SheetTitle className="sr-only">{t("reference_unit_list_title")}</SheetTitle>
          <UnitList
            units={units}
            selectedId={selectedUnitId}
            onSelect={(id) => {
              setListSheetOpen(false);
              selectUnit(id);
            }}
            onAdd={() => {
              setListSheetOpen(false);
              onAdd();
            }}
            onMove={handleMove}
            statusMap={statusMap}
            className="min-h-0 flex-1 border-r-0"
          />
        </SheetContent>
      </Sheet>

      <AlertDialog
        open={removeUnitId !== null}
        onOpenChange={(open) => {
          // 移除请求在途时不响应关闭，结果出来前对话框留在原处
          if (!open && !removingUnit) setRemoveUnitId(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("reference_unit_remove_title", { id: itemIdWithinEpisode(removeUnitId ?? "") })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("reference_unit_remove_desc")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removingUnit}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={removingUnit || (removeUnitId !== null && isUnitRemovalBlocked(removeUnitId))}
              // 离开拦截放在确认移除这一步：先问未保存修改再确认移除，放弃后取消移除就白丢了修改
              onClick={() => confirmLeave(handleRemoveUnit)}
            >
              {removingUnit ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
              {t("reference_unit_remove_confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ReferenceDurationConfirmDialog {...durationGate.dialogProps} />
      <ReferenceBatchAdmissionDialog
        admission={batchAdmission}
        onConfirm={handleBatchConfirm}
        onClose={() => setBatchAdmission(null)}
      />
    </div>
  );
}
