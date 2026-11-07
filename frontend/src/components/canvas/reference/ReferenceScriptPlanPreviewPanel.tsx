import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, Clock, Lock, OctagonAlert, Pencil, RotateCcw, Save } from "lucide-react";
import type {
  DraftSoftViolation,
  PlanNewAsset,
  ReferenceScriptPlanDraft,
  ReferenceScriptPlanFlatUnit,
  ReferenceUnitCapability,
  ScriptReviewState,
  ScriptReviewViolation,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useDraftEditor } from "@/hooks/useDraftEditor";
import { useModelCapabilities } from "@/hooks/useModelCapabilities";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { voidPromise } from "@/utils/async";
import { draftViolationMessage, groupDraftViolations, groupSoftViolations } from "@/utils/draft-violations";
import {
  AgentDraftBar,
  DiscardDraftDialog,
  DraftEpisodeViolations,
  DraftSoftViolationList,
  InvalidDraftBar,
} from "@/components/shared/DraftStatus";
import { draftFallbackText, draftFixRequestText } from "@/components/shared/draft-text";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { sumItemDuration } from "@/utils/script-shape";
import { EpisodeDurationSummary } from "@/components/shared/EpisodeDurationSummary";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { VideoModelUnresolvedNotice } from "@/components/shared/VideoModelUnresolvedNotice";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScriptPlanButton } from "@/components/canvas/shared/ScriptPlanButton";
import { ScriptPlanStart } from "@/components/canvas/shared/ScriptPlanStart";
import { PlanDurationSelect } from "@/components/canvas/shared/PlanDurationSelect";
import { PlanStructureHint } from "@/components/canvas/shared/PlanStructureHint";
import { StartBlankScriptButton } from "@/components/canvas/shared/StartBlankScriptButton";
import { ScriptHighlight } from "@/components/shared/ScriptHighlight";
import { toScriptLines, type MentionLookup } from "@/hooks/useUnitPromptHighlight";
import { dialogueSpeakers, extractMentions, normalizeAssetName } from "@/utils/reference-mentions";
import { NewAssetsSection, type NewAssetEntryRefs } from "@/components/canvas/shared/NewAssetsSection";
import { hasValidNewAssets } from "@/utils/plan-new-assets";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef } from "@/utils/episode-display";
import { ItemPositionBadge } from "@/components/shared/episode-item/ItemPositionBadge";
import { tierProblemText } from "./unit-tier-problem";
import { ReferenceSplitAlert } from "./ReferenceSplitAlert";

interface ReferenceScriptPlanPreviewPanelProps {
  projectName: string;
  episode: number;
  videoModelUnresolved?: boolean;
  /** Script-plan duration tiers for endpoint-fixed units, sharing prompt-authoring rejection; omitted self-queries. */
  planningDurations?: number[];
  /** Asset name → kind, for mention coloring — same lookup the editor/parse preview share. */
  lookup: MentionLookup;
  /** Switch to episode video-unit timelines; confirmed read-only state uses it for edits, omitted hides entry. */
  onOpenTimeline?: () => void;
}

/** Source-anchor mismatch violations: red source-section marks, excluded from line anchors/aggregates. */
const SOURCE_ANCHOR_CODES = new Set(["source_text_not_verbatim", "source_text_empty"]);
const SPEECH_VIOLATION_KEYS: Record<string, string> = {
  mixed_speech: "speech_admission_mixed_speech",
  needs_replan: "speech_admission_needs_replan",
  parse_failed: "speech_admission_parse_failed",
  empty_speaker: "speech_admission_empty_speaker",
};

/** Unified card shape from structured promoted and flat draft units. */
interface DisplayUnit {
  key: string;
  /** One-based planning position, used for unit labels. */
  position: number;
  duration_seconds: number;
  sourceText: string;
  scriptText: string;
  /** Bodies/source/duration editable in unconfirmed formal content or structurally intact quarantined drafts. */
  editable: boolean;
}

/** Header dialogue-line counts share body-highlighting parser segmentation. */
function unitStats(scriptText: string, lookup: MentionLookup): { utterances: number } {
  const lines = toScriptLines(scriptText, lookup);
  return {
    utterances: lines.filter((l) => l.kind === "dialogue" || l.kind === "voiceover").length,
  };
}

function structuredDisplayUnits(draft: ReferenceScriptPlanDraft): DisplayUnit[] {
  return draft.units.map((u, i) => ({
    key: u.unit_id,
    position: i + 1,
    duration_seconds: u.duration_seconds,
    sourceText: u.source_text,
    scriptText: u.text,
    editable: true,
  }));
}

/** Quarantined bodies: flat unit arrays, IDs derived from episode/sequence. */
interface FlatUnitsDraft {
  units: ReferenceScriptPlanFlatUnit[];
  new_assets?: PlanNewAsset[];
}

function isFlatUnit(value: unknown): value is ReferenceScriptPlanFlatUnit {
  if (value == null || typeof value !== "object") return false;
  const u = value as Record<string, unknown>;
  return typeof u.text === "string" && typeof u.duration_seconds === "number" && typeof u.source_text === "string";
}

/**
 * Draft body → editable flat units. Agents may corrupt drafts (`units` not arrays, missing fields/wrong types);
 * narrow individually, not trusting declarations. Return null if impossible, falling back to read-only.
 */
function narrowFlatUnitsDraft(content: Record<string, unknown> | null): FlatUnitsDraft | null {
  const units = content?.units;
  if (!Array.isArray(units) || !units.every(isFlatUnit) || !hasValidNewAssets(content)) return null;
  return content as unknown as FlatUnitsDraft;
}

/** Names from visual/speaker body positions plus source excerpts, expanding new-assets appearances. */
function newAssetEntries(units: DisplayUnit[]): NewAssetEntryRefs[] {
  return units.map((unit) => ({
    id: unit.key,
    position: unit.position,
    kind: "unit" as const,
    names: [...extractMentions(unit.scriptText), ...dialogueSpeakers(unit.scriptText)],
    snippet: unit.sourceText,
  }));
}

/**
 * Episode-new references can enter bodies like registered names and color by type. Unregistered decisions become
 * plain text at confirmation, not assets.
 */
function lookupWithNewAssets(lookup: MentionLookup, items: PlanNewAsset[]): MentionLookup {
  if (items.length === 0) return lookup;
  const merged: MentionLookup = Object.assign(Object.create(null) as MentionLookup, lookup);
  for (const item of items) {
    if (item.decision === "skip") continue;
    const name = normalizeAssetName(item.name);
    if (!(name in merged)) merged[name] = item.type;
  }
  return merged;
}

function draftUnitKey(episode: number, index: number): string {
  return `E${episode}U${String(index + 1).padStart(2, "0")}`;
}

function flatDisplayUnits(draft: FlatUnitsDraft, episode: number): DisplayUnit[] {
  return draft.units.map((u, i) => ({
    key: draftUnitKey(episode, i),
    position: i + 1,
    duration_seconds: u.duration_seconds,
    sourceText: u.source_text,
    scriptText: u.text,
    editable: true,
  }));
}

/**
 * Structurally corrupt, unnarrowable drafts: read-only display of any readable units for comparison. Null content
 * means corrupt draft files, leaving nothing to display; episode-wide violations already explain.
 */
function brokenDraftDisplayUnits(content: Record<string, unknown> | null, episode: number): DisplayUnit[] {
  const units: unknown = content?.units;
  if (!Array.isArray(units)) return [];
  return units.flatMap((raw: unknown, i) => {
    if (raw == null || typeof raw !== "object") return [];
    const u = raw as Partial<ReferenceScriptPlanFlatUnit>;
    return [
      {
        key: draftUnitKey(episode, i),
        position: i + 1,
        duration_seconds: typeof u.duration_seconds === "number" ? u.duration_seconds : 0,
        sourceText: typeof u.source_text === "string" ? u.source_text : "",
        scriptText: typeof u.text === "string" ? u.text : "",
        editable: false,
      },
    ];
  });
}

interface UnitViolations {
  /** Source-anchor mismatch marks source red, without duplicate line anchors/aggregates. */
  anchorSource: ScriptReviewViolation[];
  /** Line-numbered violations grouped by sourceLine for ScriptHighlight renderAfterLine rendering. */
  byLine: Map<number, ScriptReviewViolation[]>;
  /** Unit-level violations without natural lines appear in card aggregates. */
  aggregate: ScriptReviewViolation[];
}

function partitionViolations(forUnit: ScriptReviewViolation[]): UnitViolations {
  const anchorSource: ScriptReviewViolation[] = [];
  const byLine = new Map<number, ScriptReviewViolation[]>();
  const aggregate: ScriptReviewViolation[] = [];
  for (const v of forUnit) {
    if (SOURCE_ANCHOR_CODES.has(v.code)) {
      anchorSource.push(v);
    } else if (v.line != null) {
      const list = byLine.get(v.line) ?? [];
      list.push(v);
      byLine.set(v.line, list);
    } else {
      aggregate.push(v);
    }
  }
  return { anchorSource, byLine, aggregate };
}

/**
 * Server bucket decisions (bucket/tiers/endpoint-fixed/reference divergence) use currently usable images, sharing execution
 * criteria. Never infer from body-name registration; saved-state returns update decisions. Null tiers for unresolvable
 * models or missing unit conclusions mean null and read-only controls.
 */
function unitCapability(unit: DisplayUnit, tiers: ScriptReviewState["duration_tiers"]): ReferenceUnitCapability | null {
  return tiers?.units[unit.key] ?? null;
}

/**
 * Units referencing no registered scenes leave places to models; indoor/outdoor neighboring units may invent
 * inconsistent locations. Mirror backend `lib/script/reference_video/script_preview.py::unit_lacks_scene_reference`.
 *
 * Like tier narrowing, evaluate current bodies immediately, not server snapshots: in-place edits adding `@[场景]` must
 * remove notices immediately, rather than leaving notices mismatched until saving/server response.
 */
function unitLacksSceneReference(scriptText: string, lookup: MentionLookup, projectHasScene: boolean): boolean {
  if (!projectHasScene) return false;
  return !extractMentions(scriptText).some((name) => lookup[name] === "scene");
}

function InlineViolations({ violations, position }: { violations: ScriptReviewViolation[]; position: number }) {
  const { t } = useTranslation("dashboard");
  if (!violations.length) return null;
  return (
    <>
      {violations.map((v, i) => {
        const speechKey = SPEECH_VIOLATION_KEYS[v.code];
        const location = v.locations
          ?.map(({ path, line }) => `${path.join(".")}${line === null ? "" : `:${line + 1}`}`)
          .join(", ");
        const message = speechKey && location
          ? t(speechKey, { position, location })
          : draftViolationMessage(v, t, position);
        return (
          // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Server computes whole violation lists without IDs; codes may repeat, text is read-only, rows stateless
          <p key={`${v.code}-${i}`} className="mt-1 flex items-start gap-1.5 pl-1 text-xs leading-snug text-destructive">
            <OctagonAlert className="mt-px size-3 shrink-0" aria-hidden="true" />
            <span>{message}</span>
          </p>
        );
      })}
    </>
  );
}

function UnitCard({
  unit,
  violations,
  softViolations,
  lookup,
  projectHasScene,
  onScrollRef,
  editing,
  onToggleEdit,
  onTextChange,
  onSourceTextChange,
  supportedDurations,
  durationEndpointFixed,
  durationProblem,
  split,
  outOfTier,
  onDurationChange,
  busy,
}: {
  unit: DisplayUnit;
  violations: UnitViolations;
  /** Server fallback hints, excluding missing-scene notices evaluated against current bodies. */
  softViolations: DraftSoftViolation[];
  lookup: MentionLookup;
  /** Project has registered scenes; without available scene candidates suppress missing-scene notices, e.g. product-only ads. */
  projectHasScene: boolean;
  onScrollRef: (key: string, el: HTMLElement | null) => void;
  editing: boolean;
  onToggleEdit: () => void;
  onTextChange: ((text: string) => void) | null;
  /** Source/body edit together; servers validate verbatim extraction after saving, returning source-section red marks. */
  onSourceTextChange: ((sourceText: string) => void) | null;
  supportedDurations: number[] | null;
  durationEndpointFixed: boolean;
  /** Bucket video-request fact failures: labels/recovery hints produced per bucket by `tierProblemText`. */
  durationProblem: { label: string; hint: string } | null;
  /** Server conclusion for declared-reference/usable-image divergence (missing/unregistered images, changed buckets), null without divergence. */
  split: ReferenceUnitCapability | null;
  /** Saved unit duration outside narrowed tiers stays displayed but blocks confirmation through parents. */
  outOfTier: boolean;
  onDurationChange: ((seconds: number) => void) | null;
  /** Save/confirm requests lock body/duration controls, preventing adopt() server echoes overwriting postrequest edits. */
  busy: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const unitLabel = t("common:item_position_unit", { position: unit.position });
  const hasViolation = violations.anchorSource.length + violations.byLine.size + violations.aggregate.length > 0;
  const anchorBroken = violations.anchorSource.length > 0;
  const stats = useMemo(() => unitStats(unit.scriptText, lookup), [unit.scriptText, lookup]);
  const lacksScene = useMemo(
    () => unitLacksSceneReference(unit.scriptText, lookup, projectHasScene),
    [unit.scriptText, lookup, projectHasScene],
  );
  // Unknown tiers/uneditable content uses read-only seconds; offered tiers must survive backend adoption
  // unchanged after saving, so unavailable authoritative tiers must not offer silently rewritten choices.
  const durationOptions = onDurationChange && supportedDurations?.length ? supportedDurations : null;

  return (
    <article
      ref={(el) => onScrollRef(unit.key, el)}
      className={`scroll-mt-28 rounded-lg border bg-card p-4 ${hasViolation ? "border-destructive/45" : "border-border"}`}
    >
      <div className="flex items-center gap-2">
        <ItemPositionBadge position={unit.position} kind="unit" />
        {durationProblem ? (
          <span className="text-xs font-medium text-warn">
            {durationProblem.label}
            <span className="sr-only">{durationProblem.hint}</span>
          </span>
        ) : (
          <PlanDurationSelect
            seconds={unit.duration_seconds}
            options={durationOptions}
            onChange={(seconds) => onDurationChange?.(seconds)}
            disabled={busy}
            label={t("reference_script_plan_duration_label", { unit: unitLabel })}
            endpointFixed={durationEndpointFixed}
          />
        )}
        {outOfTier && (
          <span className="rounded-sm bg-destructive/10 px-1 py-px text-xs text-destructive">
            {t("reference_script_plan_duration_out_of_tier")}
          </span>
        )}
        <span className="text-xs text-muted-foreground">
          {t("reference_script_plan_unit_stats", { utterances: stats.utterances })}
        </span>
        <span className="flex-1" />
        {onTextChange && (
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onToggleEdit}
            aria-label={editing ? t("reference_script_plan_edit_done") : t("reference_script_plan_edit_text")}
            aria-pressed={editing}
          >
            <Pencil aria-hidden />
          </Button>
        )}
      </div>
      {/* Divergence does not block confirmation (planning often lacks sheets), but i2v tiers explain changed buckets instead of switching silently. */}
      {split && (
        <ReferenceSplitAlert
          capability={split}
          className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
        />
      )}

      <details open className="group mt-3">
        <summary className="focus-ring flex cursor-pointer list-none items-center gap-1 rounded-sm text-xs font-medium text-muted-foreground">
          <ChevronDown className="size-3 transition-transform duration-fast group-open:rotate-180" aria-hidden="true" />
          {t("reference_script_plan_source_text_label")}
          {anchorBroken && (
            <span className="ml-1 rounded-sm bg-destructive/10 px-1 py-px text-xs text-destructive">
              {t("reference_script_plan_source_anchor_broken")}
            </span>
          )}
        </summary>
        {editing && unit.editable && onSourceTextChange ? (
          <Textarea
            value={unit.sourceText}
            onChange={(e) => onSourceTextChange(e.target.value)}
            disabled={busy}
            placeholder={t("review_source_text_placeholder")}
            aria-label={t("reference_script_plan_unit_source_text_label", { unit: unitLabel })}
            aria-invalid={anchorBroken || undefined}
            className="mt-1.5"
          />
        ) : (
          <p
            className={`mt-1.5 border-l pl-3 text-xs leading-relaxed ${
              anchorBroken ? "border-destructive/50 text-destructive" : "border-border text-muted-foreground"
            }`}
          >
            {unit.sourceText}
          </p>
        )}
        <InlineViolations violations={violations.anchorSource} position={unit.position} />
      </details>

      <div className="mt-3">
        {editing && unit.editable && onTextChange ? (
          <Textarea
            value={unit.scriptText}
            onChange={(e) => onTextChange(e.target.value)}
            disabled={busy}
            aria-label={t("reference_script_plan_unit_text_label", { unit: unitLabel })}
          />
        ) : (
          <ScriptHighlight
            text={unit.scriptText}
            lookup={lookup}
            renderAfterLine={(sourceLine) => (
              <InlineViolations violations={violations.byLine.get(sourceLine) ?? []} position={unit.position} />
            )}
          />
        )}
      </div>

      <InlineViolations violations={violations.aggregate} position={unit.position} />

      {/* Nonblocking fallback hints differ from red violations: legal body, unconstrained visual location. */}
      {lacksScene && (
        <p className="mt-2 flex items-start gap-1.5 pl-1 text-xs leading-snug text-subtle-foreground">
          <AlertTriangle className="mt-px size-3 shrink-0 text-warn" aria-hidden="true" />
          <span>{t("reference_script_plan_unit_without_scene")}</span>
        </p>
      )}
      <DraftSoftViolationList softViolations={softViolations} position={unit.position} />
    </article>
  );
}

/** Edit only reference_video units here; other variants belong elsewhere. */
function selectUnitsContent(state: ScriptReviewState): ReferenceScriptPlanDraft | null {
  return state.content != null && "units" in state.content ? state.content : null;
}

/** Missing-scene checks use current bodies (`unitLacksSceneReference`), not server snapshots. */
const LIVE_SOFT_VIOLATION_CODES: ReadonlySet<string> = new Set(["ref_warn_unit_without_scene"]);

/**
 * Per-episode reference_video script_plan preview, peer to drama/narration `ScriptReviewGate`,
 * dedicated content-confirmation panel with document-flow unit cards (header/source/highlighted body);
 * clean state needs only confirmation to release prompt_authoring.
 *
 * Quarantined drafts replace content: unit violations attach (syntax anchored to offending lines), episode-wide violations at top.
 * Edit body/source/duration in place, save/validate, adopt when cleared. Agent-editable drafts only indicate
 * unfinished changes with formal contents read-only. Confirmed plans remain read-only, directing edits to timelines.
 */
export function ReferenceScriptPlanPreviewPanel({
  projectName,
  episode,
  videoModelUnresolved,
  planningDurations,
  lookup,
  onOpenTimeline,
}: ReferenceScriptPlanPreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const episodeLedger = useEpisodeLedger();
  const episodeRef = episodeAgentRef(episodeLedger, episode, t);
  const standaloneCapabilities = useModelCapabilities({
    projectName,
    enabled: videoModelUnresolved === undefined || planningDurations === undefined,
  });
  const modelUnresolved = videoModelUnresolved ?? standaloneCapabilities.videoModelUnresolved;
  const fixedPlanningDurations = planningDurations ?? standaloneCapabilities.planningDurations;
  // Unit duration choices use narrowed bucket tiers; endpoint-fixed empty buckets fall back to script-plan tiers.
  const unitTiers = (capability: ReferenceUnitCapability | null | undefined): number[] | null =>
    capability?.duration_endpoint_fixed ? fixedPlanningDurations : (capability?.allowed_durations ?? null);
  const pushToast = useAppStore((s) => s.pushToast);
  const savedInstructions = useProjectsStore(
    (s) =>
      s.currentProjectData?.episodes?.find((ep) => ep.episode === episode)?.script_plan_instructions ?? "",
  );

  const [editingUnitKey, setEditingUnitKey] = useState<string | null>(null);
  const [overwriteOpen, setOverwriteOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);

  const handleConfirmed = useCallback(() => {
    // Users may change projects during save/confirm awaits, unmounting tabs; suppress confirmation notices only for changed
    // projects, not same-project tab changes. Confirmation does not open Agents; page next steps continue,
    // matching ScriptReviewGate.
    if (useProjectsStore.getState().currentProjectName !== projectName) return;
    pushToast(t("dashboard:review_confirmed"), "success");
  }, [projectName, pushToast, t]);

  const {
    state,
    draft,
    setDraft,
    dirty,
    loading,
    loadError,
    saving,
    busy,
    retry: handleRetry,
    refresh,
    save: handleSave,
    confirm: handleConfirm,
    confirming,
  } = useScriptReviewDraft<ReferenceScriptPlanDraft>({
    projectName,
    episode,
    selectContent: selectUnitsContent,
    onConfirmed: handleConfirmed,
  });

  const quarantine = state?.quarantine ?? null;
  const draftEditor = useDraftEditor<FlatUnitsDraft>({
    projectName,
    episode,
    view: quarantine,
    narrow: narrowFlatUnitsDraft,
    onSettled: refresh,
  });
  const setDraftContent = draftEditor.setContent;
  const onDraft = quarantine != null;

  const updateUnit = useCallback(
    (unitIndex: number, patch: { text?: string; source_text?: string; duration_seconds?: number }) => {
      if (onDraft) {
        setDraftContent((prev) => ({
          ...prev,
          units: prev.units.map((u, i) => (i === unitIndex ? { ...u, ...patch } : u)),
        }));
        return;
      }
      setDraft((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          units: prev.units.map((u, i) => (i === unitIndex ? { ...u, ...patch } : u)),
        };
      });
    },
    [onDraft, setDraftContent, setDraft],
  );

  const updateNewAssets = useCallback(
    (items: PlanNewAsset[]) => {
      if (onDraft) setDraftContent((prev) => ({ ...prev, new_assets: items }));
      else setDraft((prev) => (prev ? { ...prev, new_assets: items } : prev));
    },
    [onDraft, setDraftContent, setDraft],
  );

  const projectHasScene = useMemo(() => Object.values(lookup).some((kind) => kind === "scene"), [lookup]);

  const cardRefs = useRef(new Map<string, HTMLElement>());
  const episodeLevelRef = useRef<HTMLElement | null>(null);
  const setCardRef = useCallback((key: string, el: HTMLElement | null) => {
    if (el) cardRefs.current.set(key, el);
    else cardRefs.current.delete(key);
  }, []);
  const scrollTo = (el: HTMLElement | null | undefined) => el?.scrollIntoView({ behavior: "smooth", block: "center" });

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-muted-foreground">{t("dashboard:loading_script_plan")}</div>;
  }

  if (loadError) {
    return (
      <div role="alert" className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertTriangle className="size-6 text-warn" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-subtle-foreground">{t("dashboard:review_load_failed")}</p>
          {loadError.message && <p className="max-w-sm px-4 font-mono text-xs text-muted-foreground">{loadError.message}</p>}
        </div>
        <Button variant="outline" onClick={handleRetry}>
          <RotateCcw aria-hidden data-icon="inline-start" />
          {t("dashboard:review_retry")}
        </Button>
      </div>
    );
  }

  const status = state?.status ?? "no_script_plan";
  if (status === "no_script_plan" || (draft == null && quarantine == null)) {
    // Without formal scripts, show first-planning start area; existing formal scripts (e.g. blank starts) require overwrite confirmation.
    if (status === "no_script_plan" && state?.script_overwrite == null) {
      return <ScriptPlanStart projectName={projectName} episode={episode} savedInstructions={savedInstructions} />;
    }
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-muted-foreground">
        <p>{t("dashboard:no_script_plan_content")}</p>
        {status === "no_script_plan" && (
          <ScriptPlanButton projectName={projectName} episode={episode} replaces="formal_script" />
        )}
      </div>
    );
  }

  const draftBusy = draftEditor.saving || draftEditor.discarding || draftEditor.repairing;
  const discardDialog = quarantine && (
    <DiscardDraftDialog
      open={discardOpen}
      agentOwned={quarantine.editable_by === "agent"}
      fallbackText={draftFallbackText(t, quarantine.doc_type, quarantine.formal_exists)}
      loading={draftEditor.discarding}
      onConfirm={async () => {
        if (await draftEditor.discard()) setDiscardOpen(false);
      }}
      onCancel={() => setDiscardOpen(false)}
    />
  );

  // Quarantined drafts replace display; formal confirmation is unavailable, sharing endpoint rejection criteria.
  // Without formal scripts/unconfirmed plans, blank handwritten starts may discard plans/quarantined drafts after confirmation.
  // Agent-editing drafts remove entry points, matching server rejection.
  const blankStartAction =
    state?.script_overwrite == null && status !== "confirmed" && quarantine?.editable_by !== "agent" ? (
      <StartBlankScriptButton projectName={projectName} episode={episode} discardsPlan />
    ) : null;

  if (quarantine != null && quarantine.editable_by === "user") {
    const content = draftEditor.content;
    const displayUnits =
      content != null ? flatDisplayUnits(content, episode) : brokenDraftDisplayUnits(quarantine.content, episode);
    const groups = groupDraftViolations(
      quarantine.violations,
      displayUnits.map((u) => u.key),
    );
    const softByUnit = groupSoftViolations(quarantine.soft_violations, LIVE_SOFT_VIOLATION_CODES);
    const supportedDurations = state?.supported_durations ?? null;
    const draftNewAssets = content?.new_assets ?? [];
    const draftLookup = lookupWithNewAssets(lookup, draftNewAssets);
    return (
      <div className="flex flex-col gap-3">
        <InvalidDraftBar
          violationCount={quarantine.violations.length}
          itemJumps={[...groups.byItem.entries()].map(([index, list]) => ({
            index,
            label: t("common:item_position_unit", { position: index + 1 }),
            count: list.length,
          }))}
          episodeLevelCount={groups.episodeLevel.length}
          onJump={(index) => scrollTo(cardRefs.current.get(displayUnits[index].key))}
          onJumpEpisodeLevel={() => scrollTo(episodeLevelRef.current)}
          editable={content != null}
          dirty={draftEditor.dirty}
          saving={draftEditor.saving}
          repairing={draftEditor.repairing}
          onRepair={draftEditor.repair}
          busy={draftBusy}
          outdated={draftEditor.outdated}
          onSave={voidPromise(draftEditor.save)}
          onReloadLatest={draftEditor.reloadLatest}
          onHandToAgent={() =>
            prefillAssistant(draftFixRequestText(t, episodeRef, "reference_script_plan", quarantine.violations))
          }
          onDiscard={() => setDiscardOpen(true)}
          regenerateAction={
            <>
              {blankStartAction}
              <ScriptPlanButton projectName={projectName} episode={episode} replaces="draft" />
            </>
          }
        />
        {discardDialog}
        <DraftEpisodeViolations
          violations={groups.episodeLevel}
          anchorRef={(el) => {
            episodeLevelRef.current = el;
          }}
        />
        {content != null && (
          <NewAssetsSection
            items={draftNewAssets}
            entries={newAssetEntries(displayUnits)}
            readOnly={false}
            disabled={draftBusy}
            onChange={updateNewAssets}
          />
        )}
        {content != null && <PlanStructureHint />}
        <div className="flex flex-col gap-2.5">
          {displayUnits.map((unit, i) => (
            <UnitCard
              key={unit.key}
              unit={unit}
              violations={partitionViolations(groups.byItem.get(i) ?? [])}
              softViolations={softByUnit.get(i) ?? []}
              lookup={draftLookup}
              projectHasScene={projectHasScene}
              onScrollRef={setCardRef}
              editing={unit.editable && editingUnitKey === unit.key}
              onToggleEdit={() => setEditingUnitKey((prev) => (prev === unit.key ? null : unit.key))}
              onTextChange={unit.editable ? (text) => updateUnit(i, { text }) : null}
              onSourceTextChange={unit.editable ? (sourceText) => updateUnit(i, { source_text: sourceText }) : null}
              // Unbucketed draft units use the full structural duration interval; adoption rechecks server-side with identical rules.
              supportedDurations={supportedDurations}
              durationEndpointFixed={false}
              durationProblem={null}
              split={null}
              outOfTier={false}
              onDurationChange={unit.editable ? (seconds) => updateUnit(i, { duration_seconds: seconds }) : null}
              busy={draftBusy}
            />
          ))}
        </div>
      </div>
    );
  }

  // Agent-editable drafts lock formal editing/confirmation until completion/discard.
  const agentEditing = quarantine != null;
  // Confirmed plans are read-only, matching save-endpoint rejection; edit on timelines.
  const confirmed = status === "confirmed" && !agentEditing;
  const readOnly = agentEditing || confirmed;
  // Existing formal scripts are wholly overwritten; danger confirmation shows consequences first.
  const overwrite = confirmed ? null : (state?.script_overwrite ?? null);
  // Confirmed without formal scripts (failed migration/deleted files) still allows reconfirmation to materialize them.
  const scriptMissing = confirmed && state?.script_overwrite == null;
  const confirmLocked = confirmed && !scriptMissing;
  const videoModelBlocked = modelUnresolved && !confirmLocked;
  const displayUnits: DisplayUnit[] = draft ? structuredDisplayUnits(draft) : [];
  const newAssets = draft?.new_assets ?? [];
  const reviewLookup = lookupWithNewAssets(lookup, newAssets);
  const softByUnit = groupSoftViolations(state?.soft_violations ?? [], LIVE_SOFT_VIOLATION_CODES);
  // Narrowed tiers may exclude saved durations after model/resolution/reference changes;
  // preserve display to avoid selects silently choosing first tiers, but forbid confirmation: _assert_reference_script_plan_ready
  // rejects the same value before prompt_authoring persistence. Guard here, not after users confirm and fail elsewhere.
  const durationTiers = state?.duration_tiers ?? null;
  const outOfTierUnitKeys = new Set(
    displayUnits
      .filter((u) => {
        const capability = unitCapability(u, durationTiers);
        const tiers = unitTiers(capability);
        return capability != null && tiers != null && tiers.length > 0 && !tiers.includes(u.duration_seconds);
      })
      .map((u) => u.key),
  );
  // Unresolvable bucket request facts mean unknown tiers; never confirm an unexecutable plan.
  const unknownUnits = displayUnits.flatMap((u) => {
    const capability = unitCapability(u, durationTiers);
    return capability?.problem ? [{ key: u.key, capability }] : [];
  });
  const unknownUnitKeys = new Set(unknownUnits.map((u) => u.key));
  const firstUnknownProblem =
    unknownUnits.length > 0
      ? tierProblemText(t, unknownUnits[0].capability.problem!, unknownUnits[0].capability.hydrated_capability)
      : null;
  // Overwrite-confirmation triggers/inner buttons share blockers: capability queries may return unresolvable models
  // after opening, otherwise leaving enabled confirmation inevitably rejected by servers.
  const overwriteBlocked = videoModelBlocked || outOfTierUnitKeys.size > 0 || unknownUnitKeys.size > 0;
  const confirmBlockedHint = videoModelBlocked
    ? t("dashboard:review_video_model_unresolved_hint")
    : firstUnknownProblem
      ? firstUnknownProblem.hint
      : outOfTierUnitKeys.size > 0
        ? t("reference_script_plan_duration_out_of_tier_hint")
        : undefined;

  return (
    <div className="flex flex-col gap-3">
      {agentEditing ? (
        <AgentDraftBar
          busy={draftBusy}
          onFinish={() =>
            prefillAssistant(t("dashboard:draft_agent_finish_prefill", { episodeRef, docType: "reference_script_plan" }))
          }
          onDiscard={() => setDiscardOpen(true)}
        />
      ) : (
        <header className="sticky top-0 z-sticky flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-3.5 py-2.5">
          <div className="flex items-center gap-2">
            {confirmed ? (
              <CheckCircle2 aria-hidden className="size-4 shrink-0 text-good" />
            ) : (
              <Clock aria-hidden className="size-4 shrink-0 text-warn" />
            )}
            <div className="flex flex-col">
              <span className="text-sm font-medium text-foreground">
                {confirmed ? t("dashboard:review_status_confirmed") : t("dashboard:review_status_pending")}
              </span>
              <span className="text-xs text-muted-foreground">
                {scriptMissing
                  ? t("dashboard:review_script_missing_hint")
                  : confirmed
                    ? t("dashboard:review_confirmed_hint")
                    : overwrite
                      ? t("dashboard:review_overwrite_hint")
                      : t("dashboard:review_pending_hint")}
              </span>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {confirmed && onOpenTimeline && (
              <Button variant="outline" size="sm" onClick={onOpenTimeline}>
                <ArrowRight aria-hidden data-icon="inline-start" />
                {t("dashboard:review_open_timeline")}
              </Button>
            )}
            {blankStartAction}
            <ScriptPlanButton
              projectName={projectName}
              episode={episode}
              replaces={confirmed ? "confirmed_plan" : "pending_plan"}
             
              disabledReason={!readOnly && dirty ? t("dashboard:script_plan_dirty_hint") : null}
            />
            {!readOnly && dirty && (
              <Button variant="outline" size="sm" onClick={voidPromise(handleSave)} disabled={busy}>
                <Save aria-hidden data-icon="inline-start" />
                {saving ? t("common:saving") : t("common:save")}
              </Button>
            )}
            {overwrite ? (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setOverwriteOpen(true)}
                disabled={busy || overwriteBlocked}
                title={confirmBlockedHint}
              >
                <AlertTriangle aria-hidden data-icon="inline-start" />
                {confirming ? t("dashboard:review_confirming") : t("dashboard:review_overwrite_action")}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={voidPromise(() => handleConfirm())}
                disabled={busy || confirmLocked || outOfTierUnitKeys.size > 0 || unknownUnitKeys.size > 0 || videoModelBlocked}
                title={confirmBlockedHint}
              >
                {confirmLocked ? (
                  <Lock aria-hidden data-icon="inline-start" />
                ) : (
                  <CheckCircle2 aria-hidden data-icon="inline-start" />
                )}
                {confirming
                  ? t("dashboard:review_confirming")
                  : scriptMissing
                    ? t("dashboard:review_rematerialize_action")
                    : confirmed
                      ? t("dashboard:review_confirmed_badge")
                      : t("reference_script_plan_confirm_continue")}
              </Button>
            )}
          </div>
        </header>
      )}
      {discardDialog}

      {videoModelBlocked && !agentEditing && <VideoModelUnresolvedNotice projectName={projectName} />}
      {firstUnknownProblem && !agentEditing && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn/10 p-3 text-sm text-subtle-foreground">
          {firstUnknownProblem.hint}
        </p>
      )}

      {overwrite && !agentEditing && (
        <ScriptOverwriteConfirmDialog
          open={overwriteOpen}
          overwrite={overwrite}
          loading={confirming}
          confirmDisabled={overwriteBlocked}
          onConfirm={async () => {
            // Failures such as concurrent writes keep dialogs open with refreshed overwrite lists.
            if (await handleConfirm({ overwriteRevision: overwrite.revision })) setOverwriteOpen(false);
          }}
          onCancel={() => setOverwriteOpen(false)}
        />
      )}

      {/* Compare episode totals/project targets; omit without targets, warn without blocking confirmation when exceeded. */}
      <EpisodeDurationSummary
        totalSeconds={sumItemDuration(displayUnits)}
        targetSeconds={state?.episode_target_duration ?? null}
      />

      {draft != null && (
        <NewAssetsSection
          items={newAssets}
          entries={newAssetEntries(displayUnits)}
          readOnly={readOnly}
          disabled={busy}
          onChange={updateNewAssets}
        />
      )}

      {!readOnly && <PlanStructureHint />}

      <div className="flex flex-col gap-2.5">
        {displayUnits.map((unit, i) => {
          const capability = unitCapability(unit, durationTiers);
          return (
            <UnitCard
              key={unit.key}
              unit={unit}
              violations={partitionViolations([])}
              softViolations={softByUnit.get(i) ?? []}
              lookup={reviewLookup}
              projectHasScene={projectHasScene}
              onScrollRef={setCardRef}
              editing={!readOnly && editingUnitKey === unit.key}
              onToggleEdit={() => setEditingUnitKey((prev) => (prev === unit.key ? null : unit.key))}
              onTextChange={readOnly ? null : (text) => updateUnit(i, { text })}
              onSourceTextChange={readOnly ? null : (sourceText) => updateUnit(i, { source_text: sourceText })}
              supportedDurations={unitTiers(capability)}
              durationEndpointFixed={capability?.duration_endpoint_fixed ?? false}
              durationProblem={
                unknownUnitKeys.has(unit.key) && capability?.problem
                  ? tierProblemText(t, capability.problem, capability.hydrated_capability)
                  : null
              }
              split={capability != null && capability.problems.length > 0 ? capability : null}
              outOfTier={outOfTierUnitKeys.has(unit.key)}
              onDurationChange={readOnly ? null : (seconds) => updateUnit(i, { duration_seconds: seconds })}
              busy={busy}
            />
          );
        })}
      </div>
    </div>
  );
}
