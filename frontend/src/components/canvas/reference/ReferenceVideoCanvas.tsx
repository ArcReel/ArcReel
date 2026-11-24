import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
import { useRetainWhile } from "@/components/shared/edit-unit/edit-unit-retention";
import { RetainedEditUnit } from "@/components/shared/edit-unit/RetainedEditUnit";
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
import { ReferenceUnitAddDialog } from "./ReferenceUnitAddDialog";
import { ReferenceBatchAdmissionDialog } from "./ReferenceBatchAdmissionDialog";
import {
  ReferenceBatchPreviewDialog,
  type ReferenceBatchSkip,
  type ReferenceBatchSkipReason,
} from "./ReferenceBatchPreviewDialog";
import { hasReferenceOverflow, referenceBatchOutcome } from "./batch-outcome";
import { AdScriptButton, AdScriptProgress } from "@/components/canvas/shared/AdScriptDialog";
import { BatchFillButton, useBatchGap } from "@/components/canvas/episode-page/BatchFillButton";
import { EpisodeHeaderActions } from "@/components/canvas/episode-page/EpisodeHeaderActions";
import type { EpisodeCanvasContext } from "@/components/canvas/episode-page/EpisodePage";
import { NoScriptBlankState } from "@/components/canvas/shared/StartBlankScriptButton";
import { VoiceLegacyBanner } from "./VoiceLegacyBanner";
import { computeVoiceLegacyNotice } from "./voice-legacy-notice";
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
  WorkflowAdmission,
} from "@/types";
import { itemPositionLabel } from "@/utils/episode-display";
import { ItemPositionOfTotal } from "@/components/shared/episode-item/ItemPositionOfTotal";
import { stepAnchor } from "@/utils/move-anchor";

export interface ReferenceVideoCanvasProps extends EpisodeCanvasContext {
  projectName: string;
  episode: number;
  hasScript?: boolean;
  showPreprocess?: boolean;
  freeDuration?: boolean;
  videoModelUnresolved?: boolean;
  planDurationOptions?: number[];
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);
const EMPTY_CAPABILITIES: ReferenceUnitCapabilityMap = Object.freeze({});

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

function UnitPromptEdit({
  unit,
  unitLabel,
  onSave,
  allowNavigation,
  children,
}: {
  unit: ReferenceVideoUnit;
  unitLabel: string;
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
    leaveTitle: t("reference_unit_leave_title", { id: unitLabel }),
  });
  return <>{children(edit)}</>;
}

const FREE_DURATION_MAX = 300;

function FreeDurationInput({
  unit,
  disabled,
  externalChangeShown,
  onCommit,
}: {
  unit: ReferenceVideoUnit;
  disabled: boolean;
  externalChangeShown: boolean;
  onCommit: (seconds: number) => Promise<boolean>;
}) {
  const { t } = useTranslation("dashboard");
  const [draft, setDraft] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const seconds = Number(draft);
  const valid = Number.isInteger(seconds) && seconds >= 1 && seconds <= FREE_DURATION_MAX;
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
  const mentionLookup = useMemo(() => buildMentionLookup(project), [project]);
  const { view: promptDraft, refresh: refreshPromptDraft } = usePromptAuthoringDraft(projectName, episode);

  const voiceLegacyNotice = useMemo(
    () => computeVoiceLegacyNotice(units, project?.characters ?? {}, project?.character_voice_binding),
    [units, project],
  );
  const handleDismissVoiceLegacyNotice = useCallback(async () => {
    const characters = useProjectsStore.getState().currentProjectData?.characters ?? {};
    try {
      await Promise.all(
        voiceLegacyNotice.characterNames.map((name) => {
          const acknowledgedAt = characters[name]?.voice_updated_at;
          if (!acknowledgedAt) return Promise.resolve();
          return API.updateCharacter(projectName, name, { voice_notice_dismissed_at: acknowledgedAt });
        }),
      );
      await useProjectsStore.getState().refreshProject(projectName, {
        onError: (err) => toastError(err, (msg) => t("voice_legacy_banner_dismiss_failed", { error: msg })),
      });
    } catch (e) {
      toastError(e, (msg) => t("voice_legacy_banner_dismiss_failed", { error: msg }));
    }
  }, [projectName, t, voiceLegacyNotice.characterNames]);

  const tasksByUnit = useLatestTasksByResource(projectName, "reference_video");

  const unitsRevision = useAppStore((s) => s.referenceVideoUnitsRevision);

  useEffect(() => {
    if (!hasScript) return;
    void loadUnits(projectName, episode);
  }, [loadUnits, projectName, episode, hasScript, unitsRevision]);

  const selected = useMemo(
    () => units.find((u) => u.unit_id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );

  const selectedCapability = selected ? (unitCapabilities[selected.unit_id] ?? null) : null;
  const effectiveDurationOptions = selectedCapability?.allowed_durations ?? undefined;
  const selectedDurationEndpointFixed = selectedCapability?.duration_endpoint_fixed ?? false;
  const selectedTierProblem =
    selectedCapability?.problem != null
      ? tierProblemText(t, selectedCapability.problem, selectedCapability.hydrated_capability)
      : null;
  const selectedSplit = selectedCapability != null && selectedCapability.problems.length > 0 ? selectedCapability : null;

  useEffect(() => {
    if (units.length > 0 && !selected) {
      select(units[0].unit_id);
    }
  }, [units, selected, select]);

  const busyUnitIds = useActiveResourceIds("reference_video", projectName);
  const ttsBusyUnitIds = useActiveResourceIds("tts", projectName);

  const uploading = useUnitFlagSet();
  const restoring = useUnitFlagSet();
  const durationSaving = useUnitFlagSet();

  const setUploading = uploading.set;
  const handleRestoringChange = restoring.set;
  const setDurationSaving = durationSaving.set;

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
        supportsManualUpload: true,
      });
    }
    return map;
  }, [units, tasksByUnit, busyUnitIds, uploading.ids]);

  const selectedBusy = !!(selected && busyUnitIds.has(selected.unit_id));

  const failureMessage = useMemo(() => {
    if (!selected) return null;
    if (statusMap[selected.unit_id] !== "failed") return null;
    return tasksByUnit.get(selected.unit_id)?.error_message ?? null;
  }, [selected, statusMap, tasksByUnit]);

  const confirmLeave = useConfirmLeave();
  const selectUnit = useCallback(
    (unitId: string) => {
      confirmLeave(() => select(unitId), { saveLabel: t("common:save_and_switch") });
    },
    [confirmLeave, select, t],
  );

  const [addTarget, setAddTarget] = useState<{ afterUnitId?: string } | null>(null);
  const [addPrompt, setAddPrompt] = useState("");
  const [addingUnit, setAddingUnit] = useState(false);
  const finishCreation = useCallback(() => Promise.resolve(true), []);
  useLeaveGuard({ dirty: false, saving: addingUnit, save: finishCreation });
  const handleAdd = useCallback((afterUnitId?: string) => {
    setAddPrompt("");
    setAddTarget({ afterUnitId });
  }, []);
  const handleAddSubmit = useCallback(
    (prompt: string) => {
      if (!addTarget || addingUnit) return;
      const { afterUnitId } = addTarget;
      confirmLeave(
        async () => {
          setAddingUnit(true);
          try {
            await addUnit(projectName, episode, {
              prompt,
              ...(afterUnitId !== undefined ? { after_unit_id: afterUnitId } : {}),
            });
            setAddTarget(null);
            return true;
          } catch (e) {
            toastError(e, (message) => t("reference_unit_add_failed", { message }));
            return false;
          } finally {
            setAddingUnit(false);
          }
        },
        { saveLabel: t("common:save_and_switch") },
      );
    },
    [addTarget, addingUnit, confirmLeave, addUnit, projectName, episode, t],
  );

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

  const isUnitRemovalBlocked = useCallback(
    (unitId: string) => isUnitLocked(unitId) || ttsBusyUnitIds.has(unitId),
    [isUnitLocked, ttsBusyUnitIds],
  );
  const [removeUnitId, setRemoveUnitId] = useState<string | null>(null);
  const unitPositionOf = useCallback(
    (unitId: string | null) => {
      const index = unitId ? units.findIndex((u) => u.unit_id === unitId) : -1;
      return itemPositionLabel("unit", index < 0 ? null : index + 1, t);
    },
    [units, t],
  );
  const [removingUnit, setRemovingUnit] = useState(false);
  const openRemoveDialog = useCallback(
    (unitId: string) => {
      if (isUnitRemovalBlocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      setRemoveUnitId(unitId);
    },
    [isUnitRemovalBlocked, t],
  );
  const handleRemoveUnit = useCallback(async () => {
    if (!removeUnitId || removingUnit) return false;
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
  const canEnqueueUnit = useCallback(
    (unitId: string) => !isUnitLocked(unitId) && !isUnitGenerationBlocked(unitId),
    [isUnitLocked, isUnitGenerationBlocked],
  );

  const batchSkipReason = useCallback(
    (unitId: string): ReferenceBatchSkipReason | null => {
      const hasLocalWrite =
        uploading.ref.current.has(unitId) ||
        restoring.ref.current.has(unitId) ||
        durationSaving.ref.current.has(unitId);
      if (hasLocalWrite) return "local_write";
      const fresh = useReferenceVideoStore
        .getState()
        .unitsByEpisode[referenceVideoCacheKey(projectName, episode)]?.find((u) => u.unit_id === unitId);
      return fresh?.generated_assets?.video_clip ? "has_video" : null;
    },
    [projectName, episode, uploading.ref, restoring.ref, durationSaving.ref],
  );

  const [editorView, setEditorView] = useState<"script" | "parse">("script");
  const editScope = useMemo(() => ({ projectName, episode }), [projectName, episode]);
  const activeEditView = useRef<{ scope: typeof editScope; onViewChange: typeof onViewChange } | null>(null);
  useLayoutEffect(() => {
    activeEditView.current = { scope: editScope, onViewChange };
    return () => { activeEditView.current = null; };
  }, [editScope, onViewChange]);
  const editUnitReferences = useCallback((unitId: string) => {
    if (activeEditView.current?.scope !== editScope) return;
    confirmLeave(() => {
      const currentView = activeEditView.current;
      if (currentView?.scope !== editScope) return Promise.resolve(false);
      currentView.onViewChange("board", { replace: true });
      select(unitId);
      setStackTab("editor");
      setEditorView("script");
    }, { saveLabel: t("common:save_and_switch") });
  }, [confirmLeave, editScope, select, t]);
  const durationGate = useReferenceDurationGate({ projectName, episode, onEditReferences: editUnitReferences });
  const [batchAdmission, setBatchAdmission] = useState<ReferenceBatchAdmission | null>(null);
  const [batchPlan, setBatchPlan] = useState<{ unitIds: string[]; skipped: ReferenceBatchSkip[] } | null>(null);

  const enqueue = useCallback(
    async (unitId: string, confirmedRequestDuration: number | undefined) => {
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      if (isUnitGenerationBlocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_needs_replan"), "error");
        return;
      }
      try {
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

  const makeEnqueueSerially = useCallback(
    (canEnqueue: (unitId: string) => boolean) =>
      async (unitIds: string[], confirmedDurations: ReadonlyMap<string, number>) => {
      for (const id of unitIds) {
        if (!canEnqueue(id)) continue;
        // react-doctor-disable-next-line react-doctor/async-await-in-loop -- 有意串行：每次入队前按最新状态复核，让后端依次做去重检查
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
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      setUploading(unitId, true);
      try {
        try {
          const result = await API.uploadReferenceUnitVideo(projectName, episode, unitId, file);
          useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
          useAppStore.getState().pushToast(t("media_upload_success", { id: unitPositionOf(unitId) }), "success");
        } catch (e) {
          toastError(e, (msg) => t("media_upload_failed", { message: msg }));
          return;
        }
        try {
          await loadUnits(projectName, episode);
        } catch (e) {
          toastError(e, (msg) => t("media_refresh_failed", { message: msg }));
        }
      } finally {
        setUploading(unitId, false);
      }
    },
    [projectName, episode, loadUnits, isUnitLocked, setUploading, unitPositionOf, t],
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
  const onGenerateNarration = project?.narration_delivery === "use_tts" ? onGenerateNarrationVoid : undefined;

  const batchTargets = useMemo(
    () => units.filter((u) => statusMap[u.unit_id] !== "ready"),
    [units, statusMap],
  );

  const runBatch = useCallback(
    async (unitIds: string[], confirmedDurations?: Record<string, number>) => {
      try {
        const admission = await enqueueReferenceVideoBatch(projectName, episode, {
          unit_ids: unitIds,
          ...(confirmedDurations ? { confirmed_request_durations: confirmedDurations } : {}),
        });
        setBatchPlan(null);
        setBatchAdmission(referenceBatchOutcome(admission) === "queued" ? null : admission);
      } catch (e) {
        setBatchPlan(null);
        setBatchAdmission(null);
        toastError(e, (msg) => t("reference_batch_request_failed", { error: msg }));
      }
    },
    [projectName, episode, t],
  );

  const handleBatchGenerate = useCallback(() => {
    if (batchTargets.length === 0) {
      useAppStore.getState().pushToast(t("reference_batch_nothing_to_do"), "info");
      return;
    }
    setStackTab("preview");
    const skipped = batchTargets.flatMap((unit): ReferenceBatchSkip[] => {
      const reason = batchSkipReason(unit.unit_id);
      return reason === null ? [] : [{ unit_id: unit.unit_id, reason }];
    });
    const skippedIds = new Set(skipped.map((item) => item.unit_id));
    const unitIds = batchTargets.map((u) => u.unit_id).filter((unitId) => !skippedIds.has(unitId));
    if (unitIds.length === 0) {
      useAppStore.getState().pushToast(t("reference_batch_nothing_to_do"), "info");
      return;
    }
    setBatchPlan({ unitIds, skipped });
  }, [batchTargets, batchSkipReason, t]);

  const submitAdmitted = useCallback(
    async (admission: WorkflowAdmission) => {
      const durationByUnit = new Map<string, number>();
      for (const tier of admission.confirmation?.tiers ?? []) {
        if (tier.request_duration_seconds == null) continue;
        for (const unitId of tier.unit_ids) durationByUnit.set(unitId, tier.request_duration_seconds);
      }
      const refused = new Set(admission.units.filter(hasReferenceOverflow).map((unit) => unit.unit_id));
      const targets = Array.from(new Set([...admission.units.map((unit) => unit.unit_id), ...durationByUnit.keys()]))
        .filter((unitId) => !refused.has(unitId) && batchSkipReason(unitId) === null);
      if (targets.length === 0) {
        setBatchPlan(null);
        useAppStore.getState().pushToast(t("reference_batch_nothing_to_do"), "info");
        return;
      }
      const confirmed: Record<string, number> = {};
      for (const unitId of targets) {
        const duration = durationByUnit.get(unitId);
        if (duration != null) confirmed[unitId] = duration;
      }
      await runBatch(targets, Object.keys(confirmed).length > 0 ? confirmed : undefined);
    },
    [batchSkipReason, runBatch, t],
  );

  const handleBatchConfirm = useCallback(() => {
    const admission = batchAdmission;
    setBatchAdmission(null);
    if (!admission || (admission.confirmation?.tiers ?? []).length === 0) return;
    void submitAdmitted(admission);
  }, [batchAdmission, submitAdmitted]);

  const onAdd = useCallback(() => handleAdd(), [handleAdd]);

  const handleDurationChange = useCallback(
    async (unitId: string, seconds: number): Promise<boolean> => {
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return false;
      }
      setDurationSaving(unitId, true);
      try {
        await patchUnit(projectName, episode, unitId, { duration_seconds: seconds });
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

  const videoGap = useBatchGap(projectName, episode, "videos", "reference_video");

  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  const workbenchRef = useRef<HTMLDivElement>(null);
  const [unitToReveal, setUnitToReveal] = useState<{ id: string } | null>(null);
  const revealedUnit = useRef<typeof unitToReveal>(null);
  useEffect(() => {
    if (scrollTarget?.type !== "reference_unit") return;
    const requestId = scrollTarget.request_id;
    if (units.some((u) => u.unit_id === scrollTarget.id)) {
      const unitId = scrollTarget.id;
      const start = useAppStore.getState().playbackStart;
      const openPreview = start?.resource_type === "reference_videos" && start.resource_id === unitId;
      const focusUnit = () => {
        onViewChange("board", { replace: true });
        select(unitId);
        setUnitToReveal({ id: unitId });
        if (openPreview) setStackTab("preview");
      };
      if (view === "board" && unitId === selectedUnitId) focusUnit();
      else confirmLeave(focusUnit, { saveLabel: t("common:save_and_switch") });
      clearScrollTarget(requestId);
      return;
    }
    if (loading) return;
    const remaining = scrollTarget.expires_at - Date.now();
    if (remaining <= 0) {
      clearScrollTarget(requestId);
      return;
    }
    const timer = setTimeout(() => clearScrollTarget(requestId), remaining);
    return () => clearTimeout(timer);
  }, [scrollTarget, units, loading, view, selectedUnitId, select, clearScrollTarget, onViewChange, confirmLeave, t]);

  useLayoutEffect(() => {
    if (view !== "board" || !unitToReveal || revealedUnit.current === unitToReveal) return;
    const rows = workbenchRef.current?.querySelectorAll<HTMLButtonElement>('[data-reference-unit][aria-current="true"]');
    const row = Array.from(rows ?? []).find(
      (button) => button.dataset.referenceUnit === unitToReveal.id && button.getClientRects().length > 0,
    );
    revealedUnit.current = unitToReveal;
    row?.scrollIntoView({ block: "nearest" });
  }, [view, unitToReveal]);

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
        <div className="col-start-2 row-start-1 flex min-w-0 flex-col border-b border-border">
          <div className="flex flex-wrap items-center gap-2 px-4 py-2">
            <ItemPositionOfTotal itemId={unit.unit_id} kind="unit" position={selectedIndex + 1} total={units.length} />
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
            onClick={() => handleBatchGenerate()}
          />
        </EpisodeHeaderActions>
      )}

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
        <div ref={workbenchRef} className="grid min-h-0 flex-1 grid-cols-[3.5rem_minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] @4xl/canvas:grid-cols-[3.5rem_minmax(0,1fr)_20rem] @6xl/canvas:grid-cols-[20rem_minmax(0,1fr)_20rem] @7xl/canvas:grid-cols-[20rem_minmax(0,1fr)_22.5rem]">
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
              unitLabel={unitPositionOf(selected.unit_id)}
              onSave={handlePromptSave}
              allowNavigation={allowNavigation}
            >
              {(edit) => renderSelectedUnit(selected, edit)}
            </UnitPromptEdit>
          ) : (
            <>
              <div className="col-start-2 row-start-1 row-end-3 flex min-h-0 flex-col">
                {!hasScript && !showPreprocess ? (
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
          if (!open && !removingUnit) setRemoveUnitId(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("reference_unit_remove_title", { id: unitPositionOf(removeUnitId) })}
            </AlertDialogTitle>
            <AlertDialogDescription>{t("reference_unit_remove_desc")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removingUnit}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={removingUnit || (removeUnitId !== null && isUnitRemovalBlocked(removeUnitId))}
              onClick={() => confirmLeave(handleRemoveUnit)}
            >
              {removingUnit ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
              {t("reference_unit_remove_confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ReferenceUnitAddDialog
        open={addTarget !== null}
        prompt={addPrompt}
        submitting={addingUnit}
        onPromptChange={setAddPrompt}
        onSubmit={handleAddSubmit}
        onClose={() => setAddTarget(null)}
      />
      <ReferenceDurationConfirmDialog {...durationGate.dialogProps} />
      {batchPlan && (
        <ReferenceBatchPreviewDialog
          projectName={projectName}
          episode={episode}
          unitIds={batchPlan.unitIds}
          skipped={batchPlan.skipped}
          onConfirm={submitAdmitted}
          onEditReferences={editUnitReferences}
          onClose={() => setBatchPlan(null)}
        />
      )}
      <ReferenceBatchAdmissionDialog
        admission={batchAdmission}
        onConfirm={handleBatchConfirm}
        onClose={() => setBatchAdmission(null)}
      />
    </div>
  );
}
