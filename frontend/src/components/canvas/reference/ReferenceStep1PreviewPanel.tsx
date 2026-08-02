import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, ChevronDown, Clock, Lock, OctagonAlert, Pencil, RotateCcw, Save } from "lucide-react";
import { API } from "@/api";
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
import { voidPromise } from "@/utils/async";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import { assetColor } from "@/components/canvas/reference/asset-colors";
import { ScriptHighlight } from "@/components/shared/ScriptHighlight";
import { toScriptLines, type MentionLookup } from "@/hooks/useShotPromptHighlight";
import { extractMentions, formatShotHeader } from "@/utils/reference-mentions";

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
  for (const name of extractMentions(text)) {
    const assetKind = lookup[name];
    if (!assetKind) continue;
    out.push({ type: assetKind, name });
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
  onDurationChange: ((seconds: number) => void) | null;
  /** 保存 / 确认请求在途：锁住时长下拉与镜头正文，避免 adopt() 用服务端回显覆盖请求发出后的新编辑。 */
  busy: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const hasViolation = violations.anchorSource.length + violations.byLine.size + violations.aggregate.length > 0;
  const anchorBroken = violations.anchorSource.length > 0;
  const stats = useMemo(() => unitStats(unit.scriptText, lookup), [unit.scriptText, lookup]);
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
              : [unit.durationSeconds, ...durationOptions]
            ).map((d) => (
              <option key={d} value={d}>
                {t("reference_step1_duration_option", { seconds: d })}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-[11px] text-text-4">{t("reference_step1_duration_option", { seconds: unit.durationSeconds })}</span>
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
        <ReferencePills references={unit.references} />
      </div>

      {quarantined && hasViolation && (
        <p className="mt-2 text-[10.5px] text-text-4">{t("reference_step1_quarantined_unit_hint")}</p>
      )}
    </article>
  );
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "";
}

/** 内容是否有未保存编辑：以序列化比对，draft 由 server content 克隆而来，键序稳定。 */
function isDirty(draft: unknown, serverContent: unknown): boolean {
  if (draft == null) return false;
  return JSON.stringify(draft) !== JSON.stringify(serverContent);
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
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_step1`));

  const [state, setState] = useState<ScriptReviewState | null>(null);
  const [draft, setDraft] = useState<ReferenceStep1Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ message: string } | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [editingUnitKey, setEditingUnitKey] = useState<string | null>(null);

  const serverContent = state?.content != null && "units" in state.content ? (state.content) : null;
  const dirty = useMemo(() => isDirty(draft, serverContent), [draft, serverContent]);
  const busy = saving || confirming;

  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  // 组件整个生命周期的取消信号：确认动作的 await 断点跨越保存 + 确认两次请求，用户可能在
  // 途中切走项目 / 剧集（组件卸载）。卸载后不再写全局 assistant 输入框 —— 否则残留的续写
  // 消息会写进用户切换到的新项目/会话的输入框。用 state（非 ref）持有：事件处理器闭包读取
  // ref.current 会被 react-hooks/refs 判定为「可能在渲染期读取」，state 值本身不受此限制，
  // 且这里只需要它在挂载期内保持同一个实例，不需要写路径绕过渲染的能力。
  const [lifecycleAbort] = useState(() => new AbortController());
  useEffect(() => {
    return () => lifecycleAbort.abort();
  }, [lifecycleAbort]);

  const adopt = useCallback((next: ScriptReviewState) => {
    setState(next);
    const content = next.content != null && "units" in next.content ? (next.content) : null;
    setDraft(content ? (JSON.parse(JSON.stringify(content)) as ReferenceStep1Draft) : null);
  }, []);

  const handleRetry = useCallback(() => {
    setLoadError(null);
    setLoading(true);
    setReloadNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const hadResponse = state != null;
    const hasContent = draft != null;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!hadResponse) setLoading(true);
    API.getScriptReview(projectName, episode, { signal })
      .then((next) => {
        // 响应已 resolve 之后才 abort 的窗口：写 state 前复核，避免离场的这轮回写。
        if (signal.aborted) return;
        setLoadError(null);
        setState(next);
        if (!dirtyRef.current) {
          const content = next.content != null && "units" in next.content ? (next.content) : null;
          setDraft(content ? (JSON.parse(JSON.stringify(content)) as ReferenceStep1Draft) : null);
        }
      })
      .catch((err) => {
        if (signal.aborted) return;
        if (!hasContent) setLoadError({ message: errorMessage(err) });
      })
      .finally(() => {
        // 收尾让位给接管方：已作废就不碰 loading，否则会打断新一轮加载态。
        if (!signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- state/draft 仅用于决定加载态与错误态分支，加入 deps 会在每次刷新后重新拉取造成循环
  }, [projectName, episode, draftRevision, reloadNonce]);

  const updateShotText = useCallback((unitIndex: number, shotIndex: number, text: string) => {
    setDraft((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        units: prev.units.map((u, i) =>
          i === unitIndex ? { ...u, shots: u.shots.map((s, j) => (j === shotIndex ? { ...s, text } : s)) } : u,
        ),
      };
    });
  }, []);

  const updateDuration = useCallback((unitIndex: number, seconds: number) => {
    setDraft((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        units: prev.units.map((u, i) => (i === unitIndex ? { ...u, duration_seconds: seconds } : u)),
      };
    });
  }, []);

  const handleSave = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      adopt(await API.saveScriptReviewContent(projectName, episode, draft));
      pushToast(t("dashboard:review_saved"), "success");
    } catch (err) {
      pushToast(errorMessage(err) || t("dashboard:save_failed", { message: "" }), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, projectName, episode, adopt, pushToast, t]);

  const handleConfirm = useCallback(async () => {
    setConfirming(true);
    try {
      if (dirty && draft) {
        adopt(await API.saveScriptReviewContent(projectName, episode, draft));
      }
      adopt(await API.confirmScriptReview(projectName, episode));
      // 两次 await 期间用户可能已切走项目/剧集（本组件已卸载）：不再写全局 assistant 输入框，
      // 那会把这条续写消息写进用户切换到的新上下文里。
      if (lifecycleAbort.signal.aborted) return;
      pushToast(t("dashboard:review_confirmed"), "success");
      // 确认放行 + 预填继续消息到会话输入框——只填不发送，用户自行核对后发送。
      useAssistantStore.getState().setInput(t("reference_step1_confirm_continue_prefill", { episode }));
      useAppStore.getState().setAssistantPanelOpen(true);
    } catch (err) {
      pushToast(errorMessage(err) || t("dashboard:review_confirm_failed"), "error");
    } finally {
      setConfirming(false);
    }
  }, [dirty, draft, projectName, episode, adopt, pushToast, t, lifecycleAbort]);

  const handleRequestFix = useCallback(() => {
    const violations = state?.quarantine?.violations ?? [];
    const report = [
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
                  {violatingUnitKeys.map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => scrollToUnit(key)}
                      className="text-red-300 underline decoration-red-300/40 underline-offset-2 hover:decoration-red-300"
                    >
                      {key} · {allViolations.filter((v) => unitKeyFromLabel(v.label) === key).length}
                    </button>
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
            disabled={busy || confirmed || quarantined}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
            title={quarantined ? t("reference_step1_confirm_blocked_hint") : undefined}
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
            supportedDurations={
              state?.duration_tiers
                ? unit.references.length > 0
                  ? state.duration_tiers.with_references
                  : state.duration_tiers.without_references
                : (state?.supported_durations ?? null)
            }
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
