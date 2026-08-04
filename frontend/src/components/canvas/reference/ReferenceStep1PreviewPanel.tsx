import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, ChevronDown, Clock, Lock, OctagonAlert, Pencil, RotateCcw, Save } from "lucide-react";
import type {
  ReferenceResource,
  ReferenceStep1Draft,
  ReferenceStep1FlatDraft,
  ReferenceStep1FlatUnit,
  ScriptReviewState,
  ScriptReviewViolation,
  Shot,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { voidPromise } from "@/utils/async";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import { assetColor } from "@/components/canvas/reference/asset-colors";
import { ScriptHighlight } from "@/components/shared/ScriptHighlight";
import { toScriptLines, type MentionLookup } from "@/hooks/useShotPromptHighlight";
import { extractMentions, formatShotHeader, normalizeAssetName } from "@/utils/reference-mentions";

interface ReferenceStep1PreviewPanelProps {
  projectName: string;
  episode: number;
  /** Asset name → kind, for mention coloring — same lookup the editor/parse preview share. */
  lookup: MentionLookup;
}

/** 原文锚失配类违约：呈现为「原文」小节的红标，不进逐行锚定或聚合区。 */
const SOURCE_ANCHOR_CODES = new Set(["source_text_not_verbatim", "source_text_empty"]);

function unitKeyFromLabel(label: string): string | null {
  const m = /^unit\s+(\S+)$/.exec(label);
  return m ? m[1] : null;
}

/** unit 卡的统一显示形状：结构化（已晋升）与扁平（隔离草稿）两种来源在这里收敛。 */
interface DisplayUnit {
  key: string;
  durationSeconds: number;
  sourceText: string;
  scriptText: string;
  references: ReferenceResource[];
  /** 非 null 时可编辑（已晋升内容）；隔离草稿的扁平产物只读，修复由 agent 在草稿上完成。 */
  shots: Shot[] | null;
}

function shotsToScriptText(shots: Shot[]): string {
  return shots.map((s, i) => `${formatShotHeader(i + 1)}${s.text}`).join("\n");
}

/** 头部统计：镜头数 + 被解析器认作台词的行数，与正文高亮同一套切分口径。 */
function unitStats(scriptText: string, lookup: MentionLookup): { shots: number; utterances: number } {
  const lines = toScriptLines(scriptText, lookup);
  return {
    shots: lines.reduce((max, l) => Math.max(max, l.shotIndex), 0),
    utterances: lines.filter((l) => l.kind === "dialogue" || l.kind === "voiceover").length,
  };
}

function structuredDisplayUnits(draft: ReferenceStep1Draft): DisplayUnit[] {
  return draft.units.map((u) => ({
    key: u.unit_id,
    durationSeconds: u.duration_seconds,
    sourceText: u.source_text,
    scriptText: shotsToScriptText(u.shots),
    references: u.references,
    shots: u.shots,
  }));
}

/**
 * 隔离草稿没有 references 字段（书写层扁平产物，结构字段一律机器派生）：客户端按 @ 引用
 * 顺序派生一份仅供展示的参考图列表，与后端 `derive_references_from_text` 同口径（首现顺序、
 * 未登记名不进列表——未登记会在正文高亮里以红色呈现，footer 不重复报它）。晋升后端会重新
 * 权威派生一份落盘，这里的派生不作为任何写入依据。
 */
function deriveDisplayReferences(text: string, lookup: MentionLookup): ReferenceResource[] {
  const out: ReferenceResource[] = [];
  // extractMentions（非 tokenizePrompt）：规范台词行里的说话人不产参考图，与后端
  // extract_mentions 同口径——tokenizePrompt 是给高亮用的，不做这条跳过。
  // extractMentions 按裸字符串去重，同一资产以 NFC/NFD 两种编码各出现一次时会各产一条；
  // 后端归一后落盘只保留一条，这里须同口径按归一形式去重，否则预览多显示一张图/一个图号。
  const seen = new Set<string>();
  for (const name of extractMentions(text)) {
    const canonical = normalizeAssetName(name);
    const assetKind = lookup[canonical];
    if (!assetKind || seen.has(canonical)) continue;
    seen.add(canonical);
    out.push({ type: assetKind, name: canonical });
  }
  return out;
}

/**
 * 隔离草稿 → unit 卡。schema 违约时后端原样回传 agent 手改的那份 content（不做收编），`units`
 * 可能不是数组、逐 unit 字段也可能缺失或类型不对：这里逐项收窄而非信任类型声明——渲染崩掉
 * 恰好发生在用户最需要看到面板的时候。收不成 unit 卡的内容由调用方作原始文本兜底呈现。
 */
function quarantinedDisplayUnits(
  content: ReferenceStep1FlatDraft | null,
  episode: number,
  lookup: MentionLookup,
): DisplayUnit[] {
  // content 为 null：隔离草稿文件本身损坏无法解析（信封形状坏），不是「schema 违约但仍可读」。
  const units: unknown = content?.units;
  if (!Array.isArray(units)) return [];
  return units.flatMap((raw: unknown, i) => {
    if (raw == null || typeof raw !== "object") return [];
    const u = raw as Partial<ReferenceStep1FlatUnit>;
    const text = typeof u.text === "string" ? u.text : "";
    return [
      {
        key: `E${episode}U${String(i + 1).padStart(2, "0")}`,
        durationSeconds: typeof u.duration_seconds === "number" ? u.duration_seconds : 0,
        sourceText: typeof u.source_text === "string" ? u.source_text : "",
        scriptText: text,
        references: deriveDisplayReferences(text, lookup),
        shots: null,
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

function partitionViolations(violations: ScriptReviewViolation[], unitKey: string): UnitViolations {
  const forUnit = violations.filter((v) => unitKeyFromLabel(v.label) === unitKey);
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
 * unit 当前生效的时长档位（按是否带参考图收窄）；解析不到收窄表时返回 null，由调用方退回
 * 未收窄的 `supported_durations`。
 *
 * 有无引用按当前正文（`extractMentions`，非 `unit.references`）判——`updateShotText` 只改
 * `shots`，`references` 要保存后服务端重派生才更新；编辑期新增/删除的 `@[名称]` 若仍按旧
 * `references` 选档位，用户在编辑框里加了引用之后，下拉仍按无引用的宽档位显示可选项。
 */
function unitDurationTiers(
  unit: DisplayUnit,
  lookup: MentionLookup,
  tiers: NonNullable<ScriptReviewState["duration_tiers"]> | null,
): number[] | null {
  if (!tiers) return null;
  const hasReferences = extractMentions(unit.scriptText).some((name) => Boolean(lookup[normalizeAssetName(name)]));
  return hasReferences ? tiers.with_references : tiers.without_references;
}

function ReferencePills({ references }: { references: ReferenceResource[] }) {
  const { t } = useTranslation("dashboard");
  if (!references.length) {
    return <span className="text-[10.5px] text-text-4">{t("reference_step1_no_references")}</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {references.map((ref, i) => {
        const palette = assetColor(ref.type);
        return (
          <span
            key={`${ref.type}:${ref.name}`}
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${palette.textClass} ${palette.bgClass}`}
            translate="no"
          >
            <span aria-hidden="true" className="text-[9px] text-text-4">
              [{t("reference_strip_image_token", { n: i + 1 })}]
            </span>
            <span aria-hidden="true" className={`h-[3px] w-[3px] rounded-full ${palette.dotClass}`} />
            {ref.name}
          </span>
        );
      })}
    </div>
  );
}

function InlineViolations({ violations }: { violations: ScriptReviewViolation[] }) {
  if (!violations.length) return null;
  return (
    <>
      {violations.map((v, i) => (
        <p key={`${v.code}-${i}`} className="mt-1 flex items-start gap-1.5 pl-1 text-[11px] leading-snug text-red-300">
          <OctagonAlert className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
          <span>{v.message}</span>
        </p>
      ))}
    </>
  );
}

function UnitCard({
  unit,
  violations,
  lookup,
  quarantined,
  onScrollRef,
  editing,
  onToggleEdit,
  onShotTextChange,
  supportedDurations,
  outOfTier,
  onDurationChange,
  busy,
}: {
  unit: DisplayUnit;
  violations: UnitViolations;
  lookup: MentionLookup;
  quarantined: boolean;
  onScrollRef: (key: string, el: HTMLElement | null) => void;
  editing: boolean;
  onToggleEdit: () => void;
  onShotTextChange: ((shotIndex: number, text: string) => void) | null;
  supportedDurations: number[] | null;
  /** unit 当前存盘时长已不在收窄后的档位表内——展示照旧，但阻断确认（父组件按此禁用确认按钮）。 */
  outOfTier: boolean;
  onDurationChange: ((seconds: number) => void) | null;
  /** 保存 / 确认请求在途：锁住时长下拉与镜头正文，避免 adopt() 用服务端回显覆盖请求发出后的新编辑。 */
  busy: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const hasViolation = violations.anchorSource.length + violations.byLine.size + violations.aggregate.length > 0;
  const anchorBroken = violations.anchorSource.length > 0;
  const stats = useMemo(() => unitStats(unit.scriptText, lookup), [unit.scriptText, lookup]);
  // 参考图 pills 按当前正文实时派生，不用 unit.references——那是服务端上一次落盘时派生的，
  // 编辑期加/删/改 @[名称] 引用后要保存才更新，用户在改的当下会看到一份对不上正文的旧列表。
  const references = useMemo(() => deriveDisplayReferences(unit.scriptText, lookup), [unit.scriptText, lookup]);
  // 档位表解析不到、或内容不可编辑（隔离草稿）时退回只读秒数：能选的档位必须是保存后
  // 后端收编不会再改的那一档，拿不到权威档位表就不提供会被静默改掉的选择。
  const durationOptions = onDurationChange && supportedDurations?.length ? supportedDurations : null;

  return (
    <article
      ref={(el) => onScrollRef(unit.key, el)}
      className={`rounded-[10px] border p-4 ${hasViolation ? "border-red-500/45" : "border-hairline"}`}
      style={CARD_STYLE}
    >
      <div className="flex items-center gap-2">
        <span className="rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[11px] text-text-2">{unit.key}</span>
        {durationOptions && onDurationChange ? (
          <select
            value={unit.durationSeconds}
            onChange={(e) => onDurationChange(Number(e.target.value))}
            disabled={busy}
            aria-label={t("reference_step1_duration_label", { unit: unit.key })}
            className="rounded-[6px] border border-hairline bg-bg-grad-a/40 px-1 py-0.5 text-[11px] text-text-3 hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
          >
            {/* 存量草稿的秒数可能已不在当前档位表内：补一个当前值选项，否则 select 会静默
                跳到首档，用户看到的秒数与盘上的对不上。 */}
            {(durationOptions.includes(unit.durationSeconds)
              ? durationOptions
              : [...durationOptions, unit.durationSeconds].sort((a, b) => a - b)
            ).map((d) => (
              <option key={d} value={d}>
                {t("reference_step1_duration_option", { seconds: d })}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-[11px] text-text-4">{t("reference_step1_duration_option", { seconds: unit.durationSeconds })}</span>
        )}
        {outOfTier && (
          <span className="rounded bg-red-500/15 px-1 py-px text-[10px] text-red-300">
            {t("reference_step1_duration_out_of_tier")}
          </span>
        )}
        <span className="text-[11px] text-text-4">
          {t("reference_step1_unit_stats", { shots: stats.shots, utterances: stats.utterances })}
        </span>
        <span className="flex-1" />
        {onShotTextChange && (
          <button
            type="button"
            onClick={onToggleEdit}
            aria-label={editing ? t("reference_step1_edit_done") : t("reference_step1_edit_text")}
            className={`rounded-[6px] p-1 transition-colors ${editing ? "bg-accent/20 text-accent" : "text-text-4 hover:text-text"}`}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      <details open className="group mt-3">
        <summary className="flex cursor-pointer list-none items-center gap-1 font-mono text-[10px] tracking-[0.08em] text-text-4">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" aria-hidden="true" />
          {t("reference_step1_source_text_label")}
          {anchorBroken && (
            <span className="ml-1 rounded bg-red-500/15 px-1 py-px text-[10px] text-red-300">
              {t("reference_step1_source_anchor_broken")}
            </span>
          )}
        </summary>
        <p
          className={`mt-1.5 border-l pl-3 text-[11.5px] leading-relaxed ${
            anchorBroken ? "border-red-400/50 text-red-200/70" : "border-hairline text-text-4"
          }`}
        >
          {unit.sourceText}
        </p>
        <InlineViolations violations={violations.anchorSource} />
      </details>

      <div className="mt-3">
        {editing && unit.shots && onShotTextChange ? (
          <div className="flex flex-col gap-2">
            {unit.shots.map((shot, i) => (
              <div key={i} className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-2.5">
                <div className="mb-1.5 font-mono text-[11px] text-text-3">
                  {t("review_shot_label", { index: i + 1 })}
                </div>
                <AutoTextarea
                  value={shot.text}
                  onChange={(text) => onShotTextChange(i, text)}
                  disabled={busy}
                  aria-label={t("review_shot_label", { index: i + 1 })}
                  className="text-text-3"
                />
              </div>
            ))}
          </div>
        ) : (
          <ScriptHighlight
            text={unit.scriptText}
            lookup={lookup}
            renderAfterLine={(sourceLine) => <InlineViolations violations={violations.byLine.get(sourceLine) ?? []} />}
          />
        )}
      </div>

      <InlineViolations violations={violations.aggregate} />

      <div className="mt-3 flex items-center gap-2 border-t border-hairline-soft pt-2.5">
        <span className="font-mono text-[10px] tracking-[0.08em] text-text-4">{t("reference_step1_references_label")}</span>
        <ReferencePills references={references} />
      </div>

      {quarantined && hasViolation && (
        <p className="mt-2 text-[10.5px] text-text-4">{t("reference_step1_quarantined_unit_hint")}</p>
      )}
    </article>
  );
}

/** 本面板只编辑 reference_video 变体的 units 内容；其余变体的内容不属于这里。 */
function selectUnitsContent(state: ScriptReviewState): ReferenceStep1Draft | null {
  return state.content != null && "units" in state.content ? state.content : null;
}

/**
 * reference_video step1 拆分结果的按集预览：与 drama/narration 的 `ScriptReviewGate` 同级、
 * 专属 reference_video 变体的审核 gate 面板——文稿流布局（unit 卡：头部 + 原文 + 高亮文稿 +
 * 参考图），隔离草稿态把违约行内锚定到出问题的行，干净态仅需确认放行 step2。
 *
 * unit 正文与时长的编辑复用既有的 `saveScriptReviewContent` 端点，故只在已晋升（非隔离
 * 草稿）内容上开放；隔离草稿的修复走 agent 文件工具 + 晋升工具的既有闭环，本面板只读呈现。
 */
export function ReferenceStep1PreviewPanel({ projectName, episode, lookup }: ReferenceStep1PreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);

  const [editingUnitKey, setEditingUnitKey] = useState<string | null>(null);

  const handleConfirmed = useCallback(() => {
    // 保存 / 确认两次 await 期间用户可能已切走项目（本组件所在的 tab 可能因此被卸载）：只在项目
    // 本身变了才抑制全局副作用，否则会把续写消息写进用户切换到的别的项目/会话。同项目内切
    // tab（如切到「视频单元」，本面板同样会被卸载）不属于这种情况——预填文案本身带着具体
    // 集号，写进全局 assistant 输入框依然准确，不该被同一份卸载信号误伤。
    if (useProjectsStore.getState().currentProjectName !== projectName) return;
    pushToast(t("dashboard:review_confirmed"), "success");
    // 确认放行 + 预填继续消息到会话输入框——只填不发送，用户自行核对后发送。
    useAssistantStore.getState().setInput(t("reference_step1_confirm_continue_prefill", { episode }));
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [projectName, episode, pushToast, t]);

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
    save: handleSave,
    confirm: handleConfirm,
    confirming,
  } = useScriptReviewDraft<ReferenceStep1Draft>({
    projectName,
    episode,
    selectContent: selectUnitsContent,
    onConfirmed: handleConfirmed,
  });

  const updateShotText = useCallback(
    (unitIndex: number, shotIndex: number, text: string) => {
      setDraft((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          units: prev.units.map((u, i) =>
            i === unitIndex ? { ...u, shots: u.shots.map((s, j) => (j === shotIndex ? { ...s, text } : s)) } : u,
          ),
        };
      });
    },
    [setDraft],
  );

  const updateDuration = useCallback(
    (unitIndex: number, seconds: number) => {
      setDraft((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          units: prev.units.map((u, i) => (i === unitIndex ? { ...u, duration_seconds: seconds } : u)),
        };
      });
    },
    [setDraft],
  );

  const handleRequestFix = useCallback(() => {
    const violations = state?.quarantine?.violations ?? [];
    // 重算已无违约、但隔离草稿仍在场（agent 已改对内容、尚未调晋升工具）：不能报「0 处违约
    // 待修复」再让用户去改一份已经没问题的东西，正确的下一步是请 agent 直接晋升。
    const report =
      violations.length === 0
        ? t("reference_step1_fix_request_promote_prefill", { episode })
        : [
            t("reference_step1_fix_request_prefill_header", { episode, count: violations.length }),
            ...violations.map((v, i) => `${i + 1}. ${v.message}`),
          ].join("\n");
    useAssistantStore.getState().setInput(report);
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [state, episode, t]);

  const cardRefs = useRef(new Map<string, HTMLElement>());
  const setCardRef = useCallback((key: string, el: HTMLElement | null) => {
    if (el) cardRefs.current.set(key, el);
    else cardRefs.current.delete(key);
  }, []);
  const scrollToUnit = useCallback((key: string) => {
    cardRefs.current.get(key)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-text-4">{t("dashboard:loading_preprocessing")}</div>;
  }

  if (loadError) {
    return (
      <div role="alert" className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertTriangle className="h-6 w-6 text-amber-400" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-[13px] font-medium text-text-2">{t("dashboard:review_load_failed")}</p>
          {loadError.message && <p className="max-w-sm px-4 font-mono text-[11px] text-text-4">{loadError.message}</p>}
        </div>
        <button type="button" onClick={handleRetry} className={GHOST_BTN_LG_CLS}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("dashboard:review_retry")}
        </button>
      </div>
    );
  }

  const status = state?.status ?? "no_step1";
  const quarantine = state?.quarantine ?? null;
  if (status === "no_step1" || (draft == null && quarantine == null)) {
    return (
      <div className="flex h-64 items-center justify-center text-text-4">{t("dashboard:no_preprocessing_content")}</div>
    );
  }

  const quarantined = quarantine != null;
  const confirmed = status === "confirmed" && !dirty && !quarantined;
  const displayUnits: DisplayUnit[] = quarantined
    ? quarantinedDisplayUnits(quarantine.content, episode, lookup)
    : draft
      ? structuredDisplayUnits(draft)
      : [];
  // 收窄后的档位表若已不再包含某 unit 存量存盘的时长（模型 / 分辨率 / 参考图配置变化所致），
  // 该值仍保留展示（避免 select 静默跳首档），但不能放行确认——_assert_reference_step1_ready
  // 会在 step2 落盘前硬拒同一个越档值，此处先一步拦下，而不是让用户确认后才在别处失败。
  const outOfTierUnitKeys = quarantined
    ? new Set<string>()
    : new Set(
        displayUnits
          .filter((u) => {
            const tiers = unitDurationTiers(u, lookup, state?.duration_tiers ?? null);
            return tiers != null && !tiers.includes(u.durationSeconds);
          })
          .map((u) => u.key),
      );
  const allViolations = quarantine?.violations ?? [];
  const unitKeys = new Set(displayUnits.map((u) => u.key));
  const unassignedViolations = allViolations.filter((v) => !unitKeys.has(unitKeyFromLabel(v.label) ?? ""));
  const violatingUnitKeys = [...new Set(allViolations.map((v) => unitKeyFromLabel(v.label)).filter((k): k is string => k != null))];
  // schema 违约会让草稿收不成任何 unit 卡（units 不是数组 / 条目不是对象）：原样摊开 agent
  // 手里那份内容，否则用户只看得到一条「结构不符」而看不到自己要改的是什么。content 为 null
  // （信封本身损坏）时没有可摊的内容，聚合区的 quarantine_unreadable 违约已经说明情况。
  const rawFallback =
    quarantined && displayUnits.length === 0 && quarantine.content != null
      ? JSON.stringify(quarantine.content, null, 2)
      : null;

  return (
    <div className="flex flex-col gap-3">
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          {quarantined ? (
            <OctagonAlert className="h-4 w-4 shrink-0 text-red-400" />
          ) : confirmed ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
          ) : (
            <Clock className="h-4 w-4 shrink-0 text-amber-400" />
          )}
          <div className="flex flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {quarantined
                ? t("reference_step1_status_quarantined")
                : confirmed
                  ? t("dashboard:review_status_confirmed")
                  : t("dashboard:review_status_pending")}
            </span>
            <span className="text-[11px] text-text-4">
              {quarantined ? (
                <>
                  {violatingUnitKeys.map((key, i) => (
                    <span key={key}>
                      {i > 0 && ", "}
                      <button
                        type="button"
                        onClick={() => scrollToUnit(key)}
                        className="text-red-300 underline decoration-red-300/40 underline-offset-2 hover:decoration-red-300"
                      >
                        {key} · {allViolations.filter((v) => unitKeyFromLabel(v.label) === key).length}
                      </button>
                    </span>
                  ))}
                  {unassignedViolations.length > 0 && (
                    <span> · {t("reference_step1_unassigned_violations", { count: unassignedViolations.length })}</span>
                  )}
                  <span> — {t("reference_step1_click_to_locate")}</span>
                </>
              ) : confirmed ? (
                t("dashboard:review_confirmed_hint")
              ) : (
                t("dashboard:review_pending_hint")
              )}
            </span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {quarantined && (
            <button type="button" onClick={handleRequestFix} className={GHOST_BTN_CLS}>
              {t("reference_step1_request_fix")}
            </button>
          )}
          {!quarantined && dirty && (
            <button type="button" onClick={voidPromise(handleSave)} disabled={busy} className={GHOST_BTN_CLS}>
              <Save className="h-3.5 w-3.5" />
              {saving ? t("common:saving") : t("common:save")}
            </button>
          )}
          <button
            type="button"
            onClick={voidPromise(handleConfirm)}
            disabled={busy || confirmed || quarantined || outOfTierUnitKeys.size > 0}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
            title={
              quarantined
                ? t("reference_step1_confirm_blocked_hint")
                : outOfTierUnitKeys.size > 0
                  ? t("reference_step1_duration_out_of_tier_hint")
                  : undefined
            }
          >
            {quarantined || confirmed ? <Lock className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            {confirming
              ? t("dashboard:review_confirming")
              : confirmed
                ? t("dashboard:review_confirmed_badge")
                : t("reference_step1_confirm_continue")}
          </button>
        </div>
      </header>

      <div className="flex flex-col gap-2.5">
        {displayUnits.map((unit, i) => (
          <UnitCard
            key={unit.key}
            unit={unit}
            violations={partitionViolations(allViolations, unit.key)}
            lookup={lookup}
            quarantined={quarantined}
            onScrollRef={setCardRef}
            editing={!quarantined && editingUnitKey === unit.key}
            onToggleEdit={() => setEditingUnitKey((prev) => (prev === unit.key ? null : unit.key))}
            onShotTextChange={quarantined ? null : (shotIndex, text) => updateShotText(i, shotIndex, text)}
            supportedDurations={unitDurationTiers(unit, lookup, state?.duration_tiers ?? null) ?? (state?.supported_durations ?? null)}
            outOfTier={outOfTierUnitKeys.has(unit.key)}
            onDurationChange={quarantined ? null : (seconds) => updateDuration(i, seconds)}
            busy={busy}
          />
        ))}
      </div>

      {(unassignedViolations.length > 0 || rawFallback) && (
        <section className="rounded-[10px] border border-red-500/45 p-4" style={CARD_STYLE}>
          <h3 className="font-mono text-[10px] tracking-[0.08em] text-text-4">
            {t("reference_step1_unanchored_section")}
          </h3>
          <InlineViolations violations={unassignedViolations} />
          {rawFallback && (
            <pre className="mt-2 max-h-64 overflow-auto rounded-[6px] bg-bg-grad-a/40 p-2.5 font-mono text-[10.5px] leading-relaxed text-text-4">
              {rawFallback}
            </pre>
          )}
        </section>
      )}
    </div>
  );
}
