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
import { referenceBatchOutcome } from "./batch-outcome";
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
  /** Whether prompt_authoring scripts/episode_N.json exists; without scripts, unit lists have nothing to read and do not fetch. */
  hasScript?: boolean;
  /** Ad reference-to-video produces in one stage, without script_plan pages. */
  showPreprocess?: boolean;
  /** Unit duration is any positive integer, not constrained by provider tiers for composition. */
  freeDuration?: boolean;
  /**
   * Root capability queries explicitly report unresolvable video models (400/422). Per-unit buckets/tiers/endpoint-fixed
   * flags instead arrive with server unit lists based on usable references (`unitCapabilitiesByEpisode`).
   */
  videoModelUnresolved?: boolean;
  /** Script-plan tiers (`duration_constraints.planning`) select/check endpoint-fixed unit durations on confirmation pages. */
  planDurationOptions?: number[];
}

const EMPTY_UNITS: readonly ReferenceVideoUnit[] = Object.freeze([]);
const EMPTY_CAPABILITIES: ReferenceUnitCapabilityMap = Object.freeze({});

/**
 * Canvas-owned per-unit occupancy for writes without task rows, outside tasks-store occupancy.
 *
 * `ids` serves rendering, `ref` fresh submit-time reads. State appears only after rendering; guarding clicks
 * after state changes but before render is exactly the recheck's purpose, like fresh isUnitBusy reads.
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
 * Submit-time recheck: captured button occupancy may be stale after batch loops, Agent enqueue,
 * or SSE persistence occupies units between render/click. Always read fresh getState().
 * Enqueue actions optimistically mark before requests, so same-tick double-clicks are blocked too.
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
 * Selected-unit body editing unit, keyed by unit_id. Leave guards ask before switching;
 * external removal uses RetainedEditUnit to retain visible editors until discard/save follows live lists.
 */
function UnitPromptEdit({
  unit,
  unitLabel,
  onSave,
  allowNavigation,
  children,
}: {
  unit: ReferenceVideoUnit;
  /** Leave-confirmation unit label, e.g. video unit 5. */
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

/** Legal free-duration seconds range, matching server validation. */
const FREE_DURATION_MAX = 300;

/**
 * Free-duration inputs submit on blur/Enter, not intermediate numeric edits.
 * Register pending values with leave guards/external-removal retention alongside bodies; invalid submissions revert to saved duration.
 */
function FreeDurationInput({
  unit,
  disabled,
  externalChangeShown,
  onCommit,
}: {
  unit: ReferenceVideoUnit;
  disabled: boolean;
  /** Body notices already explain external removal; do not duplicate here. */
  externalChangeShown: boolean;
  onCommit: (seconds: number) => Promise<boolean>;
}) {
  const { t } = useTranslation("dashboard");
  const [draft, setDraft] = useState<string | null>(null);
  // Blur-triggered saves in flight make leave guards wait, avoiding discard bypass of sent writes or duplicate save submissions.
  const [committing, setCommitting] = useState(false);
  const seconds = Number(draft);
  const valid = Number.isInteger(seconds) && seconds >= 1 && seconds <= FREE_DURATION_MAX;
  // Explicitly submitting unchanged duration still confirms duration-only replanning marks.
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
      // Preserve values edited during submission for the next submission.
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

/** Unit-header icon button: accessible names also provide hover hints. */
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
  // Shared namespaces from schema v6 mean names are declared once across buckets.
  const mentionLookup = useMemo(() => buildMentionLookup(project), [project]);
  // Quarantined prompt drafts replace unit workbenches; Agent-editable drafts only show notices above them.
  const { view: promptDraft, refresh: refreshPromptDraft } = usePromptAuthoringDraft(projectName, episode);

  const voiceLegacyNotice = useMemo(
    () => computeVoiceLegacyNotice(units, project?.characters ?? {}, project?.character_voice_binding),
    [units, project],
  );
  // Dismissal acknowledges the character's current voice version, writing its own voice_updated_at rather than
  // local now. Both backend timestamps avoid client drift (slow clocks would prevent dismissal)
  // and ISO-format differences. Later updates advance voice_updated_at, naturally reshowing banners.
  const handleDismissVoiceLegacyNotice = useCallback(async () => {
    // Fresh submit-time read: voices may update after banner render; acknowledge the latest version.
    const characters = useProjectsStore.getState().currentProjectData?.characters ?? {};
    try {
      await Promise.all(
        voiceLegacyNotice.characterNames.map((name) => {
          const acknowledgedAt = characters[name]?.voice_updated_at;
          if (!acknowledgedAt) return Promise.resolve();
          return API.updateCharacter(projectName, name, { voice_notice_dismissed_at: acknowledgedAt });
        }),
      );
      // refreshProject resolves "failed", never rejects; require onError or successful PATCH with unsynchronized stores
      // silently leaves banners showing old data without informing users.
      await useProjectsStore.getState().refreshProject(projectName, {
        onError: (err) => toastError(err, (msg) => t("voice_legacy_banner_dismiss_failed", { error: msg })),
      });
    } catch (e) {
      // Silent failures leave banners intact while users think dismissed; make failures visible.
      toastError(e, (msg) => t("voice_legacy_banner_dismiss_failed", { error: msg }));
    }
  }, [projectName, t, voiceLegacyNotice.characterNames]);

  // Resource (=unit) → latest task row. Latest-row-wins belongs in store selectors:
  // stores do not guarantee ordering (in-place SSE upserts), so old failures must not hide retry rows.
  const tasksByUnit = useLatestTasksByResource(projectName, "reference_video");

  // Reference-video completion increments project-event SSE signals, rerunning this effect to refresh grouped final videos.
  const unitsRevision = useAppStore((s) => s.referenceVideoUnitsRevision);

  useEffect(() => {
    // Without prompt_authoring scripts, /episodes/{episode}/units returns backend 404, lacking scripts to split;
    // hasScript becoming true reruns the effect for initial fetching.
    if (!hasScript) return;
    void loadUnits(projectName, episode);
  }, [loadUnits, projectName, episode, hasScript, unitsRevision]);

  const selected = useMemo(
    () => units.find((u) => u.unit_id === selectedUnitId) ?? null,
    [units, selectedUnitId],
  );

  // Server buckets/tiers use currently usable references per unit, sharing execution
  // ReferenceUnitRequestProjector rules, arriving with lists/write responses. Never infer from
  // registered body names: registered-but-imageless assets do not count as images; execution falls to i2v, so use i2v tiers.
  // Pending conclusions make controls read-only, never invented tiers.
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

  // tasks-store enqueue actions mark optimistically before requests, roll back on failure, yielding to persisted real rows;
  // this covers request→persistence without canvas-local request markers.
  const busyUnitIds = useActiveResourceIds("reference_video", projectName);
  const ttsBusyUnitIds = useActiveResourceIds("tts", projectName);

  // Final uploads/version restoration/duration saves produce no rows, so record per-unit occupancy in canvas.
  // Keep outside UnitPreviewPanel: selection reuse would leak local booleans from A
  // to B, and no selection unmounts panels without canceling requests.
  const uploading = useUnitFlagSet();
  const restoring = useUnitFlagSet();
  const durationSaving = useUnitFlagSet();

  const setUploading = uploading.set;
  const handleRestoringChange = restoring.set;
  const setDurationSaving = durationSaving.set;

  /** Whether any write occupies units: generation through tasks-store, final upload, version restoration, or duration save. */
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
        // Canvas permits final uploads: uploaded playable assets mean historical failures must no longer override ready
        // (matching timeline/grid failure-toasts).
        supportsManualUpload: true,
      });
    }
    return map;
  }, [units, tasksByUnit, busyUnitIds, uploading.ids]);

  // Pass separately from statusMap to UnitPreviewPanel: retry/regenerate keep failed/successful rows,
  // so queueRow stays nonempty and optimistic status branches never apply. Status alone would miss disabling
  // generation between enqueue and row persistence.
  const selectedBusy = !!(selected && busyUnitIds.has(selected.unit_id));

  const failureMessage = useMemo(() => {
    if (!selected) return null;
    if (statusMap[selected.unit_id] !== "failed") return null;
    return tasksByUnit.get(selected.unit_id)?.error_message ?? null;
  }, [selected, statusMap, tasksByUnit]);

  // Switching units unmounts current editors; ask first for unsaved body/duration.
  const confirmLeave = useConfirmLeave();
  const selectUnit = useCallback(
    (unitId: string) => {
      confirmLeave(() => select(unitId), { saveLabel: t("common:save_and_switch") });
    },
    [confirmLeave, select, t],
  );

  // Ask for new-unit bodies first: servers reject empty creation with needs_replan.
  // Omitted `afterUnitId` appends; new units never inherit same-ID historical artifacts/history.
  const [addTarget, setAddTarget] = useState<{ afterUnitId?: string } | null>(null);
  const [addPrompt, setAddPrompt] = useState("");
  const [addingUnit, setAddingUnit] = useState(false);
  const finishCreation = useCallback(() => Promise.resolve(true), []);
  useLeaveGuard({ dirty: false, saving: addingUnit, save: finishCreation });
  const handleAdd = useCallback((afterUnitId?: string) => {
    setAddPrompt("");
    setAddTarget({ afterUnitId });
  }, []);
  // Select after store insertion; guard leaving at submission, so canceled creation does not waste current edits.
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

  // Reorder without confirmation; drop later actions while requests run, avoiding stale anchors.
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

  // Removal additionally blocks running voiceover tasks targeting units, matching timeline-shot guards.
  const isUnitRemovalBlocked = useCallback(
    (unitId: string) => isUnitLocked(unitId) || ttsBusyUnitIds.has(unitId),
    [isUnitLocked, ttsBusyUnitIds],
  );
  const [removeUnitId, setRemoveUnitId] = useState<string | null>(null);
  /** Confirmation addresses video unit 5, or deleted if absent. */
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
      // Render disabled state may be stale; recheck occupancy before opening confirmation.
      if (isUnitRemovalBlocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      setRemoveUnitId(unitId);
    },
    [isUnitRemovalBlocked, t],
  );
  // Return removal success so leave guards decide whether to discard unsaved edits.
  const handleRemoveUnit = useCallback(async () => {
    if (!removeUnitId || removingUnit) return false;
    // Occupancy may change while users consider dirty edits; recheck at submission.
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

  // Duration-tier gate confirms requested seconds differing from request-duration bases; cancellation queues nothing.
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
  /** Per-unit entry recheck: unoccupied and planning state generatable. */
  const canEnqueueUnit = useCallback(
    (unitId: string) => !isUnitLocked(unitId) && !isUnitGenerationBlocked(unitId),
    [isUnitLocked, isUnitGenerationBlocked],
  );

  /**
   * Batch-entry recheck asks only whether final video is still missing.
   *
   * Never filter needs_replan/in-flight tasks here: these are server admission gaps individually reported
   * to reject whole batches with zero tasks. Removing them first would present only healthy subsets,
   * creating tasks while hiding gaps and losing all-or-nothing guarantees.
   *
   * Existing final videos differ: they are no longer missing targets. Completed tasks release occupancy; queues
   * deduplicate only queued/running. Resubmitting units completed during confirmation would rerun generation,
   * double-charge and overwrite fresh finals. Read live stores, not rendered units snapshots.
   *
   * Local upload/restore/duration writes are invisible to servers and about to change units; exclude them too.
   *
   * Return exclusion reasons for confirmation skips; `null` means submittable.
   */
  const batchSkipReason = useCallback(
    (unitId: string): ReferenceBatchSkipReason | null => {
      // Exclude only server-invisible local writes; admission reports generation occupancy/needs_replan.
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

  const durationGate = useReferenceDurationGate({ projectName, episode });
  /** Pending whole-batch admission outcomes (confirmation/blocked); admitted uses toasts, excluded here. */
  const [batchAdmission, setBatchAdmission] = useState<ReferenceBatchAdmission | null>(null);
  /** Presubmit confirmation inputs: units to preview plus canvas-filtered skips. */
  const [batchPlan, setBatchPlan] = useState<{ unitIds: string[]; skipped: ReferenceBatchSkip[] } | null>(null);

  const enqueue = useCallback(
    async (unitId: string, confirmedRequestDuration: number | undefined) => {
      // Fresh getState() before submission: button render snapshots may be stale
      // after batch loops/Agent enqueue/SSE persistence occupy units between render/click;
      // duration confirmations leave the same window, so recheck exactly at enqueue.
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return;
      }
      if (isUnitGenerationBlocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_needs_replan"), "error");
        return;
      }
      try {
        // Actions own optimistic marking before requests, rollback, and queued/deduped notices.
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
   * Sequential enqueue triggers backend dedup checks in order; backend workers still run concurrently.
   *
   * Before each POST recheck entry conditions: waits between requests let other entries finish later units,
   * which filtering only before loops would miss.
   *
   * Unit entries only: batches use server all-or-nothing admission evaluating every target in one request.
   */
  const makeEnqueueSerially = useCallback(
    (canEnqueue: (unitId: string) => boolean) =>
      async (unitIds: string[], confirmedDurations: ReadonlyMap<string, number>) => {
      for (const id of unitIds) {
        if (!canEnqueue(id)) continue;
        // react-doctor-disable-next-line react-doctor/async-await-in-loop -- Intentionally sequential: recheck fresh state per enqueue and trigger backend dedup checks in order
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
      // Upload/generation write the same final file and share occupancy rules; file pickers may remain
      // open while units become occupied, beyond render-time disabled states.
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
        // Upload persisted successfully: report refresh failures separately, not upload failures; SSE/reentry eventually reconcile.
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
  // Post-production projects hide narration-generation entries while existing audio remains previewable.
  const onGenerateNarration = project?.narration_delivery === "use_tts" ? onGenerateNarrationVoid : undefined;

  // Batch targets are all units missing finals, including needs_replan/in-flight/retries. Disabled-button criteria must
  // match targets rather than selected-unit state: idle selection otherwise enables empty batches,
  // running selection blocks unrelated eligible units.
  const batchTargets = useMemo(
    () => units.filter((u) => statusMap[u.unit_id] !== "ready"),
    [units, statusMap],
  );

  /**
   * One request uses server all-or-nothing admission; all five {@link referenceBatchOutcome} outcomes mean evaluation
   * succeeded. Only clean whole-batch enqueue closes with action notices; four others retain outcome panels: tier decisions,
   * blocked admission, and which units failed enqueue with reasons.
   */
  const runBatch = useCallback(
    async (unitIds: string[], confirmedDurations?: Record<string, number>) => {
      try {
        const admission = await enqueueReferenceVideoBatch(projectName, episode, {
          unit_ids: unitIds,
          ...(confirmedDurations ? { confirmed_request_durations: confirmedDurations } : {}),
        });
        // Confirmation hands off to outcome dialogs within the same render, never stacking dialogs.
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
    // Recheck live, not render snapshots: unit actions/Agents/SSE may already occupy
    // units. Skip matches without error spam: batches enqueue everything still generatable,
    // listing skipped units/reasons in confirmation instead.
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
    // Paid video confirms before submission with membership/skips/estimates and duration-tier decisions together.
    setBatchPlan({ unitIds, skipped });
  }, [batchTargets, batchSkipReason, t]);

  /**
   * Submit an admission outcome: distribute tiers back to units, targeting every outcome unit, including those not needing
   * confirmation, which otherwise would never enqueue.
   *
   * Recheck at submission: units may finish elsewhere during potentially long user deliberation;
   * frozen-list resubmission would double-charge and overwrite fresh finals.
   */
  const submitAdmitted = useCallback(
    async (admission: WorkflowAdmission) => {
      const durationByUnit = new Map<string, number>();
      for (const tier of admission.confirmation?.tiers ?? []) {
        // Unresolved tier groups have no confirmable value: omit and resend so servers still report gaps,
        // rather than treating empty tiers as explicit user decisions.
        if (tier.request_duration_seconds == null) continue;
        for (const unitId of tier.unit_ids) durationByUnit.set(unitId, tier.request_duration_seconds);
      }
      const targets = Array.from(
        new Set([...admission.units.map((u) => u.unit_id), ...durationByUnit.keys()]),
      ).filter((unitId) => batchSkipReason(unitId) === null);
      if (targets.length === 0) {
        setBatchPlan(null);
        // Dialog is now closed; without feedback users would see nothing happen.
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

  /**
   * Submission tiers differing from preview after script/model changes trigger another confirmation:
   * a new outcome, resubmitted according to that outcome after confirmation.
   */
  const handleBatchConfirm = useCallback(() => {
    const admission = batchAdmission;
    setBatchAdmission(null);
    if (!admission || (admission.confirmation?.tiers ?? []).length === 0) return;
    void submitAdmitted(admission);
  }, [batchAdmission, submitAdmitted]);

  const onAdd = useCallback(() => handleAdd(), [handleAdd]);

  // Submit duration separately from body: not text content; tier changes persist immediately without affecting dirty body drafts.
  const handleDurationChange = useCallback(
    async (unitId: string, seconds: number): Promise<boolean> => {
      // Render disabled states may lag SSE/Agent occupancy; recheck fresh at submission.
      if (isUnitLocked(unitId)) {
        useAppStore.getState().pushToast(t("reference_generate_busy"), "error");
        return false;
      }
      setDurationSaving(unitId, true);
      try {
        await patchUnit(projectName, episode, unitId, { duration_seconds: seconds });
        // Reference-to-video prices requested seconds; SSE unit invalidation eventually syncs lists, but costs still need
        // immediate local-success refresh for current-browser feedback.
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

  // Editor-column modes: write body/view parsing. Read-only parse projection shares body text
  // and editor-column space, leaving the right column for final preview.
  const [editorView, setEditorView] = useState<"script" | "parse">("script");

  const videoGap = useBatchGap(projectName, episode, "videos", "reference_video");

  // Notification return: reference_unit scroll targets switch to unit view and select targets
  // like ShotSplitView. Asynchronous units retrigger dependencies until matched/expired.
  const scrollTarget = useAppStore((s) => s.scrollTarget);
  const clearScrollTarget = useAppStore((s) => s.clearScrollTarget);
  useEffect(() => {
    if (scrollTarget?.type !== "reference_unit") return;
    const requestId = scrollTarget.request_id;
    if (units.some((u) => u.unit_id === scrollTarget.id)) {
      const unitId = scrollTarget.id;
      // Preview links bring the preview subtab forward on narrow screens.
      const start = useAppStore.getState().playbackStart;
      const openPreview = start?.resource_type === "reference_videos" && start.resource_id === unitId;
      // Combine view switch/selection into one leave guard; separate requests retain only the latter and would leave the old view after approval.
      const focusUnit = () => {
        onViewChange("board", { replace: true });
        select(unitId);
        if (openPreview) setStackTab("preview");
      };
      // Already in unit view/current unit unmounts no editing units, so no question.
      if (view === "board" && unitId === selectedUnitId) focusUnit();
      else confirmLeave(focusUnit, { saveLabel: t("common:save_and_switch") });
      clearScrollTarget(requestId);
      return;
    }
    // While units load, wait without expiration cleanup; slow/cold loadUnits responses otherwise arrive
    // after premature target removal, making selection impossible.
    if (loading) return;
    // Loaded but unmatched: one-shot expires_at cleanup prevents permanently stale store targets when units/loading
    // stop changing and effects never rerun. Late units change dependencies, rerun this effect,
    // and clear the timer.
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
      // Retain out-of-range saved options after model-tier narrowing, preventing silent selection of different seconds.
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
      // Save dirty bodies before generation, which uses server bodies.
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
        {/* Upper middle: unit header/notices plus narrow-screen editor/preview switch. */}
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

        {/* Middle: body editor/parse preview; narrow layouts share the preview cell, selected by the switch above. */}
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

        {/* Right: final preview, stacked into middle on narrow layouts under edit/preview switching. */}
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
          {/* Unprocessed reference canvases serve ads/short films only; existing formal scripts may regenerate wholly. */}
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

      {/* Notices above workbench have capped height/own scrolling so they never squeeze editor/preview below minimum height. */}
      {notices && (
        <section
          aria-label={t("reference_notices_aria")}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Overflowing capped notices scroll only here; keyboard focus is required
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
        // Workbench responds to canvas width with fixed columns:
        //   below 4xl: unit-icon strip + middle with preview stacked; 4xl adds separate preview;
        //   6xl expands icons to full lists; 7xl widens preview.
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
                  // Ads/short films have no script planning; start blank directly without formal scripts.
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

      {/* Expanded icon-strip list: search/add/sort. */}
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
          // Ignore closing during removal so dialogs remain until outcomes arrive.
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
              // Guard leaving at removal confirmation: discarding edits before confirming removal would waste them if removal is canceled.
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
