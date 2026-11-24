import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RotateCcw } from "lucide-react";
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
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { voidPromise } from "@/utils/async";
import { groupDraftViolations, groupSoftViolations } from "@/utils/draft-violations";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
import { EpisodeDurationSummary } from "@/components/shared/EpisodeDurationSummary";
import { ScriptPlanButton } from "@/components/canvas/shared/ScriptPlanButton";
import { StartBlankScriptButton } from "@/components/canvas/shared/StartBlankScriptButton";
import { NewAssetsSection, type NewAssetEntryRefs } from "@/components/canvas/shared/NewAssetsSection";
import {
  ReviewBlockingIssueList,
  ReviewEpisodeBlockingIssues,
  ReviewSoftViolationList,
  ScriptReviewStatusHeader,
} from "@/components/canvas/shared/ScriptReviewDiagnostics";
import { scriptPlanFixRequestText, withUniqueKeys } from "@/components/canvas/shared/script-review-diagnostics";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { VideoModelUnresolvedNotice } from "@/components/shared/VideoModelUnresolvedNotice";
import { useModelCapabilities, type DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import { durationIncompatibleLabel } from "@/components/canvas/shared/duration-incompatible";
import { PlanDurationSelect } from "@/components/canvas/shared/PlanDurationSelect";
import { PlanStructureHint } from "@/components/canvas/shared/PlanStructureHint";
import { speakerCandidates } from "@/utils/plan-new-assets";
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
  onOpenTimeline?: () => void;
  durationOptions?: number[];
  durationEndpointFixed?: boolean;
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
}

interface PlanItemContext {
  projectName: string;
  durationOptions: number[] | null;
  durationEndpointFixed: boolean;
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  newAssets: PlanNewAsset[];
  speakerNames: string[];
}

function durationOutOfTier(seconds: number, options: number[] | null): boolean {
  return options != null && options.length > 0 && !options.includes(seconds);
}

function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return <h4 className={cn("mb-1.5 text-xs font-medium text-muted-foreground", className)}>{children}</h4>;
}

type ReviewPlan = DramaNormalizedScript | NarrationScriptPlanDraft;

function selectReviewContent(state: ScriptReviewState): ReviewPlan | null {
  return (state.content ?? null) as ReviewPlan | null;
}

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

function ItemHeader({
  position,
  durationSeconds,
  segmentBreak,
  readOnly,
  disabled,
  context,
  onChange,
}: {
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

function ReadOnlyText({ text, className = "" }: { text: string; className?: string }) {
  if (!text) return null;
  return <p className={cn("text-sm leading-relaxed whitespace-pre-wrap", className)}>{text}</p>;
}

function ReadOnlyUtterances({ utterances }: { utterances: Utterance[] }) {
  const { t } = useTranslation("dashboard");
  return (
    <ul className="flex flex-col gap-1.5">
      {utterances.map((u, i) => (
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 只读的台词列表，随剧本整体替换、不在此重排增删，行内无状态，台词没有 ID
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

interface ItemReviewNotes {
  position: number;
  blockingIssues: ScriptReviewViolation[];
  softViolations: DraftSoftViolation[];
  anchorRef: (el: HTMLElement | null) => void;
}

function ItemCardShell({ notes, children }: { notes?: ItemReviewNotes; children: React.ReactNode }) {
  const blocked = (notes?.blockingIssues.length ?? 0) > 0;
  return (
    <article
      ref={notes?.anchorRef}
      className={cn("scroll-mt-28 rounded-lg border bg-card p-3.5", blocked ? "border-destructive/45" : "border-border")}
    >
      {children}
      {notes && (
        <>
          <ReviewBlockingIssueList issues={notes.blockingIssues} position={notes.position} />
          <ReviewSoftViolationList softViolations={notes.softViolations} position={notes.position} />
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
  position: number;
  disabled: boolean;
  readOnly: boolean;
  notes?: ItemReviewNotes;
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
  position: number;
  disabled: boolean;
  readOnly: boolean;
  notes?: ItemReviewNotes;
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

function reviewItems(content: ReviewPlan | null): { id: string }[] {
  if (content == null) return [];
  return "scenes" in content
    ? content.scenes.map((scene) => ({ id: scene.scene_id }))
    : content.segments.map((segment) => ({ id: segment.segment_id }));
}

function newAssetEntries(content: ReviewPlan | null): NewAssetEntryRefs[] {
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
  const standaloneCapabilities = useModelCapabilities({
    projectName,
    enabled: videoModelUnresolved === undefined || durationOptions === undefined,
  });
  const modelUnresolved = videoModelUnresolved ?? standaloneCapabilities.videoModelUnresolved;
  const pushToast = useAppStore((s) => s.pushToast);
  const [overwriteOpen, setOverwriteOpen] = useState(false);

  const handleConfirmed = useCallback(() => {
    pushToast(t("dashboard:review_confirmed"), "success");
  }, [pushToast, t]);

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
  } = useScriptReviewDraft<ReviewPlan>({
    projectName,
    episode,
    selectContent: selectReviewContent,
    onConfirmed: handleConfirmed,
  });

  const projectCharacters = useProjectsStore((s) => s.currentProjectData?.characters);
  const savedInstructions = useProjectsStore(
    (s) => s.currentProjectData?.episodes?.find((entry) => entry.episode === episode)?.script_plan_instructions ?? "",
  );
  const shownNewAssets = draft?.new_assets;
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
    setDraft((prev) =>
      prev && "scenes" in prev
        ? { ...prev, scenes: prev.scenes.map((s, i) => (i === index ? { ...s, ...patch } : s)) }
        : prev,
    );
  };

  const updateNarrationSegment = (index: number, patch: Partial<NarrationScriptPlanSegment>) => {
    setDraft((prev) =>
      prev && "segments" in prev
        ? { ...prev, segments: prev.segments.map((s, i) => (i === index ? { ...s, ...patch } : s)) }
        : prev,
    );
  };

  const updateNewAssets = (items: PlanNewAsset[]) => {
    setDraft((prev) => (prev ? { ...prev, new_assets: items } : prev));
  };

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-muted-foreground">{t("dashboard:loading_script_plan")}</div>;
  }

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
  if (draft == null || (status === "no_script_plan" && !dirty)) {
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

  const blankStartAction =
    state?.script_overwrite == null && status !== "confirmed" && status !== "no_script_plan" ? (
      <StartBlankScriptButton projectName={projectName} episode={episode} discardsPlan variant="ghost" />
    ) : null;

  const confirmed = status === "confirmed";
  const removed = status === "no_script_plan";
  const readOnly = confirmed || removed;
  const overwrite = readOnly ? null : (state?.script_overwrite ?? null);
  const scriptMissing = confirmed && state?.script_overwrite == null;
  const confirmLocked = confirmed && !scriptMissing;
  const blockingIssues = outdated ? [] : (state?.blocking_issues ?? []);
  const items = reviewItems(draft);
  const groups = groupDraftViolations(
    blockingIssues,
    items.map((item) => item.id),
  );
  const softByItem = groupSoftViolations(outdated ? [] : (state?.soft_violations ?? []));
  const videoModelBlocked = modelUnresolved && !confirmLocked;
  const durationBlocked =
    !confirmLocked &&
    ("scenes" in draft ? draft.scenes : draft.segments).some((item) =>
      durationOutOfTier(item.duration_seconds, itemContext.durationOptions),
    );
  const hasBlockingIssues = blockingIssues.length > 0;
  const onlyCapabilityBlocking =
    videoModelBlocked && hasBlockingIssues && blockingIssues.every((issue) => issue.code === "video_capability_unavailable");
  const confirmBlocked = outdated || removed || videoModelBlocked || durationBlocked || hasBlockingIssues;
  const confirmBlockedHint =
    outdated || removed
      ? t(confirmed ? "dashboard:review_confirmed_conflict_hint" : "dashboard:review_outdated_hint")
      : hasBlockingIssues && !onlyCapabilityBlocking
        ? t("dashboard:review_blocking_confirm_disabled_hint", { count: blockingIssues.length })
        : videoModelBlocked
          ? t("dashboard:review_video_model_unresolved_hint")
          : durationBlocked
            ? t("dashboard:review_duration_out_of_tier_hint")
            : undefined;

  const notesFor = (index: number): ItemReviewNotes => ({
    position: index + 1,
    blockingIssues: groups.byItem.get(index) ?? [],
    softViolations: softByItem.get(index) ?? [],
    anchorRef: (el) => {
      if (el) itemRefs.current.set(index, el);
      else itemRefs.current.delete(index);
    },
  });

  return (
    <div className="mx-auto flex w-full max-w-240 flex-col gap-3">
      <ScriptReviewStatusHeader
        confirmed={confirmed}
        readOnly={readOnly}
        scriptMissing={scriptMissing}
        overwrite={overwrite != null}
        blockingCount={blockingIssues.length}
        itemJumps={[...groups.byItem.entries()].map(([index, list]) => ({
          index,
          label: t("common:item_position_shot", { position: index + 1 }),
          count: list.length,
        }))}
        episodeLevelCount={groups.episodeLevel.length}
        onJump={(index) => scrollTo(itemRefs.current.get(index))}
        onJumpEpisodeLevel={() => scrollTo(episodeLevelRef.current)}
        dirty={dirty}
        outdated={outdated}
        busy={busy}
        saving={saving}
        confirming={confirming}
        confirmLocked={confirmLocked}
        confirmBlocked={confirmBlocked}
        confirmBlockedHint={confirmBlockedHint}
        saveLabel={t("dashboard:review_save_action")}
        confirmLabel={t("dashboard:review_confirm_action")}
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
        totalSeconds={sumItemDuration("scenes" in draft ? draft.scenes : draft.segments)}
        targetSeconds={state?.episode_target_duration ?? null}
      />

      <NewAssetsSection
        items={draft.new_assets ?? []}
        entries={newAssetEntries(draft)}
        readOnly={readOnly}
        disabled={busy}
        onChange={updateNewAssets}
      />

      {!readOnly && <PlanStructureHint />}

      <div className="flex flex-col gap-2.5">
        {contentMode === "drama" && "scenes" in draft
          ? withUniqueKeys(draft.scenes, (scene) => scene.scene_id).map(({ item: scene, key, index: i }) => (
              <DramaSceneCard
                key={key}
                scene={scene}
                position={i + 1}
                disabled={busy}
                readOnly={readOnly}
                notes={notesFor(i)}
                context={itemContext}
                onChange={(patch) => updateDramaScene(i, patch)}
              />
            ))
          : null}
        {contentMode === "narration" && "segments" in draft
          ? withUniqueKeys(draft.segments, (segment) => segment.segment_id).map(({ item: segment, key, index: i }) => (
              <NarrationSegmentCard
                key={key}
                segment={segment}
                position={i + 1}
                disabled={busy}
                readOnly={readOnly}
                notes={notesFor(i)}
                context={itemContext}
                onChange={(patch) => updateNarrationSegment(i, patch)}
              />
            ))
          : null}
      </div>
    </div>
  );
}
