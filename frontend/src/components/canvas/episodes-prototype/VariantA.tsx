// PROTOTYPE（#2767）变体 A「集清单为主」：进度卡 + 按集号的统一集清单；划范围在弹窗里做，候选 = 清单 + 变更摘要。
import { useId, useState, type ReactNode } from "react";
import { Loader2, MoreHorizontal, Plus, Scissors, Sparkles, Upload } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { CARD_STYLE, DROPDOWN_PANEL_STYLE, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import * as M from "./model";
import {
  AffectedList, CandidateActions, CandidateSummaryBlock, EpPills, FRESH_STYLE, ReplanDialog, UploadDialog,
  guardArtifacts, useConfirm, type ConfirmReq,
} from "./shared";
import type { Proto } from "./useProto";

const segHue = (i: number) => `oklch(0.62 0.11 ${(i * 47 + 250) % 360})`;

function prevCut(s: M.ProtoState, id: number): M.Ep | undefined {
  const list = M.cutEps(s);
  const i = list.findIndex((e) => e.id === id);
  return i > 0 ? list[i - 1] : undefined;
}

// ---------------------------------------------------------------- 主体

export function VariantA({ p }: { p: Proto }) {
  const [uploadOpen, setUploadOpen] = useState(false);
  const [replanFrom, setReplanFrom] = useState<number | null>(null);
  const [cutOpen, setCutOpen] = useState(false);
  const [rangeEdit, setRangeEdit] = useState<{ id: number; mode: "adjust" | "split" } | null>(null);
  const [confirmNode, ask] = useConfirm();
  const eps = M.byId(p.s);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div
        className="sticky top-0 z-10 flex items-center gap-3 px-5 py-3"
        style={{
          background: "linear-gradient(180deg, oklch(0.20 0.012 265 / 0.85), oklch(0.18 0.010 265 / 0.65))",
          backdropFilter: "blur(10px)",
          borderBottom: "1px solid var(--color-hairline-soft)",
        }}
      >
        <span aria-hidden className="h-3 w-[3px] rounded-full" style={{ background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))" }} />
        <h2 className="display-serif text-[15px] font-semibold tracking-tight">分集</h2>
        <span
          className="num inline-flex items-center justify-center rounded-md px-1.5 py-[2px] text-[10.5px]"
          style={{ color: "var(--color-text-3)", background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)", minWidth: 22 }}
        >
          {String(eps.length).padStart(2, "0")}
        </span>
        <div className="flex-1" />
        <SecondaryButton size="sm" leadingIcon={<Upload className="h-3.5 w-3.5" />} onClick={() => setUploadOpen(true)}>上传原文</SecondaryButton>
        <SecondaryButton size="sm" leadingIcon={<Plus className="h-3.5 w-3.5" />} onClick={p.addBlankEpisode}>新建一集</SecondaryButton>
      </div>

      <div className="mx-auto w-full max-w-4xl space-y-4 px-6 py-5">
        <ProgressCard p={p} onManualCut={() => setCutOpen(true)} onReplan={setReplanFrom} />
        {p.s.candidate && <CandidatePanel p={p} />}
        <div className="overflow-hidden rounded-2xl" style={{ ...CARD_STYLE, border: "1px solid var(--color-hairline-soft)" }}>
          {eps.map((ep) => (
            <EpRow
              key={ep.id}
              p={p}
              ep={ep}
              ask={ask}
              onReplan={() => setReplanFrom(ep.id)}
              onAdjust={() => setRangeEdit({ id: ep.id, mode: "adjust" })}
              onSplit={() => setRangeEdit({ id: ep.id, mode: "split" })}
            />
          ))}
        </div>
      </div>

      <UploadDialog p={p} open={uploadOpen} onClose={() => setUploadOpen(false)} />
      <ReplanDialog p={p} fromId={replanFrom} onClose={() => setReplanFrom(null)} />
      <ManualCutModal p={p} open={cutOpen} onClose={() => setCutOpen(false)} />
      <RangeModal p={p} edit={rangeEdit} ask={ask} onClose={() => setRangeEdit(null)} />
      {confirmNode}
    </div>
  );
}

// ---------------------------------------------------------------- 进度卡

function ProgressCard({ p, onManualCut, onReplan }: { p: Proto; onManualCut: () => void; onReplan: (id: number) => void }) {
  const [instr, setInstr] = useState(p.s.lastInstructions);
  const cuts = M.cutEps(p.s);
  const planned = M.charsOf([0, p.s.cursor]);
  const busy = !!p.s.planning || !!p.s.candidate;
  const atEnd = p.s.cursor >= M.SOURCE_LEN;
  const blocked = busy || p.s.sourceReplaced || atEnd;
  const staleEps = p.s.episodes.filter((e) => e.stale);

  return (
    <div className="space-y-3 rounded-2xl p-4" style={{ ...CARD_STYLE, border: "1px solid var(--color-hairline-soft)" }}>
      <div className="flex items-baseline gap-2 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
        <span className="font-medium" style={{ color: "var(--color-text)" }}>整本源文 · {M.SOURCE_FILE_NAME}</span>
        <span className="num">已规划 {planned.toLocaleString()} / {M.TOTAL_CHARS.toLocaleString()} 字 · {cuts.length} 集切自整本源文</span>
      </div>

      {/* 细进度条：已切出的集分段着色，游标，未规划部分斜纹 */}
      <div className="relative h-2.5 w-full overflow-hidden rounded-full" style={{ background: "repeating-linear-gradient(135deg, oklch(0.25 0.01 265) 0 4px, oklch(0.21 0.01 265) 4px 8px)" }}>
        {cuts.map((e, i) => (
          <div
            key={e.id}
            title={`第 ${e.id} 集 · ${e.title}`}
            className="absolute top-0 h-full"
            style={{
              left: `${(e.range![0] / M.SOURCE_LEN) * 100}%`,
              width: `${((e.range![1] - e.range![0]) / M.SOURCE_LEN) * 100}%`,
              background: segHue(i),
              borderRight: "1px solid oklch(0.15 0 0)",
              opacity: e.stale ? 0.55 : 1,
            }}
          />
        ))}
        <div className="absolute top-0 h-full w-[2px]" style={{ left: `${(p.s.cursor / M.SOURCE_LEN) * 100}%`, background: "white", boxShadow: "0 0 6px white" }} title="规划游标" />
      </div>

      {p.s.sourceReplaced && (
        <div className="flex items-center gap-3 rounded-md px-3 py-2 text-[12px]" style={{ border: "1px dashed oklch(0.7 0.1 200 / 0.6)", color: "oklch(0.85 0.08 200)" }}>
          <span className="flex-1">整本源文已替换，需从第 1 集起重新规划。</span>
          {cuts[0] && <SecondaryButton size="sm" disabled={busy} onClick={() => onReplan(cuts[0].id)}>从第 1 集起重新规划</SecondaryButton>}
        </div>
      )}

      {p.s.planning ? (
        <div className="flex items-center gap-3 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
          <Loader2 className="h-4 w-4 animate-spin" style={{ color: "var(--color-accent-2)" }} />
          <span>{p.s.planning.mode === "toEnd" ? "规划到源文结尾" : "先规划一批"} · 正在规划第 {p.s.planning.batchesDone + 1} 批（服务端逐窗串联，已提交的批次保留）</span>
          <div className="flex-1" />
          <SecondaryButton size="sm" onClick={p.stopPlanning}>停止</SecondaryButton>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <PrimaryButton size="sm" leadingIcon={<Sparkles className="h-3.5 w-3.5" />} disabled={blocked} onClick={() => p.beginPlanning("toEnd", instr)}>规划到源文结尾</PrimaryButton>
          <SecondaryButton size="sm" disabled={blocked} onClick={() => p.beginPlanning("batch", instr)}>先规划一批</SecondaryButton>
          <input className={`${INPUT_CLS} !w-auto min-w-0 flex-1 !py-1.5 !text-[12px]`} placeholder="附加指令（可选）" value={instr} onChange={(e) => setInstr(e.target.value)} disabled={blocked} />
          <SecondaryButton size="sm" leadingIcon={<Scissors className="h-3.5 w-3.5" />} disabled={blocked} onClick={onManualCut}>手工切分</SecondaryButton>
        </div>
      )}
      {p.s.candidate && !p.s.planning && <div className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>有一份候选规划待定，定论前不能发起新的规划。</div>}

      {atEnd && !p.s.planning && (
        <div className="rounded-md px-3 py-2 text-[12px]" style={{ background: "oklch(0.3 0.06 150 / 0.15)", color: "var(--color-text-2)" }}>
          已规划到源文结尾：全书 {M.TOTAL_CHARS.toLocaleString()} 字，切出 {cuts.length} 集，平均每集 {Math.round(M.TOTAL_CHARS / Math.max(cuts.length, 1)).toLocaleString()} 字。
          {staleEps.length > 0 && <span>「原文已重新规划」的集：{staleEps.map((e) => `第 ${e.id} 集`).join("、")}。</span>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 集行

function EpRow({ p, ep, ask, onReplan, onAdjust, onSplit }: {
  p: Proto; ep: M.Ep; ask: (r: ConfirmReq) => void; onReplan: () => void; onAdjust: () => void; onSplit: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const isCut = ep.origin === "cut";
  const canRe = isCut && M.canRestructure(p.s, ep.id);
  const hasNext = isCut && !!M.nextCut(p.s, ep.id);
  const reason = "该集或其后的集已有产物，请改用从这一集起重新规划";

  const removeAfter = () => {
    const aff = M.affectedByRemoveAfter(p.s, ep.id);
    ask({
      title: `移除第 ${ep.id} 集之后的所有切分`,
      body: aff.length ? <AffectedList eps={aff} note="没有产物的集直接移除；有产物的集留在账本里，转为无原文的集并标记「原文已重新规划」。规划游标回到本集结尾。" /> : "本集之后没有切出的集。",
      confirmLabel: "移除切分",
      danger: aff.some((e) => e.hasArtifacts),
      onOk: () => p.removeCutsAfter(ep.id),
    });
  };

  return (
    <div
      className="relative flex gap-4 px-5 py-3.5"
      style={{
        borderBottom: "1px solid var(--color-hairline-soft)",
        opacity: isCut ? 1 : 0.72,
        ...(ep.fresh ? { ...FRESH_STYLE, background: "var(--color-accent-dim)" } : {}),
      }}
    >
      <div className="num w-10 shrink-0 pt-0.5 text-[18px] font-semibold" style={{ color: isCut ? "var(--color-text)" : "var(--color-text-3)" }}>
        {String(ep.id).padStart(2, "0")}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[13.5px] font-medium">{ep.title}</span>
          <EpPills ep={ep} />
          {!isCut && <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>不占用整本源文</span>}
        </div>
        {ep.hook && <div className="text-[12px]" style={{ color: "var(--color-text-2)" }}>钩子：{ep.hook}</div>}
        <div className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {ep.origin === "none" ? "无原文" : M.readLabel(M.epChars(ep))}
          {ep.origin === "own" && ep.ownFile && ` · ${ep.ownFile}`}
        </div>
        {isCut && (
          <div className="space-y-0.5 text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
            <div><span style={{ color: "var(--color-text-4)" }}>首句 </span>{M.firstSentence(ep)}</div>
            <div><span style={{ color: "var(--color-text-4)" }}>尾句 </span>{M.lastSentence(ep)}</div>
          </div>
        )}
      </div>
      {isCut && (
        <div className="relative shrink-0">
          <button type="button" className="rounded-md p-1 hover:bg-white/5" onClick={() => setMenu(!menu)} aria-label="更多">
            <MoreHorizontal className="h-4 w-4" style={{ color: "var(--color-text-3)" }} />
          </button>
          {menu && (
            <>
              <button type="button" aria-label="关闭菜单" className="fixed inset-0 z-20 cursor-default" onClick={() => setMenu(false)} />
              <div className="absolute right-0 z-30 mt-1 w-56 rounded-lg p-1 text-[12.5px]" style={{ ...DROPDOWN_PANEL_STYLE, border: "1px solid var(--color-hairline)" }}>
                <MenuItem disabled={!!p.s.candidate || !!p.s.planning} onClick={() => { setMenu(false); onReplan(); }}>从这一集起重新规划…</MenuItem>
                <MenuItem disabled={!!p.s.planning} onClick={() => { setMenu(false); onAdjust(); }}>调整范围…</MenuItem>
                <MenuItem disabled={!canRe} hint={canRe ? undefined : reason} onClick={() => { setMenu(false); onSplit(); }}>拆分…</MenuItem>
                <MenuItem
                  disabled={!canRe || !hasNext}
                  hint={!canRe ? reason : !hasNext ? "后面没有切出的集" : undefined}
                  onClick={() => { setMenu(false); p.mergeWithNext(ep.id); }}
                >
                  与下一集合并
                </MenuItem>
                <MenuItem onClick={() => { setMenu(false); removeAfter(); }}>移除这一集之后的所有切分…</MenuItem>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MenuItem({ children, onClick, disabled, hint }: { children: ReactNode; onClick: () => void; disabled?: boolean; hint?: string }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="block w-full rounded-md px-2.5 py-1.5 text-left enabled:hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-45"
    >
      {children}
      {hint && <div className="text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{hint}</div>}
    </button>
  );
}

// ---------------------------------------------------------------- 候选面板（清单 + 变更摘要）

function CandidatePanel({ p }: { p: Proto }) {
  const c = p.s.candidate!;
  const sum = M.candidateSummary(p.s);
  const oldById = new Map(p.s.episodes.map((e) => [e.id, e]));
  return (
    <div className="space-y-3 rounded-2xl p-4" style={{ ...CARD_STYLE, border: "1px solid var(--color-accent-soft)", boxShadow: "0 0 26px -12px var(--color-accent-glow)" }}>
      <div className="flex items-center gap-2">
        <span className="text-[13.5px] font-medium">候选规划 · 从第 {c.fromEp} 集起</span>
        {c.status === "generating" && (
          <span className="inline-flex items-center gap-1.5 text-[12px]" style={{ color: "var(--color-text-3)" }}>
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> 生成中 · 已到句 {c.reached} / {M.SOURCE_LEN}（{c.eps.length} 集）
          </span>
        )}
        {c.status === "stopped" && <span className="text-[12px]" style={{ color: "oklch(0.8 0.14 60)" }}>已中途停止</span>}
        <div className="flex-1" />
        <CandidateActions p={p} />
      </div>
      <p className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>分集账本尚未改动；采纳后一次性替换，放弃则什么都不变。</p>
      {c.status !== "generating" && <CandidateSummaryBlock p={p} />}
      {sum && sum.mapping.length > 0 && (
        <div className="max-h-[420px] overflow-y-auto rounded-lg" style={{ border: "1px solid var(--color-hairline-soft)" }}>
          {sum.mapping.map((m) => {
            const old = m.oldId ? oldById.get(m.oldId) : undefined;
            return (
              <div key={m.newId} className="flex gap-3 px-4 py-2.5" style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}>
                <div className="num w-24 shrink-0 text-[12px]">
                  <div className="font-semibold" style={{ color: "var(--color-text)" }}>第 {m.newId} 集</div>
                  <div style={{ color: "var(--color-text-4)" }}>{old ? `← 原「${old.title}」` : "新增集号"}</div>
                </div>
                <div className="min-w-0 flex-1 space-y-0.5 text-[12px]">
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium">{m.cand.title}</span>
                    {sum.staleIds.includes(m.newId) && <span className="text-[10.5px]" style={{ color: "oklch(0.82 0.1 200)" }}>产物将标记「原文已重新规划」</span>}
                  </div>
                  <div style={{ color: "var(--color-text-2)" }}>钩子：{m.cand.hook}</div>
                  <div className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.charsOf(m.cand.range))}</div>
                  <div style={{ color: "var(--color-text-3)" }}><span style={{ color: "var(--color-text-4)" }}>首句 </span>{M.firstSentence(m.cand)}</div>
                  <div style={{ color: "var(--color-text-3)" }}><span style={{ color: "var(--color-text-4)" }}>尾句 </span>{M.lastSentence(m.cand)}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 句子渲染（带句间隙）

function SentenceFlow({ from, to, renderGap, sentenceStyle }: {
  from: number; to: number;
  /** 句 pos 之前的间隙（pos ∈ [from, to]） */
  renderGap: (pos: number) => ReactNode;
  sentenceStyle?: (i: number) => React.CSSProperties | undefined;
}) {
  const parts: ReactNode[] = [];
  for (let i = from; i < to; i++) {
    if (i > from && M.SOURCE.paraStart[i]) parts.push(<span key={`br${i}`} className="block h-2" />);
    parts.push(<span key={`g${i}`}>{renderGap(i)}</span>);
    const isChapter = !!M.SOURCE.chapter[i];
    parts.push(
      <span key={`s${i}`} style={{ ...(isChapter ? { fontWeight: 600, color: "var(--color-text)" } : {}), ...sentenceStyle?.(i) }}>
        {M.SOURCE.sentences[i]}
      </span>,
    );
  }
  parts.push(<span key={`g${to}`}>{renderGap(to)}</span>);
  return <div className="text-[13px] leading-[1.9]" style={{ color: "var(--color-text-2)" }}>{parts}</div>;
}

function Gap({ active, onClick, onDragOver, onDrop, title }: {
  active?: boolean; onClick?: () => void; onDragOver?: (e: React.DragEvent) => void; onDrop?: () => void; title?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      onDragOver={onDragOver}
      onDrop={onDrop}
      className="mx-[1px] inline-block h-[1.1em] w-[7px] cursor-pointer rounded-sm align-middle transition-colors hover:bg-[var(--color-accent-soft)]"
      style={{ background: active ? "var(--color-accent)" : "oklch(0.35 0.02 265 / 0.35)" }}
    />
  );
}

function BoundaryMarker({ label, draggable, onDragStart, onDragEnd }: { label: string; draggable: boolean; onDragStart?: () => void; onDragEnd?: () => void }) {
  return (
    <span
      draggable={draggable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      title={draggable ? "拖到句间隙上移动边界" : "这一侧没有相邻的切出集，不能拖动"}
      className={`mx-1 inline-flex items-center rounded px-1.5 py-[1px] align-middle text-[10.5px] font-semibold ${draggable ? "cursor-grab" : "cursor-not-allowed opacity-60"}`}
      style={{ background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))", color: "oklch(0.14 0 0)" }}
    >
      ▍{label}
    </span>
  );
}

// ---------------------------------------------------------------- 划范围 modal（调整范围 / 拆分）

function RangeModal({ p, edit, ask, onClose }: {
  p: Proto; edit: { id: number; mode: "adjust" | "split" } | null; ask: (r: ConfirmReq) => void; onClose: () => void;
}) {
  const titleId = useId();
  const [dragging, setDragging] = useState<"start" | "end" | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const ep = edit ? p.s.episodes.find((e) => e.id === edit.id) : undefined;
  if (!edit || !ep?.range) return <GlassModal open={false} onClose={onClose} ariaLabel="划范围"><div /></GlassModal>;

  const prevAll = prevCut(p.s, ep.id);
  const nextAll = M.nextCut(p.s, ep.id);
  const prev = prevAll && prevAll.range![1] === ep.range[0] ? prevAll : undefined;
  const next = nextAll && nextAll.range![0] === ep.range[1] ? nextAll : undefined;
  const CONTEXT = 10;
  const lo = prev ? Math.max(prev.range![0] + 1, ep.range[0] - CONTEXT) : ep.range[0];
  const hi = next ? Math.min(next.range![1] - 1, ep.range[1] + CONTEXT) : ep.range[1];

  const drop = (pos: number) => {
    const kind = dragging;
    setDragging(null);
    setOver(null);
    if (kind === "start" && prev) {
      if (pos <= prev.range![0] || pos >= ep.range![1]) return;
      guardArtifacts(ask, [prev, ep], `移动第 ${prev.id} / ${ep.id} 集的边界`, () => p.moveBoundary(prev.id, pos));
    } else if (kind === "end" && next) {
      if (pos <= ep.range![0] || pos >= next.range![1]) return;
      guardArtifacts(ask, [ep, next], `移动第 ${ep.id} / ${next.id} 集的边界`, () => p.moveBoundary(ep.id, pos));
    }
  };

  const renderGap = (pos: number): ReactNode => {
    const markers: ReactNode[] = [];
    if (pos === ep.range![0]) {
      markers.push(
        <BoundaryMarker key="s" label={`第 ${ep.id} 集起`} draggable={edit.mode === "adjust" && !!prev}
          onDragStart={() => setDragging("start")} onDragEnd={() => { setDragging(null); setOver(null); }} />,
      );
    }
    if (pos === ep.range![1]) {
      markers.push(
        <BoundaryMarker key="e" label={next ? `第 ${next.id} 集起` : `第 ${ep.id} 集止`} draggable={edit.mode === "adjust" && !!next}
          onDragStart={() => setDragging("end")} onDragEnd={() => { setDragging(null); setOver(null); }} />,
      );
    }
    if (markers.length) return <>{markers}</>;
    if (edit.mode === "split") {
      if (pos <= ep.range![0] || pos >= ep.range![1]) return null;
      return <Gap title="在这里拆分" onClick={() => { p.splitEp(ep.id, pos); onClose(); }} />;
    }
    if (!dragging) return null;
    return (
      <Gap
        active={over === pos}
        onDragOver={(e) => { e.preventDefault(); setOver(pos); }}
        onDrop={() => drop(pos)}
      />
    );
  };

  return (
    <GlassModal open onClose={onClose} labelledBy={titleId} widthClassName="w-full max-w-3xl">
      <div className="space-y-3 p-5">
        <h3 id={titleId} className="display-serif text-[16px] font-semibold">
          {edit.mode === "adjust" ? `调整第 ${ep.id} 集的范围` : `拆分第 ${ep.id} 集`}
        </h3>
        <p className="text-[12px]" style={{ color: "var(--color-text-3)" }}>
          {edit.mode === "adjust"
            ? "拖动首尾边界标记，放到句间隙上。边界两侧是相邻的切出集，移动边界会同时改动两集；已有产物的集会标记「原文已重新规划」。"
            : "点击本集内任一句间隙，在那里拆成两集；其后切出的集集号顺延。"}
        </p>
        <div className="num flex gap-4 text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
          {prev && <span>上一集：第 {prev.id} 集 · {M.readLabel(M.epChars(prev))}</span>}
          <span style={{ color: "var(--color-accent-2)" }}>本集：{M.readLabel(M.epChars(ep))}</span>
          {next && <span>下一集：第 {next.id} 集 · {M.readLabel(M.epChars(next))}</span>}
        </div>
        <div className="max-h-[60vh] overflow-y-auto rounded-lg p-4" style={{ background: "oklch(0.15 0.01 265 / 0.6)", border: "1px solid var(--color-hairline-soft)" }}>
          <SentenceFlow
            from={lo}
            to={hi}
            renderGap={renderGap}
            sentenceStyle={(i) => (i < ep.range![0] || i >= ep.range![1] ? { opacity: 0.45 } : undefined)}
          />
        </div>
        <div className="flex justify-end">
          <SecondaryButton size="sm" onClick={onClose}>完成</SecondaryButton>
        </div>
      </div>
    </GlassModal>
  );
}

// ---------------------------------------------------------------- 手工切分 modal

function ManualCutModal({ p, open, onClose }: { p: Proto; open: boolean; onClose: () => void }) {
  const titleId = useId();
  const [end, setEnd] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const id = M.maxId(p.s) + 1;
  const cursor = p.s.cursor;
  const close = () => { setEnd(null); setTitle(""); onClose(); };

  return (
    <GlassModal open={open} onClose={close} labelledBy={titleId} widthClassName="w-full max-w-3xl">
      <div className="space-y-3 p-5">
        <h3 id={titleId} className="display-serif text-[16px] font-semibold">手工切分 · 从规划游标起切出第 {id} 集</h3>
        <p className="text-[12px]" style={{ color: "var(--color-text-3)" }}>点击某一句末尾的切分点落刀；新集从游标（句 {cursor}）到切分点，集号接在最大集号之后。</p>
        {cursor >= M.SOURCE_LEN ? (
          <p className="text-[12.5px]">游标已在源文结尾，没有可切的原文。</p>
        ) : (
          <div className="max-h-[52vh] overflow-y-auto rounded-lg p-4" style={{ background: "oklch(0.15 0.01 265 / 0.6)", border: "1px solid var(--color-hairline-soft)" }}>
            <SentenceFlow
              from={cursor}
              to={M.SOURCE_LEN}
              renderGap={(pos) => (pos > cursor ? <Gap active={end === pos} title="切在这里" onClick={() => setEnd(pos)} /> : null)}
              sentenceStyle={(i) => (end != null && i < end ? { background: "var(--color-accent-dim)", color: "var(--color-text)" } : undefined)}
            />
          </div>
        )}
        {end != null && (
          <div className="flex items-center gap-3 text-[12px]" style={{ color: "var(--color-text-2)" }}>
            <span className="num">第 {id} 集 = 句 {cursor}–{end} · {M.readLabel(M.charsOf([cursor, end]))}</span>
            <input className={`${INPUT_CLS} !w-auto flex-1 !py-1.5 !text-[12px]`} placeholder={`标题（默认「第 ${id} 集」）`} value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
        )}
        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={close}>取消</SecondaryButton>
          <PrimaryButton size="sm" disabled={end == null} onClick={() => { if (end != null) p.manualCut(end, title.trim()); close(); }}>切出这一集</PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}
