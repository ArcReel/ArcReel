// PROTOTYPE（#2767）变体 B「原文双栏」：整本源文是主角，集边界是原文里可拖动的标记；
// 右栏是规划控制与集索引。候选按原文位置并排对比：原文两侧各一条范围栏（左现有、右候选）。
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Scissors, Upload, Plus, Square, GripHorizontal } from "lucide-react";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import * as M from "./model";
import type { Proto } from "./useProto";
import {
  AffectedList, CandidateActions, CandidateSummaryBlock, EpPills, FRESH_STYLE, OriginPill, Pill,
  ReplanDialog, UploadDialog, guardArtifacts, useConfirm,
} from "./shared";

const epHue = (id: number) => (id * 67) % 360;
const epColor = (id: number, a = 1) => `oklch(0.68 0.11 ${epHue(id)} / ${a})`;
const DIFF = "oklch(0.8 0.15 70)";

interface Chunk { start: number; end: number }

export function VariantB({ p }: { p: Proto }) {
  const s = p.s;
  const [selected, setSelected] = useState<number | null>(null);
  const [instr, setInstr] = useState(s.lastInstructions);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [replanFrom, setReplanFrom] = useState<number | null>(null);
  const [dragLeft, setDragLeft] = useState<number | null>(null);
  const [dropOver, setDropOver] = useState<number | null>(null);
  const [cutAt, setCutAt] = useState<number | null>(null);
  const [cutTitle, setCutTitle] = useState("");
  const [alt, setAlt] = useState(false);
  const [confirmNode, ask] = useConfirm();
  const headerRefs = useRef(new Map<number, HTMLElement>());

  useEffect(() => {
    const on = (e: KeyboardEvent) => setAlt(e.altKey);
    window.addEventListener("keydown", on);
    window.addEventListener("keyup", on);
    return () => { window.removeEventListener("keydown", on); window.removeEventListener("keyup", on); };
  }, []);

  const cuts = M.cutEps(s);
  const others = M.otherEps(s);
  const cand = s.candidate;
  const sum = M.candidateSummary(s);
  const replanStart = cand ? M.replanStart(s, cand.fromEp) : 0;
  const candRanges = useMemo(
    () => sum?.mapping.map((m) => ({ id: m.newId, oldId: m.oldId, range: m.cand.range })) ?? [],
    [sum],
  );
  const editing = !cand;
  const selEp = s.episodes.find((e) => e.id === selected);

  const chunks: Chunk[] = useMemo(() => {
    const pts = new Set<number>([0, s.cursor]);
    M.SOURCE.paraStart.forEach((ps, i) => { if (ps) pts.add(i); });
    cuts.forEach((e) => { pts.add(e.range![0]); pts.add(e.range![1]); });
    if (cand) {
      pts.add(replanStart);
      pts.add(cand.reached);
      candRanges.forEach((c) => { pts.add(c.range[0]); pts.add(c.range[1]); });
    }
    const sorted = [...pts].filter((x) => x < M.SOURCE_LEN).sort((a, b) => a - b);
    return sorted.map((st, i) => ({ start: st, end: sorted[i + 1] ?? M.SOURCE_LEN }));
  }, [s.cursor, cuts, cand, candRanges, replanStart]);

  const cutAtPos = (pos: number) => cuts.find((e) => e.range![0] <= pos && pos < e.range![1]);
  const candAtPos = (pos: number) => candRanges.find((c) => c.range[0] <= pos && pos < c.range[1]);
  const existingStarts = new Set(cuts.map((e) => e.range![0]));
  const candStarts = new Set(candRanges.map((c) => c.range[0]));

  const select = (id: number) => {
    setSelected(id);
    headerRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const dropBoundary = (pos: number) => {
    const left = cuts.find((e) => e.id === dragLeft);
    const right = left ? M.nextCut(s, left.id) : undefined;
    setDragLeft(null);
    setDropOver(null);
    if (!left || !right) return;
    guardArtifacts(ask, [left, right], `调整第 ${left.id} / ${right.id} 集的边界`, () => p.moveBoundary(left.id, pos));
  };

  const validDrop = (pos: number) => {
    const left = cuts.find((e) => e.id === dragLeft);
    const right = left ? M.nextCut(s, left.id) : undefined;
    return !!left && !!right && pos > left.range![0] && pos < right.range![1] && pos !== left.range![1];
  };

  // ---------------------------------------------------------------- 句间隙
  const gap = (pos: number): ReactNode => {
    if (!editing) return null;
    if (dragLeft != null) {
      const ok = validDrop(pos);
      if (!ok) return null;
      return (
        <span
          key={`g${pos}`}
          onDragOver={(e) => { e.preventDefault(); setDropOver(pos); }}
          onDragLeave={() => setDropOver((d) => (d === pos ? null : d))}
          onDrop={(e) => { e.preventDefault(); dropBoundary(pos); }}
          className="mx-[1px] inline-block h-[1.2em] w-[8px] rounded-sm align-middle"
          style={{ background: dropOver === pos ? "var(--color-accent)" : "var(--color-accent-soft)" }}
        />
      );
    }
    if (cutAt === pos) {
      return (
        <span key={`g${pos}`} className="mx-1 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 align-middle" style={{ background: "oklch(0.22 0.02 265)", border: "1px solid var(--color-accent-soft)" }}>
          <input
            ref={(el) => { if (el && document.activeElement !== el) el.focus(); }}
            className="w-28 rounded bg-transparent px-1 text-[11.5px] outline-none" style={{ color: "var(--color-text)" }}
            value={cutTitle} onChange={(e) => setCutTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { p.manualCut(pos, cutTitle); setCutAt(null); } if (e.key === "Escape") setCutAt(null); }}
          />
          <button type="button" className="text-[11px]" style={{ color: "var(--color-accent-2)" }} onClick={() => { p.manualCut(pos, cutTitle); setCutAt(null); }}>切出</button>
          <button type="button" className="text-[11px]" style={{ color: "var(--color-text-4)" }} onClick={() => setCutAt(null)}>取消</button>
        </span>
      );
    }
    if (pos > s.cursor && !s.planning && !s.sourceReplaced) {
      return (
        <span key={`g${pos}`} className="group/gap relative inline-block h-[1.2em] w-[6px] align-middle">
          <button
            type="button"
            onClick={() => { setCutAt(pos); setCutTitle(`第 ${M.maxId(s) + 1} 集`); }}
            className="absolute -top-5 left-1/2 z-10 hidden -translate-x-1/2 items-center gap-0.5 whitespace-nowrap rounded px-1.5 py-[1px] text-[10.5px] group-hover/gap:inline-flex"
            style={{ background: "var(--color-accent)", color: "oklch(0.14 0 0)" }}
          >
            <Scissors className="h-3 w-3" />在此切分
          </button>
          <span className="absolute inset-y-0 left-1/2 hidden w-px group-hover/gap:block" style={{ background: "var(--color-accent)" }} />
        </span>
      );
    }
    const ep = cutAtPos(pos);
    if (ep && pos > ep.range![0] && (alt || selected === ep.id)) {
      const allowed = M.canRestructure(s, ep.id);
      return (
        <span key={`g${pos}`} className="group/gap relative inline-block h-[1.2em] w-[6px] align-middle">
          <button
            type="button"
            disabled={!allowed}
            title={allowed ? "" : "该集或其后的集已有产物，不能拆分；请改用「从这一集起重新规划」"}
            onClick={() => p.splitEp(ep.id, pos)}
            className="absolute -top-5 left-1/2 z-10 hidden -translate-x-1/2 items-center gap-0.5 whitespace-nowrap rounded px-1.5 py-[1px] text-[10.5px] group-hover/gap:inline-flex disabled:opacity-60"
            style={{ background: allowed ? epColor(ep.id) : "oklch(0.4 0.01 265)", color: "oklch(0.14 0 0)" }}
          >
            <Scissors className="h-3 w-3" />{allowed ? "在此拆分" : "有产物，改用重新规划"}
          </button>
          <span className="absolute inset-y-0 left-1/2 hidden w-px group-hover/gap:block" style={{ background: epColor(ep.id) }} />
        </span>
      );
    }
    return null;
  };

  // ---------------------------------------------------------------- 左栏行
  const rows: ReactNode[] = [];
  chunks.forEach((ch) => {
    const pos = ch.start;
    if (pos === s.cursor && !cand) {
      rows.push(
        <div key={`cur${pos}`} className="my-3 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-accent-2)" }}>
          <span className="h-[2px] flex-1" style={{ background: "linear-gradient(90deg, var(--color-accent), transparent)", boxShadow: "0 0 8px var(--color-accent-glow)" }} />
          规划游标 · 以下尚未规划{s.planning ? "（AI 规划中…）" : "；悬停句间隙可手工切出新集"}
          <span className="h-[2px] flex-1" style={{ background: "linear-gradient(270deg, var(--color-accent), transparent)" }} />
        </div>,
      );
    }
    const startEp = cuts.find((e) => e.range![0] === pos);
    if (startEp) {
      const prev = cuts.find((e) => e.range![1] === pos);
      if (prev && editing) {
        rows.push(
          <div key={`b${pos}`} className="my-1 flex items-center gap-2">
            <span className="h-px flex-1" style={{ background: "var(--color-hairline-strong)" }} />
            <span
              draggable
              onDragStart={(e) => { e.dataTransfer.setData("text/plain", String(prev.id)); setDragLeft(prev.id); }}
              onDragEnd={() => { setDragLeft(null); setDropOver(null); }}
              className="inline-flex cursor-grab items-center gap-1 rounded-full px-2 py-[1px] text-[10.5px]"
              style={{ border: "1px solid var(--color-hairline-strong)", color: "var(--color-text-3)", background: "oklch(0.2 0.01 265)" }}
              title="拖到任意句间隙，调整相邻两集的边界"
            >
              <GripHorizontal className="h-3 w-3" />第 {prev.id} 集 ▸ 第 {startEp.id} 集
            </span>
            <span className="h-px flex-1" style={{ background: "var(--color-hairline-strong)" }} />
          </div>,
        );
      }
      rows.push(
        <div
          key={`h${startEp.id}`}
          ref={(el) => { if (el) headerRefs.current.set(startEp.id, el); else headerRefs.current.delete(startEp.id); }}
          role="button"
          tabIndex={0}
          onClick={() => setSelected(startEp.id)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setSelected(startEp.id); }}
          className="mb-1.5 mt-2 cursor-pointer scroll-mt-4 rounded-md px-3 py-1.5"
          style={{
            borderLeft: `3px solid ${epColor(startEp.id)}`,
            background: selected === startEp.id ? "var(--color-accent-dim)" : "oklch(0.21 0.01 265 / 0.6)",
            ...(startEp.fresh ? FRESH_STYLE : {}),
          }}
        >
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <span className="num font-semibold" style={{ color: epColor(startEp.id) }}>第 {startEp.id} 集</span>
            <span style={{ color: "var(--color-text)" }}>{startEp.title}</span>
            <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.epChars(startEp))}</span>
            <EpPills ep={startEp} withOrigin={false} />
          </div>
          {startEp.hook && <div className="mt-0.5 text-[11.5px]" style={{ color: "var(--color-text-3)" }}>钩子：{startEp.hook}</div>}
        </div>,
      );
    }

    const ep = cutAtPos(pos);
    const cr = candAtPos(pos);
    const unplanned = pos >= s.cursor;
    const outside = !!cand && pos < replanStart;
    const diff = !!cand && pos >= replanStart && pos < cand.reached && existingStarts.has(pos) !== candStarts.has(pos);
    const isSel = !!ep && ep.id === selected;

    rows.push(
      <div key={`c${pos}`} className="flex gap-2" style={{ marginTop: M.SOURCE.paraStart[pos] ? 8 : 0 }}>
        {/* 左 gutter：现有集 */}
        <div className="relative shrink-0" style={{ width: cand ? 40 : 4 }}>
          {ep && (
            <div className="absolute inset-y-0 right-0 w-[4px] rounded-sm" style={{ background: epColor(ep.id, outside ? 0.25 : 0.8) }} />
          )}
          {cand && ep && ep.range![0] === pos && (
            <span className="num absolute right-2 top-0 text-[10px]" style={{ color: epColor(ep.id, outside ? 0.4 : 1) }}>{ep.id}</span>
          )}
          {cand && diff && existingStarts.has(pos) && (
            <span className="absolute right-0 top-0 h-[2px] w-full" style={{ background: DIFF }} />
          )}
        </div>
        {/* 原文 */}
        <p
          className="flex-1 text-[13.5px] leading-[1.95]"
          style={{
            color: unplanned ? "var(--color-text-4)" : "var(--color-text-2)",
            opacity: outside ? 0.45 : 1,
            background: isSel ? "var(--color-accent-dim)" : undefined,
            borderTop: diff ? `1px dashed ${DIFF}` : undefined,
          }}
        >
          {Array.from({ length: ch.end - ch.start }, (_, k) => {
            const i = ch.start + k;
            const chap = M.SOURCE.chapter[i];
            return (
              <span key={i}>
                {i > 0 && gap(i)}
                {chap ? <b className="display-serif text-[15px]" style={{ color: unplanned ? "var(--color-text-3)" : "var(--color-text)" }}>{chap}</b> : M.SOURCE.sentences[i]}
              </span>
            );
          })}
          {ch.end === M.SOURCE_LEN && gap(M.SOURCE_LEN)}
        </p>
        {/* 右 gutter：候选集 */}
        {cand && (
          <div className="relative shrink-0" style={{ width: 40 }}>
            {cr ? (
              <>
                <div className="absolute inset-y-0 left-0 w-[4px] rounded-sm" style={{ background: epColor(cr.id, 0.8), outline: "1px dashed oklch(1 0 0 / 0.25)" }} />
                {cr.range[0] === pos && <span className="num absolute left-2 top-0 text-[10px]" style={{ color: epColor(cr.id) }}>{cr.id}</span>}
                {diff && candStarts.has(pos) && <span className="absolute left-0 top-0 h-[2px] w-full" style={{ background: DIFF }} />}
              </>
            ) : pos >= replanStart && cand.status === "generating" ? (
              <span className="absolute left-1 top-0 text-[10px]" style={{ color: "var(--color-text-4)" }}>…</span>
            ) : null}
          </div>
        )}
      </div>,
    );
  });

  // ---------------------------------------------------------------- 右栏
  const plannedChars = cuts.reduce((n, e) => n + M.epChars(e), 0);
  const canPlan = editing && !s.planning && s.cursor < M.SOURCE_LEN && !s.sourceReplaced;

  const removeAfter = (ep: M.Ep) => {
    const aff = M.affectedByRemoveAfter(s, ep.id);
    ask({
      title: `移除第 ${ep.id} 集之后的所有切分`,
      body: (
        <AffectedList
          eps={aff}
          note={`没有产物的集直接移除；已有产物的集留在账本里，转为无原文的集并标记「原文已重新规划」。游标回到第 ${ep.id} 集结尾。`}
        />
      ),
      confirmLabel: "移除切分",
      danger: true,
      onOk: () => p.removeCutsAfter(ep.id),
    });
  };

  const rail = cand && sum ? (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="display-serif text-[15px] font-semibold">候选规划</h3>
        <Pill tone="accent">从第 {cand.fromEp} 集起</Pill>
        <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {cand.status === "generating" ? "生成中…" : cand.status === "stopped" ? "已停止" : "已完成"}
        </span>
      </div>
      <div className="h-1 overflow-hidden rounded-full" style={{ background: "var(--color-hairline)" }}>
        <div className="h-full" style={{ width: `${((cand.reached - replanStart) / Math.max(1, M.SOURCE_LEN - replanStart)) * 100}%`, background: "var(--color-accent)" }} />
      </div>
      <p className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
        原文左侧是现有集，右侧是候选集；<span style={{ color: DIFF }}>琥珀色虚线</span>是边界不一致的位置。账本在采纳前不动。
      </p>
      <CandidateSummaryBlock p={p} />
      <div className="max-h-[36vh] space-y-1 overflow-auto">
        {sum.mapping.map((m) => (
          <div key={m.newId} className="rounded px-2 py-1 text-[12px]" style={{ borderLeft: `3px dashed ${epColor(m.newId)}`, background: "oklch(0.21 0.01 265 / 0.5)" }}>
            <span className="num" style={{ color: epColor(m.newId) }}>第 {m.newId} 集</span>
            {m.oldId === undefined && <span className="ml-1 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>（新集号）</span>}
            <span className="ml-2">{m.cand.title}</span>
            <div className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.charsOf(m.cand.range))}</div>
          </div>
        ))}
      </div>
      <CandidateActions p={p} />
    </div>
  ) : (
    <div className="space-y-4">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="display-serif text-[15px] font-semibold">分集</h3>
          <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>{s.episodes.length} 集</span>
          <div className="flex-1" />
          <SecondaryButton size="sm" leadingIcon={<Upload className="h-3.5 w-3.5" />} onClick={() => setUploadOpen(true)}>上传原文</SecondaryButton>
          <SecondaryButton size="sm" leadingIcon={<Plus className="h-3.5 w-3.5" />} onClick={p.addBlankEpisode}>新建一集</SecondaryButton>
        </div>
        <div className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
          {M.SOURCE_FILE_NAME} · 已规划 {plannedChars.toLocaleString()} / {M.TOTAL_CHARS.toLocaleString()} 字
        </div>
        <div className="h-1 overflow-hidden rounded-full" style={{ background: "var(--color-hairline)" }}>
          <div className="h-full" style={{ width: `${(s.cursor / M.SOURCE_LEN) * 100}%`, background: "var(--color-accent)" }} />
        </div>
        {s.sourceReplaced && (
          <div className="space-y-1.5 rounded-md px-2.5 py-2 text-[11.5px]" style={{ border: "1px dashed oklch(0.7 0.1 200 / 0.6)", color: "oklch(0.85 0.08 200)" }}>
            整本源文已替换，接续规划不可用，需要从第 1 集起重新规划。
            {cuts[0] && <div><SecondaryButton size="sm" onClick={() => setReplanFrom(cuts[0].id)}>从第 1 集起重新规划</SecondaryButton></div>}
          </div>
        )}
        {s.planning ? (
          <div className="flex items-center gap-2 rounded-md px-2.5 py-2 text-[12px]" style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)" }}>
            <span className="h-2 w-2 animate-pulse rounded-full" style={{ background: "var(--color-accent)" }} />
            {s.planning.mode === "toEnd" ? "正在规划到源文结尾" : "正在规划一批"} · 已提交 {s.planning.batchesDone} 批
            <div className="flex-1" />
            <SecondaryButton size="sm" leadingIcon={<Square className="h-3 w-3" />} onClick={p.stopPlanning}>停止</SecondaryButton>
          </div>
        ) : s.cursor >= M.SOURCE_LEN ? (
          <div className="text-[12px]" style={{ color: "oklch(0.8 0.12 150)" }}>已规划到源文结尾 · 共 {cuts.length} 集切自整本源文</div>
        ) : (
          <>
            <textarea className={INPUT_CLS} rows={2} placeholder="附加指令（可选）" value={instr} onChange={(e) => setInstr(e.target.value)} />
            <div className="flex gap-2">
              <PrimaryButton size="sm" disabled={!canPlan} onClick={() => p.beginPlanning("toEnd", instr)}>规划到源文结尾</PrimaryButton>
              <SecondaryButton size="sm" disabled={!canPlan} onClick={() => p.beginPlanning("batch", instr)}>先规划一批</SecondaryButton>
            </div>
          </>
        )}
      </div>

      {selEp && (
        <div className="space-y-2 rounded-md p-2.5" style={{ border: `1px solid ${epColor(selEp.id, 0.5)}`, background: "oklch(0.21 0.01 265 / 0.6)" }}>
          <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
            <span className="num font-semibold" style={{ color: epColor(selEp.id) }}>第 {selEp.id} 集</span>
            <span>{selEp.title}</span>
            <EpPills ep={selEp} />
          </div>
          {selEp.origin === "cut" ? (
            <>
              <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
                在原文里拖动集之间的标记调整边界；本集内悬停句间隙可拆分（按住 Alt 可在任意集内拆分）。
              </p>
              <div className="flex flex-wrap gap-1.5">
                <SecondaryButton size="sm" onClick={() => setReplanFrom(selEp.id)}>从这一集起重新规划</SecondaryButton>
                {(() => {
                  const nx = M.nextCut(s, selEp.id);
                  const ok = !!nx && M.canRestructure(s, selEp.id);
                  return (
                    <SecondaryButton
                      size="sm" disabled={!ok}
                      title={!nx ? "没有下一集" : ok ? "" : "该集或其后的集已有产物，请改用重新规划"}
                      onClick={() => p.mergeWithNext(selEp.id)}
                    >与下一集合并</SecondaryButton>
                  );
                })()}
                <SecondaryButton size="sm" disabled={M.affectedByRemoveAfter(s, selEp.id).length === 0} onClick={() => removeAfter(selEp)}>移除这一集之后的所有切分</SecondaryButton>
              </div>
            </>
          ) : (
            <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
              {selEp.origin === "own" ? `原文来自 ${selEp.ownFile}，不占用整本源文。` : "没有原文，不经脚本规划，只能手写脚本。"}
            </p>
          )}
        </div>
      )}

      <div className="space-y-1">
        <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>切自整本源文</div>
        {cuts.map((e) => (
          <button
            key={e.id} type="button" onClick={() => select(e.id)}
            className="block w-full rounded-md px-2 py-1.5 text-left transition-colors hover:bg-[oklch(0.26_0.012_265/0.5)]"
            style={{ borderLeft: `3px solid ${epColor(e.id)}`, background: selected === e.id ? "var(--color-accent-dim)" : undefined, ...(e.fresh ? FRESH_STYLE : {}) }}
          >
            <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
              <span className="num" style={{ color: epColor(e.id) }}>第 {e.id} 集</span>
              <span style={{ color: "var(--color-text)" }}>{e.title}</span>
              <EpPills ep={e} withOrigin={false} />
            </div>
            <div className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.epChars(e))}</div>
            <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>首：{M.firstSentence(e)}</div>
            <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>尾：{M.lastSentence(e)}</div>
          </button>
        ))}
        {cuts.length === 0 && <div className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>还没有切出的集</div>}
      </div>

      <div className="space-y-1">
        <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>不占用整本源文</div>
        {others.map((e) => (
          <button
            key={e.id} type="button" onClick={() => setSelected(e.id)}
            className="flex w-full flex-wrap items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-[oklch(0.26_0.012_265/0.5)]"
            style={{ background: selected === e.id ? "var(--color-accent-dim)" : undefined, ...(e.fresh ? FRESH_STYLE : {}) }}
          >
            <span className="num" style={{ color: "var(--color-text-2)" }}>第 {e.id} 集</span>
            <span>{e.title}</span>
            <OriginPill ep={e} />
            <EpPills ep={e} withOrigin={false} />
            {e.origin === "own" && <span className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.epChars(e))}</span>}
          </button>
        ))}
        {others.length === 0 && <div className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>无</div>}
      </div>
    </div>
  );

  return (
    <div className="flex h-full">
      <div className="flex-1 overflow-y-auto px-8 py-6 pb-24">
        <div className="mx-auto max-w-[760px]">
          <div className="mb-4 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>
            <span className="display-serif text-[14px]" style={{ color: "var(--color-text-2)" }}>{M.SOURCE_FILE_NAME}</span>
            {cand ? "· 候选对比：左栏现有 / 右栏候选" : "· 拖动集之间的标记调整边界 · 游标之后悬停句间隙可手工切分"}
          </div>
          {rows}
        </div>
      </div>
      <aside className="w-[340px] shrink-0 overflow-y-auto px-4 py-5 pb-24" style={{ borderLeft: "1px solid var(--color-hairline)", background: "oklch(0.18 0.01 265 / 0.5)" }}>
        {rail}
      </aside>
      <UploadDialog p={p} open={uploadOpen} onClose={() => setUploadOpen(false)} />
      <ReplanDialog p={p} fromId={replanFrom} onClose={() => setReplanFrom(null)} />
      {confirmNode}
    </div>
  );
}
