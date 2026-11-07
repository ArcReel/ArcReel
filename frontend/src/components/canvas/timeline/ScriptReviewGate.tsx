import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, Loader2, Lock, RotateCcw, Save } from "lucide-react";
import { cn } from "cn";
import type {
  DraftSoftViolation,
  DramaNormalizedScript,
  DramaSceneContent,
  NarrationScriptPlanDraft,
  NarrationScriptPlanSegment,
  PlanNewAsset,
  ScriptReviewState,
  ScriptReviewViolation,
  Utterance,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useDraftEditor } from "@/hooks/useDraftEditor";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { voidPromise } from "@/utils/async";
import { groupDraftViolations, groupSoftViolations } from "@/utils/draft-violations";
import {
  AgentDraftBar,
  DiscardDraftDialog,
  DraftEpisodeViolations,
  DraftSoftViolationList,
  DraftViolationList,
  InvalidDraftBar,
} from "@/components/shared/DraftStatus";
import { draftFallbackText, draftFixRequestText } from "@/components/shared/draft-text";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { EpisodeDurationSummary } from "@/components/shared/EpisodeDurationSummary";
import { ScriptPlanButton } from "@/components/canvas/shared/ScriptPlanButton";
import { StartBlankScriptButton } from "@/components/canvas/shared/StartBlankScriptButton";
import { NewAssetsSection, type NewAssetEntryRefs } from "@/components/canvas/shared/NewAssetsSection";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { VideoModelUnresolvedNotice } from "@/components/shared/VideoModelUnresolvedNotice";
import { useModelCapabilities, type DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import { durationIncompatibleLabel } from "@/components/canvas/shared/duration-incompatible";
import { PlanDurationSelect } from "@/components/canvas/shared/PlanDurationSelect";
import { PlanStructureHint } from "@/components/canvas/shared/PlanStructureHint";
import { hasValidNewAssets, speakerCandidates } from "@/utils/plan-new-assets";
import { ScriptPlanStart } from "@/components/canvas/shared/ScriptPlanStart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { sumItemDuration } from "@/utils/script-shape";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef } from "@/utils/episode-display";
import { ItemPositionBadge } from "@/components/shared/episode-item/ItemPositionBadge";
import { UtteranceListEditor } from "./UtteranceListEditor";
import { ReferencesSection } from "./ReferencesSection";
import { SegmentBreakToggle } from "./SegmentBreakToggle";

interface ScriptReviewGateProps {
  projectName: string;
  episode: number;
  contentMode: "narration" | "drama";
  videoModelUnresolved?: boolean;
  /** Switch to episode timelines; confirmed read-only state uses it for edit navigation, omitted means no entry. */
  onOpenTimeline?: () => void;
  /** Current video-model/resolution script-plan duration tiers match confirmation conversion; unknown means read-only durations. */
  durationOptions?: number[];
  durationEndpointFixed?: boolean;
  /** Cause of out-of-tier duration controls wording, default model unsupported. */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
}

/** Entry-edit context: duration tiers, episode-new assets, speaker candidates. */
interface PlanItemContext {
  projectName: string;
  durationOptions: number[] | null;
  durationEndpointFixed: boolean;
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  newAssets: PlanNewAsset[];
  speakerNames: string[];
}

/** Out-of-tier durations reject confirmation and show red card explanations; do not judge unknown tiers. */
function durationOutOfTier(seconds: number, options: number[] | null): boolean {
  return options != null && options.length > 0 && !options.includes(seconds);
}

/** Entry-card body section subtitle. */
function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return <h4 className={cn("mb-1.5 text-xs font-medium text-muted-foreground", className)}>{children}</h4>;
}

/** Editable script_plan draft union for drama/narration. */
type ReviewDraft = DramaNormalizedScript | NarrationScriptPlanDraft;

/** Drama/narration panel; reference_video does not route here. */
function selectReviewContent(state: ScriptReviewState): ReviewDraft | null {
  return (state.content ?? null) as ReviewDraft | null;
}

/** Read-only appearing character/scene/prop reference pills. */
function MetaChips({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((name) => (
        <Badge key={name} variant="outline">
          {name}
        </Badge>
      ))}
    </div>
  );
}

/** Entry header: position/duration/chapter break; editable tiers/select and break toggle, red explanations for out-of-tier values. */
function ItemHeader({
  position,
  durationSeconds,
  segmentBreak,
  readOnly,
  disabled,
  context,
  onChange,
}: {
  /** One-based planning-entry position. */
  position: number;
  durationSeconds: number;
  segmentBreak: boolean;
  readOnly: boolean;
  disabled: boolean;
  context: PlanItemContext;
  onChange: (patch: { duration_seconds?: number; segment_break?: boolean }) => void;
}) {
  const { t } = useTranslation("dashboard");
  const itemLabel = t("common:item_position_shot", { position });
  const outOfTier = !readOnly && durationOutOfTier(durationSeconds, context.durationOptions);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <ItemPositionBadge position={position} kind="shot" />
        {readOnly ? (
          <>
            <span className="text-xs text-muted-foreground tabular-nums">{durationSeconds}s</span>
            {segmentBreak && <Badge variant="outline">{t("review_segment_break")}</Badge>}
          </>
        ) : (
          <>
            <PlanDurationSelect
              seconds={durationSeconds}
              options={context.durationOptions}
              onChange={(duration_seconds) => onChange({ duration_seconds })}
              disabled={disabled}
              label={t("review_item_duration_label", { item: itemLabel })}
              endpointFixed={context.durationEndpointFixed}
            />
            <SegmentBreakToggle
              checked={segmentBreak}
              onChange={(segment_break) => onChange({ segment_break })}
              disabled={disabled}
            />
          </>
        )}
      </div>
      {outOfTier && context.durationOptions && (
        <p className="flex items-start gap-1.5 text-xs leading-snug text-destructive">
          <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden="true" />
          <span>
            {durationIncompatibleLabel(
              t,
              durationSeconds,
              context.durationOptions,
              context.durationWarningReason?.(durationSeconds),
            )}
          </span>
        </p>
      )}
    </div>
  );
}

/** Entry character/scene/prop references reuse timeline editors when editable, including episode-new candidates. */
function ItemReferences({
  contentMode,
  characters,
  scenes,
  props,
  disabled,
  context,
  onChange,
}: {
  contentMode: "narration" | "drama";
  characters: string[];
  scenes: string[];
  props: string[];
  disabled: boolean;
  context: PlanItemContext;
  onChange: (patch: Record<string, string[]>) => void;
}) {
  return (
    <div className="mb-3">
      <ReferencesSection
        projectName={context.projectName}
        contentMode={contentMode}
        characterNames={characters}
        sceneNames={scenes}
        propNames={props}
        onSave={onChange}
        disabled={disabled}
        newAssets={context.newAssets}
      />
    </div>
  );
}

/** Read-only body preserves newlines, omitting empties. */
function ReadOnlyText({ text, className = "" }: { text: string; className?: string }) {
  if (!text) return null;
  return <p className={cn("text-sm leading-relaxed whitespace-pre-wrap", className)}>{text}</p>;
}

function ReadOnlyUtterances({ utterances }: { utterances: Utterance[] }) {
  const { t } = useTranslation("dashboard");
  return (
    <ul className="flex flex-col gap-1.5">
      {utterances.map((u, i) => (
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Read-only dialogue replaced with whole scripts, never locally reordered/added/deleted, without IDs or row state
        <li key={String(i)} className="flex items-start gap-2 text-sm leading-relaxed">
          <Badge variant="outline" className="mt-0.5">
            {u.kind === "dialogue" ? u.speaker : t("utterance_kind_voiceover")}
          </Badge>
          <span className={u.kind === "voiceover" ? "italic text-subtle-foreground" : "text-foreground"}>{u.text}</span>
        </li>
      ))}
    </ul>
  );
}

/** Draft-card violations: red violations/amber fallback hints attach to entries; cards are navigation targets. */
interface ItemDraftNotes {
  violations: ScriptReviewViolation[];
  softViolations: DraftSoftViolation[];
  anchorRef: (el: HTMLElement | null) => void;
}

function ItemCardShell({ notes, children }: { notes?: ItemDraftNotes; children: React.ReactNode }) {
  const violating = (notes?.violations.length ?? 0) > 0;
  return (
    <article
      ref={notes?.anchorRef}
      className={cn("scroll-mt-28 rounded-lg border bg-card p-3.5", violating ? "border-destructive/45" : "border-border")}
    >
      {children}
      {notes && (
        <>
          <DraftViolationList violations={notes.violations} />
          <DraftSoftViolationList softViolations={notes.softViolations} />
        </>
      )}
    </article>
  );
}

function DramaSceneCard({
  scene,
  position,
  disabled,
  readOnly,
  notes,
  context,
  onChange,
}: {
  scene: DramaSceneContent;
  /** One-based planning-entry position. */
  position: number;
  disabled: boolean;
  readOnly: boolean;
  notes?: ItemDraftNotes;
  context: PlanItemContext;
  onChange: (patch: Partial<DramaSceneContent>) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <ItemCardShell notes={notes}>
      <div className={`flex items-start justify-between gap-2 ${readOnly ? "mb-3" : "mb-2"}`}>
        <ItemHeader
          position={position}
          durationSeconds={scene.duration_seconds}
          segmentBreak={scene.segment_break}
          readOnly={readOnly}
          disabled={disabled}
          context={context}
          onChange={onChange}
        />
        {readOnly && <MetaChips items={[...scene.characters_in_scene, ...scene.scenes, ...scene.props]} />}
      </div>
      {!readOnly && (
        <ItemReferences
          contentMode="drama"
          characters={scene.characters_in_scene}
          scenes={scene.scenes}
          props={scene.props}
          disabled={disabled}
          context={context}
          onChange={onChange}
        />
      )}

      <SectionLabel>{t("review_utterances_label")}</SectionLabel>
      {readOnly ? (
        <ReadOnlyUtterances utterances={scene.utterances} />
      ) : (
        <UtteranceListEditor
          utterances={scene.utterances}
          disabled={disabled}
          speakerCandidates={context.speakerNames}
          onChange={(utterances: Utterance[]) => onChange({ utterances })}
        />
      )}

      <SectionLabel className="mt-3">{t("review_source_text_label")}</SectionLabel>
      {readOnly ? (
        <ReadOnlyText text={scene.source_text} className="text-muted-foreground" />
      ) : (
        <Textarea
          value={scene.source_text}
          disabled={disabled}
          onChange={(e) => onChange({ source_text: e.target.value })}
          placeholder={t("review_source_text_placeholder")}
          aria-label={t("review_source_text_label")}
        />
      )}
    </ItemCardShell>
  );
}

function NarrationSegmentCard({
  segment,
  position,
  disabled,
  readOnly,
  notes,
  context,
  onChange,
}: {
  segment: NarrationScriptPlanSegment;
  /** One-based planning-entry position. */
  position: number;
  disabled: boolean;
  readOnly: boolean;
  notes?: ItemDraftNotes;
  context: PlanItemContext;
  onChange: (patch: Partial<NarrationScriptPlanSegment>) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <ItemCardShell notes={notes}>
      <div className={`flex items-start justify-between gap-2 ${readOnly ? "mb-3" : "mb-2"}`}>
        <ItemHeader
          position={position}
          durationSeconds={segment.duration_seconds}
          segmentBreak={segment.segment_break}
          readOnly={readOnly}
          disabled={disabled}
          context={context}
          onChange={onChange}
        />
        {readOnly && <MetaChips items={[...segment.characters_in_segment, ...segment.scenes, ...segment.props]} />}
      </div>
      {!readOnly && (
        <ItemReferences
          contentMode="narration"
          characters={segment.characters_in_segment}
          scenes={segment.scenes}
          props={segment.props}
          disabled={disabled}
          context={context}
          onChange={onChange}
        />
      )}

      <SectionLabel>{t("review_novel_text_label")}</SectionLabel>
      {readOnly ? (
        <ReadOnlyText text={segment.novel_text} className="text-foreground" />
      ) : (
        <Textarea
          value={segment.novel_text}
          onChange={(e) => onChange({ novel_text: e.target.value })}
          placeholder={t("review_novel_text_placeholder")}
          aria-label={t("review_novel_text_label")}
          disabled={disabled}
        />
      )}
    </ItemCardShell>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isUtterance(value: unknown): value is Utterance {
  if (!isRecord(value) || typeof value.text !== "string") return false;
  return value.kind === "voiceover" || (value.kind === "dialogue" && typeof value.speaker === "string");
}

function isDramaScene(value: unknown): value is DramaSceneContent {
  return (
    isRecord(value) &&
    typeof value.scene_id === "string" &&
    typeof value.duration_seconds === "number" &&
    typeof value.segment_break === "boolean" &&
    isStringArray(value.characters_in_scene) &&
    Array.isArray(value.utterances) &&
    value.utterances.every(isUtterance) &&
    typeof value.source_text === "string"
  );
}

function isNarrationSegment(value: unknown): value is NarrationScriptPlanSegment {
  return (
    isRecord(value) &&
    typeof value.segment_id === "string" &&
    typeof value.novel_text === "string" &&
    typeof value.duration_seconds === "number" &&
    typeof value.segment_break === "boolean" &&
    isStringArray(value.characters_in_segment)
  );
}

/**
 * Draft body → editable cards. Narrow fields individually, distrusting declarations: Agents may corrupt drafts; forced rendering
 * would crash or invent missing values users never wrote. Return null if narrowing fails, showing only violations/disposition.
 */
function narrowDramaDraft(content: Record<string, unknown> | null): ReviewDraft | null {
  const scenes = content?.scenes;
  if (!Array.isArray(scenes) || !scenes.every(isDramaScene) || !hasValidNewAssets(content)) return null;
  return content as unknown as DramaNormalizedScript;
}

function narrowNarrationDraft(content: Record<string, unknown> | null): ReviewDraft | null {
  const segments = content?.segments;
  if (!Array.isArray(segments) || !segments.every(isNarrationSegment) || !hasValidNewAssets(content)) return null;
  return content as unknown as NarrationScriptPlanDraft;
}

function reviewItems(content: ReviewDraft | null): { id: string }[] {
  if (content == null) return [];
  return "scenes" in content
    ? content.scenes.map((scene) => ({ id: scene.scene_id }))
    : content.segments.map((segment) => ({ id: segment.segment_id }));
}

/** Referenced names/source excerpts per entry, expanding appearances in new-assets sections. */
function newAssetEntries(content: ReviewDraft | null): NewAssetEntryRefs[] {
  if (content == null) return [];
  return "scenes" in content
    ? content.scenes.map((scene, i) => ({
        id: scene.scene_id,
        position: i + 1,
        kind: "shot" as const,
        names: [
          ...scene.characters_in_scene,
          ...scene.scenes,
          ...scene.props,
          ...scene.utterances.flatMap((u) => (u.kind === "dialogue" ? [u.speaker] : [])),
        ],
        snippet: scene.source_text,
      }))
    : content.segments.map((segment, i) => ({
        id: segment.segment_id,
        position: i + 1,
        kind: "shot" as const,
        names: [...segment.characters_in_segment, ...segment.scenes, ...segment.props],
        snippet: segment.novel_text,
      }));
}

/**
 * script_plan→prompt_authoring web confirmation: structured plans displayed/editable manually or by Agents;
 * explicit user confirmation alone permits prompt_authoring visual generation. Drama (utterances + source_text) and narration
 * (novel_text) share this panel; reference_video uses `ReferenceScriptPlanPreviewPanel`.
 *
 * Quarantined drafts replace displayed content, with entry-level violations attached and episode-level at top. Edit/save/validate
 * in place; cleared violations adopt drafts. Agent-editable drafts only indicate unfinished changes, keeping formal content read-only. After confirmation,
 * plans remain read-only without card editors, directing changes to timelines.
 */
export function ScriptReviewGate({
  projectName,
  episode,
  contentMode,
  videoModelUnresolved,
  onOpenTimeline,
  durationOptions,
  durationEndpointFixed = false,
  durationWarningReason,
}: ScriptReviewGateProps) {
  const { t } = useTranslation("dashboard");
  const episodeLedger = useEpisodeLedger();
  const episodeRef = episodeAgentRef(episodeLedger, episode, t);
  // Parent-supplied model capabilities (resolvability/duration tiers) suppress local queries.
  const standaloneCapabilities = useModelCapabilities({
    projectName,
    enabled: videoModelUnresolved === undefined || durationOptions === undefined,
  });
  const modelUnresolved = videoModelUnresolved ?? standaloneCapabilities.videoModelUnresolved;
  const pushToast = useAppStore((s) => s.pushToast);
  const [overwriteOpen, setOverwriteOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);

  const handleConfirmed = useCallback(() => {
    pushToast(t("dashboard:review_confirmed"), "success");
  }, [pushToast, t]);

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
  } = useScriptReviewDraft<ReviewDraft>({
    projectName,
    episode,
    selectContent: selectReviewContent,
    onConfirmed: handleConfirmed,
  });

  const quarantine = state?.quarantine ?? null;
  const draftEditor = useDraftEditor<ReviewDraft>({
    projectName,
    episode,
    view: quarantine,
    narrow: contentMode === "drama" ? narrowDramaDraft : narrowNarrationDraft,
    onSettled: refresh,
  });

  const projectCharacters = useProjectsStore((s) => s.currentProjectData?.characters);
  const savedInstructions = useProjectsStore(
    (s) => s.currentProjectData?.episodes?.find((entry) => entry.episode === episode)?.script_plan_instructions ?? "",
  );
  const shownContent = quarantine != null && quarantine.editable_by === "user" ? draftEditor.content : draft;
  const shownNewAssets = shownContent?.new_assets;
  const itemContext = useMemo<PlanItemContext>(() => {
    const newAssets = shownNewAssets ?? [];
    return {
      projectName,
      durationOptions: durationOptions ?? standaloneCapabilities.planningDurations,
      durationEndpointFixed: durationEndpointFixed || (durationOptions === undefined && standaloneCapabilities.durationEndpointFixed),
      durationWarningReason,
      newAssets,
      speakerNames: speakerCandidates(projectCharacters ?? {}, newAssets),
    };
  }, [
    projectName,
    durationOptions,
    durationEndpointFixed,
    durationWarningReason,
    standaloneCapabilities.planningDurations,
    standaloneCapabilities.durationEndpointFixed,
    shownNewAssets,
    projectCharacters,
  ]);

  const itemRefs = useRef(new Map<number, HTMLElement>());
  const episodeLevelRef = useRef<HTMLElement | null>(null);
  const scrollTo = (el: HTMLElement | null | undefined) => el?.scrollIntoView({ behavior: "smooth", block: "center" });

  const updateDramaScene = (index: number, patch: Partial<DramaSceneContent>) => {
    const apply = (prev: ReviewDraft) =>
      "scenes" in prev ? { ...prev, scenes: prev.scenes.map((s, i) => (i === index ? { ...s, ...patch } : s)) } : prev;
    if (quarantine != null) draftEditor.setContent(apply);
    else setDraft((prev) => (prev ? apply(prev) : prev));
  };

  const updateNarrationSegment = (index: number, patch: Partial<NarrationScriptPlanSegment>) => {
    const apply = (prev: ReviewDraft) =>
      "segments" in prev
        ? { ...prev, segments: prev.segments.map((s, i) => (i === index ? { ...s, ...patch } : s)) }
        : prev;
    if (quarantine != null) draftEditor.setContent(apply);
    else setDraft((prev) => (prev ? apply(prev) : prev));
  };

  const updateNewAssets = (items: PlanNewAsset[]) => {
    const apply = (prev: ReviewDraft): ReviewDraft => ({ ...prev, new_assets: items });
    if (quarantine != null) draftEditor.setContent(apply);
    else setDraft((prev) => (prev ? apply(prev) : prev));
  };

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-muted-foreground">{t("dashboard:loading_script_plan")}</div>;
  }

  // Loading errors differ from absent script_plan empty states; show error/retry.
  if (loadError) {
    return (
      <div role="alert" className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertTriangle className="size-6 text-warn" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-subtle-foreground">{t("dashboard:review_load_failed")}</p>
          {loadError.message && <p className="max-w-sm px-4 text-xs text-muted-foreground">{loadError.message}</p>}
        </div>
        <Button variant="outline" size="sm" onClick={handleRetry}>
          <RotateCcw aria-hidden data-icon="inline-start" />
          {t("dashboard:review_retry")}
        </Button>
      </div>
    );
  }

  const status = state?.status ?? "no_script_plan";
  if (status === "no_script_plan" || (draft == null && quarantine == null)) {
    // Neither plans nor formal scripts means first-planning start area. Existing formal scripts (e.g. blank starts) require overwrite confirmation for new plans.
    const formalScript = state?.script_overwrite != null || onOpenTimeline !== undefined;
    if (status === "no_script_plan" && !formalScript) {
      return (
        <div className="mx-auto w-full max-w-190">
          <ScriptPlanStart projectName={projectName} episode={episode} savedInstructions={savedInstructions} />
        </div>
      );
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

  const docType = contentMode === "drama" ? "drama_script_plan" : "narration_script_plan";
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

  // Without formal scripts/unconfirmed plans, users may discard these plans and start blank; confirm plan/quarantined-draft discard first.
  const blankStartAction =
    state?.script_overwrite == null && status !== "confirmed" ? (
      <StartBlankScriptButton projectName={projectName} episode={episode} discardsPlan variant="ghost" />
    ) : null;

  // Quarantined drafts present: show drafts; formal confirmation unavailable, sharing endpoint rejection criteria.
  if (quarantine != null && quarantine.editable_by === "user") {
    const content = draftEditor.content;
    const items = reviewItems(content);
    const groups = groupDraftViolations(
      quarantine.violations,
      items.map((item) => item.id),
    );
    const softByItem = groupSoftViolations(quarantine.soft_violations);
    const notesFor = (index: number): ItemDraftNotes => ({
      violations: groups.byItem.get(index) ?? [],
      softViolations: softByItem.get(index) ?? [],
      anchorRef: (el) => {
        if (el) itemRefs.current.set(index, el);
        else itemRefs.current.delete(index);
      },
    });
    return (
      <div className="mx-auto flex w-full max-w-240 flex-col gap-3">
        <InvalidDraftBar
          violationCount={quarantine.violations.length}
          itemJumps={[...groups.byItem.entries()].map(([index, list]) => ({
            index,
            label: t("common:item_position_shot", { position: index + 1 }),
            count: list.length,
          }))}
          episodeLevelCount={groups.episodeLevel.length}
          onJump={(index) => scrollTo(itemRefs.current.get(index))}
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
          onHandToAgent={() => prefillAssistant(draftFixRequestText(t, episodeRef, docType, quarantine.violations))}
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
            items={content.new_assets ?? []}
            entries={newAssetEntries(content)}
            readOnly={false}
            disabled={draftBusy}
            onChange={updateNewAssets}
          />
        )}
        {content != null && <PlanStructureHint />}
        {content != null && (
          <div className="flex flex-col gap-2.5">
            {"scenes" in content
              ? content.scenes.map((scene, i) => (
                  <DramaSceneCard
                    // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Quarantined scene_id may be missing/duplicate, the violation itself; annotations/anchors/edits address positions without reorder; include IDs to remount changed contents
                    key={`${String(i)}-${scene.scene_id}`}
                    scene={scene}
                    position={i + 1}
                    disabled={draftBusy}
                    readOnly={false}
                    notes={notesFor(i)}
                    context={itemContext}
                    onChange={(patch) => updateDramaScene(i, patch)}
                  />
                ))
              : content.segments.map((segment, i) => (
                  <NarrationSegmentCard
                    // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Quarantined segment_id may be missing/duplicate, the violation itself; annotations/anchors/edits address positions without reorder; include IDs to remount changed contents
                    key={`${String(i)}-${segment.segment_id}`}
                    segment={segment}
                    position={i + 1}
                    disabled={draftBusy}
                    readOnly={false}
                    notes={notesFor(i)}
                    context={itemContext}
                    onChange={(patch) => updateNarrationSegment(i, patch)}
                  />
                ))}
          </div>
        )}
      </div>
    );
  }

  // Agent-editable drafts keep formal content read-only, locking confirmation/editing until completion/discard.
  const agentEditing = quarantine != null;
  // Confirmed plans are read-only, matching save-endpoint rejection; edit contents on timelines.
  const confirmed = status === "confirmed" && !agentEditing;
  const readOnly = confirmed || agentEditing;
  // Existing formal scripts are wholly overwritten by confirmation; danger buttons show consequences before confirming.
  const overwrite = confirmed ? null : (state?.script_overwrite ?? null);
  // Confirmed but missing formal script (failed migration conversion/deleted file) still permits reconfirmation to materialize scripts.
  const scriptMissing = confirmed && state?.script_overwrite == null;
  const confirmLocked = confirmed && !scriptMissing;
  const videoModelBlocked = modelUnresolved && !confirmLocked;
  // Out-of-tier entries reject confirmation; reselect in place first.
  const durationBlocked =
    !confirmLocked &&
    draft != null &&
    ("scenes" in draft ? draft.scenes : draft.segments).some((item) =>
      durationOutOfTier(item.duration_seconds, itemContext.durationOptions),
    );
  const confirmBlocked = videoModelBlocked || durationBlocked;
  const confirmBlockedHint = videoModelBlocked
    ? t("dashboard:review_video_model_unresolved_hint")
    : durationBlocked
      ? t("dashboard:review_duration_out_of_tier_hint")
      : undefined;

  return (
    <div className="mx-auto flex w-full max-w-240 flex-col gap-3">
      {agentEditing ? (
        <AgentDraftBar
          busy={draftBusy}
          onFinish={() => prefillAssistant(t("dashboard:draft_agent_finish_prefill", { episodeRef, docType }))}
          onDiscard={() => setDiscardOpen(true)}
        />
      ) : (
        <ReviewStatusBar
          confirmed={confirmed}
          scriptMissing={scriptMissing}
          overwrite={overwrite != null}
          onOpenTimeline={confirmed ? onOpenTimeline : undefined}
          regenerateAction={
            <>
              {blankStartAction}
              <ScriptPlanButton
                projectName={projectName}
                episode={episode}
                replaces={confirmed ? "confirmed_plan" : "pending_plan"}
                disabledReason={dirty && !confirmed ? t("dashboard:script_plan_dirty_hint") : null}
              />
            </>
          }
          saveAction={
            dirty && !confirmed ? (
              <Button variant="outline" size="sm" onClick={voidPromise(handleSave)} disabled={busy}>
                {saving ? (
                  <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
                ) : (
                  <Save aria-hidden data-icon="inline-start" />
                )}
                {saving ? t("common:saving") : t("dashboard:review_save_action")}
              </Button>
            ) : null
          }
          confirmAction={
            overwrite ? (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setOverwriteOpen(true)}
                disabled={busy || confirmBlocked}
                title={confirmBlockedHint}
              >
                <AlertTriangle aria-hidden data-icon="inline-start" />
                {confirming ? t("dashboard:review_confirming") : t("dashboard:review_overwrite_action")}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={voidPromise(() => handleConfirm())}
                disabled={busy || confirmLocked || confirmBlocked}
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
                      : t("dashboard:review_confirm_action")}
              </Button>
            )
          }
        />
      )}
      {discardDialog}

      {videoModelBlocked && !agentEditing && <VideoModelUnresolvedNotice projectName={projectName} />}

      {overwrite && !agentEditing && (
        <ScriptOverwriteConfirmDialog
          open={overwriteOpen}
          overwrite={overwrite}
          loading={confirming}
          // Dialogs may open before capability responses report unresolvable models; inner confirmation buttons share
          // trigger criteria, avoiding a submission inevitably rejected server-side.
          confirmDisabled={confirmBlocked}
          onConfirm={async () => {
            // Failures, e.g. concurrent episode writes during confirmation, keep dialogs open with refreshed overwrite lists.
            if (await handleConfirm({ overwriteRevision: overwrite.revision })) setOverwriteOpen(false);
          }}
          onCancel={() => setOverwriteOpen(false)}
        />
      )}

      {/* Compare episode totals/project targets; omit without targets, warn without blocking confirmation when exceeded. */}
      <EpisodeDurationSummary
        totalSeconds={sumItemDuration(draft == null ? [] : "scenes" in draft ? draft.scenes : draft.segments)}
        targetSeconds={state?.episode_target_duration ?? null}
      />

      {draft != null && (
        <NewAssetsSection
          items={draft.new_assets ?? []}
          entries={newAssetEntries(draft)}
          readOnly={readOnly}
          disabled={busy}
          onChange={updateNewAssets}
        />
      )}

      {!readOnly && <PlanStructureHint />}

      <div className="flex flex-col gap-2.5">
        {contentMode === "drama" && draft != null && "scenes" in draft
          ? draft.scenes.map((scene, i) => (
              <DramaSceneCard
                // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Key by scene_id, position fallback only for missing Agent-draft IDs; cards edit in place without reorder/add/remove
                key={scene.scene_id || i}
                scene={scene}
                position={i + 1}
                disabled={busy}
                readOnly={readOnly}
                context={itemContext}
                onChange={(patch) => updateDramaScene(i, patch)}
              />
            ))
          : null}
        {contentMode === "narration" && draft != null && "segments" in draft
          ? draft.segments.map((segment, i) => (
              <NarrationSegmentCard
                // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Key by segment_id, position fallback only for missing Agent-draft IDs; cards edit in place without reorder/add/remove
                key={segment.segment_id || i}
                segment={segment}
                position={i + 1}
                disabled={busy}
                readOnly={readOnly}
                context={itemContext}
                onChange={(patch) => updateNarrationSegment(i, patch)}
              />
            ))
          : null}
      </div>
    </div>
  );
}

/** Confirmation bar: pending/confirmed/confirmed without formal script, save/confirm actions right. */
function ReviewStatusBar({
  confirmed,
  scriptMissing,
  overwrite,
  onOpenTimeline,
  regenerateAction,
  saveAction,
  confirmAction,
}: {
  confirmed: boolean;
  scriptMissing: boolean;
  overwrite: boolean;
  onOpenTimeline?: () => void;
  regenerateAction: React.ReactNode;
  saveAction: React.ReactNode;
  confirmAction: React.ReactNode;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <header className="sticky top-0 z-sticky flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-lg border border-border bg-card px-3.5 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        {confirmed ? (
          <CheckCircle2 aria-hidden className="size-4 shrink-0 text-good" />
        ) : (
          <Clock aria-hidden className="size-4 shrink-0 text-warn" />
        )}
        <div className="flex min-w-0 flex-col">
          <span className="text-sm font-medium text-foreground">
            {confirmed ? t("review_status_confirmed") : t("review_status_pending")}
          </span>
          <span className="text-xs text-muted-foreground">
            {scriptMissing
              ? t("review_script_missing_hint")
              : confirmed
                ? t("review_confirmed_hint")
                : overwrite
                  ? t("review_overwrite_hint")
                  : t("review_pending_hint")}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {onOpenTimeline && (
          <Button variant="ghost" size="sm" onClick={onOpenTimeline}>
            <ArrowRight aria-hidden data-icon="inline-start" />
            {t("review_open_timeline")}
          </Button>
        )}
        {regenerateAction}
        {saveAction}
        {confirmAction}
      </div>
    </header>
  );
}
