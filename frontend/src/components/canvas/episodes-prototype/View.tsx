// PROTOTYPE（#2831，基于 #2767 选定的「原文双栏」）：多文件整本源文的呈现与文件操作。
// 三个变体只在「文件结构与文件操作放在哪里」上分岔：
//   A 原文内文件条：左栏每个文件开头一条文件横条，类型、替换、编辑、上下移、删除都在横条上，逐项立即确认。
//   B 右栏文件清单：右栏顶部可拖动排序的文件清单（类型、覆盖进度、操作），左栏只用细分隔线标出文件边界。
//   C 分文件标签 + 管理模式：左栏一次只看一个文件（顶部标签切换）；文件操作集中在「管理源文件」里暂存，预览合并影响后一次应用。
// 分集点、移动分界、候选对比的交互沿用 #2767。
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, FileText, GripVertical, MoveHorizontal, Pencil, Plus, RefreshCw, Scissors, Settings2, Sparkles, Square, Trash2, Upload, X } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import * as M from "./model";
import type { Proto } from "./useProto";
import {
  AffectedList, CandidateActions, CandidateSummaryBlock, EditDialog, EpPills, ExternalBanners, FRESH_STYLE, ImpactBody, KindSelect,
  NewEpisodeDialog, OriginPill, Pill, PlanModePicker, ReplaceDialog, ReplanDialog, UploadDialog, WARN, epColor, guardArtifacts, useConfirm, useImpact,
  type ImpactReq,
} from "./shared";

export type VariantKey = "A" | "B" | "C";
const DIFF = "oklch(0.8 0.15 70)";

type PointAction =
  | { kind: "move"; left: M.Ep; right: M.Ep }
  | { kind: "cut"; span: M.Span }
  | { kind: "split"; ep: M.Ep }
  | { kind: "blocked"; ep: M.Ep }
  | null;

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

export function View({ p, variant }: { p: Proto; variant: VariantKey }) {
  const s = p.s;
  const lay = M.L(s);
  const [selected, setSelected] = useState<number | null>(null);
  const [instr, setInstr] = useState(s.lastInstructions);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [replanFrom, setReplanFrom] = useState<number | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  const [moving, setMoving] = useState<number | null>(null);
  const [cutTitle, setCutTitle] = useState("");
  const [replaceId, setReplaceId] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [manageOpen, setManageOpen] = useState(false);
  const [newEp, setNewEp] = useState<{ after: number | null } | null>(null);
  const [batch, setBatch] = useState(false);
  const [activeFileRaw, setActiveFile] = useState<string>(s.files[0]?.id ?? "");
  const [confirmNode, ask] = useConfirm();
  const [impactNode, askImpact] = useImpact(p);
  const headerRefs = useRef(new Map<number, HTMLElement>());
  const fileRefs = useRef(new Map<string, HTMLElement>());
  const scrollRef = useRef<HTMLDivElement>(null);

  const activeFile = s.files.some((f) => f.id === activeFileRaw) ? activeFileRaw : s.files[0]?.id ?? "";
  const cuts = M.cutEps(s);
  const others = M.otherEps(s);
  const allSpans = M.spans(s);
  const cand = s.candidate;
  const sum = M.candidateSummary(s);
  const replanStart = cand ? M.replanStart(s, cand.fromEp) : 0;
  const candIds = M.candidateIds(s);
  const candRanges = cand?.eps.map((c, i) => ({ id: candIds[i], range: c.range })) ?? [];
  const editing = !cand && !s.planning;
  const lockedFiles = new Set(s.files.filter((f) => f.pendingText != null).map((f) => f.id));

  const moveLeft = moving != null ? cuts.find((e) => e.id === moving) : undefined;
  const moveRight = moveLeft ? M.nextCut(s, moveLeft.id) : undefined;

  const actionAt = (pos: number): PointAction => {
    if (lockedFiles.has(M.fileAt(s, Math.max(0, pos - 1)).id)) return null;
    if (moveLeft && moveRight) {
      return pos > moveLeft.range![0] && pos < moveRight.range![1] && pos !== moveLeft.range![1] ? { kind: "move", left: moveLeft, right: moveRight } : null;
    }
    const sp = M.spanAt(s, pos);
    if (sp) return { kind: "cut", span: sp };
    const ep = cuts.find((e) => e.range![0] < pos && pos < e.range![1]);
    if (ep) return M.canRestructure(s, ep.id) ? { kind: "split", ep } : { kind: "blocked", ep };
    return null;
  };
  const action = pending != null ? actionAt(pending) : null;

  const place = (pos: number) => {
    if (!editing) return;
    const a = actionAt(pos);
    if (!a) { setPending(null); return; }
    if (a.kind === "cut" && (pending == null || actionAt(pending)?.kind !== "cut")) setCutTitle("");
    setPending(pos);
  };
  const cancel = () => { setPending(null); setMoving(null); };
  const confirm = () => {
    if (pending == null || !action) return;
    const pos = pending;
    if (action.kind === "move") {
      const { left, right } = action;
      guardArtifacts(s, ask, [left, right], `调整${M.label(s, left.id)}和${M.label(s, right.id)}的分界`, () => p.moveBoundary(left.id, pos));
      setMoving(null);
    } else if (action.kind === "cut") p.cutAt(pos, cutTitle);
    else if (action.kind === "split") p.splitEp(action.ep.id, pos);
    setPending(null);
  };

  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  useEffect(() => {
    keyRef.current = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const inField = !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
      if (e.key === "Escape") { cancel(); return; }
      if (pending == null || inField || e.altKey) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const step = (e.shiftKey ? 10 : 1) * (e.key === "ArrowLeft" ? -1 : 1);
        const next = Math.min(lay.len, Math.max(0, pending + step));
        if (actionAt(next)) setPending(next);
      }
      if (e.key === "Enter") { e.preventDefault(); confirm(); }
    };
  });
  useEffect(() => {
    const on = (e: KeyboardEvent) => keyRef.current(e);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const scrollToEp = (id: number) => {
    const ep = s.episodes.find((e) => e.id === id);
    if (variant === "C" && ep?.loc) setActiveFile(ep.loc.file);
    requestAnimationFrame(() => headerRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const scrollToFile = (fid: string) => {
    if (variant === "C") { setActiveFile(fid); scrollRef.current?.scrollTo({ top: 0 }); return; }
    fileRefs.current.get(fid)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const select = (id: number) => { setSelected(id); scrollToEp(id); };

  // ---------------------------------------------------------------- 文件操作
  const fileName = (id: string) => s.files.find((f) => f.id === id)?.name ?? "";
  const fileOps = {
    move: (id: string, dir: -1 | 1) => {
      const order = s.files.map((f) => f.id);
      const i = order.indexOf(id);
      const j = i + dir;
      if (j < 0 || j >= order.length) return;
      [order[i], order[j]] = [order[j], order[i]];
      fileOps.reorder(order);
    },
    reorder: (order: string[]) => {
      if (order.join() === s.files.map((f) => f.id).join()) return;
      askImpact({
        title: "调整整本源文的文件顺序",
        intro: <span style={{ color: "var(--color-text-3)" }}>{order.map(fileName).join(" → ")}</span>,
        next: M.reorderFiles(s, order), confirmLabel: "调整顺序", showMoves: true,
      });
    },
    remove: (id: string) => askImpact({
      title: `删除「${fileName(id)}」`,
      intro: <span style={{ color: "var(--color-text-3)" }}>文件从整本源文中移除，其他文件不受影响。</span>,
      next: M.deleteFile(s, id), confirmLabel: "删除文件",
    }),
    kind: (id: string, k: M.Kind) => {
      const next = M.setKind(s, id, k);
      if (M.impact(s, next).kindStale.length === 0) { p.commit(next); return; }
      askImpact({ title: `把「${fileName(id)}」改为${M.KIND_LABEL[k]}`, next, confirmLabel: "修改类型" });
    },
    replace: (id: string) => setReplaceId(id),
    edit: (id: string) => setEditId(id),
  };
  const opsDisabled = !editing;

  // ---------------------------------------------------------------- 行切分点
  const shownFiles = variant === "C" ? lay.files.filter((f) => f.id === activeFile) : lay.files;
  const rowPts = new Set<number>(lay.paraStarts);
  cuts.forEach((e) => { rowPts.add(e.range![0]); rowPts.add(e.range![1]); });
  allSpans.forEach((sp) => rowPts.add(sp.a));
  if (cand) { rowPts.add(replanStart); rowPts.add(cand.reached); candRanges.forEach((c) => { rowPts.add(c.range[0]); rowPts.add(c.range[1]); }); }
  const sortedPts = [...rowPts].filter((x) => x < lay.len).sort((a, b) => a - b);
  const rows = sortedPts
    .map((st, i) => ({ start: st, end: sortedPts[i + 1] ?? lay.len }))
    .filter((r) => shownFiles.some((f) => f.start <= r.start && r.start < f.end));

  const cutAtPos = (pos: number) => cuts.find((e) => e.range![0] <= pos && pos < e.range![1]);
  const candAtPos = (pos: number) => candRanges.find((c) => c.range[0] <= pos && pos < c.range[1]);
  const existingStarts = new Set(cuts.map((e) => e.range![0]));
  const candStarts = new Set(candRanges.map((c) => c.range[0]));
  const firstTail = allSpans.find((sp) => !sp.gap);

  // ---------------------------------------------------------------- 插入点与操作条
  const cutPreview = action?.kind === "cut" && pending != null ? M.cutAt(s, pending, cutTitle) : null;
  const toolbar = (): ReactNode => {
    if (pending == null || !action) return null;
    const hint = <span className="text-[10.5px]" style={{ color: "var(--color-text-4)" }}>← → 微调</span>;
    const cancelBtn = (
      <button type="button" aria-label="取消" className="rounded p-0.5" style={{ color: "var(--color-text-4)" }} onClick={cancel}><X className="h-3.5 w-3.5" /></button>
    );
    if (action.kind === "move") {
      const b = action.left.range![1];
      return (
        <>
          <span style={{ color: "var(--color-text-3)" }}>
            {M.label(s, action.left.id)} {M.charsOf(s, [action.left.range![0], pending]).toLocaleString()} 字 · {M.label(s, action.right.id)} {M.charsOf(s, [pending, action.right.range![1]]).toLocaleString()} 字
            <span className="ml-1" style={{ color: "var(--color-text-4)" }}>（{pending < b ? "前移" : "后移"} {M.charsOf(s, [b, pending]).toLocaleString()} 字）</span>
          </span>
          {hint}
          <button type="button" className="font-medium" style={{ color: "var(--color-accent-2)" }} onClick={confirm}>移到这里</button>
          {cancelBtn}
        </>
      );
    }
    if (action.kind === "cut") {
      const nid = M.maxId(s) + 1;
      return (
        <>
          <Scissors className="h-3 w-3" style={{ color: "var(--color-accent-2)" }} />
          <span style={{ color: "var(--color-text-3)" }}>新的一集 · {M.charsOf(s, [action.span.a, pending]).toLocaleString()} 字</span>
          <input
            aria-label="标题" placeholder="标题（可选）"
            className="w-28 rounded bg-transparent px-1 outline-none"
            style={{ color: "var(--color-text)", borderBottom: "1px solid var(--color-hairline-strong)" }}
            value={cutTitle} onChange={(e) => setCutTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") confirm(); }}
          />
          {hint}
          <button type="button" className="font-medium" style={{ color: "var(--color-accent-2)" }} onClick={confirm}>添加为{cutPreview ? M.label(cutPreview, nid) : ""}</button>
          {cancelBtn}
        </>
      );
    }
    if (action.kind === "split") {
      const ep = action.ep;
      return (
        <>
          <Scissors className="h-3 w-3" style={{ color: epColor(ep.id) }} />
          <span style={{ color: "var(--color-text-3)" }}>把{M.label(s, ep.id)}拆成 {M.charsOf(s, [ep.range![0], pending]).toLocaleString()} 字 + {M.charsOf(s, [pending, ep.range![1]]).toLocaleString()} 字</span>
          {hint}
          <button type="button" className="font-medium" style={{ color: epColor(ep.id) }} onClick={confirm}>拆分</button>
          {cancelBtn}
        </>
      );
    }
    return (
      <>
        <span style={{ color: "var(--color-text-3)" }}>{M.label(s, action.ep.id)}或之后的集已开始制作，不能在这里拆分。</span>
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

  const segTint = (a: number): string | undefined => {
    if (pending == null || !action) return undefined;
    if (action.kind === "cut" && a >= action.span.a && a < pending) return "oklch(0.35 0.06 280 / 0.45)";
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
    lay.chapters.forEach(([a, b]) => { if (a > start && a < end) pts.add(a); if (b > start && b < end) pts.add(b); });
    if (pending != null && action && pending > start && pending < end) pts.add(pending);
    const sorted = [...pts].sort((a, b) => a - b);
    const out: ReactNode[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      const a = sorted[i];
      const b = sorted[i + 1];
      if (pending === a && action && a !== start) out.push(<Fragment key={`m${a}`}>{marker}</Fragment>);
      const isChapter = lay.chapters.some(([ca]) => ca === a);
      const tint = segTint(a);
      out.push(
        <span key={a} data-off={a} className={isChapter ? "display-serif text-[15px] font-semibold" : undefined}
          style={{ background: tint, color: tint ? "var(--color-text)" : isChapter ? (unplanned ? "var(--color-text-3)" : "var(--color-text)") : undefined }}>
          {lay.text.slice(a, b)}
        </span>,
      );
    }
    if (pending === end && action) out.push(<Fragment key="mend">{marker}</Fragment>);
    return out;
  };

  const onTextMouseUp = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("[data-no-caret]")) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const off = offsetFromPoint(e.clientX, e.clientY);
    if (off != null) place(off);
  };

  // ---------------------------------------------------------------- 文件头（A 横条 / B 细分隔线）
  const coverage = (fid: string) => M.fileCoverage(s, fid);
  const fileHead = (f: M.FileSpan, idx: number): ReactNode => {
    const cov = coverage(f.id);
    const locked = lockedFiles.has(f.id);
    const refCb = (el: HTMLElement | null) => { if (el) fileRefs.current.set(f.id, el); else fileRefs.current.delete(f.id); };
    if (variant === "B") {
      return (
        <div key={`f${f.id}`} ref={refCb} data-no-caret className="mb-2 mt-8 flex scroll-mt-16 items-center gap-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>
          <span className="h-px w-6" style={{ background: "var(--color-hairline-strong)" }} />
          <FileText className="h-3 w-3" />
          <span style={{ color: "var(--color-text-3)" }}>{f.name}</span>
          <span>· {M.KIND_LABEL[f.kind]}</span>
          {locked && <Pill tone="warn">外部已修改</Pill>}
          <span className="h-px flex-1" style={{ background: "var(--color-hairline-strong)" }} />
        </div>
      );
    }
    if (variant === "C") return null;
    return (
      <div key={`f${f.id}`} ref={refCb} data-no-caret className="mb-3 mt-8 scroll-mt-16 rounded-md px-3 py-2" style={{ background: "oklch(0.23 0.012 265 / 0.9)", border: "1px solid var(--color-hairline-strong)" }}>
        <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
          <FileText className="h-3.5 w-3.5" style={{ color: "var(--color-text-3)" }} />
          <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>{idx + 1}/{lay.files.length}</span>
          <span className="font-medium" style={{ color: "var(--color-text)" }}>{f.name}</span>
          <KindSelect compact value={f.kind} disabled={opsDisabled || locked} onChange={(k) => fileOps.kind(f.id, k)} />
          <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>已分集 {cov.cut.toLocaleString()} / {cov.total.toLocaleString()} 字</span>
          {locked && <Pill tone="warn">外部已修改，待更新</Pill>}
          <div className="flex-1" />
          <IconBtn title="上移" disabled={opsDisabled || idx === 0} onClick={() => fileOps.move(f.id, -1)}><ArrowUp className="h-3.5 w-3.5" /></IconBtn>
          <IconBtn title="下移" disabled={opsDisabled || idx === lay.files.length - 1} onClick={() => fileOps.move(f.id, 1)}><ArrowDown className="h-3.5 w-3.5" /></IconBtn>
          <IconBtn title="替换" disabled={opsDisabled || locked} onClick={() => fileOps.replace(f.id)}><RefreshCw className="h-3.5 w-3.5" />替换</IconBtn>
          <IconBtn title="编辑" disabled={opsDisabled || locked} onClick={() => fileOps.edit(f.id)}><Pencil className="h-3.5 w-3.5" />编辑</IconBtn>
          <IconBtn title="删除" disabled={opsDisabled} onClick={() => fileOps.remove(f.id)}><Trash2 className="h-3.5 w-3.5" /></IconBtn>
        </div>
      </div>
    );
  };

  // ---------------------------------------------------------------- 左栏
  const left: ReactNode[] = [];
  rows.forEach((row) => {
    const pos = row.start;
    const fIdx = lay.files.findIndex((f) => f.start === pos);
    if (fIdx >= 0) { const h = fileHead(lay.files[fIdx], fIdx); if (h) left.push(h); }

    const sp = allSpans.find((x) => x.a === pos);
    if (sp && !cand) {
      if (firstTail && sp === firstTail) {
        left.push(
          <div key={`cur${pos}`} data-no-caret className="my-3 flex items-center gap-2 text-[11px]" style={{ color: "var(--color-accent-2)" }}>
            <span className="h-[2px] flex-1" style={{ background: "linear-gradient(90deg, var(--color-accent), transparent)" }} />
            以下内容尚未分集{s.planning && !s.planning.gap ? " · AI 正在规划…" : " · 点击任意位置，可在那里分出新的一集"}
            <span className="h-[2px] flex-1" style={{ background: "linear-gradient(270deg, var(--color-accent), transparent)" }} />
          </div>,
        );
      } else if (sp.gap) {
        const planningHere = s.planning?.gap && s.planning.from === sp.a;
        left.push(
          <div key={`gap${pos}`} data-no-caret className="my-3 flex flex-wrap items-center gap-2 rounded-md px-3 py-2 text-[11.5px]" style={{ border: "1px dashed var(--color-accent-soft)", color: "var(--color-text-3)" }}>
            <span>未切分的原文 · {M.charsOf(s, [sp.a, sp.b]).toLocaleString()} 字</span>
            <span style={{ color: "var(--color-text-4)" }}>夹在已切出的集之间，点击其中任意位置可手工分出一集</span>
            <div className="flex-1" />
            {planningHere ? <span style={{ color: "var(--color-accent-2)" }}>AI 正在规划…</span> : (
              <SecondaryButton size="sm" disabled={!editing || lockedFiles.has(sp.file.id)} leadingIcon={<Sparkles className="h-3 w-3" />} onClick={() => p.beginGapPlanning(sp.a, sp.b)}>规划这段未切分的原文</SecondaryButton>
            )}
          </div>,
        );
      }
    }

    const startEp = cuts.find((e) => e.range![0] === pos);
    if (startEp) {
      const prev = cuts.find((e) => e.range![1] === pos && M.nextCut(s, e.id)?.id === startEp.id);
      if (prev && !cand) {
        const active = moving === prev.id;
        left.push(
          <div key={`b${pos}`} data-no-caret className="my-1 flex items-center gap-2">
            <span className="h-px flex-1" style={{ background: active ? "var(--color-accent)" : "var(--color-hairline-strong)" }} />
            <button type="button" disabled={!editing || lockedFiles.has(prev.loc!.file)} onClick={() => { setPending(null); setMoving(active ? null : prev.id); }}
              className="inline-flex items-center gap-1 rounded-full px-2 py-[1px] text-[10.5px] disabled:opacity-50"
              style={{ border: `1px solid ${active ? "var(--color-accent)" : "var(--color-hairline-strong)"}`, color: active ? "var(--color-accent-2)" : "var(--color-text-3)", background: active ? "var(--color-accent-dim)" : "oklch(0.2 0.01 265)" }}>
              <MoveHorizontal className="h-3 w-3" />
              {active ? "正在移动分界 · 点击原文中的新位置" : `${M.label(s, prev.id)} / ${M.label(s, startEp.id)} 分界`}
            </button>
            <span className="h-px flex-1" style={{ background: active ? "var(--color-accent)" : "var(--color-hairline-strong)" }} />
          </div>,
        );
      }
      left.push(
        <div key={`h${startEp.id}`} data-no-caret
          ref={(el) => { if (el) headerRefs.current.set(startEp.id, el); else headerRefs.current.delete(startEp.id); }}
          role="button" tabIndex={0} onClick={() => setSelected(startEp.id)} onKeyDown={(e) => { if (e.key === "Enter") setSelected(startEp.id); }}
          className="mb-1.5 mt-2 cursor-pointer scroll-mt-16 rounded-md px-3 py-1.5"
          style={{ borderLeft: `3px solid ${epColor(startEp.id)}`, background: selected === startEp.id ? "var(--color-accent-dim)" : "oklch(0.21 0.01 265 / 0.6)", ...(startEp.fresh ? FRESH_STYLE : {}) }}>
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <span className="num font-semibold" style={{ color: epColor(startEp.id) }}>{M.label(s, startEp.id)}</span>
            <span style={{ color: "var(--color-text)" }}>{startEp.title}</span>
            <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.epChars(s, startEp))}</span>
            <EpPills ep={startEp} withOrigin={false} />
          </div>
          {startEp.hook && <div className="mt-0.5 text-[11.5px]" style={{ color: "var(--color-text-3)" }}>钩子：{startEp.hook}</div>}
        </div>,
      );
    }

    const ep = cutAtPos(pos);
    const cr = candAtPos(pos);
    const unplanned = !ep;
    const outside = !!cand && pos < replanStart;
    const diff = !!cand && pos >= replanStart && pos < cand.reached && existingStarts.has(pos) !== candStarts.has(pos);
    const isParaStart = lay.paraStarts.includes(pos);
    const end = lay.text[row.end - 1] === "\n" ? row.end - 1 : row.end;
    left.push(
      <div key={`c${pos}`} className="flex gap-2">
        <div className="relative shrink-0" style={{ width: cand ? 40 : 4 }}>
          {ep && <div className="absolute inset-y-0 right-0 w-[4px]" style={{ background: epColor(ep.id, outside ? 0.25 : 0.8) }} />}
          {cand && diff && existingStarts.has(pos) && <span className="absolute right-0 top-0 h-[2px] w-full" style={{ background: DIFF }} />}
        </div>
        <p className="flex-1 text-[13.5px] leading-[1.95]"
          style={{ color: unplanned ? "var(--color-text-4)" : "var(--color-text-2)", opacity: outside ? 0.4 : 1, background: ep && ep.id === selected ? "var(--color-accent-dim)" : undefined, borderTop: diff ? `1px dashed ${DIFF}` : undefined, paddingTop: isParaStart ? 8 : 0, cursor: editing ? "text" : undefined }}>
          {renderText(pos, end, unplanned)}
        </p>
        {cand && (
          <div className="relative shrink-0" style={{ width: 40 }}>
            {cr && <div className="absolute inset-y-0 left-0 w-[4px]" style={{ background: epColor(cr.id, 0.55) }} />}
            {cr && diff && candStarts.has(pos) && <span className="absolute left-0 top-0 h-[2px] w-full" style={{ background: DIFF }} />}
          </div>
        )}
      </div>,
    );
  });

  // ---------------------------------------------------------------- 右栏
  const plannedChars = cuts.reduce((n, e) => n + M.epChars(s, e), 0);
  const total = M.totalChars(s);
  const tailLocked = !!firstTail && lockedFiles.has(firstTail.file.id);
  const pct = (a: number, b: number) => Math.round((a / Math.max(1, b)) * 100);

  const removeAfter = (ep: M.Ep) => ask({
    title: `清除${M.label(s, ep.id)}之后的分集`,
    body: <AffectedList s={s} eps={M.affectedByRemoveAfter(s, ep.id)} note="尚未开始制作的集会被删除；已开始制作的集保留已生成的内容，转为无原文的集并移到最后。" />,
    confirmLabel: "清除", danger: true, onOk: () => p.removeCutsAfter(ep.id),
  });

  const removeEp = (ep: M.Ep) => ask({
    title: `删除${M.label(s, ep.id)}《${ep.title}》`,
    body: (
      <div className="space-y-1.5 text-[12px]" style={{ color: "var(--color-text-2)" }}>
        {ep.hasArtifacts && <p style={{ color: WARN }}>这一集已经生成的脚本、分镜图和视频会一起删除，无法恢复。</p>}
        {ep.origin === "cut" && <p>它那段原文会回到「未切分的原文」，之后可以手工切出或重新规划。</p>}
        <p style={{ color: "var(--color-text-3)" }}>之后的集依次前移。</p>
      </div>
    ),
    confirmLabel: "删除", danger: true, onOk: () => { p.deleteEp(ep.id); setSelected(null); },
  });

  const commonActions = (ep: M.Ep) => (
    <>
      <SecondaryButton size="sm" onClick={() => setNewEp({ after: ep.id })}>在这一集之后新建</SecondaryButton>
      <SecondaryButton size="sm" leadingIcon={<Trash2 className="h-3 w-3" />} onClick={() => removeEp(ep)}>删除这一集</SecondaryButton>
    </>
  );

  const epActions = (ep: M.Ep) => {
    const nx = M.nextCut(s, ep.id);
    const ok = M.canRestructure(s, ep.id);
    return (
      <div className="flex flex-wrap gap-1.5 px-2 pb-2">
        <SecondaryButton size="sm" onClick={() => setReplanFrom(ep.id)}>从这一集开始重新规划</SecondaryButton>
        <SecondaryButton size="sm" disabled={!nx || !ok} onClick={() => p.mergeWithNext(ep.id)}>与下一集合并</SecondaryButton>
        <SecondaryButton size="sm" disabled={M.affectedByRemoveAfter(s, ep.id).length === 0} onClick={() => removeAfter(ep)}>清除之后的分集</SecondaryButton>
        {commonActions(ep)}
      </div>
    );
  };

  const cutRow = (e: M.Ep) => (
    <div key={e.id} className="rounded-md" style={{ borderLeft: `3px solid ${epColor(e.id)}`, background: selected === e.id ? "var(--color-accent-dim)" : undefined, ...(e.fresh ? FRESH_STYLE : {}) }}>
      <button type="button" onClick={() => select(e.id)} className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-[oklch(0.26_0.012_265/0.5)]">
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
          <span className="num" style={{ color: epColor(e.id) }}>{M.label(s, e.id)}</span>
          <span style={{ color: "var(--color-text)" }}>{e.title}</span>
          <EpPills ep={e} withOrigin={false} />
        </div>
        <div className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{M.readLabel(M.epChars(s, e))}</div>
        <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>开头：{M.headExcerpt(s, e.range)}</div>
        <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>结尾：{M.tailExcerpt(s, e.range)}</div>
      </button>
      {selected === e.id && epActions(e)}
    </div>
  );

  const gapRow = (sp: M.Span) => {
    const planningHere = s.planning?.gap && s.planning.from === sp.a;
    return (
      <div key={`g${sp.a}`} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[11.5px]" style={{ border: "1px dashed var(--color-accent-soft)", color: "var(--color-text-3)" }}>
        <button type="button" className="text-left" onClick={() => scrollToFile(sp.file.id)}>
          未切分的原文 · <span className="num">{M.charsOf(s, [sp.a, sp.b]).toLocaleString()}</span> 字
        </button>
        <div className="flex-1" />
        {planningHere ? <span style={{ color: "var(--color-accent-2)" }}>规划中…</span> : (
          <SecondaryButton size="sm" disabled={!editing || lockedFiles.has(sp.file.id)} onClick={() => p.beginGapPlanning(sp.a, sp.b)}>规划这段</SecondaryButton>
        )}
      </div>
    );
  };

  // 集清单按文件分组，组内按源文位置排列切出的集与空段
  const cutList = lay.files.map((f) => {
    const items: { at: number; node: ReactNode }[] = [
      ...cuts.filter((e) => e.loc!.file === f.id).map((e) => ({ at: e.range![0], node: cutRow(e) })),
      ...allSpans.filter((sp) => sp.gap && sp.file.id === f.id).map((sp) => ({ at: sp.a, node: gapRow(sp) })),
    ].sort((a, b) => a.at - b.at);
    const tail = allSpans.find((sp) => !sp.gap && sp.file.id === f.id);
    return (
      <div key={f.id} className="space-y-1">
        <button type="button" onClick={() => scrollToFile(f.id)} className="flex w-full items-center gap-1.5 pt-1 text-left text-[11px]" style={{ color: variant === "C" && f.id === activeFile ? "var(--color-accent-2)" : "var(--color-text-3)" }}>
          <FileText className="h-3 w-3" />{f.name}<Pill>{M.KIND_LABEL[f.kind]}</Pill>
        </button>
        {items.map((x) => x.node)}
        {tail && <div className="px-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>之后 {M.charsOf(s, [tail.a, tail.b]).toLocaleString()} 字尚未分集</div>}
      </div>
    );
  });

  const rail = cand && sum ? (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="display-serif text-[15px] font-semibold">新的分集方案</h3>
        <Pill tone="accent">从{M.label(s, cand.fromEp)}起</Pill>
      </div>
      <div className="rounded-md p-2.5" style={{ background: "oklch(0.21 0.01 265 / 0.6)", border: "1px solid var(--color-hairline-soft)" }}>
        <CandidateSummaryBlock p={p} />
      </div>
      {cand.status === "generating" && <ProgressBar value={(cand.reached - cand.start) / Math.max(1, lay.len - cand.start)} />}
      <p className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
        左侧色条是现有分集，右侧是新方案；<span style={{ color: DIFF }}>琥珀色虚线</span>标出分界不同的位置。采纳之前，现有分集不会变化。
      </p>
      <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>新方案的集</div>
      <div className="space-y-1">
        {cand.eps.map((c, i) => (
          <div key={candIds[i]} className="rounded px-2 py-1.5 text-[12px]" style={{ borderLeft: `3px dashed ${epColor(candIds[i])}`, background: "oklch(0.21 0.01 265 / 0.5)" }}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="num" style={{ color: epColor(candIds[i]) }}>{M.charsOf(s, c.range).toLocaleString()} 字</span>
              <span>{c.title}</span>
              <span className="text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{M.fileAt(s, c.range[0]).name}</span>
            </div>
            <div className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }}>钩子：{c.hook}</div>
          </div>
        ))}
        {cand.status === "generating" && <div className="px-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>之后的集等待规划…</div>}
      </div>
      <CandidateActions p={p} />
    </div>
  ) : (
    <div className="space-y-4">
      <ExternalBanners p={p} onReview={askImpact} />
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="display-serif text-[15px] font-semibold">分集</h3>
          <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>{s.episodes.length} 集</span>
          <div className="flex-1" />
          <SecondaryButton size="sm" leadingIcon={<Upload className="h-3.5 w-3.5" />} onClick={() => setUploadOpen(true)}>上传原文</SecondaryButton>
          <SecondaryButton size="sm" leadingIcon={<Plus className="h-3.5 w-3.5" />} onClick={() => setNewEp({ after: null })}>新建一集</SecondaryButton>
        </div>
        {variant === "B" ? (
          <FilesPanelB p={p} ops={fileOps} disabled={opsDisabled} locked={lockedFiles} onJump={scrollToFile} />
        ) : (
          <div className="flex items-center gap-2 text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
            整本源文 · {s.files.length} 个文件 · 已分集 {plannedChars.toLocaleString()} / {total.toLocaleString()} 字
            <div className="flex-1" />
            {variant === "C" && <SecondaryButton size="sm" disabled={opsDisabled} leadingIcon={<Settings2 className="h-3.5 w-3.5" />} onClick={() => setManageOpen(true)}>管理源文件</SecondaryButton>}
          </div>
        )}
        {s.planning ? (
          <div className="space-y-1.5 rounded-md px-2.5 py-2 text-[12px]" style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)" }}>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 animate-pulse rounded-full" style={{ background: "var(--color-accent)" }} />
              {s.planning.gap ? "AI 正在规划未切分的原文" : "AI 正在规划"} · 已完成 {pct(s.planning.pos - s.planning.from, s.planning.until - s.planning.from)}%
              <div className="flex-1" />
              <SecondaryButton size="sm" leadingIcon={<Square className="h-3 w-3" />} onClick={p.stopPlanning}>停止</SecondaryButton>
            </div>
            <ProgressBar value={(s.planning.pos - s.planning.from) / Math.max(1, s.planning.until - s.planning.from)} />
            <div className="text-[11px]" style={{ color: "var(--color-text-3)" }}>新分出的集会陆续出现在列表中。停止后，已分出的集会保留。</div>
          </div>
        ) : firstTail ? (
          <>
            <textarea className={INPUT_CLS} rows={2} placeholder="附加要求（可选）" value={instr} onChange={(e) => setInstr(e.target.value)} />
            <PlanModePicker batch={batch} onChange={setBatch} />
            <PrimaryButton size="sm" disabled={!editing || tailLocked} onClick={() => { cancel(); p.beginPlanning(instr, batch); }}>{cuts.length ? "AI 规划剩余内容" : "AI 规划分集"}</PrimaryButton>
            <div className="text-[11px]" style={{ color: "var(--color-text-4)" }}>从最后一个切出的集之后（{firstTail.file.name}）接着规划到源文结尾；中间未切分的原文用「规划这段」单独规划。</div>
          </>
        ) : (
          <div className="text-[12px]" style={{ color: "oklch(0.8 0.12 150)" }}>整本源文末尾已全部分集</div>
        )}
      </div>

      <div className="space-y-2">
        <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>来自整本源文</div>
        {cutList}
      </div>

      <div className="space-y-1">
        <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>其他集</div>
        {others.map((e) => (
          <div key={e.id} className="rounded-md" style={{ background: selected === e.id ? "var(--color-accent-dim)" : undefined, ...(e.fresh ? FRESH_STYLE : {}) }}>
            <button type="button" onClick={() => setSelected(e.id)} className="flex w-full flex-wrap items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-[oklch(0.26_0.012_265/0.5)]">
              <span className="num" style={{ color: "var(--color-text-2)" }}>{M.label(s, e.id)}</span>
              <span>{e.title}</span>
              <OriginPill ep={e} />
              {e.origin === "own" && e.ownKind && <Pill>{M.KIND_LABEL[e.ownKind]}</Pill>}
              <EpPills ep={e} withOrigin={false} />
            </button>
            {selected === e.id && <div className="flex flex-wrap gap-1.5 px-2 pb-2">{commonActions(e)}</div>}
          </div>
        ))}
      </div>
    </div>
  );

  // ---------------------------------------------------------------- C：文件标签
  const tabsC = variant === "C" ? (
    <div className="flex items-end gap-1 overflow-x-auto">
      {lay.files.map((f) => {
        const cov = coverage(f.id);
        const on = f.id === activeFile;
        return (
          <button key={f.id} type="button" onClick={() => scrollToFile(f.id)}
            className="min-w-[150px] rounded-t-md px-3 pb-1.5 pt-1.5 text-left"
            style={{ background: on ? "oklch(0.24 0.012 265)" : "transparent", borderBottom: on ? "2px solid var(--color-accent)" : "2px solid transparent" }}>
            <div className="flex items-center gap-1.5 text-[12px]" style={{ color: on ? "var(--color-text)" : "var(--color-text-3)" }}>
              <span className="max-w-[160px] truncate">{f.name}</span>
              <Pill>{M.KIND_LABEL[f.kind]}</Pill>
              {lockedFiles.has(f.id) && <Pill tone="warn">外部已修改</Pill>}
            </div>
            <div className="mt-1 h-[3px] rounded-full" style={{ background: "var(--color-hairline)" }}>
              <div className="h-full rounded-full" style={{ width: `${pct(cov.cut, cov.total)}%`, background: "var(--color-accent)" }} />
            </div>
          </button>
        );
      })}
      <div className="flex-1" />
      <SecondaryButton size="sm" disabled={opsDisabled || lockedFiles.has(activeFile)} leadingIcon={<Pencil className="h-3 w-3" />} onClick={() => fileOps.edit(activeFile)}>编辑此文件</SecondaryButton>
    </div>
  ) : null;

  const onNext = (r: ImpactReq) => askImpact(r);

  return (
    <div className="flex h-full">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-8 pb-24">
        <div className="sticky top-0 z-10 -mx-8 mb-3 px-8 pt-2.5 text-[11.5px]" style={{ background: "oklch(0.18 0.01 265 / 0.94)", backdropFilter: "blur(8px)", borderBottom: "1px solid var(--color-hairline-soft)" }}>
          <div className="mx-auto max-w-[800px] pb-2" style={{ color: "var(--color-text-4)" }}>
            {tabsC ?? (
              <div className="flex items-center gap-2">
                <span className="display-serif text-[14px]" style={{ color: "var(--color-text-2)" }}>整本源文</span>
                <span>· {s.files.length} 个文件，按顺序连成一整本</span>
                {moveLeft && <><div className="flex-1" /><SecondaryButton size="sm" onClick={cancel}>取消移动</SecondaryButton></>}
              </div>
            )}
          </div>
        </div>
        {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
        <div className="mx-auto max-w-[800px]" onMouseUp={onTextMouseUp}>{left}</div>
      </div>
      <aside className="w-[360px] shrink-0 overflow-y-auto px-4 py-5 pb-24" style={{ borderLeft: "1px solid var(--color-hairline)", background: "oklch(0.18 0.01 265 / 0.5)" }}>
        {rail}
      </aside>
      <UploadDialog p={p} open={uploadOpen} onClose={() => setUploadOpen(false)} />
      <ReplanDialog p={p} fromId={replanFrom} onClose={() => setReplanFrom(null)} />
      <ReplaceDialog p={p} fileId={replaceId} onClose={() => setReplaceId(null)} onNext={onNext} />
      <EditDialog p={p} fileId={editId} onClose={() => setEditId(null)} onNext={onNext} />
      <NewEpisodeDialog p={p} open={!!newEp} afterId={newEp?.after ?? null} onClose={() => setNewEp(null)} />
      {variant === "C" && <ManageFilesDialog p={p} open={manageOpen} onClose={() => setManageOpen(false)} />}
      {confirmNode}
      {impactNode}
    </div>
  );
}

function IconBtn({ children, title, disabled, onClick }: { children: ReactNode; title: string; disabled?: boolean; onClick: () => void }) {
  return (
    <button type="button" title={title} aria-label={title} disabled={disabled} onClick={onClick}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] enabled:hover:bg-[oklch(0.3_0.01_265)] disabled:opacity-35"
      style={{ color: "var(--color-text-3)" }}>
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- B：右栏文件清单

interface FileOps {
  reorder: (order: string[]) => void;
  remove: (id: string) => void;
  kind: (id: string, k: M.Kind) => void;
  replace: (id: string) => void;
  edit: (id: string) => void;
}

function FilesPanelB({ p, ops, disabled, locked, onJump }: { p: Proto; ops: FileOps; disabled: boolean; locked: Set<string>; onJump: (id: string) => void }) {
  const s = p.s;
  const [drag, setDrag] = useState<string | null>(null);
  const [order, setOrder] = useState<string[] | null>(null);
  const ids = order ?? s.files.map((f) => f.id);
  const total = M.totalChars(s);
  const planned = M.cutEps(s).reduce((n, e) => n + M.epChars(s, e), 0);
  return (
    <div className="space-y-1 rounded-md p-2" style={{ background: "oklch(0.21 0.01 265 / 0.5)", border: "1px solid var(--color-hairline-soft)" }}>
      <div className="flex items-center gap-2 px-1 text-[11px]" style={{ color: "var(--color-text-3)" }}>
        整本源文 · {s.files.length} 个文件
        <div className="flex-1" />
        <span className="num" style={{ color: "var(--color-text-4)" }}>已分集 {planned.toLocaleString()} / {total.toLocaleString()} 字</span>
      </div>
      {ids.map((id, i) => {
        const f = s.files.find((x) => x.id === id);
        if (!f) return null;
        const cov = M.fileCoverage(s, id);
        const isLocked = locked.has(id);
        return (
          <div key={id}
            draggable={!disabled}
            onDragStart={() => { setDrag(id); setOrder(s.files.map((x) => x.id)); }}
            onDragOver={(e) => {
              e.preventDefault();
              if (!drag || drag === id) return;
              const next = ids.filter((x) => x !== drag);
              next.splice(i, 0, drag);
              setOrder(next);
            }}
            onDragEnd={() => { const o = order; setDrag(null); setOrder(null); if (o) ops.reorder(o); }}
            className="rounded px-1 py-1"
            style={{ opacity: drag === id ? 0.4 : 1, background: "oklch(0.2 0.01 265 / 0.6)" }}>
            <div className="flex items-center gap-1.5 text-[12px]">
              <GripVertical className="h-3.5 w-3.5 shrink-0 cursor-grab" style={{ color: "var(--color-text-4)" }} />
              <span className="num w-3 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{i + 1}</span>
              <button type="button" className="min-w-0 flex-1 truncate text-left" style={{ color: "var(--color-text)" }} onClick={() => onJump(id)} title={f.name}>{f.name}</button>
              <KindSelect compact value={f.kind} disabled={disabled || isLocked} onChange={(k) => ops.kind(id, k)} />
              <IconBtn title="替换" disabled={disabled || isLocked} onClick={() => ops.replace(id)}><RefreshCw className="h-3 w-3" /></IconBtn>
              <IconBtn title="编辑" disabled={disabled || isLocked} onClick={() => ops.edit(id)}><Pencil className="h-3 w-3" /></IconBtn>
              <IconBtn title="删除" disabled={disabled} onClick={() => ops.remove(id)}><Trash2 className="h-3 w-3" /></IconBtn>
            </div>
            <div className="ml-9 mr-1 flex items-center gap-2">
              <div className="h-[3px] flex-1 rounded-full" style={{ background: "var(--color-hairline)" }}>
                <div className="h-full rounded-full" style={{ width: `${Math.round((cov.cut / Math.max(1, cov.total)) * 100)}%`, background: "var(--color-accent)" }} />
              </div>
              <span className="num text-[10px]" style={{ color: isLocked ? WARN : "var(--color-text-4)" }}>
                {isLocked ? "外部已修改" : `${M.epsInFile(s, id).length} 集 · ${cov.total.toLocaleString()} 字`}
              </span>
            </div>
          </div>
        );
      })}
      <div className="px-1 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>拖动调整顺序；切出的集跟着文件整块移动</div>
    </div>
  );
}

// ---------------------------------------------------------------- C：管理源文件（暂存 → 预览合并影响 → 一次应用）

function ManageFilesDialog({ p, open, onClose }: { p: Proto; open: boolean; onClose: () => void }) {
  const s = p.s;
  const [order, setOrder] = useState<string[] | null>(null);
  const [kinds, setKinds] = useState<Record<string, M.Kind>>({});
  const [deleted, setDeleted] = useState<Set<string>>(new Set());
  const [replaced, setReplaced] = useState<Record<string, string>>({});
  const [added, setAdded] = useState<M.SrcFile[]>([]);
  const [drag, setDrag] = useState<string | null>(null);

  const ids = order ?? s.files.map((f) => f.id);
  const allFiles = [...s.files, ...added];
  const reset = () => { setOrder(null); setKinds({}); setDeleted(new Set()); setReplaced({}); setAdded([]); };
  const close = () => { reset(); onClose(); };

  let next = s;
  if (added.length) next = M.insertFiles(next, added, [...s.files.map((f) => f.id), ...added.map((f) => f.id)]);
  for (const [id, text] of Object.entries(replaced)) next = M.rewriteFile(next, id, text, { why: "替换" });
  for (const id of deleted) next = M.deleteFile(next, id);
  for (const [id, k] of Object.entries(kinds)) next = M.setKind(next, id, k);
  next = M.reorderFiles(next, ids.filter((id) => !deleted.has(id)));
  const dirty = !!(order || added.length || deleted.size || Object.keys(replaced).length || Object.keys(kinds).length);

  return (
    <GlassModal open={open} onClose={close} labelledBy="manage-files-title" widthClassName="w-full max-w-4xl">
      <div className="grid grid-cols-2 gap-5 p-5 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
        <div className="space-y-2">
          <h3 id="manage-files-title" className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>管理源文件</h3>
          <p className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>整本源文按下面的顺序连成一整本。改动先暂存，右侧实时显示对已切出的集的影响，确认后一次应用。</p>
          {ids.map((id, i) => {
            const f = allFiles.find((x) => x.id === id);
            if (!f) return null;
            const isNew = added.some((x) => x.id === id);
            const del = deleted.has(id);
            const rep = replaced[id] != null;
            return (
              <div key={id} draggable onDragStart={() => setDrag(id)} onDragEnd={() => setDrag(null)}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (!drag || drag === id) return;
                  const nx = ids.filter((x) => x !== drag);
                  nx.splice(i, 0, drag);
                  setOrder(nx);
                }}
                className="flex items-center gap-1.5 rounded px-1.5 py-1.5"
                style={{ opacity: drag === id ? 0.4 : 1, background: "oklch(0.21 0.01 265 / 0.6)", border: "1px solid var(--color-hairline-soft)" }}>
                <GripVertical className="h-3.5 w-3.5 shrink-0 cursor-grab" style={{ color: "var(--color-text-4)" }} />
                <span className="num w-3 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>{i + 1}</span>
                <span className={`min-w-0 flex-1 truncate ${del ? "line-through" : ""}`} style={{ color: del ? "var(--color-text-4)" : "var(--color-text)" }} title={f.name}>{f.name}</span>
                {isNew && <Pill tone="accent">新</Pill>}
                {rep && <Pill tone="replan">已替换</Pill>}
                <KindSelect compact value={kinds[id] ?? f.kind} disabled={del} onChange={(k) => setKinds((x) => ({ ...x, [id]: k }))} />
                {!isNew && (
                  <IconBtn title={rep ? "撤销替换" : "替换为示例修订版"} disabled={del} onClick={() => setReplaced((x) => {
                    const y = { ...x };
                    if (rep) delete y[id]; else y[id] = M.revise(f.text, 2);
                    return y;
                  })}><RefreshCw className="h-3 w-3" />{rep ? "撤销" : "替换"}</IconBtn>
                )}
                <IconBtn title={del ? "撤销删除" : "删除"} onClick={() => {
                  if (isNew) { setAdded((x) => x.filter((y) => y.id !== id)); setOrder((o) => o?.filter((y) => y !== id) ?? null); return; }
                  setDeleted((x) => { const y = new Set(x); if (del) y.delete(id); else y.add(id); return y; });
                }}>{del ? "撤销" : <Trash2 className="h-3 w-3" />}</IconBtn>
              </div>
            );
          })}
          <SecondaryButton size="sm" leadingIcon={<Plus className="h-3 w-3" />} onClick={() => {
            const nf: M.SrcFile = { id: M.newFileId(), name: `雨夜码头·卷${["四", "五", "六"][added.length] ?? "七"}.txt`, kind: "novel", text: M.genNovel(4 + added.length, 5, 2) };
            setAdded((x) => [...x, nf]);
            setOrder([...ids, nf.id]);
          }}>添加文件（示例）</SecondaryButton>
        </div>
        <div className="space-y-2">
          <div className="text-[10.5px] uppercase tracking-[0.14em]" style={{ color: "var(--color-text-4)" }}>对已切出的集的影响</div>
          <div className="max-h-[55vh] overflow-y-auto pr-1">
            {dirty ? <ImpactBody prev={s} next={next} showMoves /> : <p className="text-[12px]" style={{ color: "var(--color-text-4)" }}>还没有改动。</p>}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <SecondaryButton size="sm" onClick={close}>取消</SecondaryButton>
            <PrimaryButton size="sm" disabled={!dirty} onClick={() => { p.commit(next); close(); }}>应用更改</PrimaryButton>
          </div>
        </div>
      </div>
    </GlassModal>
  );
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="relative h-1 overflow-hidden rounded-full" style={{ background: "var(--color-hairline)" }}>
      <div className="absolute inset-y-0 left-0" style={{ width: `${Math.min(1, value) * 100}%`, background: "var(--color-accent)" }} />
    </div>
  );
}
