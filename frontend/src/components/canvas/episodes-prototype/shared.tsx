// PROTOTYPE（#2767）：三个变体共用的小件——徽标、确认清单、上传对话框、重新规划对话框、状态面板。
import { useId, useRef, useState, type ReactNode } from "react";
import { GripVertical, X } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS, radioCardClass } from "@/components/ui/darkroom-tokens";
import * as M from "./model";
import type { Proto } from "./useProto";

// ---------------------------------------------------------------- 徽标

export function Pill({ children, tone = "muted", title }: { children: ReactNode; tone?: "muted" | "accent" | "replan" | "ok"; title?: string }) {
  const style: Record<string, React.CSSProperties> = {
    muted: { color: "var(--color-text-3)", border: "1px solid var(--color-hairline)", background: "oklch(0.22 0.01 265 / 0.5)" },
    accent: { color: "var(--color-accent-2)", border: "1px solid var(--color-accent-soft)", background: "var(--color-accent-dim)" },
    // 「原文已重新规划」：虚线青色，与产物过期（琥珀实色）刻意区分
    replan: { color: "oklch(0.82 0.1 200)", border: "1px dashed oklch(0.7 0.1 200 / 0.7)", background: "oklch(0.3 0.05 200 / 0.25)" },
    ok: { color: "oklch(0.8 0.12 150)", border: "1px solid oklch(0.6 0.1 150 / 0.4)", background: "oklch(0.3 0.06 150 / 0.2)" },
  };
  return (
    <span title={title} className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-1.5 py-[1px] text-[10.5px]" style={style[tone]}>
      {children}
    </span>
  );
}

export function OriginPill({ ep }: { ep: M.Ep }) {
  if (ep.origin === "cut") return <Pill>切自整本源文</Pill>;
  if (ep.origin === "own") return <Pill tone="accent" title={ep.ownFile}>自带原文</Pill>;
  return <Pill>无原文</Pill>;
}

export function EpPills({ ep, withOrigin = true }: { ep: M.Ep; withOrigin?: boolean }) {
  return (
    <>
      {withOrigin && <OriginPill ep={ep} />}
      {ep.hasArtifacts && <Pill tone="ok">已开始制作</Pill>}
      {ep.stale && <Pill tone="replan" title="这一集的原文范围已变更，之前生成的内容可能与新原文不一致">原文已重新规划</Pill>}
      {ep.fresh && <Pill tone="accent">新</Pill>}
    </>
  );
}

export const FRESH_STYLE: React.CSSProperties = {
  boxShadow: "0 0 0 1px var(--color-accent-soft), 0 0 22px -8px var(--color-accent-glow)",
};

// ---------------------------------------------------------------- 确认

export interface ConfirmReq {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onOk: () => void;
}

export function useConfirm(): [ReactNode, (r: ConfirmReq) => void] {
  const [req, setReq] = useState<ConfirmReq | null>(null);
  const node = (
    <ConfirmDialog
      open={!!req}
      title={req?.title ?? ""}
      description={req?.body}
      confirmLabel={req?.confirmLabel ?? ""}
      tone={req?.danger ? "danger" : "default"}
      onConfirm={() => { req?.onOk(); setReq(null); }}
      onCancel={() => setReq(null)}
    />
  );
  return [node, setReq];
}

export function AffectedList({ eps, note }: { eps: M.Ep[]; note?: string }) {
  return (
    <div className="space-y-1.5 text-[12px]" style={{ color: "var(--color-text-2)" }}>
      <ul className="space-y-0.5">
        {eps.map((e) => (
          <li key={e.id} className="flex items-center gap-2">
            <span className="num">第 {e.id} 集</span> {e.title}
            {e.hasArtifacts && <Pill tone="ok">已开始制作</Pill>}
          </li>
        ))}
      </ul>
      {note && <p style={{ color: "var(--color-text-3)" }}>{note}</p>}
    </div>
  );
}

/** 手工操作波及有产物的集时，先出确认清单再写账本 */
export function guardArtifacts(ask: (r: ConfirmReq) => void, touched: M.Ep[], title: string, run: () => void) {
  const hit = touched.filter((e) => e.hasArtifacts);
  if (hit.length === 0) { run(); return; }
  ask({
    title,
    body: <AffectedList eps={hit} note="以下集已经生成过内容。修改后会标记为「原文已重新规划」，已生成的内容会保留，你可以之后再决定是否删除。" />,
    confirmLabel: "仍然修改",
    danger: true,
    onOk: run,
  });
}

// ---------------------------------------------------------------- 上传对话框

interface PendingFile { key: string; name: string; chars: number }

const SAMPLE_FILES: PendingFile[] = [
  { key: "a", name: "第10话.txt", chars: 1830 },
  { key: "b", name: "第2话.txt", chars: 2410 },
  { key: "c", name: "第1话.txt", chars: 2020 },
];

export function UploadDialog({ p, open, onClose }: { p: Proto; open: boolean; onClose: () => void }) {
  const titleId = useId();
  const [mode, setMode] = useState<"whole" | "per">("per");
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const base = M.maxId(p.s) + 1;
  const hasCuts = M.cutEps(p.s).length > 0;

  const addFiles = (list: PendingFile[]) =>
    setFiles((prev) => [...prev, ...list.sort((a, b) => a.name.localeCompare(b.name, "zh", { numeric: true }))]);

  const onDrop = (overKey: string) => {
    if (!dragKey || dragKey === overKey) return;
    setFiles((prev) => {
      const from = prev.findIndex((f) => f.key === dragKey);
      const to = prev.findIndex((f) => f.key === overKey);
      const next = [...prev];
      const [x] = next.splice(from, 1);
      next.splice(to, 0, x);
      return next;
    });
  };

  const close = () => { setFiles([]); onClose(); };

  return (
    <GlassModal open={open} onClose={close} labelledBy={titleId} widthClassName="w-full max-w-xl">
      <div className="space-y-4 p-5">
        <h3 id={titleId} className="display-serif text-[16px] font-semibold">上传原文</h3>
        <div className="flex gap-2">
          <label className={radioCardClass(mode === "whole")}>
            <input type="radio" className="sr-only" checked={mode === "whole"} onChange={() => setMode("whole")} />
            <div className="font-medium">整本源文</div>
            <div className="mt-0.5 text-[11px]" style={{ color: "var(--color-text-3)" }}>一份完整小说，之后由分集规划切出集</div>
          </label>
          <label className={radioCardClass(mode === "per")}>
            <input type="radio" className="sr-only" checked={mode === "per"} onChange={() => setMode("per")} />
            <div className="font-medium">逐集原文</div>
            <div className="mt-0.5 text-[11px]" style={{ color: "var(--color-text-3)" }}>每个文件就是一集的原文，可多次追加</div>
          </label>
        </div>

        {mode === "whole" ? (
          <div className="space-y-2 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
            <div>当前整本源文：<span className="num">{M.SOURCE_FILE_NAME}</span>（{M.TOTAL_CHARS.toLocaleString()} 字）</div>
            {hasCuts && (
              <div className="rounded-md px-3 py-2 text-[12px]" style={{ border: "1px dashed oklch(0.7 0.1 200 / 0.6)", color: "oklch(0.85 0.08 200)" }}>
                替换后，已切出的 {M.cutEps(p.s).length} 集要从第 1 集起重新规划；自带原文和无原文的集不受影响。
              </div>
            )}
            <SecondaryButton size="sm" onClick={() => { p.replaceSource(); close(); }}>选择文件并替换（模拟）</SecondaryButton>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <input
                ref={inputRef} type="file" multiple className="hidden"
                onChange={(e) => {
                  addFiles(Array.from(e.target.files ?? []).map((f, i) => ({ key: `${f.name}-${Date.now()}-${i}`, name: f.name, chars: Math.round(f.size / 3) })));
                  e.target.value = "";
                }}
              />
              <SecondaryButton size="sm" onClick={() => inputRef.current?.click()}>选择文件</SecondaryButton>
              <SecondaryButton size="sm" onClick={() => addFiles(SAMPLE_FILES.map((f) => ({ ...f, key: `${f.key}-${Date.now()}` })))}>添加示例文件</SecondaryButton>
              <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>集号从第 {base} 集往后接续，按列表顺序分配；拖动可调整顺序</span>
            </div>
            {files.length > 0 && (
              <table className="w-full text-[12.5px]">
                <thead>
                  <tr style={{ color: "var(--color-text-4)" }} className="text-left text-[11px]">
                    <th className="w-6" /><th className="py-1">文件</th><th>将成为</th><th className="w-6" />
                  </tr>
                </thead>
                <tbody>
                  {files.map((f, i) => (
                    <tr
                      key={f.key}
                      draggable
                      onDragStart={() => setDragKey(f.key)}
                      onDragOver={(e) => { e.preventDefault(); onDrop(f.key); }}
                      onDragEnd={() => setDragKey(null)}
                      style={{ opacity: dragKey === f.key ? 0.4 : 1, borderTop: "1px solid var(--color-hairline-soft)" }}
                    >
                      <td className="cursor-grab py-1.5" style={{ color: "var(--color-text-4)" }}><GripVertical className="h-3.5 w-3.5" /></td>
                      <td>{f.name}<span className="ml-2 num text-[11px]" style={{ color: "var(--color-text-4)" }}>{f.chars} 字</span></td>
                      <td className="num" style={{ color: "var(--color-accent-2)" }}>→ 第 {base + i} 集</td>
                      <td>
                        <button type="button" onClick={() => setFiles((prev) => prev.filter((x) => x.key !== f.key))}><X className="h-3.5 w-3.5" /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={close}>取消</SecondaryButton>
          {mode === "per" && (
            <PrimaryButton size="sm" disabled={files.length === 0} onClick={() => { p.addOwnEpisodes(files.map(({ name, chars }) => ({ name, chars }))); close(); }}>
              {files.length === 0 ? "添加" : files.length === 1 ? `添加为第 ${base} 集` : `添加为第 ${base}–${base + files.length - 1} 集`}
            </PrimaryButton>
          )}
        </div>
      </div>
    </GlassModal>
  );
}

// ---------------------------------------------------------------- 重新规划对话框

export function ReplanDialog({ p, fromId, onClose }: { p: Proto; fromId: number | null; onClose: () => void }) {
  const titleId = useId();
  const [instr, setInstr] = useState("");
  const blockers = fromId != null ? M.replanBlockers(p.s, fromId) : [];
  const first = M.cutEps(p.s)[0];
  const sourceBlocked = p.s.sourceReplaced && fromId !== first?.id;
  const replaced = fromId != null ? M.cutEps(p.s).filter((e) => e.range![0] >= M.replanStart(p.s, fromId)) : [];
  return (
    <GlassModal open={fromId != null} onClose={onClose} labelledBy={titleId} widthClassName="w-full max-w-lg">
      <div className="space-y-3 p-5 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
        <h3 id={titleId} className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>从第 {fromId} 集起重新规划</h3>
        <p>AI 会先生成一份新的分集方案供你预览。在你确认采纳之前，现有分集不会有任何变化。</p>
        <p>
          将重新规划第 {replaced.map((e) => e.id).join("、")} 集
          {replaced.some((e) => e.hasArtifacts) ? `，其中第 ${replaced.filter((e) => e.hasArtifacts).map((e) => e.id).join("、")} 集已开始制作。` : "。"}
        </p>
        {blockers.length > 0 && (
          <p style={{ color: "oklch(0.8 0.14 60)" }}>{blockers.map((e) => `第 ${e.id} 集`).join("、")}的原文不来自整本源文，且位于重新规划的范围内，因此无法从这一集开始重新规划。</p>
        )}
        {sourceBlocked && <p style={{ color: "oklch(0.8 0.14 60)" }}>整本源文已更换，请从第 1 集开始重新规划。</p>}
        <textarea className={INPUT_CLS} rows={3} placeholder="附加要求（可选），例如：节奏再快一些，每集结尾留悬念" value={instr} onChange={(e) => setInstr(e.target.value)} />
        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={onClose}>取消</SecondaryButton>
          <PrimaryButton size="sm" disabled={blockers.length > 0 || sourceBlocked || !!p.s.candidate} onClick={() => { if (fromId != null) p.beginReplan(fromId, instr); onClose(); }}>开始规划</PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}

// ---------------------------------------------------------------- 候选的变更摘要（各变体都可复用）

function Row({ label, value, warn }: { label: string; value: ReactNode; warn?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="w-[72px] shrink-0" style={{ color: "var(--color-text-4)" }}>{label}</span>
      <span style={{ color: warn ? "oklch(0.85 0.08 200)" : "var(--color-text-2)" }}>{value}</span>
    </div>
  );
}

export function CandidateSummaryBlock({ p }: { p: Proto }) {
  const sum = M.candidateSummary(p.s);
  const c = p.s.candidate;
  if (!sum || !c) return null;
  const oldChars = sum.replaced.reduce((n, e) => n + M.epChars(e), 0);
  const newChars = c.eps.reduce((n, e) => n + M.charsOf(e.range), 0);
  const avg = (n: number, k: number) => (k ? Math.round(n / k).toLocaleString() : "—");
  return (
    <div className="space-y-1 text-[12px]">
      <Row label="集数" value={`${sum.replaced.length} 集 → ${sum.newCount} 集${c.status === "generating" ? "（规划中）" : ""}`} />
      <Row label="平均每集" value={`${avg(oldChars, sum.replaced.length)} 字 → ${avg(newChars, sum.newCount)} 字`} />
      <Row label="覆盖范围" value={`第 ${sum.replaced[0]?.id ?? "—"} 集起至${c.reached >= M.SOURCE_LEN ? "源文结尾" : `源文 ${Math.round((c.reached / M.SOURCE_LEN) * 100)}% 处`}`} />
      {sum.staleIds.length > 0 && <Row warn label="需要复核" value={`第 ${sum.staleIds.join("、")} 集已开始制作，原文范围有变化，将标记「原文已重新规划」`} />}
      {/* 规划中尚未覆盖到的集还没有结论，停止或完成后才给出去向 */}
      {c.status !== "generating" && sum.toNone.length > 0 && <Row warn label="保留为无原文" value={`第 ${sum.toNone.join("、")} 集在新方案中没有对应原文；已生成的内容会保留，这些集转为无原文的集`} />}
      {c.status !== "generating" && sum.removed.length > 0 && <Row label="将移除" value={`第 ${sum.removed.join("、")} 集（尚未开始制作）`} />}
      {c.instructions && <Row label="附加要求" value={c.instructions} />}
      {c.status === "stopped" && !sum.adoptable && (
        <div style={{ color: "oklch(0.8 0.14 60)" }}>新方案在中途停止，最后一集的结尾与现有分集的边界对不上，因此无法采纳。请放弃后重新规划。</div>
      )}
    </div>
  );
}

export function CandidateActions({ p }: { p: Proto }) {
  const sum = M.candidateSummary(p.s);
  const c = p.s.candidate;
  if (!c || !sum) return null;
  return (
    <div className="flex items-center gap-2">
      {c.status === "generating" ? (
        <SecondaryButton size="sm" onClick={p.stopCandidate}>停止</SecondaryButton>
      ) : (
        <>
          <SecondaryButton size="sm" onClick={p.discardCandidate}>放弃新方案</SecondaryButton>
          <PrimaryButton size="sm" disabled={!sum.adoptable} onClick={p.adoptCandidate}>采纳新方案</PrimaryButton>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 状态面板（原型规则：每次操作后可见完整状态）

export function StateInspector({ p }: { p: Proto }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="fixed bottom-16 left-4 z-[99] w-[340px] text-[11px]">
      <button type="button" onClick={() => setOpen(!open)} className="block rounded-full px-3 py-1" style={{ background: "oklch(0.95 0.12 95)", color: "oklch(0.2 0 0)" }}>
        {open ? "收起账本" : "账本状态"}
      </button>
      {open && (
        <div className="mt-2 max-h-[50vh] overflow-auto rounded-lg p-3 font-mono" style={{ background: "oklch(0.12 0 0 / 0.95)", color: "oklch(0.85 0 0)" }}>
          <div>cursor: 字 {p.s.cursor} / {M.SOURCE_LEN} · planning: {p.s.planning ? `${p.s.planning.mode} #${p.s.planning.batchesDone}` : "—"}</div>
          <div>candidate: {p.s.candidate ? `${p.s.candidate.status} from ${p.s.candidate.fromEp}, ${p.s.candidate.eps.length} 集` : "—"} · sourceReplaced: {String(p.s.sourceReplaced)}</div>
          <table className="mt-2 w-full">
            <tbody>
              {M.byId(p.s).map((e) => (
                <tr key={e.id}>
                  <td>#{e.id}</td><td>{e.origin}</td><td>{e.range ? `${e.range[0]}–${e.range[1]}` : "—"}</td>
                  <td>{e.hasArtifacts ? "产物" : ""}</td><td>{e.stale ? "stale" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 space-y-0.5" style={{ color: "oklch(0.65 0 0)" }}>
            {p.s.log.map((l, i) => <div key={i}>· {l}</div>)}
          </div>
          <button type="button" className="mt-2 underline" onClick={p.reset}>重置原型数据</button>
        </div>
      )}
    </div>
  );
}
