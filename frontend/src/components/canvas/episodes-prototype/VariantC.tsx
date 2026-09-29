// PROTOTYPE（#2767）变体 C「源文条带」：像剪辑时间线一样按字数比例看整本源文。
// 条带上拖手柄粗调边界，下方「边界微调」按句精调；候选以第二条条带同比例尺对照。
import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Plus, Scissors, Square, Upload } from "lucide-react";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import * as M from "./model";
import {
  AffectedList, CandidateActions, CandidateSummaryBlock, EpPills, FRESH_STYLE, OriginPill, Pill,
  ReplanDialog, UploadDialog, guardArtifacts, useConfirm, type ConfirmReq,
} from "./shared";
import type { Proto } from "./useProto";

// ---------------------------------------------------------------- 比例尺

const CUM: number[] = [0];
for (let i = 0; i < M.SOURCE_LEN; i++) CUM.push(CUM[i] + M.SOURCE.sentences[i].length);

function pct(pos: number): number {
  return (CUM[pos] / M.TOTAL_CHARS) * 100;
}

function posFromFrac(f: number): number {
  const target = Math.min(Math.max(f, 0), 1) * M.TOTAL_CHARS;
  let best = 0;
  for (let i = 0; i <= M.SOURCE_LEN; i++) {
    if (Math.abs(CUM[i] - target) < Math.abs(CUM[best] - target)) best = i;
  }
  return best;
}

function chars(a: number, b: number): string {
  return M.charsOf([a, b]).toLocaleString();
}

function segBg(id: number): string {
  return `oklch(0.32 0.05 ${(265 + id * 37) % 360} / 0.7)`;
}

const HATCH =
  "repeating-linear-gradient(135deg, oklch(0.26 0.01 265 / 0.6) 0 6px, oklch(0.2 0.01 265 / 0.6) 6px 12px)";

type Focus =
  | { kind: "boundary"; leftId: number }
  | { kind: "cut"; pos: number }
  | { kind: "split"; id: number; pos: number }
  | null;

interface Drag { leftId: number; pos: number }

// ---------------------------------------------------------------- 主体

export function VariantC({ p }: { p: Proto }) {
  const [instr, setInstr] = useState(p.s.lastInstructions);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [replanFrom, setReplanFrom] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(M.cutEps(p.s)[0]?.id ?? null);
  const [selectedCand, setSelectedCand] = useState<number | null>(null);
  const [focus, setFocus] = useState<Focus>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [confirmNode, ask] = useConfirm();
  const stripRef = useRef<HTMLDivElement>(null);

  const s = p.s;
  const cuts = M.cutEps(s);
  const others = M.otherEps(s);
  const busy = !!s.planning || s.candidate?.status === "generating";
  const atEnd = s.cursor >= M.SOURCE_LEN;
  const selEp = s.episodes.find((e) => e.id === selected) ?? null;

  const rangeOf = (ep: M.Ep): [number, number] => {
    const r = ep.range!;
    if (!drag) return r;
    const right = M.nextCut(s, drag.leftId);
    if (ep.id === drag.leftId) return [r[0], drag.pos];
    if (right && ep.id === right.id) return [drag.pos, r[1]];
    return r;
  };

  const fracAt = (clientX: number): number => {
    const rect = stripRef.current!.getBoundingClientRect();
    return (clientX - rect.left) / rect.width;
  };

  const dragLimits = (leftId: number): [number, number] => {
    const left = s.episodes.find((e) => e.id === leftId)!;
    const right = M.nextCut(s, leftId)!;
    return [left.range![0] + 1, right.range![1] - 1];
  };

  const onHandleDown = (e: ReactPointerEvent<HTMLDivElement>, leftId: number) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const left = s.episodes.find((x) => x.id === leftId)!;
    setDrag({ leftId, pos: left.range![1] });
    setFocus({ kind: "boundary", leftId });
  };
  const onHandleMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const [lo, hi] = dragLimits(drag.leftId);
    const pos = Math.min(Math.max(posFromFrac(fracAt(e.clientX)), lo), hi);
    if (pos !== drag.pos) setDrag({ ...drag, pos });
  };
  const onHandleUp = () => {
    if (!drag) return;
    const d = drag;
    setDrag(null);
    const left = s.episodes.find((x) => x.id === d.leftId)!;
    if (d.pos === left.range![1]) return;
    commitBoundary(d.leftId, d.pos);
  };

  const commitBoundary = (leftId: number, pos: number) => {
    const left = s.episodes.find((x) => x.id === leftId)!;
    const right = M.nextCut(s, leftId)!;
    guardArtifacts(ask, [left, right], `调整第 ${left.id} / ${right.id} 集的边界`, () => p.moveBoundary(leftId, pos));
  };

  const onUnplannedClick = (e: React.MouseEvent) => {
    if (busy || s.candidate) return;
    let pos = posFromFrac(fracAt(e.clientX));
    if (pos <= s.cursor) pos = Math.min(s.cursor + 1, M.SOURCE_LEN);
    setFocus({ kind: "cut", pos });
    setSelected(null);
  };

  const dragLeft = drag ? s.episodes.find((x) => x.id === drag.leftId) : undefined;
  const dragRight = drag ? M.nextCut(s, drag.leftId) : undefined;

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* ---- 标题栏 + 规划控制 ---- */}
      <div
        className="sticky top-0 z-20 space-y-2 px-5 py-3"
        style={{
          background: "linear-gradient(180deg, oklch(0.20 0.012 265 / 0.9), oklch(0.18 0.010 265 / 0.75))",
          backdropFilter: "blur(10px)",
          borderBottom: "1px solid var(--color-hairline-soft)",
        }}
      >
        <div className="flex items-center gap-3">
          <h2 className="display-serif text-[15px] font-semibold tracking-tight">分集</h2>
          <span className="num text-[11px]" style={{ color: "var(--color-text-3)" }}>
            {M.SOURCE_FILE_NAME} · 已规划 {chars(0, s.cursor)} / {M.TOTAL_CHARS.toLocaleString()} 字 · {cuts.length} 集切自整本源文 · {others.length} 集不占用
          </span>
          <div className="flex-1" />
          <SecondaryButton size="sm" leadingIcon={<Upload className="h-3.5 w-3.5" />} onClick={() => setUploadOpen(true)}>上传原文</SecondaryButton>
          <SecondaryButton size="sm" leadingIcon={<Plus className="h-3.5 w-3.5" />} onClick={p.addBlankEpisode}>新建一集</SecondaryButton>
        </div>
        <div className="flex items-center gap-2">
          <input
            className={`${INPUT_CLS} max-w-[420px] py-1.5 text-[12px]`}
            placeholder="附加指令（可选，不写进项目）"
            value={instr}
            onChange={(e) => setInstr(e.target.value)}
            disabled={busy}
          />
          {s.planning ? (
            <>
              <span className="text-[12px]" style={{ color: "var(--color-accent-2)" }}>
                正在{s.planning.mode === "toEnd" ? "规划到源文结尾" : "规划一批"} · 已提交 {s.planning.batchesDone} 批，每批提交后即可见
              </span>
              <SecondaryButton size="sm" leadingIcon={<Square className="h-3 w-3" />} onClick={p.stopPlanning}>停止</SecondaryButton>
            </>
          ) : (
            <>
              <PrimaryButton size="sm" disabled={busy || atEnd || s.sourceReplaced || !!s.candidate} onClick={() => p.beginPlanning("toEnd", instr)}>
                规划到源文结尾
              </PrimaryButton>
              <SecondaryButton size="sm" disabled={busy || atEnd || s.sourceReplaced || !!s.candidate} onClick={() => p.beginPlanning("batch", instr)}>
                先规划一批
              </SecondaryButton>
              {atEnd && <span className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>已规划到源文结尾：全书 {M.TOTAL_CHARS.toLocaleString()} 字，切出 {cuts.length} 集</span>}
            </>
          )}
        </div>
        {s.sourceReplaced && (
          <div className="rounded-md px-3 py-1.5 text-[12px]" style={{ border: "1px dashed oklch(0.7 0.1 200 / 0.6)", color: "oklch(0.85 0.08 200)" }}>
            整本源文已替换：接续规划与从中间起的重新规划不可用。
            {cuts[0] && (
              <button type="button" className="ml-2 underline" onClick={() => setReplanFrom(cuts[0].id)}>从第 {cuts[0].id} 集起重新规划</button>
            )}
          </div>
        )}
      </div>

      <div className="space-y-5 px-6 py-5">
        {/* ---- 源文条带 ---- */}
        <section>
          <div className="mb-1 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>
            <span className="num uppercase tracking-[0.18em]">Source strip</span>
            <span>拖动集之间的手柄粗调边界 · 点斜纹区落刀切出新集</span>
          </div>
          {/* 章节刻度 */}
          <div className="relative h-5">
            {M.SOURCE.chapter.map((c, i) =>
              c ? (
                <div key={i} className="absolute top-0 flex h-full flex-col items-start" style={{ left: `${pct(i)}%` }}>
                  <span className="whitespace-nowrap text-[9.5px]" style={{ color: "var(--color-text-4)" }}>{c.split(" ")[0]}</span>
                  <span className="h-1.5 w-px" style={{ background: "var(--color-text-4)" }} />
                </div>
              ) : null,
            )}
          </div>
          <div ref={stripRef} className="relative h-16 select-none overflow-visible rounded-md" style={{ border: "1px solid var(--color-hairline)" }}>
            {/* 未规划区 */}
            <button
              type="button"
              className="absolute inset-y-0 cursor-crosshair"
              style={{ left: `${pct(s.cursor)}%`, right: 0, background: HATCH }}
              onClick={onUnplannedClick}
              title="点击在此处落刀，切出新的一集"
            >
              {s.cursor < M.SOURCE_LEN && pct(M.SOURCE_LEN) - pct(s.cursor) > 8 && (
                <span className="absolute left-2 top-1.5 text-[10.5px]" style={{ color: "var(--color-text-3)" }}>未规划 · {chars(s.cursor, M.SOURCE_LEN)} 字</span>
              )}
            </button>
            {/* 规划游标 */}
            <div className="pointer-events-none absolute -top-1 -bottom-1 w-0.5" style={{ left: `${pct(s.cursor)}%`, background: "var(--color-accent)", boxShadow: "0 0 8px var(--color-accent-glow)" }} />
            {/* 集段 */}
            {cuts.map((ep) => {
              const r = rangeOf(ep);
              const w = pct(r[1]) - pct(r[0]);
              const isSel = selected === ep.id;
              return (
                <button
                  key={ep.id}
                  type="button"
                  onClick={() => { setSelected(ep.id); setSelectedCand(null); setFocus(null); }}
                  className="absolute inset-y-0 overflow-hidden px-1.5 py-1 text-left"
                  style={{
                    left: `${pct(r[0])}%`,
                    width: `${w}%`,
                    background: segBg(ep.id),
                    border: ep.stale ? "1.5px dashed oklch(0.75 0.1 200)" : "1px solid oklch(1 0 0 / 0.08)",
                    outline: isSel ? "2px solid var(--color-accent)" : undefined,
                    outlineOffset: -2,
                    ...(ep.fresh ? FRESH_STYLE : {}),
                  }}
                  title={`第 ${ep.id} 集 ${ep.title}`}
                >
                  <div className="num text-[11px] font-semibold">{ep.id}</div>
                  {w > 5 && <div className="truncate text-[10.5px]" style={{ color: "var(--color-text-2)" }}>{ep.title}</div>}
                  {ep.hasArtifacts && <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full" style={{ background: "oklch(0.8 0.12 150)" }} />}
                </button>
              );
            })}
            {/* 边界手柄 */}
            {cuts.map((ep) => {
              const right = M.nextCut(s, ep.id);
              if (!right || right.range![0] !== ep.range![1]) return null;
              const at = drag?.leftId === ep.id ? drag.pos : ep.range![1];
              const active = (focus?.kind === "boundary" && focus.leftId === ep.id) || drag?.leftId === ep.id;
              return (
                <div
                  key={`h-${ep.id}`}
                  role="slider"
                  aria-label={`第 ${ep.id} / ${right.id} 集边界`}
                  aria-valuenow={at}
                  tabIndex={0}
                  className="absolute -top-1 -bottom-1 z-10 flex w-3 -translate-x-1/2 cursor-col-resize justify-center"
                  style={{ left: `${pct(at)}%`, pointerEvents: busy || s.candidate ? "none" : undefined }}
                  onPointerDown={(e) => onHandleDown(e, ep.id)}
                  onPointerMove={onHandleMove}
                  onPointerUp={onHandleUp}
                >
                  <span className="h-full w-[3px] rounded" style={{ background: active ? "var(--color-accent-2)" : "oklch(0.85 0 0 / 0.55)" }} />
                </div>
              );
            })}
            {/* 落刀 / 拆分预览线 */}
            {(focus?.kind === "cut" || focus?.kind === "split") && (
              <div className="pointer-events-none absolute -top-2 -bottom-2 w-0 z-10" style={{ left: `${pct(focus.pos)}%`, borderLeft: "2px dashed oklch(0.72 0.19 25)" }} />
            )}
            {/* 拖动提示 */}
            {drag && dragLeft && dragRight && (
              <div
                className="pointer-events-none absolute -top-9 z-30 -translate-x-1/2 whitespace-nowrap rounded px-2 py-1 text-[11px]"
                style={{ left: `${pct(drag.pos)}%`, background: "oklch(0.12 0 0 / 0.95)", color: "var(--color-text)" }}
              >
                句 {drag.pos} · 第 {dragLeft.id} 集 {chars(dragLeft.range![0], drag.pos)} 字 / 第 {dragRight.id} 集 {chars(drag.pos, dragRight.range![1])} 字
              </div>
            )}
          </div>

          {s.candidate && (
            <CandidateStrip p={p} selectedCand={selectedCand} onSelect={(i) => { setSelectedCand(i); setSelected(null); setFocus(null); }} />
          )}
        </section>

        {/* ---- 下部三栏 ---- */}
        <section className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1.2fr)_minmax(0,0.8fr)] gap-4">
          <div>
            {selectedCand != null && s.candidate ? (
              <CandidateDetail p={p} index={selectedCand} />
            ) : selEp ? (
              <DetailCard
                p={p}
                ep={selEp}
                ask={ask}
                onReplan={setReplanFrom}
                onSplit={(id) => {
                  const e = s.episodes.find((x) => x.id === id)!;
                  setFocus({ kind: "split", id, pos: Math.floor((e.range![0] + e.range![1]) / 2) });
                }}
                onEditBoundary={(leftId) => setFocus({ kind: "boundary", leftId })}
              />
            ) : (
              <Panel kicker="Episode">
                <p className="text-[12px]" style={{ color: "var(--color-text-4)" }}>点条带上的一段查看这一集。</p>
              </Panel>
            )}
          </div>
          <FineTune
            p={p}
            focus={focus}
            onFocus={setFocus}
            commitBoundary={commitBoundary}
            onDone={(sel) => { setFocus(null); if (sel != null) setSelected(sel); }}
          />
          <Tray p={p} selected={selected} onSelect={(id) => { setSelected(id); setSelectedCand(null); setFocus(null); }} />
        </section>
      </div>

      <UploadDialog p={p} open={uploadOpen} onClose={() => setUploadOpen(false)} />
      <ReplanDialog p={p} fromId={replanFrom} onClose={() => setReplanFrom(null)} />
      {confirmNode}
    </div>
  );
}

// ---------------------------------------------------------------- 候选条带

function CandidateStrip({ p, selectedCand, onSelect }: { p: Proto; selectedCand: number | null; onSelect: (i: number) => void }) {
  const c = p.s.candidate!;
  const sum = M.candidateSummary(p.s)!;
  const start = M.replanStart(p.s, c.fromEp);
  const oldById = new Map(sum.replaced.map((e) => [e.id, e]));
  const oldBounds = new Set(sum.replaced.map((e) => e.range![0]));
  return (
    <div className="mt-1">
      {/* 连线：旧集号起点 → 新集号起点；位置不变为灰竖线，变动为红斜线 */}
      <svg className="block h-7 w-full" viewBox="0 0 1000 28" preserveAspectRatio="none">
        {sum.mapping.map((m) => {
          const old = m.oldId != null ? oldById.get(m.oldId) : undefined;
          if (!old) return null;
          const x1 = pct(old.range![0]) * 10;
          const x2 = pct(m.cand.range[0]) * 10;
          const same = old.range![0] === m.cand.range[0];
          return (
            <line
              key={m.newId}
              x1={x1} y1={0} x2={x2} y2={28}
              stroke={same ? "oklch(0.6 0 0 / 0.5)" : "oklch(0.72 0.19 25)"}
              strokeWidth={same ? 1 : 1.5}
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>
      <div className="mb-1 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>
        <span className="num uppercase tracking-[0.18em]">Candidate</span>
        <span>
          从第 {c.fromEp} 集起的候选 ·{" "}
          {c.status === "generating" ? "生成中…" : c.status === "stopped" ? "已中途停止" : "已生成"} · 红线 = 集号起点移动 · 红色刻度 = 新出现的边界
        </span>
      </div>
      <div className="relative h-14 rounded-md" style={{ border: "1px dashed var(--color-accent-soft)" }}>
        {/* 替换范围外灰掉 */}
        <div className="absolute inset-y-0 left-0" style={{ width: `${pct(start)}%`, background: "oklch(0.18 0 0 / 0.75)" }}>
          {pct(start) > 10 && <span className="absolute left-2 top-1.5 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>不在替换范围</span>}
        </div>
        {c.reached < M.SOURCE_LEN && (
          <div className="absolute inset-y-0" style={{ left: `${pct(c.reached)}%`, right: 0, background: HATCH, opacity: c.status === "generating" ? 1 : 0.4 }}>
            {c.status === "generating" && <span className="absolute left-2 top-1.5 animate-pulse text-[10.5px]" style={{ color: "var(--color-accent-2)" }}>AI 读取中…</span>}
          </div>
        )}
        {sum.mapping.map((m, i) => {
          const r = m.cand.range;
          const w = pct(r[1]) - pct(r[0]);
          const isNewBoundary = !oldBounds.has(r[0]);
          return (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(i)}
              className="absolute inset-y-0 overflow-hidden px-1.5 py-1 text-left"
              style={{
                left: `${pct(r[0])}%`,
                width: `${w}%`,
                background: segBg(m.newId),
                border: "1px solid oklch(1 0 0 / 0.08)",
                borderLeft: isNewBoundary ? "2px solid oklch(0.72 0.19 25)" : undefined,
                outline: selectedCand === i ? "2px solid var(--color-accent)" : undefined,
                outlineOffset: -2,
              }}
            >
              <div className="num text-[11px] font-semibold">
                {m.newId}
                {m.oldId == null && <span style={{ color: "oklch(0.8 0.14 60)" }}>+</span>}
              </div>
              {w > 5 && <div className="truncate text-[10.5px]" style={{ color: "var(--color-text-2)" }}>{m.cand.title}</div>}
            </button>
          );
        })}
      </div>
      <div className="mt-3 flex items-start gap-6 rounded-lg px-4 py-3" style={{ background: "oklch(0.2 0.01 265 / 0.55)", border: "1px solid var(--color-hairline-soft)" }}>
        <div className="flex-1"><CandidateSummaryBlock p={p} /></div>
        <CandidateActions p={p} />
      </div>
    </div>
  );
}

function CandidateDetail({ p, index }: { p: Proto; index: number }) {
  const sum = M.candidateSummary(p.s);
  const m = sum?.mapping[index];
  if (!m) return null;
  const old = m.oldId != null ? sum.replaced.find((e) => e.id === m.oldId) : undefined;
  return (
    <Panel kicker="Candidate episode">
      <div className="flex items-baseline gap-2">
        <span className="num text-[13px] font-semibold">第 {m.newId} 集</span>
        <span className="text-[11px]" style={{ color: "var(--color-text-3)" }}>
          {m.oldId != null ? `← 旧第 ${m.oldId} 集「${old?.title ?? ""}」` : "← 新增，接在最大集号之后"}
        </span>
      </div>
      <div className="mt-1 text-[14px] font-semibold">{m.cand.title}</div>
      <div className="mt-1 text-[12px]" style={{ color: "var(--color-text-2)" }}>{m.cand.hook}</div>
      <div className="num mt-2 text-[11px]" style={{ color: "var(--color-text-3)" }}>
        {M.readLabel(M.charsOf(m.cand.range))}
        {old && ` · 旧 ${M.charsOf(old.range!).toLocaleString()} 字`}
      </div>
      <HeadTail head={M.firstSentence(m.cand)} tail={M.lastSentence(m.cand)} />
      {old?.hasArtifacts && sum.staleIds.includes(old.id) && (
        <div className="mt-2"><Pill tone="replan">采纳后标记「原文已重新规划」</Pill></div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------- 详情卡

function DetailCard({
  p, ep, ask, onReplan, onSplit, onEditBoundary,
}: {
  p: Proto;
  ep: M.Ep;
  ask: (r: ConfirmReq) => void;
  onReplan: (id: number) => void;
  onSplit: (id: number) => void;
  onEditBoundary: (leftId: number) => void;
}) {
  const s = p.s;
  const busy = !!s.planning || !!s.candidate;
  const isCut = ep.origin === "cut";
  const next = isCut ? M.nextCut(s, ep.id) : undefined;
  const canRe = isCut && M.canRestructure(s, ep.id);
  const after = isCut ? M.affectedByRemoveAfter(s, ep.id) : [];
  return (
    <Panel kicker="Episode">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="num text-[13px] font-semibold">第 {ep.id} 集</span>
        <EpPills ep={ep} />
      </div>
      <div className="mt-1 text-[14px] font-semibold">{ep.title}</div>
      <div className="mt-1 text-[12px]" style={{ color: ep.hook ? "var(--color-text-2)" : "var(--color-text-4)" }}>{ep.hook || "（无钩子）"}</div>
      <div className="num mt-2 text-[11px]" style={{ color: "var(--color-text-3)" }}>
        {M.readLabel(M.epChars(ep))}
        {ep.range && ` · 句 ${ep.range[0]}–${ep.range[1]}`}
      </div>
      {isCut && <HeadTail head={M.firstSentence(ep)} tail={M.lastSentence(ep)} />}
      {isCut && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          <SecondaryButton size="sm" disabled={busy} onClick={() => onReplan(ep.id)}>从这一集起重新规划</SecondaryButton>
          {next && next.range![0] === ep.range![1] && (
            <SecondaryButton size="sm" disabled={busy} onClick={() => onEditBoundary(ep.id)}>微调与下一集的边界</SecondaryButton>
          )}
          <SecondaryButton size="sm" disabled={busy || !canRe} leadingIcon={<Scissors className="h-3 w-3" />} onClick={() => onSplit(ep.id)}>拆分</SecondaryButton>
          <SecondaryButton size="sm" disabled={busy || !canRe || !next} onClick={() => p.mergeWithNext(ep.id)}>与下一集合并</SecondaryButton>
          <SecondaryButton
            size="sm"
            disabled={busy || after.length === 0}
            onClick={() =>
              ask({
                title: `移除第 ${ep.id} 集之后的所有切分`,
                body: <AffectedList eps={after} note={`有产物的集留在账本里转为无原文的集并标记「原文已重新规划」；没有产物的集直接移除。规划游标回到第 ${ep.id} 集结尾。`} />,
                confirmLabel: "移除切分",
                danger: true,
                onOk: () => p.removeCutsAfter(ep.id),
              })
            }
          >
            移除这一集之后的所有切分
          </SecondaryButton>
        </div>
      )}
      {isCut && !canRe && (
        <p className="mt-2 text-[11px]" style={{ color: "oklch(0.8 0.14 60)" }}>这一集或其后已有产物，不能拆分或合并；请改用「从这一集起重新规划」。</p>
      )}
      {!isCut && (
        <p className="mt-3 text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
          {ep.origin === "own" ? `原文来自上传文件 ${ep.ownFile ?? ""}，不占用整本源文。` : "这一集没有原文，不占用整本源文；可在集页补填原文。"}
        </p>
      )}
    </Panel>
  );
}

function HeadTail({ head, tail }: { head: string; tail: string }) {
  return (
    <div className="mt-2 space-y-1 rounded-md px-2.5 py-2 text-[11.5px] leading-[1.6]" style={{ background: "oklch(0.16 0.01 265 / 0.6)", color: "var(--color-text-2)" }}>
      <div><span className="mr-1.5 text-[10px]" style={{ color: "var(--color-text-4)" }}>首句</span>{head}</div>
      <div><span className="mr-1.5 text-[10px]" style={{ color: "var(--color-text-4)" }}>尾句</span>{tail}</div>
    </div>
  );
}

// ---------------------------------------------------------------- 边界微调

function FineTune({
  p, focus, onFocus, commitBoundary, onDone,
}: {
  p: Proto;
  focus: Focus;
  onFocus: (f: Focus) => void;
  commitBoundary: (leftId: number, pos: number) => void;
  onDone: (selectId: number | null) => void;
}) {
  const s = p.s;
  if (!focus) {
    return (
      <Panel kicker="Fine tune">
        <p className="text-[12px] leading-[1.7]" style={{ color: "var(--color-text-4)" }}>
          选中条带上的边界手柄、点斜纹区落刀，或在详情卡里点「拆分」，这里会显示切点前后的原文，点句间的缝即可按句精调。
        </p>
      </Panel>
    );
  }

  let center: number;
  let lo: number;
  let hi: number;
  let title: string;
  let hint: string;
  if (focus.kind === "boundary") {
    const left = s.episodes.find((e) => e.id === focus.leftId);
    const right = M.nextCut(s, focus.leftId);
    if (!left?.range || !right?.range) return null;
    center = left.range[1];
    lo = left.range[0] + 1;
    hi = right.range[1] - 1;
    title = `第 ${left.id} / ${right.id} 集边界`;
    hint = `第 ${left.id} 集 ${M.charsOf([left.range[0], center]).toLocaleString()} 字 / 第 ${right.id} 集 ${M.charsOf([center, right.range[1]]).toLocaleString()} 字`;
  } else if (focus.kind === "cut") {
    center = focus.pos;
    lo = s.cursor + 1;
    hi = M.SOURCE_LEN;
    title = `手工落刀：切出第 ${M.maxId(s) + 1} 集`;
    hint = `句 ${s.cursor}–${center} · ${M.readLabel(M.charsOf([s.cursor, center]))}`;
  } else {
    const ep = s.episodes.find((e) => e.id === focus.id);
    if (!ep?.range) return null;
    center = focus.pos;
    lo = ep.range[0] + 1;
    hi = ep.range[1] - 1;
    title = `拆分第 ${ep.id} 集`;
    hint = `前段 ${M.charsOf([ep.range[0], center]).toLocaleString()} 字 / 后段 ${M.charsOf([center, ep.range[1]]).toLocaleString()} 字`;
  }

  const from = Math.max(0, center - 6);
  const to = Math.min(M.SOURCE_LEN, center + 6);
  const pick = (g: number) => {
    if (g < lo || g > hi || g === center) return;
    if (focus.kind === "boundary") commitBoundary(focus.leftId, g);
    else onFocus({ ...focus, pos: g });
  };

  const gap = (g: number) => {
    const allowed = g >= lo && g <= hi;
    const isCur = g === center;
    return (
      <button
        key={`g-${g}`}
        type="button"
        disabled={!allowed}
        onClick={() => pick(g)}
        className="mx-0.5 inline-block h-4 align-middle transition-colors"
        style={{
          width: isCur ? 4 : 8,
          borderRadius: 2,
          background: isCur ? "oklch(0.72 0.19 25)" : allowed ? "oklch(0.5 0.02 265 / 0.35)" : "transparent",
          cursor: allowed && !isCur ? "pointer" : "default",
        }}
        title={allowed ? `切在句 ${g} 之前` : undefined}
      />
    );
  };

  const nodes = [];
  for (let i = from; i < to; i++) {
    nodes.push(gap(i));
    const ch = M.SOURCE.chapter[i];
    const faded = focus.kind === "cut" && i < s.cursor;
    nodes.push(
      <span key={`s-${i}`} style={{ color: ch ? "var(--color-accent-2)" : faded ? "var(--color-text-4)" : i < center ? "var(--color-text)" : "var(--color-text-2)", fontWeight: ch ? 600 : undefined }}>
        {M.SOURCE.paraStart[i] && i !== from ? <br /> : null}
        {M.SOURCE.sentences[i]}
      </span>,
    );
  }
  nodes.push(gap(to));

  return (
    <Panel kicker="Fine tune">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-semibold">{title}</span>
        <span className="num text-[11px]" style={{ color: "var(--color-text-3)" }}>句 {center}</span>
        <div className="flex-1" />
        <button type="button" className="text-[11px] underline" style={{ color: "var(--color-text-4)" }} onClick={() => onDone(null)}>关闭</button>
      </div>
      <div className="num mt-1 text-[11px]" style={{ color: "var(--color-text-3)" }}>{hint}</div>
      <div className="mt-2 max-h-[280px] overflow-y-auto rounded-md px-3 py-2 text-[12.5px] leading-[1.9]" style={{ background: "oklch(0.16 0.01 265 / 0.6)" }}>
        {nodes}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>红色 = 当前切点，灰色缝可点</span>
        <div className="flex-1" />
        {focus.kind === "cut" && (
          <PrimaryButton size="sm" disabled={!!s.planning || !!s.candidate} onClick={() => { const id = M.maxId(s) + 1; p.manualCut(center); onDone(id); }}>
            在此切出第 {M.maxId(s) + 1} 集
          </PrimaryButton>
        )}
        {focus.kind === "split" && (
          <PrimaryButton size="sm" onClick={() => { p.splitEp(focus.id, center); onDone(focus.id); }}>在此拆分</PrimaryButton>
        )}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- 托盘

function Tray({ p, selected, onSelect }: { p: Proto; selected: number | null; onSelect: (id: number) => void }) {
  const others = M.otherEps(p.s);
  return (
    <Panel kicker="Not from source">
      <div className="mb-2 text-[12px] font-semibold">不占用整本源文</div>
      {others.length === 0 ? (
        <p className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>没有自带原文或无原文的集。</p>
      ) : (
        <div className="space-y-1.5">
          {others.map((ep) => (
            <button
              key={ep.id}
              type="button"
              onClick={() => onSelect(ep.id)}
              className="block w-full rounded-md px-2.5 py-2 text-left"
              style={{
                background: "oklch(0.2 0.01 265 / 0.55)",
                border: selected === ep.id ? "1px solid var(--color-accent)" : "1px solid var(--color-hairline-soft)",
                ...(ep.fresh ? FRESH_STYLE : {}),
              }}
            >
              <div className="flex items-center gap-1.5">
                <span className="num text-[12px] font-semibold">第 {ep.id} 集</span>
                <span className="truncate text-[12px]">{ep.title}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1">
                <OriginPill ep={ep} />
                <EpPills ep={ep} withOrigin={false} />
                <span className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
                  {ep.origin === "own" ? M.readLabel(M.epChars(ep)) : "—"}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Panel({ kicker, children }: { kicker: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl px-4 py-3" style={{ background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.16 0.010 265 / 0.55))", border: "1px solid var(--color-hairline-soft)" }}>
      <div className="num mb-2 text-[10px] uppercase tracking-[0.18em]" style={{ color: "var(--color-text-4)" }}>{kicker}</div>
      {children}
    </div>
  );
}
