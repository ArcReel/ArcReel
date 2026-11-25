import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ChevronDown, OctagonAlert, Pencil, RotateCcw } from "lucide-react";
import type {
  DraftSoftViolation,
  PlanNewAsset,
  ReferenceScriptPlanDraft,
  ReferenceUnitCapability,
  ScriptReviewState,
  ScriptReviewViolation,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useModelCapabilities } from "@/hooks/useModelCapabilities";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { voidPromise } from "@/utils/async";
import { draftViolationMessage, groupDraftViolations, groupSoftViolations } from "@/utils/draft-violations";
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
import {
  ReviewEpisodeBlockingIssues,
  ReviewSoftViolationList,
  ScriptReviewStatusHeader,
} from "@/components/canvas/shared/ScriptReviewDiagnostics";
import { scriptPlanFixRequestText, withUniqueKeys } from "@/components/canvas/shared/script-review-diagnostics";
import { ScriptHighlight } from "@/components/shared/ScriptHighlight";
import { toScriptLines, type MentionLookup } from "@/hooks/useUnitPromptHighlight";
import { dialogueSpeakers, extractMentions, normalizeAssetName } from "@/utils/reference-mentions";
import { NewAssetsSection, type NewAssetEntryRefs } from "@/components/canvas/shared/NewAssetsSection";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef } from "@/utils/episode-display";
import { ItemPositionBadge } from "@/components/shared/episode-item/ItemPositionBadge";
import { tierProblemText } from "./unit-tier-problem";
import { ReferenceSplitAlert } from "./ReferenceSplitAlert";

interface ReferenceScriptPlanPreviewPanelProps {
  projectName: string;
  episode: number;
  videoModelUnresolved?: boolean;
  planningDurations?: number[];
  lookup: MentionLookup;
  onOpenTimeline?: () => void;
}

const SOURCE_ANCHOR_CODES = new Set(["source_text_not_verbatim", "source_text_empty"]);
const SPEECH_VIOLATION_KEYS: Record<string, string> = {
  mixed_speech: "speech_admission_mixed_speech",
  needs_replan: "speech_admission_needs_replan",
  parse_failed: "speech_admission_parse_failed",
  empty_speaker: "speech_admission_empty_speaker",
};

interface DisplayUnit {
  key: string;
  position: number;
  duration_seconds: number;
  sourceText: string;
  scriptText: string;
}

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
  }));
}

function newAssetEntries(units: DisplayUnit[]): NewAssetEntryRefs[] {
  return units.map((unit) => ({
    id: unit.key,
    position: unit.position,
    kind: "unit" as const,
    names: [...extractMentions(unit.scriptText), ...dialogueSpeakers(unit.scriptText)],
    snippet: unit.sourceText,
  }));
}

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

interface UnitViolations {
  anchorSource: ScriptReviewViolation[];
  byLine: Map<number, ScriptReviewViolation[]>;
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

function unitCapability(unit: DisplayUnit, tiers: ScriptReviewState["duration_tiers"]): ReferenceUnitCapability | null {
  return tiers?.units[unit.key] ?? null;
}

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
        const speechKey = v.params == null ? SPEECH_VIOLATION_KEYS[v.code] : undefined;
        const location = v.locations
          ?.map(({ path, line }) => `${path.join(".")}${line === null ? "" : `:${line + 1}`}`)
          .join(", ");
        const message =
          speechKey && location
            ? t(speechKey, { position, location })
            : draftViolationMessage(v, t, position);
        return (
          // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 违约由服务端整表算出、没有 ID，同一 code 可重复出现；只读文本、行内无状态
          <p key={`${v.code}-${i}`} className="mt-1 flex items-start gap-1.5 pl-1 text-xs leading-snug text-destructive">
            <OctagonAlert className="mt-px size-3 shrink-0" aria-hidden="true" />
            <span className="wrap-break-word">{message}</span>
          </p>
        );
      })}
    </>
  );
}

function UnitCard({
  unit,
  index,
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
  index: number;
  violations: UnitViolations;
  softViolations: DraftSoftViolation[];
  lookup: MentionLookup;
  projectHasScene: boolean;
  onScrollRef: (index: number, el: HTMLElement | null) => void;
  editing: boolean;
  onToggleEdit: () => void;
  onTextChange: ((text: string) => void) | null;
  onSourceTextChange: ((sourceText: string) => void) | null;
  supportedDurations: number[] | null;
  durationEndpointFixed: boolean;
  durationProblem: { label: string; hint: string } | null;
  split: ReferenceUnitCapability | null;
  outOfTier: boolean;
  onDurationChange: ((seconds: number) => void) | null;
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
  const durationOptions = onDurationChange && supportedDurations?.length ? supportedDurations : null;
  const lineViolations = useMemo(
    () => [...violations.byLine.entries()].sort(([a], [b]) => a - b).flatMap(([, list]) => list),
    [violations.byLine],
  );

  return (
    <article
      ref={(el) => onScrollRef(index, el)}
      className={`scroll-mt-28 rounded-lg border bg-card p-4 ${hasViolation ? "border-destructive/45" : "border-border"}`}
    >
      <div className="flex flex-wrap items-center gap-2">
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
        {editing && onSourceTextChange ? (
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
            className={`mt-1.5 border-l pl-3 text-xs leading-relaxed wrap-break-word ${
              anchorBroken ? "border-destructive/50 text-destructive" : "border-border text-muted-foreground"
            }`}
          >
            {unit.sourceText}
          </p>
        )}
        <InlineViolations violations={violations.anchorSource} position={unit.position} />
      </details>

      <div className="mt-3">
        {editing && onTextChange ? (
          <>
            <Textarea
              value={unit.scriptText}
              onChange={(e) => onTextChange(e.target.value)}
              disabled={busy}
              aria-label={t("reference_script_plan_unit_text_label", { unit: unitLabel })}
            />
            <InlineViolations violations={lineViolations} position={unit.position} />
          </>
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

      {lacksScene && (
        <p className="mt-2 flex items-start gap-1.5 pl-1 text-xs leading-snug text-subtle-foreground">
          <AlertTriangle className="mt-px size-3 shrink-0 text-warn" aria-hidden="true" />
          <span>{t("reference_script_plan_unit_without_scene")}</span>
        </p>
      )}
      <ReviewSoftViolationList softViolations={softViolations} position={unit.position} />
    </article>
  );
}

function selectUnitsContent(state: ScriptReviewState): ReferenceScriptPlanDraft | null {
  return state.content != null && "units" in state.content ? state.content : null;
}

const LIVE_SOFT_VIOLATION_CODES: ReadonlySet<string> = new Set(["ref_warn_unit_without_scene"]);

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
  const unitTiers = (capability: ReferenceUnitCapability | null | undefined): number[] | null =>
    capability?.duration_endpoint_fixed ? fixedPlanningDurations : (capability?.allowed_durations ?? null);
  const pushToast = useAppStore((s) => s.pushToast);
  const savedInstructions = useProjectsStore(
    (s) =>
      s.currentProjectData?.episodes?.find((ep) => ep.episode === episode)?.script_plan_instructions ?? "",
  );

  const [editingUnitKey, setEditingUnitKey] = useState<string | null>(null);
  const [overwriteOpen, setOverwriteOpen] = useState(false);

  const handleConfirmed = useCallback(() => {
    if (useProjectsStore.getState().currentProjectName !== projectName) return;
    pushToast(t("dashboard:review_confirmed"), "success");
  }, [projectName, pushToast, t]);

  const {
    state,
    draft,
    setDraft,
    dirty,
    outdated,
    loading,
    loadError,
    saving,
    busy,
    retry: handleRetry,
    reloadLatest,
    save: handleSave,
    confirm: handleConfirm,
    confirming,
  } = useScriptReviewDraft<ReferenceScriptPlanDraft>({
    projectName,
    episode,
    selectContent: selectUnitsContent,
    onConfirmed: handleConfirmed,
  });

  const updateUnit = useCallback(
    (unitIndex: number, patch: { text?: string; source_text?: string; duration_seconds?: number }) => {
      setDraft((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          units: prev.units.map((u, i) => (i === unitIndex ? { ...u, ...patch } : u)),
        };
      });
    },
    [setDraft],
  );

  const updateNewAssets = useCallback(
    (items: PlanNewAsset[]) => {
      setDraft((prev) => (prev ? { ...prev, new_assets: items } : prev));
    },
    [setDraft],
  );

  const projectHasScene = useMemo(() => Object.values(lookup).some((kind) => kind === "scene"), [lookup]);

  const cardRefs = useRef(new Map<number, HTMLElement>());
  const episodeLevelRef = useRef<HTMLElement | null>(null);
  const setCardRef = useCallback((index: number, el: HTMLElement | null) => {
    if (el) cardRefs.current.set(index, el);
    else cardRefs.current.delete(index);
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
  if (draft == null || (status === "no_script_plan" && !dirty)) {
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

  const blankStartAction =
    state?.script_overwrite == null && status !== "confirmed" && status !== "no_script_plan" ? (
      <StartBlankScriptButton projectName={projectName} episode={episode} discardsPlan />
    ) : null;

  const confirmed = status === "confirmed";
  const removed = status === "no_script_plan";
  const readOnly = confirmed || removed;
  const overwrite = readOnly ? null : (state?.script_overwrite ?? null);
  const scriptMissing = confirmed && state?.script_overwrite == null;
  const confirmLocked = confirmed && !scriptMissing;
  const blockingIssues = outdated ? [] : (state?.blocking_issues ?? []);
  const videoModelBlocked = modelUnresolved && !confirmLocked;
  const displayUnits = structuredDisplayUnits(draft);
  const groups = groupDraftViolations(
    blockingIssues,
    displayUnits.map((u) => u.key),
  );
  const newAssets = draft.new_assets ?? [];
  const reviewLookup = lookupWithNewAssets(lookup, newAssets);
  const softByUnit = groupSoftViolations(outdated ? [] : (state?.soft_violations ?? []), LIVE_SOFT_VIOLATION_CODES);
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
  const unknownUnits = displayUnits.flatMap((u) => {
    const capability = unitCapability(u, durationTiers);
    return capability?.problem ? [{ key: u.key, capability }] : [];
  });
  const unknownUnitKeys = new Set(unknownUnits.map((u) => u.key));
  const firstUnknownProblem =
    unknownUnits.length > 0
      ? tierProblemText(t, unknownUnits[0].capability.problem!, unknownUnits[0].capability.hydrated_capability)
      : null;
  const hasBlockingIssues = blockingIssues.length > 0;
  const onlyCapabilityBlocking =
    videoModelBlocked && hasBlockingIssues && blockingIssues.every((issue) => issue.code === "video_capability_unavailable");
  const confirmBlocked =
    outdated || removed || videoModelBlocked || outOfTierUnitKeys.size > 0 || unknownUnitKeys.size > 0 || hasBlockingIssues;
  const confirmBlockedHint =
    outdated || removed
      ? t(confirmed ? "dashboard:review_confirmed_conflict_hint" : "dashboard:review_outdated_hint")
      : hasBlockingIssues && !onlyCapabilityBlocking
        ? t("dashboard:review_blocking_confirm_disabled_hint", { count: blockingIssues.length })
        : videoModelBlocked
          ? t("dashboard:review_video_model_unresolved_hint")
          : firstUnknownProblem
            ? firstUnknownProblem.hint
            : outOfTierUnitKeys.size > 0
              ? t("reference_script_plan_duration_out_of_tier_hint")
              : undefined;

  return (
    <div className="flex flex-col gap-3">
      <ScriptReviewStatusHeader
        confirmed={confirmed}
        readOnly={readOnly}
        scriptMissing={scriptMissing}
        overwrite={overwrite != null}
        blockingCount={blockingIssues.length}
        itemJumps={[...groups.byItem.entries()].map(([index, list]) => ({
          index,
          label: t("common:item_position_unit", { position: index + 1 }),
          count: list.length,
        }))}
        episodeLevelCount={groups.episodeLevel.length}
        onJump={(index) => scrollTo(cardRefs.current.get(index))}
        onJumpEpisodeLevel={() => scrollTo(episodeLevelRef.current)}
        dirty={dirty}
        outdated={outdated}
        busy={busy}
        saving={saving}
        confirming={confirming}
        confirmLocked={confirmLocked}
        confirmBlocked={confirmBlocked}
        confirmBlockedHint={confirmBlockedHint}
        saveLabel={t("common:save")}
        confirmLabel={t("reference_script_plan_confirm_continue")}
        onSave={voidPromise(handleSave)}
        onConfirm={voidPromise(() => handleConfirm())}
        onRequestOverwrite={() => setOverwriteOpen(true)}
        onReloadLatest={reloadLatest}
        onHandToAgent={
          !confirmed && hasBlockingIssues
            ? () => prefillAssistant(scriptPlanFixRequestText(t, episodeRef, blockingIssues))
            : undefined
        }
        onOpenTimeline={confirmed ? onOpenTimeline : undefined}
        regenerateAction={
          <>
            {blankStartAction}
            <ScriptPlanButton
              projectName={projectName}
              episode={episode}
              replaces={confirmed ? "confirmed_plan" : "pending_plan"}
              disabledReason={dirty ? t("dashboard:script_plan_dirty_hint") : null}
            />
          </>
        }
      />

      {videoModelBlocked && <VideoModelUnresolvedNotice projectName={projectName} />}
      {firstUnknownProblem && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn/10 p-3 text-sm text-subtle-foreground">
          {firstUnknownProblem.hint}
        </p>
      )}

      <ReviewEpisodeBlockingIssues
        issues={groups.episodeLevel}
        anchorRef={(el) => {
          episodeLevelRef.current = el;
        }}
      />

      {overwrite && (
        <ScriptOverwriteConfirmDialog
          open={overwriteOpen}
          overwrite={overwrite}
          loading={confirming}
          confirmDisabled={confirmBlocked}
          onConfirm={async () => {
            if (await handleConfirm({ overwriteRevision: overwrite.revision })) setOverwriteOpen(false);
          }}
          onCancel={() => setOverwriteOpen(false)}
        />
      )}

      <EpisodeDurationSummary
        totalSeconds={sumItemDuration(displayUnits)}
        targetSeconds={state?.episode_target_duration ?? null}
      />

      <NewAssetsSection
        items={newAssets}
        entries={newAssetEntries(displayUnits)}
        readOnly={readOnly}
        disabled={busy}
        onChange={updateNewAssets}
      />

      {!readOnly && <PlanStructureHint />}

      <div className="flex flex-col gap-2.5">
        {withUniqueKeys(displayUnits, (unit) => unit.key).map(({ item: unit, key: unitKey, index: i }) => {
          const capability = unitCapability(unit, durationTiers);
          return (
            <UnitCard
              key={unitKey}
              unit={unit}
              index={i}
              violations={partitionViolations(groups.byItem.get(i) ?? [])}
              softViolations={softByUnit.get(i) ?? []}
              lookup={reviewLookup}
              projectHasScene={projectHasScene}
              onScrollRef={setCardRef}
              editing={!readOnly && editingUnitKey === unitKey}
              onToggleEdit={() => setEditingUnitKey((prev) => (prev === unitKey ? null : unitKey))}
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
