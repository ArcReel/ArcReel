import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, Lock, RotateCcw, Save } from "lucide-react";
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
  draftFallbackText,
  draftFixRequestText,
  prefillAssistant,
} from "@/components/shared/DraftStatus";
import { EpisodeDurationSummary } from "@/components/shared/EpisodeDurationSummary";
import { ScriptPlanButton } from "@/components/canvas/shared/ScriptPlanButton";
import { StartBlankScriptButton } from "@/components/canvas/shared/StartBlankScriptButton";
import { NewAssetsSection, hasValidNewAssets, type NewAssetEntryRefs } from "@/components/canvas/shared/NewAssetsSection";
import { ScriptOverwriteConfirmDialog } from "@/components/shared/ScriptOverwriteConfirmDialog";
import { VideoModelUnresolvedNotice } from "@/components/shared/VideoModelUnresolvedNotice";
import { useModelCapabilities, type DurationOutOfRangeReason } from "@/hooks/useModelCapabilities";
import { PlanDurationSelect, durationIncompatibleLabel } from "@/components/canvas/shared/PlanDurationSelect";
import { PlanStructureHint } from "@/components/canvas/shared/PlanStructureHint";
import { speakerCandidates } from "@/utils/plan-new-assets";
import { PrimaryButton } from "@/components/legacy/PrimaryButton";
import { AutoTextarea } from "@/components/legacy/AutoTextarea";
import {
  ACCENT_BUTTON_STYLE,
  ACCENT_BTN_CLS,
  CARD_STYLE,
  GHOST_BTN_CLS,
  GHOST_BTN_LG_CLS,
} from "@/components/shared/darkroom-tokens";
import { sumItemDuration } from "@/utils/script-shape";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef, itemIdWithinEpisode } from "@/utils/episode-display";
import { UtteranceListEditor } from "./UtteranceListEditor";
import { ReferencesSection } from "./ReferencesSection";
import { SegmentBreakToggle } from "./SegmentBreakToggle";

interface ScriptReviewGateProps {
  projectName: string;
  episode: number;
  contentMode: "narration" | "drama";
  videoModelUnresolved?: boolean;
  /** 切到本集时间线；确认后的只读态据此给出去时间线修改的入口，未提供时不渲染入口。 */
  onOpenTimeline?: () => void;
  /** 当前视频模型与分辨率下的剧本规划时长档位（与确认转换同一口径）；未知时时长只读。 */
  durationOptions?: number[];
  durationEndpointFixed?: boolean;
  /** 时长不在档位内的成因，决定说明文案；缺省时按「模型不支持」说明。 */
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
}

/** 条目卡编辑字段所需的上下文：时长档位、本集新增资产与说话人候选。 */
interface PlanItemContext {
  projectName: string;
  durationOptions: number[] | null;
  durationEndpointFixed: boolean;
  durationWarningReason?: (seconds: number) => DurationOutOfRangeReason | null;
  newAssets: PlanNewAsset[];
  speakerNames: string[];
}

/** 时长不在当前档位内：确认会被拒绝，卡片就地标红说明。档位未知时不判。 */
function durationOutOfTier(seconds: number, options: number[] | null): boolean {
  return options != null && options.length > 0 && !options.includes(seconds);
}

const SECTION_LABEL_STYLE: React.CSSProperties = {
  color: "var(--muted-foreground)",
  letterSpacing: "0.08em",
  fontFamily: "var(--font-mono)",
};

/** 两条 script_plan 变体（drama / narration）的可编辑草稿联合。 */
type ReviewDraft = DramaNormalizedScript | NarrationScriptPlanDraft;

/** 本面板承接 drama / narration 两个变体的内容，reference_video 变体不会路由到这里。 */
function selectReviewContent(state: ScriptReviewState): ReviewDraft | null {
  return (state.content ?? null) as ReviewDraft | null;
}

/** 只读的资产引用 pills（出场角色 / 场景 / 道具）。 */
function MetaChips({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((name) => (
        <span
          key={name}
          className="rounded-sm border border-border bg-card/50 px-1.5 py-0.5 text-[10.5px] text-muted-foreground"
        >
          {name}
        </span>
      ))}
    </div>
  );
}

/** 条目头部：ID、时长与章节切分点；可编辑时时长用档位下拉、切分点用开关，越档就地标红说明。 */
function ItemHeader({
  id,
  durationSeconds,
  segmentBreak,
  readOnly,
  disabled,
  context,
  onChange,
}: {
  id: string;
  durationSeconds: number;
  segmentBreak: boolean;
  readOnly: boolean;
  disabled: boolean;
  context: PlanItemContext;
  onChange: (patch: { duration_seconds?: number; segment_break?: boolean }) => void;
}) {
  const { t } = useTranslation("dashboard");
  const shortId = itemIdWithinEpisode(id);
  const outOfTier = !readOnly && durationOutOfTier(durationSeconds, context.durationOptions);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-sm bg-card/70 px-1.5 py-0.5 font-mono text-[11px] text-subtle-foreground">{shortId}</span>
        {readOnly ? (
          <>
            <span className="text-[11px] text-muted-foreground">{durationSeconds}s</span>
            {segmentBreak && (
              <span className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t("review_segment_break")}
              </span>
            )}
          </>
        ) : (
          <>
            <PlanDurationSelect
              seconds={durationSeconds}
              options={context.durationOptions}
              onChange={(duration_seconds) => onChange({ duration_seconds })}
              disabled={disabled}
              label={t("review_item_duration_label", { item: shortId })}
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
        <p className="flex items-start gap-1.5 text-[11px] leading-snug text-red-300">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
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

/** 条目的角色 / 场景 / 道具引用：可编辑时复用时间线的引用编辑，候选含本集新增资产。 */
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

/** 只读正文：保留换行，空值不渲染。 */
function ReadOnlyText({ text, className = "" }: { text: string; className?: string }) {
  if (!text) return null;
  return <p className={`whitespace-pre-wrap text-[12.5px] leading-relaxed ${className}`}>{text}</p>;
}

function ReadOnlyUtterances({ utterances }: { utterances: Utterance[] }) {
  const { t } = useTranslation("dashboard");
  return (
    <ul className="flex flex-col gap-1.5">
      {utterances.map((u, i) => (
        <li key={String(i)} className="flex items-start gap-2 text-[12.5px] leading-relaxed">
          <span className="mt-0.5 shrink-0 rounded-sm border border-border bg-card/55 px-1.5 py-px text-[10.5px] text-muted-foreground">
            {u.kind === "dialogue" ? u.speaker : t("utterance_kind_voiceover")}
          </span>
          <span className={u.kind === "voiceover" ? "italic text-subtle-foreground" : "text-foreground"}>{u.text}</span>
        </li>
      ))}
    </ul>
  );
}

/** 条目卡在草稿上呈现时的违约挂载：违约（红）与降级提示（琥珀）随条目呈现，卡片是跳转落点。 */
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
      className={`scroll-mt-28 rounded-lg border p-3.5 ${violating ? "border-red-500/45" : "border-border"}`}
      style={CARD_STYLE}
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
  disabled,
  readOnly,
  notes,
  context,
  onChange,
}: {
  scene: DramaSceneContent;
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
          id={scene.scene_id}
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

      <label className="mb-1 block text-[10.5px]" style={SECTION_LABEL_STYLE}>
        {t("review_utterances_label")}
      </label>
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

      <label className="mb-1 mt-3 block text-[10.5px]" style={SECTION_LABEL_STYLE}>
        {t("review_source_text_label")}
      </label>
      {readOnly ? (
        <ReadOnlyText text={scene.source_text} className="text-muted-foreground" />
      ) : (
        <AutoTextarea
          value={scene.source_text}
          disabled={disabled}
          onChange={(source_text) => onChange({ source_text })}
          placeholder={t("review_source_text_placeholder")}
          aria-label={t("review_source_text_label")}
          className="text-muted-foreground"
        />
      )}
    </ItemCardShell>
  );
}

function NarrationSegmentCard({
  segment,
  disabled,
  readOnly,
  notes,
  context,
  onChange,
}: {
  segment: NarrationScriptPlanSegment;
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
          id={segment.segment_id}
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

      <label className="mb-1 block text-[10.5px]" style={SECTION_LABEL_STYLE}>
        {t("review_novel_text_label")}
      </label>
      {readOnly ? (
        <ReadOnlyText text={segment.novel_text} className="text-foreground" />
      ) : (
        <AutoTextarea
          value={segment.novel_text}
          onChange={(novel_text) => onChange({ novel_text })}
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
 * 草稿正文 → 可编辑卡片的形状。逐项收窄、不信任类型声明：草稿可能被 Agent 改坏，按卡片的字段契约
 * 硬渲染要么崩、要么把缺失字段补成用户没写过的值。收不成时返回 null，面板只呈现违约与处置入口。
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

/** 各条目引用到的名字与原文片段，供「本集新增资产」区展开出场位置。 */
function newAssetEntries(content: ReviewDraft | null): NewAssetEntryRefs[] {
  if (content == null) return [];
  return "scenes" in content
    ? content.scenes.map((scene) => ({
        id: scene.scene_id,
        names: [
          ...scene.characters_in_scene,
          ...scene.scenes,
          ...scene.props,
          ...scene.utterances.flatMap((u) => (u.kind === "dialogue" ? [u.speaker] : [])),
        ],
        snippet: scene.source_text,
      }))
    : content.segments.map((segment) => ({
        id: segment.segment_id,
        names: [...segment.characters_in_segment, ...segment.scenes, ...segment.props],
        snippet: segment.novel_text,
      }));
}

/**
 * script_plan→prompt_authoring web 内容确认面板：把 script_plan 结构化中间态在网页结构化呈现、可手动 / Agent 编辑，
 * 用户显式确认后才放行 prompt_authoring 视觉生成。drama（utterances + source_text）与 narration
 * （novel_text）共用本面板；reference_video 变体的专属面板见 `ReferenceScriptPlanPreviewPanel`。
 *
 * 待修复草稿在场时面板改为呈现草稿：违约挂到所在条目、整集层面的违约置顶，可就地改草稿后保存并
 * 校验，违约清零即采用。Agent 的可编辑草稿在场时只提示有一份未完成的修改，正式内容只读。确认之后
 * 脚本规划只读，卡片不渲染编辑控件，指引到时间线修改。
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
  // 父组件已给出模型能力（是否可解析、时长档位）时不再自查。
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

  // 加载错误态：区别于「无 script_plan 产物」空态，展示错误信息 + 重试入口。
  if (loadError) {
    return (
      <div role="alert" className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertTriangle className="h-6 w-6 text-amber-400" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-[13px] font-medium text-subtle-foreground">{t("dashboard:review_load_failed")}</p>
          {loadError.message && (
            <p className="max-w-sm px-4 font-mono text-[11px] text-muted-foreground">{loadError.message}</p>
          )}
        </div>
        <button type="button" onClick={handleRetry} className={GHOST_BTN_LG_CLS}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("dashboard:review_retry")}
        </button>
      </div>
    );
  }

  const status = state?.status ?? "no_script_plan";
  if (status === "no_script_plan" || (draft == null && quarantine == null)) {
    // 没有规划时也能在这里发起 AI 规划；已有正式脚本（如从空白开始）时，新规划经覆盖确认才替换它。
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-muted-foreground">
        <p>{t("dashboard:no_script_plan_content")}</p>
        {status === "no_script_plan" && (
          <ScriptPlanButton
            projectName={projectName}
            episode={episode}
            replaces={state?.script_overwrite != null ? "formal_script" : "none"}
            className={GHOST_BTN_LG_CLS}
          />
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

  // 本集还没有正式脚本、规划也未确认时，可以不用这份规划、从空白开始手写；规划与待修复草稿随之弃置，先确认。
  const blankStartAction =
    state?.script_overwrite == null && status !== "confirmed" ? (
      <StartBlankScriptButton projectName={projectName} episode={episode} discardsPlan className={GHOST_BTN_CLS} />
    ) : null;

  // 待修复草稿在场：面板呈现草稿本身，正式内容此刻不可确认（确认端点按同一判据拒绝）。
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
      <div className="flex flex-col gap-3">
        <InvalidDraftBar
          violationCount={quarantine.violations.length}
          itemJumps={[...groups.byItem.entries()].map(([index, list]) => ({
            index,
            label: items[index]?.id ? itemIdWithinEpisode(items[index].id) : `#${index + 1}`,
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
              <ScriptPlanButton projectName={projectName} episode={episode} replaces="draft" className={GHOST_BTN_CLS} />
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
                    key={`${String(i)}-${scene.scene_id}`}
                    scene={scene}
                    disabled={draftBusy}
                    readOnly={false}
                    notes={notesFor(i)}
                    context={itemContext}
                    onChange={(patch) => updateDramaScene(i, patch)}
                  />
                ))
              : content.segments.map((segment, i) => (
                  <NarrationSegmentCard
                    key={`${String(i)}-${segment.segment_id}`}
                    segment={segment}
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

  // Agent 的可编辑草稿在场：正式内容只读，确认与编辑一并锁住，待 Agent 完成或丢弃这份修改。
  const agentEditing = quarantine != null;
  // 已确认的脚本规划只读：保存端点按同一判据拒绝，内容修改改在时间线上做。
  const confirmed = status === "confirmed" && !agentEditing;
  const readOnly = confirmed || agentEditing;
  // 该集已有正式脚本：确认会整份覆盖它，确认按钮改呈 danger，点击先列出后果再确认。
  const overwrite = confirmed ? null : (state?.script_overwrite ?? null);
  // 已确认但该集没有正式脚本（迁移转换失败或文件被删）：确认仍可用，重新确认即转出正式脚本。
  const scriptMissing = confirmed && state?.script_overwrite == null;
  const confirmLocked = confirmed && !scriptMissing;
  const videoModelBlocked = modelUnresolved && !confirmLocked;
  // 有条目的时长不在当前档位内：确认会被拒绝，先就地改选。
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
    <div className="flex flex-col gap-3">
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
                className={GHOST_BTN_CLS}
                disabledReason={dirty && !confirmed ? t("dashboard:script_plan_dirty_hint") : null}
              />
            </>
          }
          saveAction={
            dirty && !confirmed ? (
              <button type="button" onClick={voidPromise(handleSave)} disabled={busy} className={GHOST_BTN_CLS}>
                <Save className="h-3.5 w-3.5" />
                {saving ? t("common:saving") : t("dashboard:review_save_action")}
              </button>
            ) : null
          }
          confirmAction={
            overwrite ? (
              <PrimaryButton
                tone="danger"
                onClick={() => setOverwriteOpen(true)}
                disabled={busy || confirmBlocked}
                title={confirmBlockedHint}
                leadingIcon={<AlertTriangle className="h-3.5 w-3.5" />}
              >
                {confirming ? t("dashboard:review_confirming") : t("dashboard:review_overwrite_action")}
              </PrimaryButton>
            ) : (
              <button
                type="button"
                onClick={voidPromise(() => handleConfirm())}
                disabled={busy || confirmLocked || confirmBlocked}
                title={confirmBlockedHint}
                className={ACCENT_BTN_CLS}
                style={ACCENT_BUTTON_STYLE}
              >
                {confirmLocked ? <Lock className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                {confirming
                  ? t("dashboard:review_confirming")
                  : scriptMissing
                    ? t("dashboard:review_rematerialize_action")
                    : confirmed
                      ? t("dashboard:review_confirmed_badge")
                      : t("dashboard:review_confirm_action")}
              </button>
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
          // 框在能力请求返回之前就可能被打开，之后答复模型无法解析：框内的确认按钮与触发它的
          // 那颗按钮同一判据，否则这里还能提交一次注定被服务端拒绝的确认。
          confirmDisabled={confirmBlocked}
          onConfirm={async () => {
            // 失败（如确认期间该集被并发写入）时框保持打开，呈现刷新后的覆盖清单。
            if (await handleConfirm({ overwriteRevision: overwrite.revision })) setOverwriteOpen(false);
          }}
          onCancel={() => setOverwriteOpen(false)}
        />
      )}

      {/* 本集合计与项目目标的对比；未设目标时不渲染，超出只提示不阻断确认 */}
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
                key={scene.scene_id || i}
                scene={scene}
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
                key={segment.segment_id || i}
                segment={segment}
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

/** 内容确认状态条：待确认 / 已确认 / 已确认但缺正式脚本，右侧放保存与确认动作。 */
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
    <header
      className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-lg border border-border px-3.5 py-2.5 backdrop-blur-md"
      style={CARD_STYLE}
    >
      <div className="flex items-center gap-2">
        {confirmed ? <CheckCircle2 className="h-4 w-4 text-emerald-400" /> : <Clock className="h-4 w-4 text-amber-400" />}
        <div className="flex flex-col">
          <span className="text-[12.5px] font-medium text-foreground">
            {confirmed ? t("review_status_confirmed") : t("review_status_pending")}
          </span>
          <span className="text-[11px] text-muted-foreground">
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

      <div className="flex items-center gap-2">
        {onOpenTimeline && (
          <button type="button" onClick={onOpenTimeline} className={GHOST_BTN_CLS}>
            <ArrowRight className="h-3.5 w-3.5" />
            {t("review_open_timeline")}
          </button>
        )}
        {regenerateAction}
        {saveAction}
        {confirmAction}
      </div>
    </header>
  );
}
