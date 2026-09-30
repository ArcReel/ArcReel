// PROTOTYPE（#2767）「分集」视图 B「原文双栏」：整本源文是主角，右栏是规划控制与集清单。
// 分集点落在任意字符处（点击原文放置插入点，不做断句识别）；移动分界是「选中分界 → 点击新位置」两步，
// 中途可以自由滚动，←/→ 逐字微调（Shift 一次 10 字），Enter 确认，Esc 取消。
// 候选按原文位置并排对比：原文两侧各一条范围栏（左现有、右新方案）。
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { MoveHorizontal, Plus, Scissors, Square, Upload, X } from "lucide-react";
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

type PointAction =
  | { kind: "move"; left: M.Ep; right: M.Ep }
  | { kind: "cut" }
  | { kind: "split"; ep: M.Ep }
  | { kind: "blocked"; ep: M.Ep }
  | null;

/** 把点击坐标换成整本源文的字符偏移：文本节点的父元素带 data-off（该节点首字符的偏移） */
function offsetFromPoint(x: number, y: number): number | null {
  const doc = document as Document & {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
  };
  let node: Node | null = null;
  let off = 0;
  if (doc.caretPositionFromPoint) {
    const pos = doc.caretPositionFromPoint(x, y);
    if (!pos) return null;
    node = pos.offsetNode;
    off = pos.offset;
  } else if (doc.caretRangeFromPoint) {
    const r = doc.caretRangeFromPoint(x, y);
    if (!r) return null;
    node = r.startContainer;
    off = r.startOffset;
  }
  if (!node || node.nodeType !== Node.TEXT_NODE) return null;
  const base = node.parentElement?.getAttribute("data-off");
  return base == null ? null : Number(base) + off;
}

export function VariantB({ p }: { p: Proto }) {
  const s = p.s;
  const [selected, setSelected] = useState<number | null>(null);
  const [instr, setInstr] = useState(s.lastInstructions);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [replanFrom, setReplanFrom] = useState<number | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  const [moving, setMoving] = useState<number | null>(null);
  const [cutTitle, setCutTitle] = useState("");
  const [confirmNode, ask] = useConfirm();
  const headerRefs = useRef(new Map<number, HTMLElement>());

  const cuts = M.cutEps(s);
  const others = M.otherEps(s);
  const cand = s.candidate;
  const sum = M.candidateSummary(s);
  const replanStart = cand ? M.replanStart(s, cand.fromEp) : 0;
  const candRanges = useMemo(
    () => sum?.mapping.map((m) => ({ id: m.newId, oldId: m.oldId, range: m.cand.range })) ?? [],
    [sum],
  );
  const editing = !cand && !s.planning;
  const nextId = M.maxId(s) + 1;

  const moveLeft = moving != null ? cuts.find((e) => e.id === moving) : undefined;
  const moveRight = moveLeft ? M.nextCut(s, moveLeft.id) : undefined;

  const actionAt = (pos: number): PointAction => {
    if (moveLeft && moveRight) {
      return pos > moveLeft.range![0] && pos < moveRight.range![1] && pos !== moveLeft.range![1]
        ? { kind: "move", left: moveLeft, right: moveRight } : null;
    }
    if (pos > s.cursor && !s.sourceReplaced) return { kind: "cut" };
    const ep = cuts.find((e) => e.range![0] < pos && pos < e.range![1]);
    if (ep) return M.canRestructure(s, ep.id) ? { kind: "split", ep } : { kind: "blocked", ep };
    return null;
  };
  const action = pending != null ? actionAt(pending) : null;

  const place = (pos: number) => {
    if (!editing) return;
    const a = actionAt(pos);
    if (!a) { setPending(null); return; }
    if (a.kind === "cut" && (pending == null || actionAt(pending)?.kind !== "cut")) setCutTitle(`第 ${nextId} 集`);
    setPending(pos);
  };

  const cancel = () => { setPending(null); setMoving(null); };

  const confirm = () => {
    if (pending == null || !action) return;
    const pos = pending;
    if (action.kind === "move") {
      const { left, right } = action;
      guardArtifacts(ask, [left, right], `调整第 ${left.id} 集和第 ${right.id} 集的分界`, () => p.moveBoundary(left.id, pos));
      setMoving(null);
    } else if (action.kind === "cut") {
      p.manualCut(pos, cutTitle);
    } else if (action.kind === "split") {
      p.splitEp(action.ep.id, pos);
    }
    setPending(null);
  };

  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  const onKey = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement | null;
    const inField = !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
    if (e.key === "Escape") { cancel(); return; }
    if (pending == null || inField) return;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const step = (e.shiftKey ? 10 : 1) * (e.key === "ArrowLeft" ? -1 : 1);
      const next = Math.min(M.SOURCE_LEN, Math.max(0, pending + step));
      if (actionAt(next)) setPending(next);
    }
    if (e.key === "Enter") { e.preventDefault(); confirm(); }
  };
  useEffect(() => {
    keyRef.current = onKey;
  });
  useEffect(() => {
    const on = (e: KeyboardEvent) => keyRef.current(e);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  // 新方案开始生成时，把原文滚到重新规划的起点
  const candFrom = cand?.fromEp;
  useEffect(() => {
    if (candFrom != null) headerRefs.current.get(candFrom)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [candFrom]);

  const select = (id: number) => {
    setSelected(id);
    headerRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // ---------------------------------------------------------------- 行切分点
  const rowPts = new Set<number>([0, s.cursor, ...M.SOURCE.paraStarts]);
  cuts.forEach((e) => { rowPts.add(e.range![0]); rowPts.add(e.range![1]); });
  if (cand) {
    rowPts.add(replanStart);
    rowPts.add(cand.reached);
    candRanges.forEach((c) => { rowPts.add(c.range[0]); rowPts.add(c.range[1]); });
  }
  const sortedPts = [...rowPts].filter((x) => x < M.SOURCE_LEN).sort((a, b) => a - b);
  const rows = sortedPts.map((st, i) => ({ start: st, end: sortedPts[i + 1] ?? M.SOURCE_LEN }));

  const cutAtPos = (pos: number) => cuts.find((e) => e.range![0] <= pos && pos < e.range![1]);
  const candAtPos = (pos: number) => candRanges.find((c) => c.range[0] <= pos && pos < c.range[1]);
  const existingStarts = new Set(cuts.map((e) => e.range![0]));
  const candStarts = new Set(candRanges.map((c) => c.range[0]));

  // ---------------------------------------------------------------- 插入点与浮动操作条
  const toolbar = (): ReactNode => {
    if (pending == null || !action) return null;
    const hint = <span className="text-[10.5px]" style={{ color: "var(--color-text-4)" }}>← → 微调</span>;
    const cancelBtn = (
      <button type="button" aria-label="取消" className="rounded p-0.5 hover:bg-[oklch(0.3_0.01_265)]" style={{ color: "var(--color-text-4)" }} onClick={cancel}>
        <X className="h-3.5 w-3.5" />
      </button>
    );
    if (action.kind === "move") {
      const b = action.left.range![1];
      const leftChars = M.charsOf([action.left.range![0], pending]);
      const rightChars = M.charsOf([pending, action.right.range![1]]);
      return (
        <>
          <span style={{ color: "var(--color-text-3)" }}>
            第 {action.left.id} 集 {leftChars.toLocaleString()} 字 · 第 {action.right.id} 集 {rightChars.toLocaleString()} 字
            <span className="ml-1" style={{ color: "var(--color-text-4)" }}>（{pending < b ? "前移" : "后移"} {M.charsOf([b, pending]).toLocaleString()} 字）</span>
          </span>
          {hint}
          <button type="button" className="font-medium" style={{ color: "var(--color-accent-2)" }} onClick={confirm}>移到这里</button>
          {cancelBtn}
        </>
      );
    }
    if (action.kind === "cut") {
      return (
        <>
          <Scissors className="h-3 w-3" style={{ color: "var(--color-accent-2)" }} />
          <span style={{ color: "var(--color-text-3)" }}>新的一集 · {M.charsOf([s.cursor, pending]).toLocaleString()} 字</span>
          <input
            aria-label="标题"
            className="w-28 rounded bg-transparent px-1 outline-none"
            style={{ color: "var(--color-text)", borderBottom: "1px solid var(--color-hairline-strong)" }}
            value={cutTitle} onChange={(e) => setCutTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") confirm(); }}
          />
          {hint}
          <button type="button" className="font-medium" style={{ color: "var(--color-accent-2)" }} onClick={confirm}>添加为第 {nextId} 集</button>
          {cancelBtn}
        </>
      );
    }
    if (action.kind === "split") {
      const ep = action.ep;
      return (
        <>
          <Scissors className="h-3 w-3" style={{ color: epColor(ep.id) }} />
          <span style={{ color: "var(--color-text-3)" }}>
            把第 {ep.id} 集拆成 {M.charsOf([ep.range![0], pending]).toLocaleString()} 字 + {M.charsOf([pending, ep.range![1]]).toLocaleString()} 字，之后的集号依次后移
          </span>
          {hint}
          <button type="button" className="font-medium" style={{ color: epColor(ep.id) }} onClick={confirm}>拆分</button>
          {cancelBtn}
        </>
      );
    }
    return (
      <>
        <span style={{ color: "var(--color-text-3)" }}>
          第 {action.ep.id} 集或之后的集已开始制作，不能在这里拆分。可以移动两集之间的分界，或从这一集开始重新规划。
        </span>
        {cancelBtn}
      </>
    );
  };

  const caretColor = action?.kind === "split" || action?.kind === "blocked" ? epColor(action.ep.id) : "var(--color-accent)";
  const marker = (
    <span key="caret" data-no-caret className="relative inline-block h-[1.35em] w-0 align-text-bottom">
      <span className="absolute -left-px top-0 h-full w-[2px] rounded" style={{ background: caretColor, boxShadow: `0 0 8px ${caretColor}` }} />
      <span
        className="absolute left-0 top-full z-20 mt-1.5 inline-flex -translate-x-6 items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-1 text-[11.5px] leading-normal"
        style={{ background: "oklch(0.2 0.015 265 / 0.97)", border: `1px solid ${caretColor}`, boxShadow: "0 10px 28px -8px oklch(0 0 0 / 0.7)" }}
      >
        {toolbar()}
      </span>
    </span>
  );

  /** 按「谁会拿到这段文字」给插入点与原分界之间的片段着色 */
  const segTint = (a: number): string | undefined => {
    if (pending == null || !action) return undefined;
    if (action.kind === "cut" && a >= s.cursor && a < pending) return "oklch(0.35 0.06 280 / 0.45)";
    if (action.kind === "move") {
      const b = action.left.range![1];
      if (pending < b && a >= pending && a < b) return epColor(action.right.id, 0.22);
      if (pending > b && a >= b && a < pending) return epColor(action.left.id, 0.22);
    }
    if (action.kind === "split" && a >= action.ep.range![0] && a < pending) return epColor(action.ep.id, 0.16);
    return undefined;
  };

  const renderText = (start: number, end: number, unplanned: boolean): ReactNode[] => {
    const pts = new Set<number>([start, end]);
    M.SOURCE.chapters.forEach(([a, b]) => { if (a > start && a < end) pts.add(a); if (b > start && b < end) pts.add(b); });
    if (pending != null && action && pending > start && pending < end) pts.add(pending);
    const sorted = [...pts].sort((a, b) => a - b);
    const out: ReactNode[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      const a = sorted[i];
      const b = sorted[i + 1];
      if (pending === a && action) out.push(<Fragment key={`m${a}`}>{marker}</Fragment>);
      const isChapter = M.SOURCE.chapters.some(([ca]) => ca === a);
      const tint = segTint(a);
      out.push(
        <span
          key={a}
          data-off={a}
          className={isChapter ? "display-serif text-[15px] font-semibold" : undefined}
          style={{
            background: tint,
            color: tint ? "var(--color-text)" : isChapter ? (unplanned ? "var(--color-text-3)" : "var(--color-text)") : undefined,
          }}
        >
          {M.SOURCE.text.slice(a, b)}
        </span>,
      );
    }
    if (pending === M.SOURCE_LEN && end === M.SOURCE_LEN && action) out.push(<Fragment key="mend">{marker}</Fragment>);
    return out;
  };

  const onTextMouseUp = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("[data-no-caret]")) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const off = offsetFromPoint(e.clientX, e.clientY);
    if (off != null) place(off);
  };

  // ---------------------------------------------------------------- 左栏
  const left: ReactNode[] = [];
  rows.forEach((row) => {
    const pos = row.start;
    if (pos === s.cursor && !cand) {
      left.push(
        <div key={`cur${pos}`} data-no-caret className="my-3 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-accent-2)" }}>
          <span className="h-[2px] flex-1" style={{ background: "linear-gradient(90deg, var(--color-accent), transparent)", boxShadow: "0 0 8px var(--color-accent-glow)" }} />
          以下内容尚未分集{s.planning ? " · AI 正在规划…" : " · 点击任意位置，可在那里分出新的一集"}
          <span className="h-[2px] flex-1" style={{ background: "linear-gradient(270deg, var(--color-accent), transparent)" }} />
        </div>,
      );
    }
    const startEp = cuts.find((e) => e.range![0] === pos);
    if (startEp) {
      const prev = cuts.find((e) => e.range![1] === pos);
      if (prev && !cand) {
        const active = moving === prev.id;
        left.push(
          <div key={`b${pos}`} data-no-caret className="my-1 flex items-center gap-2">
            <span className="h-px flex-1" style={{ background: active ? "var(--color-accent)" : "var(--color-hairline-strong)" }} />
            <button
              type="button"
              disabled={!editing}
              onClick={() => { setPending(null); setMoving(active ? null : prev.id); }}
              className="inline-flex items-center gap-1 rounded-full px-2 py-[1px] text-[10.5px] transition-colors enabled:hover:border-[var(--color-accent)] disabled:opacity-50"
              style={{
                border: `1px solid ${active ? "var(--color-accent)" : "var(--color-hairline-strong)"}`,
                color: active ? "var(--color-accent-2)" : "var(--color-text-3)",
                background: active ? "var(--color-accent-dim)" : "oklch(0.2 0.01 265)",
              }}
              title={active ? "再次点击取消" : "移动这条分界"}
            >
              <MoveHorizontal className="h-3 w-3" />
              {active ? `正在移动第 ${prev.id} / ${startEp.id} 集的分界 · 点击原文中的新位置` : `第 ${prev.id} / ${startEp.id} 集分界`}
            </button>
            <span className="h-px flex-1" style={{ background: active ? "var(--color-accent)" : "var(--color-hairline-strong)" }} />
          </div>,
        );
      }
      left.push(
        <div
          key={`h${startEp.id}`}
          data-no-caret
          ref={(el) => { if (el) headerRefs.current.set(startEp.id, el); else headerRefs.current.delete(startEp.id); }}
          role="button"
          tabIndex={0}
          onClick={() => setSelected(startEp.id)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setSelected(startEp.id); }}
          className="mb-1.5 mt-2 cursor-pointer scroll-mt-16 rounded-md px-3 py-1.5"
          style={{
            borderLeft: `3px solid ${epColor(startEp.id)}`,
            background: selected === startEp.id ? "var(--color-accent-dim)" : "oklch(0.21 0.01 265 / 0.6)",
            opacity: moveLeft && startEp.id !== moveLeft.id && startEp.id !== moveRight?.id ? 0.45 : 1,
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
    const outOfMove = !!moveLeft && !(moveRight && pos >= moveLeft.range![0] && pos < moveRight.range![1]);
    const isParaStart = M.SOURCE.paraStarts.includes(pos);
    const end = M.SOURCE.text[row.end - 1] === "\n" ? row.end - 1 : row.end;

    left.push(
      <div key={`c${pos}`} className="flex gap-2">
        <div className="relative shrink-0" style={{ width: cand ? 40 : 4 }}>
          {ep && <div className="absolute inset-y-0 right-0 w-[4px]" style={{ background: epColor(ep.id, outside ? 0.25 : 0.8) }} />}
          {cand && ep && ep.range![0] === pos && (
            <span className="num absolute right-2 top-0 text-[10px]" style={{ color: epColor(ep.id, outside ? 0.4 : 1) }}>{ep.id}</span>
          )}
          {cand && diff && existingStarts.has(pos) && <span className="absolute right-0 top-0 h-[2px] w-full" style={{ background: DIFF }} />}
        </div>
        <p
          className="flex-1 text-[13.5px] leading-[1.95]"
          style={{
            color: unplanned ? "var(--color-text-4)" : "var(--color-text-2)",
            opacity: outside || outOfMove ? 0.4 : 1,
            background: isSel ? "var(--color-accent-dim)" : undefined,
            borderTop: diff ? `1px dashed ${DIFF}` : undefined,
            // 段落间距放在文字内边距里，让左右色条在同一集内连成一条
            paddingTop: isParaStart ? 8 : 0,
            cursor: editing ? "text" : undefined,
          }}
        >
          {renderText(pos, end, unplanned)}
        </p>
        {cand && (
          <div className="relative shrink-0" style={{ width: 40 }}>
            {cr ? (
              <>
                <div className="absolute inset-y-0 left-0 w-[4px]" style={{ background: epColor(cr.id, 0.55) }} />
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
  const canPlan = editing && s.cursor < M.SOURCE_LEN && !s.sourceReplaced;
  const pct = (a: number, b: number) => Math.round((a / Math.max(1, b)) * 100);

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

  const moveText = (from: number, to: number) => {
    if (from === to) return "";
    return `${to < from ? "前移" : "后移"} ${M.charsOf([from, to]).toLocaleString()} 字`;
  };

  const renderEpActions = (ep: M.Ep) => {
    const nx = M.nextCut(s, ep.id);
    const restructurable = M.canRestructure(s, ep.id);
    return (
      <div className="space-y-1.5 px-2 pb-2">
        <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {restructurable
            ? "在左侧原文中点击这一集里的任意位置，可以在那里拆分；点击两集之间的分界可以移动它。"
            : "这一集或之后的集已开始制作，不能拆分或合并。可以移动分界，或从这一集开始重新规划。"}
        </p>
        <div className="flex flex-wrap gap-1.5">
          <SecondaryButton size="sm" onClick={() => setReplanFrom(ep.id)}>从这一集开始重新规划</SecondaryButton>
          <SecondaryButton size="sm" disabled={!nx || !restructurable} title={!nx ? "已经是最后一集" : ""} onClick={() => p.mergeWithNext(ep.id)}>与下一集合并</SecondaryButton>
          <SecondaryButton size="sm" disabled={M.affectedByRemoveAfter(s, ep.id).length === 0} onClick={() => removeAfter(ep)}>清除之后的分集</SecondaryButton>
        </div>
      </div>
    );
  };

  const rail = cand && sum ? (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="display-serif text-[15px] font-semibold">新的分集方案</h3>
        <Pill tone="accent">从第 {cand.fromEp} 集起</Pill>
        <div className="flex-1" />
        <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {cand.status === "generating" ? `正在规划 · ${pct(cand.reached - replanStart, M.SOURCE_LEN - replanStart)}%` : cand.status === "stopped" ? "已停止" : "规划完成"}
        </span>
      </div>
      {cand.status === "generating" && <ProgressBar value={(cand.reached - replanStart) / Math.max(1, M.SOURCE_LEN - replanStart)} />}
      <p className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
        左侧色条是现有分集，右侧是新方案；<span style={{ color: DIFF }}>琥珀色虚线</span>标出分界不同的位置。采纳之前，现有分集不会变化。
      </p>
      <div className="rounded-md p-2.5" style={{ background: "oklch(0.21 0.01 265 / 0.6)", border: "1px solid var(--color-hairline-soft)" }}>
        <CandidateSummaryBlock p={p} />
      </div>
      <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>逐集变化</div>
      <div className="space-y-1">
        {sum.mapping.map((m) => {
          const old = m.oldId !== undefined ? sum.replaced.find((e) => e.id === m.oldId) : undefined;
          const nc = M.charsOf(m.cand.range);
          const moves = old
            ? [
                moveText(old.range![0], m.cand.range[0]) && `开头${moveText(old.range![0], m.cand.range[0])}`,
                moveText(old.range![1], m.cand.range[1]) && `结尾${moveText(old.range![1], m.cand.range[1])}`,
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
                {old ? `${M.epChars(old).toLocaleString()} → ${nc.toLocaleString()} 字` : `${nc.toLocaleString()} 字`}
                {moves.length > 0 && ` · ${moves.join("，")}`}
              </div>
              {!same && <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>钩子：{m.cand.hook}</div>}
            </div>
          );
        })}
        {[...sum.toNone, ...sum.removed].map((id) => {
          const old = sum.replaced.find((e) => e.id === id);
          const pending = cand.status === "generating";
          return (
            <div key={`x${id}`} className="rounded px-2 py-1.5 text-[12px]" style={{ borderLeft: "3px dashed var(--color-hairline-strong)", background: "oklch(0.21 0.01 265 / 0.35)" }}>
              <span className="num" style={{ color: "var(--color-text-3)" }}>第 {id} 集</span>
              <span className={`ml-1.5 ${pending ? "" : "line-through"}`} style={{ color: "var(--color-text-4)" }}>{old?.title}</span>
              <span className="ml-1.5 text-[11px]" style={{ color: "var(--color-text-3)" }}>
                {pending ? "等待规划" : sum.toNone.includes(id) ? "保留已生成内容，转为无原文的集" : "将移除"}
              </span>
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
        {s.planning ? (
          <div className="space-y-1.5 rounded-md px-2.5 py-2 text-[12px]" style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)" }}>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 animate-pulse rounded-full" style={{ background: "var(--color-accent)" }} />
              AI 正在规划 · 已完成 {pct(s.cursor, M.SOURCE_LEN)}%
              <div className="flex-1" />
              <SecondaryButton size="sm" leadingIcon={<Square className="h-3 w-3" />} onClick={p.stopPlanning}>停止</SecondaryButton>
            </div>
            <ProgressBar value={s.cursor / M.SOURCE_LEN} />
            <div className="text-[11px]" style={{ color: "var(--color-text-3)" }}>新分出的集会陆续出现在列表中。停止后，已分出的集会保留。</div>
          </div>
        ) : (
          <>
            <ProgressBar value={s.cursor / M.SOURCE_LEN} idle />
            {s.sourceReplaced ? (
              <div className="space-y-1.5 rounded-md px-2.5 py-2 text-[11.5px]" style={{ border: "1px dashed oklch(0.7 0.1 200 / 0.6)", color: "oklch(0.85 0.08 200)" }}>
                整本源文已更换。请从第 1 集开始重新规划，让分集与新源文对应。
                {cuts[0] && <div><SecondaryButton size="sm" onClick={() => setReplanFrom(cuts[0].id)}>从第 1 集开始重新规划</SecondaryButton></div>}
              </div>
            ) : s.cursor >= M.SOURCE_LEN ? (
              <div className="text-[12px]" style={{ color: "oklch(0.8 0.12 150)" }}>整本源文已全部分集，共 {cuts.length} 集</div>
            ) : (
              <>
                <textarea className={INPUT_CLS} rows={2} placeholder="附加要求（可选），例如：每集结尾留悬念" value={instr} onChange={(e) => setInstr(e.target.value)} />
                <PrimaryButton size="sm" disabled={!canPlan} onClick={() => { cancel(); p.beginPlanning("toEnd", instr); }}>
                  {cuts.length ? "AI 规划剩余内容" : "AI 规划分集"}
                </PrimaryButton>
              </>
            )}
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
              <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>开头：{M.headExcerpt(e)}</div>
              <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>结尾：{M.tailExcerpt(e)}</div>
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

  return (
    <div className="flex h-full">
      <div className="flex-1 overflow-y-auto px-8 pb-24">
        <div className="sticky top-0 z-10 -mx-8 mb-3 px-8 py-2.5 text-[11.5px]" style={{ background: "oklch(0.18 0.01 265 / 0.92)", backdropFilter: "blur(8px)", borderBottom: "1px solid var(--color-hairline-soft)" }}>
          <div className="mx-auto flex max-w-[760px] items-center gap-2" style={{ color: "var(--color-text-4)" }}>
            <span className="display-serif text-[14px]" style={{ color: "var(--color-text-2)" }}>{M.SOURCE_FILE_NAME}</span>
            {cand ? (
              <span>· 对比新方案：左侧现有分集，右侧新方案</span>
            ) : moveLeft && moveRight ? (
              <>
                <span style={{ color: "var(--color-accent-2)" }}>· 正在移动第 {moveLeft.id} / {moveRight.id} 集的分界，点击原文中的新位置（可以先滚动到目标处）</span>
                <div className="flex-1" />
                <SecondaryButton size="sm" onClick={cancel}>取消</SecondaryButton>
              </>
            ) : (
              <span>· 点击原文任意位置放置分集点 · 点击两集之间的分界可以移动它</span>
            )}
          </div>
        </div>
        {/* 点击原文放置插入点；键盘操作见文件头注释 */}
        {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
        <div className="mx-auto max-w-[760px]" onMouseUp={onTextMouseUp}>
          {left}
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

/** 批内没有更细的进度（一次非流式调用），已完成部分实填、其后用一段流动光带表示「正在进行」 */
function ProgressBar({ value, idle = false }: { value: number; idle?: boolean }) {
  return (
    <div className="relative h-1 overflow-hidden rounded-full" style={{ background: "var(--color-hairline)" }}>
      <div className="absolute inset-y-0 left-0" style={{ width: `${value * 100}%`, background: "var(--color-accent)" }} />
      {!idle && value < 1 && (
        <div className="absolute inset-y-0 overflow-hidden" style={{ left: `${value * 100}%`, right: 0 }}>
          <div className="h-full w-1/4" style={{ background: "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)", animation: "arc-proto-indet 1.4s ease-in-out infinite" }} />
        </div>
      )}
      <style>{"@keyframes arc-proto-indet{0%{transform:translateX(-100%)}100%{transform:translateX(400%)}}"}</style>
    </div>
  );
}
