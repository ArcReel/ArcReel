import { useId, useMemo, useState } from "react";
import { Loader2, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
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
  manual: "border-primary/40 bg-primary/12 text-primary",
  ambiguous: "border-warn/50 bg-warn/10 text-warn",
  not_found: "border-input text-muted-foreground",
  unsupported: "border-border/50 text-muted-foreground line-through",
  required_unbound: "border-destructive/50 bg-destructive/10 text-destructive",
};

/** 一条目标的四元组写法：`#节点 class_type .输入 “标题”`。 */
function TargetLabel({ target }: { target: ComfyuiBindingTarget }) {
  return (
    <span className="font-mono text-xs text-foreground" translate="no">
      #{target.node}
      <span className="text-muted-foreground"> {target.class_type}</span>
      {target.input !== undefined && <span className="text-foreground"> .{target.input}</span>}
      {target.title ? <span className="ml-1.5 font-sans text-muted-foreground">“{target.title}”</span> : null}
    </span>
  );
}

function ScoreBar({ score, best }: { score: number; best: number }) {
  const pct = Math.max(6, Math.round((score / Math.max(best, 1)) * 100));
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5">
      {/* 比例条用 SVG 画：宽度随得分变化，写成元素属性而不是内联样式 */}
      <svg aria-hidden viewBox="0 0 100 4" preserveAspectRatio="none" className="h-1 w-14 overflow-hidden rounded-full">
        <rect width="100" height="4" className="fill-muted" />
        <rect width={pct} height="4" className="fill-primary" />
      </svg>
      <span className="font-mono text-xs tabular-nums text-muted-foreground">{score}</span>
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
    <div className="flex flex-col gap-1" role={multiple ? "group" : "radiogroup"} aria-label={t("ce_cf_candidates_label")}>
      {candidates.map((candidate, index) => {
        const selected = chosen.has(index);
        const order = selected && ordered ? (targets ?? []).findIndex((x) => sameTarget(x, candidate.target)) + 1 : 0;
        return (
          <label
            key={`${candidate.target.node}.${candidate.target.input ?? ""}`}
            className={cn(
              "flex cursor-pointer items-start gap-2.5 rounded-md border px-2.5 py-1.5 transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
              selected ? "border-primary/50 bg-primary/15" : "border-border/50 hover:border-border",
            )}
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
              className={cn(
                "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border font-mono text-xs leading-none text-primary-foreground",
                selected ? "border-primary bg-primary" : "border-input",
              )}
            >
              {order > 0 ? order : ""}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5">
                <TargetLabel target={candidate.target} />
                <ScoreBar score={candidate.score} best={best} />
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {candidate.signals.map((signal) => signal.message).join(" · ")}
              </span>
              {candidate.origin !== "inferred" && (
                <span className="mt-0.5 inline-block text-xs text-primary">
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
    // 原生下拉：选项是带节点号与字面值的一长串落点，值选中即生效、随即回到占位项
    <select
      className="h-8 w-full max-w-72 min-w-0 rounded-lg border border-input bg-input/30 px-2.5 font-mono text-xs text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
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
  const policyLabelId = useId();
  const target = targets?.[0];

  if (bindingKey === "fps") {
    return <span className="text-xs text-muted-foreground">{t("ce_cf_fps_readonly_note")}</span>;
  }
  if (bindingKey === "width" || bindingKey === "height" || bindingKey === "frames") {
    if (!target) return null;
    return (
      <span className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          {t("ce_cf_step_label")}
          <Input
            type="number"
            min={1}
            inputMode="numeric"
            autoComplete="off"
            value={target.step ?? ""}
            onChange={(event) => onPatch({ step: positiveInteger(event.target.value) })}
            className="w-20"
          />
        </label>
        {bindingKey === "frames" && (
          <>
            <span className="text-xs text-muted-foreground">{t("ce_cf_frames_step_note")}</span>
            <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              {t("ce_cf_frames_fps_label")}
              <Input
                type="number"
                min={1}
                step="any"
                inputMode="decimal"
                autoComplete="off"
                value={target.fps ?? ""}
                onChange={(event) => onPatch({ fps: positiveNumber(event.target.value) })}
                className="w-24"
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
      <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
        <span id={policyLabelId}>{t("ce_cf_seed_policy_label")}</span>
        <ToggleGroup
          aria-labelledby={policyLabelId}
          variant="outline"
          size="sm"
          value={[policy]}
          onValueChange={(next: string[]) => {
            // 单选：再次点击已选项不取消选择
            if (next[0] === "random" || next[0] === "keep") onPatch({ policy: next[0] });
          }}
        >
          {(["random", "keep"] as const).map((option) => (
            <ToggleGroupItem key={option} value={option}>
              {t(option === "random" ? "ce_cf_seed_random" : "ce_cf_seed_keep")}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
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
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <p className="max-w-xl text-xs text-muted-foreground">{t("ce_cf_bindings_desc")}</p>
        <span className="text-xs tabular-nums text-muted-foreground" aria-live="polite">
          {t("ce_cf_tally", {
            bound: tally.bound,
            ambiguous: tally.ambiguous,
            notFound: tally.notFound,
            unsupported: tally.unsupported,
          })}
        </span>
      </div>

      {inference.notes.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1">
          {inference.notes.map((note) => (
            <li key={note.code} className="text-xs text-warn">
              {note.message}
            </li>
          ))}
        </ul>
      )}

      <div className="divide-y divide-border">
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
              <div className="grid grid-cols-[minmax(9rem,12rem)_6rem_minmax(0,1fr)_auto] items-center gap-x-3">
                <span className="inline-flex items-baseline gap-1.5 text-sm">
                  <span className="font-mono text-xs text-primary" translate="no">
                    {key}
                  </span>
                  <span className="text-subtle-foreground">{t(`ce_cf_key_${key}`)}</span>
                  {required && (
                    <span className="text-warn" title={t("ce_cf_required")} aria-label={t("ce_cf_required")}>
                      *
                    </span>
                  )}
                </span>

                <span
                  className={cn(
                    "inline-flex items-center justify-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
                    STATUS_CLS[status],
                  )}
                >
                  {t(`ce_cf_status_${status}`)}
                </span>

                <span className="flex min-w-0 items-center gap-1.5 truncate text-xs">
                  {targets && targets.length > 0 ? (
                    <>
                      {isListBindingKey(key) && targets.length > 1 ? (
                        <span className="text-subtle-foreground">{t("ce_cf_target_count", { n: targets.length })}</span>
                      ) : (
                        <TargetLabel target={targets[0]} />
                      )}
                      {origin !== null && (
                        <span className="shrink-0 rounded-sm border border-primary/40 px-1 text-xs text-primary">
                          {t(origin === "kept" ? "ce_cf_origin_kept" : "ce_cf_origin_rematched")}
                        </span>
                      )}
                    </>
                  ) : status === "unsupported" ? (
                    <span className="text-muted-foreground">{t("ce_cf_unsupported_text")}</span>
                  ) : status === "ambiguous" ? (
                    <span className="text-warn">
                      {candidates.length > 0
                        ? t("ce_cf_ambiguous_text", { n: candidates.length })
                        : t("ce_cf_lost_text")}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t(`ce_cf_hint_${key}`)}</span>
                  )}
                </span>

                <Button
                  variant="ghost"
                  size="xs"
                  aria-expanded={open}
                  aria-controls={panelId}
                  onClick={() => setToggled((current) => ({ ...current, [key]: !open }))}
                >
                  {open
                    ? t("ce_cf_collapse")
                    : candidates.length > 0
                      ? t("ce_cf_expand_candidates", { n: candidates.length })
                      : t("ce_cf_expand_manual")}
                </Button>
              </div>

              {/* 说明与展开区与上面那行同列宽，从状态列起排，与语义键名错开 */}
              <div className="grid grid-cols-[minmax(9rem,12rem)_6rem_minmax(0,1fr)_auto] gap-x-3">
                {result && result.notes.length > 0 && (
                  <ul className="col-span-3 col-start-2 mt-1 flex flex-col gap-0.5">
                    {result.notes.map((note) => (
                      <li key={note.code} className="text-xs text-muted-foreground">
                        {note.message}
                      </li>
                    ))}
                  </ul>
                )}

                {open && (
                  <div id={panelId} className="col-span-3 col-start-2 mt-2 flex flex-col gap-2">
                    {result?.state === "needs_confirmation" && (
                      <p className="text-xs text-warn">{t("ce_cf_needs_confirmation")}</p>
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
                          <Button
                            variant="ghost"
                            size="xs"
                            title={t("ce_cf_mark_unsupported_hint")}
                            onClick={() => onChange(key, [])}
                          >
                            <X aria-hidden data-icon="inline-start" />
                            {t("ce_cf_mark_unsupported")}
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="xs"
                          title={t("ce_cf_reinfer_hint")}
                          disabled={reinferring !== null}
                          onClick={() => onReinfer(key)}
                        >
                          {reinferring === key ? (
                            <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
                          ) : (
                            <RefreshCw aria-hidden data-icon="inline-start" />
                          )}
                          {t("ce_cf_reinfer")}
                        </Button>
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
