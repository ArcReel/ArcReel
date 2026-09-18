import { useId, useMemo, useState } from "react";
import { Loader2, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import type {
  ComfyuiBindingCandidate,
  ComfyuiBindingKey,
  ComfyuiBindingTarget,
  ComfyuiBindings,
  ComfyuiInferResponse,
  ComfyuiMediaType,
} from "@/types";
import {
  bindingKeysFor,
  isListBindingKey,
  isMultiTargetBindingKey,
  isRequiredBindingKey,
  literalInputText,
  manualTargets,
  rowOrigin,
  rowStatus,
  sameTarget,
  selectedCandidateIndexes,
  statusTally,
  toggleListTarget,
  toggleSetTarget,
  type ComfyuiNodeEntry,
  type ComfyuiRowStatus,
} from "./comfyui-bindings";

const STATUS_CLS: Record<ComfyuiRowStatus, string> = {
  auto: "border-good/40 bg-good/10 text-good",
  manual: "border-accent/40 bg-accent-dim text-accent-2",
  ambiguous: "border-warn/50 bg-warn/10 text-warn",
  not_found: "border-hairline-strong bg-bg-grad-a/60 text-text-3",
  unsupported: "border-hairline-soft bg-transparent text-text-4 line-through decoration-text-4/60",
  required_unbound: "border-danger/50 bg-danger/10 text-danger",
};

/** 一条目标的四元组写法：`#节点 class_type .输入 “标题”`。 */
function TargetLabel({ target }: { target: ComfyuiBindingTarget }) {
  return (
    <span className="font-mono text-[11.5px] text-text" translate="no">
      #{target.node}
      <span className="text-text-3"> {target.class_type}</span>
      {target.input !== undefined && <span className="text-text"> .{target.input}</span>}
      {target.title ? <span className="ml-1.5 font-sans text-[11px] text-text-4">“{target.title}”</span> : null}
    </span>
  );
}

function ScoreBar({ score, best }: { score: number; best: number }) {
  const pct = Math.max(6, Math.round((score / Math.max(best, 1)) * 100));
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5">
      <span aria-hidden className="h-1 w-14 overflow-hidden rounded-full bg-bg-grad-a">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </span>
      <span className="font-mono text-[10px] tabular-nums text-text-4">{score}</span>
    </span>
  );
}

interface CandidateListProps {
  bindingKey: ComfyuiBindingKey;
  candidates: ComfyuiBindingCandidate[];
  targets: ComfyuiBindingTarget[] | undefined;
  onToggle: (candidate: ComfyuiBindingCandidate) => void;
}

/** 候选列表。参考图是有序多选（选中顺序即参考图序号），其余语义键单选。 */
function CandidateList({ bindingKey, candidates, targets, onToggle }: CandidateListProps) {
  const { t } = useTranslation("dashboard");
  const groupName = useId();
  const multiple = isListBindingKey(bindingKey) || isMultiTargetBindingKey(bindingKey);
  const ordered = isListBindingKey(bindingKey);
  const chosen = selectedCandidateIndexes(candidates, targets);
  const best = candidates[0]?.score ?? 1;

  // 各个候选由 radiogroup 直接持有，中间不隔一层 listitem。
  return (
    <div className="space-y-1" role={multiple ? "group" : "radiogroup"} aria-label={t("ce_cf_candidates_label")}>
      {candidates.map((candidate, index) => {
        const selected = chosen.has(index);
        const order = selected && ordered ? (targets ?? []).findIndex((x) => sameTarget(x, candidate.target)) + 1 : 0;
        return (
          <label
            key={`${candidate.target.node}.${candidate.target.input ?? ""}`}
            className={`flex cursor-pointer items-start gap-2.5 rounded-[7px] border px-2.5 py-1.5 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ${
              selected ? "border-accent/45 bg-accent-dim" : "border-hairline-soft hover:border-hairline"
            }`}
          >
            <input
              type={multiple ? "checkbox" : "radio"}
              name={multiple ? undefined : groupName}
              className="sr-only"
              checked={selected}
              onChange={() => onToggle(candidate)}
            />
            <span
              aria-hidden
              className={`mt-[3px] flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border font-mono text-[8px] text-bg ${
                selected ? "border-accent-2 bg-accent-2" : "border-hairline-strong"
              }`}
            >
              {order > 0 ? order : ""}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5">
                <TargetLabel target={candidate.target} />
                <ScoreBar score={candidate.score} best={best} />
              </span>
              <span className="mt-0.5 block text-[11px] leading-[1.5] text-text-4">
                {candidate.signals.map((signal) => signal.message).join(" · ")}
              </span>
              {candidate.origin !== "inferred" && (
                <span className="mt-0.5 inline-block font-mono text-[10px] uppercase tracking-[0.08em] text-accent-2">
                  {t(candidate.origin === "kept" ? "ce_cf_origin_kept" : "ce_cf_origin_rematched")}
                </span>
              )}
            </span>
          </label>
        );
      })}
    </div>
  );
}

interface ManualPickerProps {
  bindingKey: ComfyuiBindingKey;
  nodes: ComfyuiNodeEntry[];
  onPick: (target: ComfyuiBindingTarget) => void;
}

/** 从 workflow 的全部落点里手选。产物是节点级的，其余语义键只列字面值输入。 */
function ManualPicker({ bindingKey, nodes, onPick }: ManualPickerProps) {
  const { t } = useTranslation("dashboard");
  const options = useMemo(() => {
    const byNode = new Map(nodes.map((node) => [node.id, node]));
    return manualTargets(nodes, bindingKey).map((target) => {
      const node = byNode.get(target.node);
      const value = target.input !== undefined && node ? literalInputText(node, target.input) : "";
      const head = `#${target.node} ${target.class_type}${target.input === undefined ? "" : `.${target.input}`}`;
      return {
        id: `${target.node}.${target.input ?? ""}`,
        label: value === "" ? head : `${head} = ${value}`,
        target,
      };
    });
  }, [nodes, bindingKey]);

  return (
    <select
      className={`${INPUT_CLS} w-72 py-1 font-mono text-[12px]`}
      translate="no"
      value=""
      aria-label={t("ce_cf_manual_pick_label", { key: bindingKey })}
      onChange={(event) => {
        const hit = options.find((option) => option.id === event.target.value);
        if (hit) onPick(hit.target);
      }}
    >
      <option value="">{t(bindingKey === "output" ? "ce_cf_manual_pick_output" : "ce_cf_manual_pick")}</option>
      {options.map((option) => (
        <option key={option.id} value={option.id}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

interface ExtrasProps {
  bindingKey: ComfyuiBindingKey;
  targets: ComfyuiBindingTarget[] | undefined;
  onPatch: (patch: Partial<ComfyuiBindingTarget>) => void;
}

/**
 * 手填帧率：有限且不小于 1 才收下，否则给 `undefined`（这一项不声明）。允许小数——29.97 这类
 * 帧率是真实存在的。
 *
 * `min={1}` 只拦得住原生控件的上下箭头，手打的负数、`Infinity` 与空串照样进得来；定义保存时
 * 不再走一次表单校验，故在写进状态这一步就挡掉。
 */
function positiveNumber(raw: string): number | undefined {
  const value = Number(raw);
  return Number.isFinite(value) && value >= 1 ? value : undefined;
}

/** 对齐步长：schema 声明为整数，小数存下去会被定义校验打回，故在这里就不收。 */
function positiveInteger(raw: string): number | undefined {
  const value = Number(raw);
  return Number.isInteger(value) && value >= 1 ? value : undefined;
}

/** 条目自带的附加项：对齐步长、手填帧率、种子策略、帧率的只读说明。 */
function BindingExtras({ bindingKey, targets, onPatch }: ExtrasProps) {
  const { t } = useTranslation("dashboard");
  const target = targets?.[0];

  if (bindingKey === "fps") {
    return <span className="text-[11.5px] text-text-4">{t("ce_cf_fps_readonly_note")}</span>;
  }
  if (bindingKey === "width" || bindingKey === "height" || bindingKey === "frames") {
    if (!target) return null;
    return (
      <span className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-1.5 text-[11.5px] text-text-3">
          {t("ce_cf_step_label")}
          <input
            type="number"
            min={1}
            inputMode="numeric"
            autoComplete="off"
            value={target.step ?? ""}
            onChange={(event) => onPatch({ step: positiveInteger(event.target.value) })}
            className={`${INPUT_CLS} w-16 py-0.5 text-[12px]`}
          />
        </label>
        {bindingKey === "frames" && (
          <>
            <span className="text-[11px] text-text-4">{t("ce_cf_frames_step_note")}</span>
            <label className="inline-flex items-center gap-1.5 text-[11.5px] text-text-3">
              {t("ce_cf_frames_fps_label")}
              <input
                type="number"
                min={1}
                step="any"
                inputMode="decimal"
                autoComplete="off"
                value={target.fps ?? ""}
                onChange={(event) => onPatch({ fps: positiveNumber(event.target.value) })}
                className={`${INPUT_CLS} w-20 py-0.5 text-[12px]`}
              />
            </label>
          </>
        )}
      </span>
    );
  }
  if (bindingKey === "seed") {
    if (!target) return null;
    const policy = target.policy ?? "random";
    return (
      <span className="inline-flex items-center gap-2 text-[11.5px] text-text-3">
        {t("ce_cf_seed_policy_label")}
        {(["random", "keep"] as const).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={policy === option}
            onClick={() => onPatch({ policy: option })}
            className={`rounded-full border px-2 py-0.5 font-mono text-[10.5px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              policy === option ? "border-accent/45 bg-accent-dim text-accent-2" : "border-hairline-soft text-text-3"
            }`}
          >
            {t(option === "random" ? "ce_cf_seed_random" : "ce_cf_seed_keep")}
          </button>
        ))}
      </span>
    );
  }
  return null;
}

export interface ComfyuiBindingTableProps {
  nodes: ComfyuiNodeEntry[];
  mediaType: ComfyuiMediaType;
  inference: ComfyuiInferResponse;
  bindings: ComfyuiBindings;
  /** 本轮被用户改过的语义键：改过即算「手动指定」，不再显示为自动识别。 */
  touched: ReadonlySet<ComfyuiBindingKey>;
  onChange: (key: ComfyuiBindingKey, targets: ComfyuiBindingTarget[] | undefined) => void;
  onReinfer: (key: ComfyuiBindingKey) => void;
  /** 正在重新识别的语义键；同一时刻只会有一个。 */
  reinferring: ComfyuiBindingKey | null;
}

/**
 * 节点绑定表：一行一个语义键，展开后是候选列表、手选下拉与该键的附加项。
 * 并列候选与待确认的行默认展开——它们挡着保存，折叠起来用户找不到要做什么。
 */
export function ComfyuiBindingTable({
  nodes,
  mediaType,
  inference,
  bindings,
  touched,
  onChange,
  onReinfer,
  reinferring,
}: ComfyuiBindingTableProps) {
  const { t } = useTranslation("dashboard");
  // 用户亲手开合过的行，压过下面那份默认值；每行各算各的，不受其他行影响。
  const [toggled, setToggled] = useState<Partial<Record<ComfyuiBindingKey, boolean>>>({});
  const panelIdPrefix = useId();

  // 默认展开哪几行只由到手的这一轮推断定：跟着行状态走的话，用户选中第一个候选的那一刻面板
  // 就合上了，有序多选的第二张点不到，宽高帧数与种子的附加项也正好在这时才出现。
  const defaultOpen = useMemo(() => {
    const open = new Set<string>();
    for (const [key, result] of Object.entries(inference.bindings)) {
      if (result.state === "ambiguous" || result.state === "needs_confirmation") open.add(key);
    }
    return open;
  }, [inference]);

  const keys = bindingKeysFor(mediaType);
  const statuses = keys.map((key) =>
    rowStatus(key, inference.bindings[key]?.state, bindings[key], {
      touched: touched.has(key),
      candidates: inference.bindings[key]?.candidates ?? [],
    }),
  );
  const tally = statusTally(statuses);

  return (
    <div>
      <div className="mb-2.5 flex flex-wrap items-end justify-between gap-3">
        <p className="max-w-xl text-[12px] leading-[1.55] text-text-3">{t("ce_cf_bindings_desc")}</p>
        <span className="font-mono text-[11px] tabular-nums text-text-4" aria-live="polite">
          {t("ce_cf_tally", {
            bound: tally.bound,
            ambiguous: tally.ambiguous,
            notFound: tally.notFound,
            unsupported: tally.unsupported,
          })}
        </span>
      </div>

      {inference.notes.length > 0 && (
        <ul className="mb-2.5 space-y-1">
          {inference.notes.map((note) => (
            <li key={note.code} className="text-[11.5px] leading-[1.5] text-warm-bright">
              {note.message}
            </li>
          ))}
        </ul>
      )}

      <div className="divide-y divide-hairline-soft">
        {keys.map((key, index) => {
          const result = inference.bindings[key];
          const candidates = result?.candidates ?? [];
          const targets = bindings[key];
          const status = statuses[index];
          const open = toggled[key] ?? defaultOpen.has(key);
          const panelId = `${panelIdPrefix}-${key}`;
          const required = isRequiredBindingKey(key);
          // 重导入在这一行留下的记号，显示在条目旁边——折叠着也看得见哪些是沿用、哪些重匹配过。
          const origin = rowOrigin(candidates, targets);

          return (
            <div key={key} className="py-2.5">
              <div className="grid grid-cols-[minmax(150px,190px)_96px_minmax(0,1fr)_auto] items-center gap-3">
                <span className="inline-flex items-baseline gap-1.5 text-[12.5px]">
                  <span className="font-mono text-[11.5px] text-accent-2" translate="no">
                    {key}
                  </span>
                  <span className="text-text-2">{t(`ce_cf_key_${key}`)}</span>
                  {required && (
                    <span className="text-warm-bright" title={t("ce_cf_required")} aria-label={t("ce_cf_required")}>
                      *
                    </span>
                  )}
                </span>

                <span
                  className={`inline-flex items-center justify-center rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold ${STATUS_CLS[status]}`}
                >
                  {t(`ce_cf_status_${status}`)}
                </span>

                <span className="flex min-w-0 items-center gap-1.5 truncate text-[12px]">
                  {targets && targets.length > 0 ? (
                    <>
                      {isListBindingKey(key) && targets.length > 1 ? (
                        <span className="text-text-2">{t("ce_cf_target_count", { n: targets.length })}</span>
                      ) : (
                        <TargetLabel target={targets[0]} />
                      )}
                      {origin !== null && (
                        <span className="shrink-0 rounded-[4px] border border-accent/35 px-1 py-px font-mono text-[9.5px] uppercase tracking-[0.08em] text-accent-2">
                          {t(origin === "kept" ? "ce_cf_origin_kept" : "ce_cf_origin_rematched")}
                        </span>
                      )}
                    </>
                  ) : status === "unsupported" ? (
                    <span className="text-text-4">{t("ce_cf_unsupported_text")}</span>
                  ) : status === "ambiguous" ? (
                    <span className="text-warm-bright">
                      {candidates.length > 0
                        ? t("ce_cf_ambiguous_text", { n: candidates.length })
                        : t("ce_cf_lost_text")}
                    </span>
                  ) : (
                    <span className="text-text-4">{t(`ce_cf_hint_${key}`)}</span>
                  )}
                </span>

                <button
                  type="button"
                  aria-expanded={open}
                  aria-controls={panelId}
                  onClick={() => setToggled((current) => ({ ...current, [key]: !open }))}
                  className="text-[11.5px] text-text-3 transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {open
                    ? t("ce_cf_collapse")
                    : candidates.length > 0
                      ? t("ce_cf_expand_candidates", { n: candidates.length })
                      : t("ce_cf_expand_manual")}
                </button>
              </div>

              {result && result.notes.length > 0 && (
                <ul className="ml-[202px] mt-1 space-y-0.5">
                  {result.notes.map((note) => (
                    <li key={note.code} className="text-[11px] leading-[1.5] text-text-4">
                      {note.message}
                    </li>
                  ))}
                </ul>
              )}

              {open && (
                <div id={panelId} className="ml-[202px] mt-2 space-y-2">
                  {result?.state === "needs_confirmation" && (
                    <p className="text-[11.5px] text-warm-bright">{t("ce_cf_needs_confirmation")}</p>
                  )}
                  {candidates.length > 0 && (
                    <CandidateList
                      bindingKey={key}
                      candidates={candidates}
                      targets={targets}
                      onToggle={(candidate) => {
                        if (isListBindingKey(key)) {
                          const ordered = toggleListTarget(candidates, targets ?? [], candidate.target);
                          onChange(key, ordered.length > 0 ? ordered : undefined);
                          return;
                        }
                        if (!isMultiTargetBindingKey(key)) {
                          onChange(key, [candidate.target]);
                          return;
                        }
                        const next = toggleSetTarget(targets ?? [], candidate.target);
                        onChange(key, next.length > 0 ? next : undefined);
                      }}
                    />
                  )}
                  <div className="flex flex-wrap items-center gap-3">
                    <ManualPicker
                      bindingKey={key}
                      nodes={nodes}
                      onPick={(target) => {
                        const current = targets ?? [];
                        if (isListBindingKey(key)) {
                          if (current.some((entry) => sameTarget(entry, target))) return;
                          onChange(key, toggleListTarget(candidates, current, target));
                          return;
                        }
                        if (!isMultiTargetBindingKey(key)) {
                          onChange(key, [target]);
                          return;
                        }
                        // 手选只做加法：同键的其余落点是别的分支在用的，删要从候选列表里取消勾选。
                        if (current.some((entry) => sameTarget(entry, target))) return;
                        onChange(key, [...current, target]);
                      }}
                    />
                    <BindingExtras
                      bindingKey={key}
                      targets={targets}
                      onPatch={(patch) => {
                        if (!targets || targets.length === 0) return;
                        onChange(key, [{ ...targets[0], ...patch }, ...targets.slice(1)]);
                      }}
                    />
                    <span className="ml-auto inline-flex items-center gap-1">
                      {!required && status !== "unsupported" && (
                        <button
                          type="button"
                          title={t("ce_cf_mark_unsupported_hint")}
                          onClick={() => onChange(key, [])}
                          className={`${GHOST_BTN_CLS} px-2 py-1 text-[11px]`}
                        >
                          <X className="h-3 w-3" aria-hidden />
                          {t("ce_cf_mark_unsupported")}
                        </button>
                      )}
                      <button
                        type="button"
                        title={t("ce_cf_reinfer_hint")}
                        disabled={reinferring !== null}
                        onClick={() => onReinfer(key)}
                        className={`${GHOST_BTN_CLS} px-2 py-1 text-[11px]`}
                      >
                        {reinferring === key ? (
                          <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />
                        ) : (
                          <RefreshCw className="h-3 w-3" aria-hidden />
                        )}
                        {t("ce_cf_reinfer")}
                      </button>
                    </span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
