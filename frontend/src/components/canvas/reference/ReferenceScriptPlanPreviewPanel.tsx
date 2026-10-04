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
import { groupDraftViolations, groupSoftViolations } from "@/utils/draft-violations";
import {
  AgentDraftBar,
  DiscardDraftDialog,
  DraftEpisodeViolations,
  DraftSoftViolationList,
  InvalidDraftBar,
  draftFallbackText,
  draftFixRequestText,
  prefillAssistant,
} from "@/components/shared/DraftStatus";
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
import { NewAssetsSection, hasValidNewAssets, type NewAssetEntryRefs } from "@/components/canvas/shared/NewAssetsSection";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { episodeAgentRef, itemIdsInEpisodeText, itemIdWithinEpisode } from "@/utils/episode-display";
import { tierProblemText } from "./unit-tier-problem";
import { ReferenceSplitAlert } from "./ReferenceSplitAlert";

interface ReferenceScriptPlanPreviewPanelProps {
  projectName: string;
  episode: number;
  videoModelUnresolved?: boolean;
  /** 剧本规划档位；端点固定的单元按它选时长、判越档，与提示词编写的拒绝判据同源。未给时自查。 */
  planningDurations?: number[];
  /** Asset name → kind, for mention coloring — same lookup the editor/parse preview share. */
  lookup: MentionLookup;
  /** 切到本集视频单元时间线；确认后的只读态据此给出去时间线修改的入口，未提供时不渲染入口。 */
  onOpenTimeline?: () => void;
}

/** 原文锚失配类违约：呈现为「原文」小节的红标，不进逐行锚定或聚合区。 */
const SOURCE_ANCHOR_CODES = new Set(["source_text_not_verbatim", "source_text_empty"]);
const SPEECH_VIOLATION_KEYS: Record<string, string> = {
  mixed_speech: "speech_admission_mixed_speech",
  needs_replan: "speech_admission_needs_replan",
  parse_failed: "speech_admission_parse_failed",
  empty_speaker: "speech_admission_empty_speaker",
};

/** unit 卡的统一显示形状：结构化（已晋升）与扁平（草稿）两种来源在这里收敛。 */
interface DisplayUnit {
  key: string;
  duration_seconds: number;
  sourceText: string;
  scriptText: string;
  /** 正文与时长可就地修改（未确认的正式内容，或结构完好的待修复草稿）。 */
  editable: boolean;
}

/** 头部统计：被解析器认作台词的行数，与正文高亮同一套切分口径。 */
function unitStats(scriptText: string, lookup: MentionLookup): { utterances: number } {
  const lines = toScriptLines(scriptText, lookup);
  return {
    utterances: lines.filter((l) => l.kind === "dialogue" || l.kind === "voiceover").length,
  };
}

function structuredDisplayUnits(draft: ReferenceScriptPlanDraft): DisplayUnit[] {
  return draft.units.map((u) => ({
    key: u.unit_id,
    duration_seconds: u.duration_seconds,
    sourceText: u.source_text,
    scriptText: u.text,
    editable: true,
  }));
}

/** 待修复草稿的正文：扁平 unit 数组，unit ID 由集号与序号派生。 */
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
 * 草稿正文 → 可编辑的扁平 unit 数组。草稿可能被 Agent 改坏（`units` 不是数组、逐 unit 字段缺失或
 * 类型不对），逐项收窄而非信任类型声明；收不成时返回 null，面板退回只读呈现。
 */
function narrowFlatUnitsDraft(content: Record<string, unknown> | null): FlatUnitsDraft | null {
  const units = content?.units;
  if (!Array.isArray(units) || !units.every(isFlatUnit) || !hasValidNewAssets(content)) return null;
  return content as unknown as FlatUnitsDraft;
}

/** 各 unit 正文引用到的名字（画面位与说话人位）与原文片段，供「本集新增资产」区展开出场位置。 */
function newAssetEntries(units: DisplayUnit[]): NewAssetEntryRefs[] {
  return units.map((unit) => ({
    id: unit.key,
    names: [...extractMentions(unit.scriptText), ...dialogueSpeakers(unit.scriptText)],
    snippet: unit.sourceText,
  }));
}

/**
 * 本集新增项的称呼与已登记名一样可以写进正文，高亮时按它的类型着色。「不登记」的项确认时退为
 * 纯文本，不算资产。
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
    duration_seconds: u.duration_seconds,
    sourceText: u.source_text,
    scriptText: u.text,
    editable: true,
  }));
}

/**
 * 结构已损坏、收不成可编辑形状的草稿：尽量摊出还能读的 unit 供对照，只读。content 为 null
 * （草稿文件本身损坏）时没有可摊的内容，整集层面的违约已说明情况。
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
        duration_seconds: typeof u.duration_seconds === "number" ? u.duration_seconds : 0,
        sourceText: typeof u.source_text === "string" ? u.source_text : "",
        scriptText: typeof u.text === "string" ? u.text : "",
        editable: false,
      },
    ];
  });
}

interface UnitViolations {
  /** 原文锚失配：呈现为「原文」小节的红标，不重复出现在逐行锚定或聚合区。 */
  anchorSource: ScriptReviewViolation[];
  /** 有行号的违约，按 sourceLine 分组，交给 ScriptHighlight 的 renderAfterLine 逐行渲染。 */
  byLine: Map<number, ScriptReviewViolation[]>;
  /** unit 级、无自然行归属的违约：落卡内聚合区。 */
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
 * unit 的服务端定桶结论（桶、档位、端点固定、引用分裂）：按此刻可用的参考图判定，与执行侧同一
 * 判据。面板不按正文里「名字已登记」自判；结论随保存后的状态回流更新。型号解析不到（tiers 为
 * null）或该 unit 尚无结论时为 null，控件保持只读。
 */
function unitCapability(unit: DisplayUnit, tiers: ScriptReviewState["duration_tiers"]): ReferenceUnitCapability | null {
  return tiers?.units[unit.key] ?? null;
}

/**
 * 该 unit 一个已登记场景资产都没引用——画面地点由模型自由决定，室内外交替的相邻 unit 会
 * 各自发挥、对不上。镜像后端 `lib/script/reference_video/script_preview.py::unit_lacks_scene_reference`。
 *
 * 与档位收窄同样按当前正文实时判、不取服务端快照：本面板可就地改正文，补上 `@[场景]` 必须
 * 当场撤下提示，等保存后才由服务端回话会让提示与眼前的正文对不上。
 */
function unitLacksSceneReference(scriptText: string, lookup: MentionLookup, projectHasScene: boolean): boolean {
  if (!projectHasScene) return false;
  return !extractMentions(scriptText).some((name) => lookup[name] === "scene");
}

function InlineViolations({ violations, unitKey }: { violations: ScriptReviewViolation[]; unitKey: string }) {
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
          ? t(speechKey, { unitId: itemIdWithinEpisode(unitKey), location })
          : itemIdsInEpisodeText(v.message);
        return (
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
  /** 服务端下发的降级提示（「未引用场景」除外，那条按当前正文实时判）。 */
  softViolations: DraftSoftViolation[];
  lookup: MentionLookup;
  /** 项目登记了场景资产——没有可引用的场景时不发「未引用场景」提示（纯商品的广告项目即属此列）。 */
  projectHasScene: boolean;
  onScrollRef: (key: string, el: HTMLElement | null) => void;
  editing: boolean;
  onToggleEdit: () => void;
  onTextChange: ((text: string) => void) | null;
  supportedDurations: number[] | null;
  durationEndpointFixed: boolean;
  /** 所落桶的视频请求事实失败：标签与带修复指引的提示，由 `tierProblemText` 按桶生成。 */
  durationProblem: { label: string; hint: string } | null;
  /** 声明引用与可用参考图分裂时的服务端结论（缺图 / 未登记引用、桶的改变）；无分裂为 null。 */
  split: ReferenceUnitCapability | null;
  /** unit 当前存盘时长已不在收窄后的档位表内——展示照旧，但阻断确认（父组件按此禁用确认按钮）。 */
  outOfTier: boolean;
  onDurationChange: ((seconds: number) => void) | null;
  /** 保存 / 确认请求在途：锁住时长下拉与正文，避免 adopt() 用服务端回显覆盖请求发出后的新编辑。 */
  busy: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const hasViolation = violations.anchorSource.length + violations.byLine.size + violations.aggregate.length > 0;
  const anchorBroken = violations.anchorSource.length > 0;
  const stats = useMemo(() => unitStats(unit.scriptText, lookup), [unit.scriptText, lookup]);
  const lacksScene = useMemo(
    () => unitLacksSceneReference(unit.scriptText, lookup, projectHasScene),
    [unit.scriptText, lookup, projectHasScene],
  );
  // 档位表解析不到、或内容不可编辑时退回只读秒数：能选的档位必须是保存后
  // 后端收编不会再改的那一档，拿不到权威档位表就不提供会被静默改掉的选择。
  const durationOptions = onDurationChange && supportedDurations?.length ? supportedDurations : null;

  return (
    <article
      ref={(el) => onScrollRef(unit.key, el)}
      className={`scroll-mt-28 rounded-lg border bg-card p-4 ${hasViolation ? "border-destructive/45" : "border-border"}`}
    >
      <div className="flex items-center gap-2">
        <span className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs text-subtle-foreground">{itemIdWithinEpisode(unit.key)}</span>
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
            label={t("reference_script_plan_duration_label", { unit: itemIdWithinEpisode(unit.key) })}
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
      {/* 面板不因分裂拦确认（规划期资产图常尚未生成），但按 i2v 取档时要说明桶已改变，不静默换桶。 */}
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
        <p
          className={`mt-1.5 border-l pl-3 text-xs leading-relaxed ${
            anchorBroken ? "border-destructive/50 text-destructive" : "border-border text-muted-foreground"
          }`}
        >
          {unit.sourceText}
        </p>
        <InlineViolations violations={violations.anchorSource} unitKey={unit.key} />
      </details>

      <div className="mt-3">
        {editing && unit.editable && onTextChange ? (
          <Textarea
            value={unit.scriptText}
            onChange={(e) => onTextChange(e.target.value)}
            disabled={busy}
            aria-label={t("reference_script_plan_unit_text_label", { unit: itemIdWithinEpisode(unit.key) })}
          />
        ) : (
          <ScriptHighlight
            text={unit.scriptText}
            lookup={lookup}
            renderAfterLine={(sourceLine) => (
              <InlineViolations violations={violations.byLine.get(sourceLine) ?? []} unitKey={unit.key} />
            )}
          />
        )}
      </div>

      <InlineViolations violations={violations.aggregate} unitKey={unit.key} />

      {/* 降级提示（不阻断确认），与违约的红标区分开：正文合法，只是画面地点没被钉住。 */}
      {lacksScene && (
        <p className="mt-2 flex items-start gap-1.5 pl-1 text-xs leading-snug text-subtle-foreground">
          <AlertTriangle className="mt-px size-3 shrink-0 text-warn" aria-hidden="true" />
          <span>{t("reference_script_plan_unit_without_scene")}</span>
        </p>
      )}
      <DraftSoftViolationList softViolations={softViolations} />
    </article>
  );
}

/** 本面板只编辑 reference_video 变体的 units 内容；其余变体的内容不属于这里。 */
function selectUnitsContent(state: ScriptReviewState): ReferenceScriptPlanDraft | null {
  return state.content != null && "units" in state.content ? state.content : null;
}

/** 「未引用场景」按当前正文实时判（见 `unitLacksSceneReference`），不取服务端快照。 */
const LIVE_SOFT_VIOLATION_CODES: ReadonlySet<string> = new Set(["ref_warn_unit_without_scene"]);

/**
 * reference_video script_plan 拆分结果的按集预览：与 drama/narration 的 `ScriptReviewGate` 同级、
 * 专属 reference_video 变体的内容确认面板——文稿流布局（unit 卡：头部 + 原文 + 高亮正文），
 * 干净态仅需确认放行 prompt_authoring。
 *
 * 待修复草稿在场时面板呈现草稿本身：违约挂到所在 unit（语法类行内锚定到出问题的行）、整集层面的
 * 违约置顶，正文与时长可就地修改后保存并校验，违约清零即采用。Agent 的可编辑草稿在场时只提示有一份
 * 未完成的修改，正式内容只读。确认之后脚本规划只读，指引到时间线修改。
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
  // 单元可选的时长档位：所落桶收窄后的档位；端点固定时桶档位是空集，改取剧本规划档位。
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
    // 保存 / 确认两次 await 期间用户可能已切走项目（本组件所在的 tab 可能因此被卸载）：只在项目
    // 本身变了才抑制全局副作用，否则会把续写消息写进用户切换到的别的项目/会话。同项目内切
    // tab（如切到「视频单元」，本面板同样会被卸载）不属于这种情况——预填文案本身带着具体
    // 集 ID，写进全局 assistant 输入框依然准确，不该被同一份卸载信号误伤。
    if (useProjectsStore.getState().currentProjectName !== projectName) return;
    pushToast(t("dashboard:review_confirmed"), "success");
    // 确认放行 + 预填继续消息到会话输入框——只填不发送，用户自行核对后发送。
    prefillAssistant(t("reference_script_plan_confirm_continue_prefill", { episodeRef }));
  }, [projectName, episodeRef, pushToast, t]);

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
    (unitIndex: number, patch: { text?: string; duration_seconds?: number }) => {
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
    // 本集还没有正式脚本时这里就是首次规划的起步区；已有正式脚本（如从空白开始）时，新规划经覆盖确认才替换它。
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

  // 待修复草稿在场：面板呈现草稿本身，正式内容此刻不可确认（确认端点按同一判据拒绝）。
  // 本集还没有正式脚本、规划也未确认时，可以不用这份规划、从空白开始手写；规划与待修复草稿随之弃置，先确认。
  // Agent 正在编辑草稿时不给入口，服务端同样拒绝。
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
            label: itemIdWithinEpisode(displayUnits[index].key),
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
              // 草稿的 unit 尚未定桶，时长可选档位取结构区间全集；采用时由服务端按同一判据重判。
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

  // Agent 的可编辑草稿在场：正式内容只读，确认与编辑一并锁住，待 Agent 完成或丢弃这份修改。
  const agentEditing = quarantine != null;
  // 已确认的脚本规划只读：保存端点按同一判据拒绝，内容修改改在时间线上做。
  const confirmed = status === "confirmed" && !agentEditing;
  const readOnly = agentEditing || confirmed;
  // 该集已有正式脚本：确认会整份覆盖它，确认按钮改呈 danger，点击先列出后果再确认。
  const overwrite = confirmed ? null : (state?.script_overwrite ?? null);
  // 已确认但该集没有正式脚本（迁移转换失败或文件被删）：确认仍可用，重新确认即转出正式脚本。
  const scriptMissing = confirmed && state?.script_overwrite == null;
  const confirmLocked = confirmed && !scriptMissing;
  const videoModelBlocked = modelUnresolved && !confirmLocked;
  const displayUnits: DisplayUnit[] = draft ? structuredDisplayUnits(draft) : [];
  const newAssets = draft?.new_assets ?? [];
  const reviewLookup = lookupWithNewAssets(lookup, newAssets);
  const softByUnit = groupSoftViolations(state?.soft_violations ?? [], LIVE_SOFT_VIOLATION_CODES);
  // 收窄后的档位表若已不再包含某 unit 存量存盘的时长（模型 / 分辨率 / 参考图配置变化所致），
  // 该值仍保留展示（避免 select 静默跳首档），但不能放行确认——_assert_reference_script_plan_ready
  // 会在 prompt_authoring 落盘前硬拒同一个越档值，此处先一步拦下，而不是让用户确认后才在别处失败。
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
  // 所落桶的视频请求事实解析不出的 unit：档位未知，不能确认一份执行不了的方案。
  const unknownUnits = displayUnits.flatMap((u) => {
    const capability = unitCapability(u, durationTiers);
    return capability?.problem ? [{ key: u.key, capability }] : [];
  });
  const unknownUnitKeys = new Set(unknownUnits.map((u) => u.key));
  const firstUnknownProblem =
    unknownUnits.length > 0
      ? tierProblemText(t, unknownUnits[0].capability.problem!, unknownUnits[0].capability.hydrated_capability)
      : null;
  // 覆盖确认的拦截条件，触发按钮与框内确认按钮共用一位：能力请求可能在框打开之后才答复
  // 模型无法解析，此时框内还留着一颗能提交、但服务端必拒的确认按钮。
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
            // 失败（如确认期间该集被并发写入）时框保持打开，呈现刷新后的覆盖清单。
            if (await handleConfirm({ overwriteRevision: overwrite.revision })) setOverwriteOpen(false);
          }}
          onCancel={() => setOverwriteOpen(false)}
        />
      )}

      {/* 本集合计与项目目标的对比；未设目标时不渲染，超出只提示不阻断确认 */}
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
