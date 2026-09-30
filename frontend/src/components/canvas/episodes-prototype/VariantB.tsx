// PROTOTYPE（#2767）变体 B「原文双栏」：整本源文是主角，集边界是原文里可拖动的标记；
// 右栏是规划控制与集索引。候选按原文位置并排对比：原文两侧各一条范围栏（左现有、右候选）。
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
  const [splitAt, setSplitAt] = useState<number | null>(null);
  const [confirmNode, ask] = useConfirm();
  const headerRefs = useRef(new Map<number, HTMLElement>());

  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === "Escape") { setCutAt(null); setSplitAt(null); } };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
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

  // ---------------------------------------------------------------- 句间隙（仅拖动边界时作为放置点）
  const gap = (pos: number): ReactNode => {
    if (!editing || dragLeft == null || !validDrop(pos)) return null;
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
  };

  const canCut = editing && !s.planning && !s.sourceReplaced && dragLeft == null;
  const nextId = M.maxId(s) + 1;

  const cutForm = (pos: number) => (
    <span className="mx-1 inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 align-middle text-[11.5px]" style={{ background: "oklch(0.22 0.02 265)", border: "1px solid var(--color-accent)" }}>
      <Scissors className="h-3 w-3" style={{ color: "var(--color-accent-2)" }} />
      <span style={{ color: "var(--color-text-3)" }}>新的一集 · {M.charsOf([s.cursor, pos]).toLocaleString()} 字</span>
      <input
        ref={(el) => { if (el && document.activeElement !== el) el.focus(); }}
        aria-label="标题"
        className="w-28 rounded bg-transparent px-1 outline-none" style={{ color: "var(--color-text)", borderBottom: "1px solid var(--color-hairline-strong)" }}
        value={cutTitle} onChange={(e) => setCutTitle(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { p.manualCut(pos, cutTitle); setCutAt(null); } }}
      />
      <button type="button" className="font-medium" style={{ color: "var(--color-accent-2)" }} onClick={() => { p.manualCut(pos, cutTitle); setCutAt(null); }}>添加为第 {nextId} 集</button>
      <button type="button" style={{ color: "var(--color-text-4)" }} onClick={() => setCutAt(null)}>取消</button>
    </span>
  );

  const splitForm = (ep: M.Ep, pos: number) => (
    <span className="mx-1 inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 align-middle text-[11.5px]" style={{ background: "oklch(0.22 0.02 265)", border: `1px solid ${epColor(ep.id)}` }}>
      <Scissors className="h-3 w-3" style={{ color: epColor(ep.id) }} />
      <span style={{ color: "var(--color-text-3)" }}>
        拆成 {M.charsOf([ep.range![0], pos]).toLocaleString()} 字 + {M.charsOf([pos, ep.range![1]]).toLocaleString()} 字，之后的集号依次后移
      </span>
      <button type="button" className="font-medium" style={{ color: epColor(ep.id) }} onClick={() => { p.splitEp(ep.id, pos); setSplitAt(null); }}>拆分</button>
      <button type="button" style={{ color: "var(--color-text-4)" }} onClick={() => setSplitAt(null)}>取消</button>
    </span>
  );

  // ---------------------------------------------------------------- 左栏行
  const rows: ReactNode[] = [];
  chunks.forEach((ch) => {
    const pos = ch.start;
    if (pos === s.cursor && !cand) {
      rows.push(
        <div key={`cur${pos}`} className="my-3 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-accent-2)" }}>
          <span className="h-[2px] flex-1" style={{ background: "linear-gradient(90deg, var(--color-accent), transparent)", boxShadow: "0 0 8px var(--color-accent-glow)" }} />
          以下内容尚未分集{s.planning ? " · AI 正在规划…" : " · 点击任意一句，可在这句之后切出新的一集"}
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
              title="拖动到任意两句之间，调整这两集的分界"
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
            const cuttable = canCut && unplanned;
            const splittable = canCut && !!ep && isSel && i + 1 < ep.range![1];
            const allowedSplit = splittable && M.canRestructure(s, ep.id);
            const inCutPreview = cutAt != null && i >= s.cursor && i < cutAt;
            const onPick = () => {
              if (cuttable) { setSplitAt(null); setCutAt(i + 1); setCutTitle(`第 ${nextId} 集`); }
              else if (allowedSplit) { setCutAt(null); setSplitAt(i + 1); }
            };
            const clickable = cuttable || allowedSplit;
            const text = chap ? <b className="display-serif text-[15px]" style={{ color: unplanned ? "var(--color-text-3)" : "var(--color-text)" }}>{chap}</b> : M.SOURCE.sentences[i];
            return (
              <Fragment key={i}>
                {i > 0 && gap(i)}
                {clickable ? (
                  <span
                    role="button" tabIndex={0}
                    onClick={onPick}
                    onKeyDown={(e) => { if (e.key === "Enter") onPick(); }}
                    title={cuttable ? "在这句之后切出新的一集" : "在这句之后拆分"}
                    className="cursor-pointer rounded-sm transition-colors hover:bg-[oklch(0.3_0.04_280/0.55)] hover:text-[var(--color-text)]"
                    style={{ background: inCutPreview ? "oklch(0.3 0.05 280 / 0.45)" : undefined, color: inCutPreview ? "var(--color-text)" : undefined }}
                  >{text}</span>
                ) : text}
                {cutAt === i + 1 && cutForm(i + 1)}
                {splitAt === i + 1 && ep && splitForm(ep, i + 1)}
              </Fragment>
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
      title: `清除第 ${ep.id} 集之后的分集`,
      body: (
        <AffectedList
          eps={aff}
          note={`尚未开始制作的集会被删除；已开始制作的集会保留已生成的内容，转为无原文的集。第 ${ep.id} 集之后的原文将回到「尚未分集」状态。`}
        />
      ),
      confirmLabel: "清除",
      danger: true,
      onOk: () => p.removeCutsAfter(ep.id),
    });
  };

  const shift = (d: number) => (d === 0 ? "" : d > 0 ? `后移 ${d} 句` : `前移 ${-d} 句`);
  const candBatchesTotal = cand ? M.batchesNeeded(replanStart) : 0;
  const candBatchesDone = cand ? Math.min(candBatchesTotal, Math.ceil((cand.reached - replanStart) / M.WINDOW)) : 0;

  const rail = cand && sum ? (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="display-serif text-[15px] font-semibold">新的分集方案</h3>
        <Pill tone="accent">从第 {cand.fromEp} 集起</Pill>
        <div className="flex-1" />
        <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {cand.status === "generating" ? `正在规划第 ${candBatchesDone + 1} / 约 ${candBatchesTotal} 批` : cand.status === "stopped" ? "已停止" : "规划完成"}
        </span>
      </div>
      {cand.status === "generating" && <BatchBar done={candBatchesDone} total={candBatchesTotal} />}
      <p className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
        左侧色条是现有分集，右侧是新方案；<span style={{ color: DIFF }}>琥珀色虚线</span>标出分界不同的位置。采纳之前，现有分集不会变化。
      </p>
      <div className="rounded-md p-2.5" style={{ background: "oklch(0.21 0.01 265 / 0.6)", border: "1px solid var(--color-hairline-soft)" }}>
        <CandidateSummaryBlock p={p} />
      </div>
      <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>逐集变化</div>
      <div className="max-h-[40vh] space-y-1 overflow-auto">
        {sum.mapping.map((m) => {
          const old = m.oldId !== undefined ? sum.replaced.find((e) => e.id === m.oldId) : undefined;
          const oc = old ? M.epChars(old) : 0;
          const nc = M.charsOf(m.cand.range);
          const moves = old
            ? [
                shift(m.cand.range[0] - old.range![0]) && `开头${shift(m.cand.range[0] - old.range![0])}`,
                shift(m.cand.range[1] - old.range![1]) && `结尾${shift(m.cand.range[1] - old.range![1])}`,
              ].filter(Boolean)
            : [];
          const same = !!old && moves.length === 0;
          const flagged = sum.staleIds.includes(m.newId);
          return (
            <div key={m.newId} className="rounded px-2 py-1.5 text-[12px]" style={{ borderLeft: `3px dashed ${epColor(m.newId)}`, background: "oklch(0.21 0.01 265 / 0.5)", opacity: same ? 0.6 : 1 }}>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="num" style={{ color: epColor(m.newId) }}>第 {m.newId} 集</span>
                {old && old.title !== m.cand.title && <span className="line-through" style={{ color: "var(--color-text-4)" }}>{old.title}</span>}
                <span>{m.cand.title}</span>
                {!old && <Pill tone="accent">新增</Pill>}
                {same && <Pill>无变化</Pill>}
                {flagged && <Pill tone="replan">需要复核</Pill>}
              </div>
              <div className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
                {old ? `${oc.toLocaleString()} → ${nc.toLocaleString()} 字` : `${nc.toLocaleString()} 字`}
                {moves.length > 0 && ` · ${moves.join("，")}`}
              </div>
              {!same && <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>钩子：{m.cand.hook}</div>}
            </div>
          );
        })}
        {[...sum.toNone, ...sum.removed].map((id) => {
          const old = sum.replaced.find((e) => e.id === id);
          return (
            <div key={`x${id}`} className="rounded px-2 py-1.5 text-[12px]" style={{ borderLeft: "3px dashed var(--color-hairline-strong)", background: "oklch(0.21 0.01 265 / 0.35)" }}>
              <span className="num" style={{ color: "var(--color-text-3)" }}>第 {id} 集</span>
              <span className="ml-1.5 line-through" style={{ color: "var(--color-text-4)" }}>{old?.title}</span>
              <span className="ml-1.5 text-[11px]" style={{ color: "var(--color-text-3)" }}>{sum.toNone.includes(id) ? "保留已生成内容，转为无原文的集" : "将移除"}</span>
            </div>
          );
        })}
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
          {M.SOURCE_FILE_NAME} · 已分集 {plannedChars.toLocaleString()} / {M.TOTAL_CHARS.toLocaleString()} 字
        </div>
        <div className="h-1 overflow-hidden rounded-full" style={{ background: "var(--color-hairline)" }}>
          <div className="h-full" style={{ width: `${(s.cursor / M.SOURCE_LEN) * 100}%`, background: "var(--color-accent)" }} />
        </div>
        {s.sourceReplaced && (
          <div className="space-y-1.5 rounded-md px-2.5 py-2 text-[11.5px]" style={{ border: "1px dashed oklch(0.7 0.1 200 / 0.6)", color: "oklch(0.85 0.08 200)" }}>
            整本源文已更换。请从第 1 集开始重新规划，让分集与新源文对应。
            {cuts[0] && <div><SecondaryButton size="sm" onClick={() => setReplanFrom(cuts[0].id)}>从第 1 集开始重新规划</SecondaryButton></div>}
          </div>
        )}
        {s.planning ? (
          <div className="space-y-1.5 rounded-md px-2.5 py-2 text-[12px]" style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)" }}>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 animate-pulse rounded-full" style={{ background: "var(--color-accent)" }} />
              {s.planning.mode === "toEnd" ? `正在规划第 ${s.planning.batchesDone + 1} / 约 ${s.planning.batchesTotal} 批` : "正在规划下一批"}
              <div className="flex-1" />
              <SecondaryButton size="sm" leadingIcon={<Square className="h-3 w-3" />} onClick={p.stopPlanning}>停止</SecondaryButton>
            </div>
            <BatchBar done={s.planning.batchesDone} total={s.planning.batchesTotal} />
            <div className="text-[11px]" style={{ color: "var(--color-text-3)" }}>
              每批约 5 万字，完成一批就会加入列表。停止后，已完成的批次会保留。
            </div>
          </div>
        ) : s.cursor >= M.SOURCE_LEN ? (
          <div className="text-[12px]" style={{ color: "oklch(0.8 0.12 150)" }}>整本源文已全部分集，共 {cuts.length} 集</div>
        ) : (
          <>
            <textarea className={INPUT_CLS} rows={2} placeholder="附加要求（可选），例如：每集结尾留悬念" value={instr} onChange={(e) => setInstr(e.target.value)} />
            <div className="flex gap-2">
              <PrimaryButton size="sm" disabled={!canPlan} onClick={() => p.beginPlanning("toEnd", instr)}>AI 规划剩余内容</PrimaryButton>
              <SecondaryButton size="sm" disabled={!canPlan} onClick={() => p.beginPlanning("batch", instr)}>只规划下一批</SecondaryButton>
            </div>
          </>
        )}
      </div>

      <div className="space-y-1">
        <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>来自整本源文</div>
        {cuts.map((e) => (
          <div key={e.id} className="rounded-md" style={{ borderLeft: `3px solid ${epColor(e.id)}`, background: selected === e.id ? "var(--color-accent-dim)" : undefined, ...(e.fresh ? FRESH_STYLE : {}) }}>
            <button type="button" onClick={() => select(e.id)} className="block w-full rounded-md px-2 py-1.5 text-left transition-colors hover:bg-[oklch(0.26_0.012_265/0.5)]">
              <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
                <span className="num" style={{ color: epColor(e.id) }}>第 {e.id} 集</span>
                <span style={{ color: "var(--color-text)" }}>{e.title}</span>
                <EpPills ep={e} withOrigin={false} />
              </div>
              <div className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.epChars(e))}</div>
              <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>首：{M.firstSentence(e)}</div>
              <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>尾：{M.lastSentence(e)}</div>
            </button>
            {selected === e.id && renderEpActions(e)}
          </div>
        ))}
        {cuts.length === 0 && <div className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>还没有从整本源文分出的集</div>}
      </div>

      <div className="space-y-1">
        <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>其他集</div>
        {others.map((e) => (
          <div key={e.id} className="rounded-md" style={{ background: selected === e.id ? "var(--color-accent-dim)" : undefined, ...(e.fresh ? FRESH_STYLE : {}) }}>
            <button
              type="button" onClick={() => setSelected(e.id)}
              className="flex w-full flex-wrap items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-[oklch(0.26_0.012_265/0.5)]"
            >
              <span className="num" style={{ color: "var(--color-text-2)" }}>第 {e.id} 集</span>
              <span>{e.title}</span>
              <OriginPill ep={e} />
              <EpPills ep={e} withOrigin={false} />
              {e.origin === "own" && <span className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.epChars(e))}</span>}
            </button>
            {selected === e.id && (
              <p className="px-2 pb-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>
                {e.origin === "own" ? `原文来自上传的文件 ${e.ownFile}，可以在集页面查看和编辑。` : "这一集没有原文，可以在集页面补充原文，或直接编写脚本。"}
              </p>
            )}
          </div>
        ))}
        {others.length === 0 && <div className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>暂无</div>}
      </div>
    </div>
  );

  function renderEpActions(ep: M.Ep) {
    const nx = M.nextCut(s, ep.id);
    const restructurable = M.canRestructure(s, ep.id);
    return (
      <div className="space-y-1.5 px-2 pb-2">
        <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {restructurable
            ? "在左侧原文中点击这一集里的某一句，可以在那里拆分；拖动两集之间的分界标记可以调整范围。"
            : "这一集或之后的集已开始制作，不能拆分或合并。需要调整时，请从这一集开始重新规划。"}
        </p>
        <div className="flex flex-wrap gap-1.5">
          <SecondaryButton size="sm" onClick={() => setReplanFrom(ep.id)}>从这一集开始重新规划</SecondaryButton>
          <SecondaryButton size="sm" disabled={!nx || !restructurable} title={!nx ? "已经是最后一集" : ""} onClick={() => p.mergeWithNext(ep.id)}>与下一集合并</SecondaryButton>
          <SecondaryButton size="sm" disabled={M.affectedByRemoveAfter(s, ep.id).length === 0} onClick={() => removeAfter(ep)}>清除之后的分集</SecondaryButton>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <div className="flex-1 overflow-y-auto px-8 py-6 pb-24">
        <div className="mx-auto max-w-[760px]">
          <div className="mb-4 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>
            <span className="display-serif text-[14px]" style={{ color: "var(--color-text-2)" }}>{M.SOURCE_FILE_NAME}</span>
            {cand ? "· 对比新方案：左侧现有分集，右侧新方案" : "· 拖动两集之间的分界标记可调整范围"}
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

/** 按批显示进度：每批是一次非流式调用，批内没有更细的进度，当前批用不定进度动画 */
function BatchBar({ done, total }: { done: number; total: number }) {
  return (
    <div className="flex gap-1">
      {Array.from({ length: total }, (_, i) => (
        <div key={i} className="h-1 flex-1 overflow-hidden rounded-full" style={{ background: "var(--color-hairline)" }}>
          {i < done && <div className="h-full w-full" style={{ background: "var(--color-accent)" }} />}
          {i === done && <div className="h-full w-1/3" style={{ background: "var(--color-accent)", animation: "arc-proto-indet 1.2s ease-in-out infinite" }} />}
        </div>
      ))}
      <style>{"@keyframes arc-proto-indet{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}"}</style>
    </div>
  );
}
