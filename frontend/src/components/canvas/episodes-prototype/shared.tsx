// PROTOTYPE（#2831）：三个变体共用的小件——徽标、影响清单确认、上传 / 替换 / 编辑对话框、外部改动提示、重新规划、状态面板。
import { useId, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, GripVertical, Lock, X } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS, radioCardClass } from "@/components/ui/darkroom-tokens";
import * as M from "./model";
import type { Proto } from "./useProto";

export const epHue = (id: number) => (id * 67) % 360;
export const epColor = (id: number, a = 1) => `oklch(0.68 0.11 ${epHue(id)} / ${a})`;
export const WARN = "oklch(0.8 0.14 60)";
export const REPLAN = "oklch(0.85 0.08 200)";

// ---------------------------------------------------------------- 徽标

export function Pill({ children, tone = "muted", title }: { children: ReactNode; tone?: "muted" | "accent" | "replan" | "ok" | "warn"; title?: string }) {
  const style: Record<string, React.CSSProperties> = {
    muted: { color: "var(--color-text-3)", border: "1px solid var(--color-hairline)", background: "oklch(0.22 0.01 265 / 0.5)" },
    accent: { color: "var(--color-accent-2)", border: "1px solid var(--color-accent-soft)", background: "var(--color-accent-dim)" },
    replan: { color: "oklch(0.82 0.1 200)", border: "1px dashed oklch(0.7 0.1 200 / 0.7)", background: "oklch(0.3 0.05 200 / 0.25)" },
    ok: { color: "oklch(0.8 0.12 150)", border: "1px solid oklch(0.6 0.1 150 / 0.4)", background: "oklch(0.3 0.06 150 / 0.2)" },
    warn: { color: WARN, border: `1px solid oklch(0.7 0.12 60 / 0.5)`, background: "oklch(0.3 0.06 60 / 0.25)" },
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
      {ep.stale && <Pill tone="replan" title="这一集的原文已变更，之前生成的内容可能与新原文不一致">原文已重新规划</Pill>}
      {ep.fresh && <Pill tone="accent">新</Pill>}
    </>
  );
}

export const FRESH_STYLE: React.CSSProperties = {
  boxShadow: "0 0 0 1px var(--color-accent-soft), 0 0 22px -8px var(--color-accent-glow)",
};

export function KindSelect({ value, onChange, disabled, compact }: { value: M.Kind; onChange: (k: M.Kind) => void; disabled?: boolean; compact?: boolean }) {
  return (
    <select
      aria-label="源文件类型"
      disabled={disabled}
      value={value}
      onChange={(e) => onChange(e.target.value as M.Kind)}
      onClick={(e) => e.stopPropagation()}
      className={`rounded bg-transparent outline-none ${compact ? "px-1 py-0 text-[11px]" : "px-1.5 py-0.5 text-[12px]"}`}
      style={{ border: "1px solid var(--color-hairline-strong)", color: "var(--color-text-2)" }}
    >
      <option value="novel">小说</option>
      <option value="script">剧本</option>
    </select>
  );
}

// ---------------------------------------------------------------- 简单确认

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

export function AffectedList({ s, eps, note }: { s: M.ProtoState; eps: M.Ep[]; note?: string }) {
  return (
    <div className="space-y-1.5 text-[12px]" style={{ color: "var(--color-text-2)" }}>
      <ul className="space-y-0.5">
        {eps.map((e) => (
          <li key={e.id} className="flex items-center gap-2">
            <span className="num">{M.label(s, e.id)}</span> {e.title}
            {e.hasArtifacts && <Pill tone="ok">已开始制作</Pill>}
          </li>
        ))}
      </ul>
      {note && <p style={{ color: "var(--color-text-3)" }}>{note}</p>}
    </div>
  );
}

export function guardArtifacts(s: M.ProtoState, ask: (r: ConfirmReq) => void, touched: M.Ep[], title: string, run: () => void) {
  const hit = touched.filter((e) => e.hasArtifacts);
  if (hit.length === 0) { run(); return; }
  ask({
    title,
    body: <AffectedList s={s} eps={hit} note="以下集已经生成过内容。修改后会标记为「原文已重新规划」，已生成的内容会保留，你可以之后再决定是否删除。" />,
    confirmLabel: "仍然修改",
    danger: true,
    onOk: run,
  });
}

// ---------------------------------------------------------------- 影响清单（替换 / 编辑 / 删除 / 调序 / 改类型 / 外部改动）

function Group({ title, note, tone, children }: { title: string; note: string; tone?: "warn" | "replan"; children: ReactNode }) {
  const color = tone === "warn" ? WARN : tone === "replan" ? REPLAN : "var(--color-text-2)";
  return (
    <div className="rounded-md px-2.5 py-2" style={{ background: "oklch(0.21 0.01 265 / 0.6)", border: "1px solid var(--color-hairline-soft)" }}>
      <div className="text-[12px] font-medium" style={{ color }}>{title}</div>
      <div className="mb-1 text-[11px]" style={{ color: "var(--color-text-4)" }}>{note}</div>
      <ul className="space-y-0.5 text-[12px]" style={{ color: "var(--color-text-2)" }}>{children}</ul>
    </div>
  );
}

function EpLi({ s, e, extra }: { s: M.ProtoState; e: M.Ep; extra?: ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5">
      <span className="h-2 w-2 rounded-full" style={{ background: epColor(e.id) }} />
      <span className="num">{M.label(s, e.id)}</span>
      <span>{e.title}</span>
      {extra}
    </li>
  );
}

export function ImpactBody({ prev, next, showMoves }: { prev: M.ProtoState; next: M.ProtoState; showMoves?: boolean }) {
  const im = M.impact(prev, next);
  const moved = showMoves ? im.moved : [];
  if (M.impactEmpty(im) && moved.length === 0) {
    return <p className="text-[12px]" style={{ color: "var(--color-text-3)" }}>不影响任何已切出的集。</p>;
  }
  return (
    <div className="space-y-2">
      {moved.length > 0 && (
        <Group title={`播出顺序变化 · ${moved.length} 集`} note="切出的集跟着文件整块移动，原文和已生成的内容都不变。">
          {moved.map((m) => <EpLi key={m.ep.id} s={prev} e={m.ep} extra={<span className="num" style={{ color: "var(--color-accent-2)" }}>→ 第 {m.to} 集</span>} />)}
        </Group>
      )}
      {im.changedStale.length > 0 && (
        <Group tone="replan" title={`原文有变化 · ${im.changedStale.length} 集已开始制作`} note="会标记「原文已重新规划」，已生成的内容保留，是否重做由你决定。">
          {im.changedStale.map((e) => <EpLi key={e.id} s={prev} e={e} />)}
        </Group>
      )}
      {im.retired.length > 0 && (
        <Group tone="warn" title={`原文已不存在 · ${im.retired.length} 集已开始制作`} note="转为无原文的集并移到最后，已生成的内容保留。">
          {im.retired.map((e) => <EpLi key={e.id} s={prev} e={e} />)}
        </Group>
      )}
      {im.removed.length > 0 && (
        <Group tone="warn" title={`原文已不存在 · ${im.removed.length} 集将删除`} note="这些集还没有开始制作。">
          {im.removed.map((e) => <EpLi key={e.id} s={prev} e={e} />)}
        </Group>
      )}
      {im.changedPlain.length > 0 && (
        <Group title={`原文有变化 · ${im.changedPlain.length} 集尚未制作`} note="范围随新原文更新，不需要处理。">
          {im.changedPlain.map((e) => <EpLi key={e.id} s={prev} e={e} />)}
        </Group>
      )}
      {im.kindStale.length > 0 && (
        <Group tone="replan" title={`源文件类型改变 · ${im.kindStale.length} 集`} note="这些集的脚本规划是按旧类型写的，会标为过期；正式脚本不受影响，是否重新规划由你决定。">
          {im.kindStale.map((e) => <EpLi key={e.id} s={prev} e={e} />)}
        </Group>
      )}
      {im.shifted.length > 0 && (
        <Group title={`原文未变 · ${im.shifted.length} 集只调整位置`} note="文字没有变化，已生成的内容不受影响。">
          {im.shifted.map((e) => <EpLi key={e.id} s={prev} e={e} />)}
        </Group>
      )}
    </div>
  );
}

export interface ImpactReq {
  title: string;
  intro?: ReactNode;
  next: M.ProtoState;
  confirmLabel: string;
  showMoves?: boolean;
}

export function useImpact(p: Proto): [ReactNode, (r: ImpactReq) => void] {
  const [req, setReq] = useState<(ImpactReq & { prev: M.ProtoState }) | null>(null);
  const titleId = useId();
  const node = (
    <GlassModal open={!!req} onClose={() => setReq(null)} labelledBy={titleId} widthClassName="w-full max-w-lg">
      {req && (
        <div className="space-y-3 p-5 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
          <h3 id={titleId} className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>{req.title}</h3>
          {req.intro && <div>{req.intro}</div>}
          <div className="max-h-[50vh] overflow-y-auto pr-1">
            <ImpactBody prev={req.prev} next={req.next} showMoves={req.showMoves} />
          </div>
          <div className="flex justify-end gap-2">
            <SecondaryButton size="sm" onClick={() => setReq(null)}>取消</SecondaryButton>
            <PrimaryButton size="sm" onClick={() => { p.commit(req.next); setReq(null); }}>{req.confirmLabel}</PrimaryButton>
          </div>
        </div>
      )}
    </GlassModal>
  );
  return [node, (r) => setReq({ ...r, prev: p.s })];
}

// ---------------------------------------------------------------- 上传对话框

interface Pending { key: string; name: string; text: string; kind: M.Kind; existing?: boolean }

const SAMPLE_WHOLE = () => [
  { name: "雨夜码头·卷四.txt", text: M.genNovel(4, 5, 2) },
  { name: "雨夜码头·卷二（补遗）.txt", text: M.genNovel(6, 7, 1, 30) },
];
const SAMPLE_PER = () => [
  { name: "第10话.txt", text: M.genNovel(7, 0, 1, 70) },
  { name: "第2话.txt", text: M.genNovel(8, 1, 1, 80) },
  { name: "第1话.txt", text: M.genNovel(9, 2, 1, 75) },
];

export function UploadDialog({ p, open, onClose }: { p: Proto; open: boolean; onClose: () => void }) {
  const titleId = useId();
  const [mode, setMode] = useState<"whole" | "per">("whole");
  const [kind, setKind] = useState<M.Kind>("novel");
  const [added, setAdded] = useState<Pending[]>([]);
  const [order, setOrder] = useState<string[] | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const existing: Pending[] = p.s.files.map((f) => ({ key: f.id, name: f.name, text: f.text, kind: f.kind, existing: true }));
  const allByKey = new Map([...existing, ...added].map((x) => [x.key, x]));
  const rows: Pending[] = mode === "whole"
    ? (order ?? [...existing.map((x) => x.key), ...added.map((x) => x.key)]).map((k) => allByKey.get(k)!).filter(Boolean)
    : added;

  const addFiles = (list: { name: string; text: string }[]) => {
    const sorted = [...list].sort((a, b) => a.name.localeCompare(b.name, "zh", { numeric: true }));
    const items = sorted.map((f, i) => ({ key: M.newFileId() + i, name: f.name, text: f.text, kind }));
    setAdded((prev) => [...prev, ...items]);
    // 新上传默认接在末尾
    setOrder((prev) => (prev ? [...prev, ...items.map((x) => x.key)] : null));
  };

  const onDragOver = (overKey: string) => {
    if (!dragKey || dragKey === overKey) return;
    const keys = rows.map((r) => r.key);
    const from = keys.indexOf(dragKey);
    const to = keys.indexOf(overKey);
    keys.splice(from, 1);
    keys.splice(to, 0, dragKey);
    if (mode === "whole") setOrder(keys);
    else setAdded(keys.map((k) => added.find((x) => x.key === k)!));
  };

  const [after, setAfter] = useState<number | null>(null);
  const close = () => { setAdded([]); setOrder(null); setAfter(null); onClose(); };
  const base = after == null ? p.s.episodes.length + 1 : M.posOf(p.s, after) + 1;
  const setRowKind = (key: string, k: M.Kind) => setAdded((prev) => prev.map((x) => (x.key === key ? { ...x, kind: k } : x)));

  const submit = () => {
    if (mode === "whole") {
      const files: M.SrcFile[] = added.map((x) => ({ id: x.key, name: x.name, kind: x.kind, text: x.text }));
      p.commit(M.insertFiles(p.s, files, rows.map((r) => r.key)));
    } else {
      p.addOwnEpisodes(added.map((x) => ({ name: x.name, chars: x.text.replace(/\n/g, "").length, kind: x.kind })), after);
    }
    close();
  };

  return (
    <GlassModal open={open} onClose={close} labelledBy={titleId} widthClassName="w-full max-w-xl">
      <div className="space-y-4 p-5">
        <h3 id={titleId} className="display-serif text-[16px] font-semibold">上传原文</h3>
        <div className="flex gap-2">
          <label className={radioCardClass(mode === "whole")}>
            <input type="radio" className="sr-only" checked={mode === "whole"} onChange={() => { setMode("whole"); setAdded([]); setOrder(null); }} />
            <div className="font-medium">整本源文</div>
            <div className="mt-0.5 text-[11px]" style={{ color: "var(--color-text-3)" }}>可以分成多个文件，按顺序连成一整本，由分集规划切出集</div>
          </label>
          <label className={radioCardClass(mode === "per")}>
            <input type="radio" className="sr-only" checked={mode === "per"} onChange={() => { setMode("per"); setAdded([]); setOrder(null); }} />
            <div className="font-medium">逐集原文</div>
            <div className="mt-0.5 text-[11px]" style={{ color: "var(--color-text-3)" }}>每个文件就是一集的原文</div>
          </label>
        </div>

        <div className="flex items-center gap-2 text-[12px]" style={{ color: "var(--color-text-3)" }}>
          源文件类型
          {(["novel", "script"] as const).map((k) => (
            <label key={k} className={radioCardClass(kind === k)} style={{ padding: "2px 10px" }}>
              <input type="radio" className="sr-only" checked={kind === k} onChange={() => { setKind(k); setAdded((prev) => prev.map((x) => ({ ...x, kind: k }))); }} />
              {M.KIND_LABEL[k]}
            </label>
          ))}
          <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>对本次添加的文件生效，可以逐个修改</span>
        </div>

        <div className="flex items-center gap-2">
          <input
            ref={inputRef} type="file" multiple className="hidden"
            onChange={(e) => {
              const list = Array.from(e.target.files ?? []);
              void Promise.all(list.map(async (f) => ({ name: f.name, text: await f.text() }))).then(addFiles);
              e.target.value = "";
            }}
          />
          <SecondaryButton size="sm" onClick={() => inputRef.current?.click()}>选择文件（可多选）</SecondaryButton>
          <SecondaryButton size="sm" onClick={() => addFiles(mode === "whole" ? SAMPLE_WHOLE() : SAMPLE_PER())}>添加示例文件</SecondaryButton>
        </div>

        {rows.length > 0 && (
          <div>
            <div className="mb-1.5 text-[11px]" style={{ color: "var(--color-text-4)" }}>
              {mode === "whole"
                ? "整本源文按下面的顺序连成一整本。新文件默认接在末尾，拖动可以放到任意位置；新文件是未切分的原文，放在哪里都不影响已切出的集。"
                : <span className="inline-flex flex-wrap items-center gap-1.5">按列表顺序放在 <PositionSelect s={p.s} value={after} onChange={setAfter} />，拖动可调整顺序，之后的集顺延。</span>}
            </div>
            <table className="w-full text-[12.5px]">
              <tbody>
                {rows.map((f, i) => (
                  <tr
                    key={f.key}
                    draggable={!f.existing}
                    onDragStart={() => setDragKey(f.key)}
                    onDragOver={(e) => { e.preventDefault(); onDragOver(f.key); }}
                    onDragEnd={() => setDragKey(null)}
                    style={{ opacity: dragKey === f.key ? 0.4 : f.existing ? 0.6 : 1, borderTop: "1px solid var(--color-hairline-soft)" }}
                  >
                    <td className="w-6 py-1.5" style={{ color: "var(--color-text-4)" }}>
                      {f.existing ? <Lock className="h-3 w-3" /> : <GripVertical className="h-3.5 w-3.5 cursor-grab" />}
                    </td>
                    <td className="num w-6 text-[11px]" style={{ color: "var(--color-text-4)" }}>{i + 1}</td>
                    <td>
                      {f.name}
                      <span className="ml-2 num text-[11px]" style={{ color: "var(--color-text-4)" }}>{f.text.replace(/\n/g, "").length.toLocaleString()} 字</span>
                      {f.existing && <span className="ml-2 text-[11px]" style={{ color: "var(--color-text-4)" }}>已有</span>}
                      {!f.existing && mode === "whole" && <span className="ml-2"><Pill tone="accent">新</Pill></span>}
                    </td>
                    <td className="w-16">
                      {f.existing ? <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>{M.KIND_LABEL[f.kind]}</span> : <KindSelect compact value={f.kind} onChange={(k) => setRowKind(f.key, k)} />}
                    </td>
                    {mode === "per" && <td className="num w-20 text-[11.5px]" style={{ color: "var(--color-accent-2)" }}>→ 第 {base + i} 集</td>}
                    <td className="w-6">
                      {!f.existing && (
                        <button type="button" aria-label="移除" onClick={() => { setAdded((prev) => prev.filter((x) => x.key !== f.key)); setOrder((prev) => prev?.filter((k) => k !== f.key) ?? null); }}>
                          <X className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={close}>取消</SecondaryButton>
          <PrimaryButton size="sm" disabled={added.length === 0} onClick={submit}>
            {mode === "whole" ? (added.length ? `添加 ${added.length} 个文件` : "添加") : added.length > 1 ? `添加为第 ${base}–${base + added.length - 1} 集` : added.length ? `添加为第 ${base} 集` : "添加"}
          </PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}

// ---------------------------------------------------------------- 替换 / 编辑

export function ReplaceDialog({ p, fileId, onClose, onNext }: { p: Proto; fileId: string | null; onClose: () => void; onNext: (r: ImpactReq) => void }) {
  const titleId = useId();
  const f = p.s.files.find((x) => x.id === fileId);
  const [picked, setPicked] = useState<{ name: string; text: string } | null>(null);
  const [kind, setKind] = useState<M.Kind | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const close = () => { setPicked(null); setKind(null); onClose(); };
  if (!f) return null;
  const k = kind ?? f.kind;
  return (
    <GlassModal open={!!fileId} onClose={close} labelledBy={titleId} widthClassName="w-full max-w-md">
      <div className="space-y-3 p-5 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
        <h3 id={titleId} className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>替换「{f.name}」</h3>
        <p style={{ color: "var(--color-text-3)" }}>新文件会放在原来的位置，沿用原来的文件名。ArcReel 会把新旧内容对齐，只有文字真的变了的集才需要处理。</p>
        <div className="flex items-center gap-2">
          <input ref={inputRef} type="file" className="hidden" onChange={(e) => { const x = e.target.files?.[0]; if (x) void x.text().then((text) => setPicked({ name: x.name, text })); e.target.value = ""; }} />
          <SecondaryButton size="sm" onClick={() => inputRef.current?.click()}>选择文件</SecondaryButton>
          <SecondaryButton size="sm" onClick={() => setPicked({ name: `${f.name.replace(/\.txt$/, "")}（修订版）.txt`, text: M.revise(f.text, 2) })}>使用示例修订版</SecondaryButton>
        </div>
        {picked && <div className="text-[12px]">新文件：{picked.name} · {picked.text.replace(/\n/g, "").length.toLocaleString()} 字</div>}
        <div className="flex items-center gap-2 text-[12px]">源文件类型 <KindSelect value={k} onChange={setKind} /><span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>沿用原类型，可以修改</span></div>
        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={close}>取消</SecondaryButton>
          <PrimaryButton size="sm" disabled={!picked} onClick={() => {
            if (!picked) return;
            onNext({ title: `替换「${f.name}」`, next: M.rewriteFile(p.s, f.id, picked.text, { kind: k, why: "替换" }), confirmLabel: "替换" });
            close();
          }}>下一步：查看受影响的集</PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}

export function EditDialog({ p, fileId, onClose, onNext }: { p: Proto; fileId: string | null; onClose: () => void; onNext: (r: ImpactReq) => void }) {
  const titleId = useId();
  const f = p.s.files.find((x) => x.id === fileId);
  const [text, setText] = useState<string | null>(null);
  const close = () => { setText(null); onClose(); };
  if (!f) return null;
  const value = text ?? f.text;
  return (
    <GlassModal open={!!fileId} onClose={close} labelledBy={titleId} widthClassName="w-full max-w-3xl">
      <div className="space-y-3 p-5 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
        <h3 id={titleId} className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>编辑「{f.name}」</h3>
        <textarea className={`${INPUT_CLS} font-mono`} rows={20} value={value} onChange={(e) => setText(e.target.value)} />
        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={close}>取消</SecondaryButton>
          <PrimaryButton size="sm" disabled={value === f.text} onClick={() => {
            onNext({ title: `保存对「${f.name}」的修改`, next: M.rewriteFile(p.s, f.id, value, { why: "编辑" }), confirmLabel: "保存" });
            close();
          }}>保存</PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}

// ---------------------------------------------------------------- 服务之外的改动

export function ExternalBanners({ p, onReview }: { p: Proto; onReview: (r: ImpactReq) => void }) {
  const pend = p.s.files.filter((f) => f.pendingText != null);
  if (!pend.length) return null;
  return (
    <div className="space-y-2">
      {pend.map((f) => {
        const next = M.applyPending(p.s, f.id);
        const im = M.impact(p.s, next);
        const parts = [
          im.shifted.length && `${im.shifted.length} 集只调整位置`,
          im.changedStale.length + im.changedPlain.length && `${im.changedStale.length + im.changedPlain.length} 集原文有变化`,
          im.retired.length + im.removed.length && `${im.retired.length + im.removed.length} 集的原文已被删除`,
        ].filter(Boolean);
        return (
          <div key={f.id} className="space-y-1.5 rounded-md px-3 py-2 text-[12px]" style={{ border: `1px solid oklch(0.7 0.12 60 / 0.5)`, background: "oklch(0.3 0.06 60 / 0.18)" }}>
            <div className="flex items-center gap-1.5 font-medium" style={{ color: WARN }}><AlertTriangle className="h-3.5 w-3.5" />「{f.name}」在 ArcReel 之外被修改过</div>
            <div style={{ color: "var(--color-text-2)" }}>
              分集仍按上次登记时的原文显示。更新后会按上次的内容对齐新文件：{parts.length ? parts.join("，") : "不影响已切出的集"}。更新之前，这个文件不能规划分集或手工切分。
            </div>
            <SecondaryButton size="sm" onClick={() => onReview({
              title: `按新内容更新「${f.name}」的分集`,
              intro: <span style={{ color: "var(--color-text-3)" }}>文件在 ArcReel 之外被修改。以下是按上次登记的内容对齐后的结果。</span>,
              next, confirmLabel: "更新分集",
            })}>查看并更新</SecondaryButton>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- 规划选项（一键规划与重新规划共用，#2775）

export function PlanModePicker({ batch, onChange }: { batch: boolean; onChange: (b: boolean) => void }) {
  return (
    <div className="flex gap-1.5 text-[11.5px]">
      {([false, true] as const).map((b) => (
        <label key={String(b)} className={radioCardClass(batch === b)} style={{ padding: "2px 10px" }}>
          <input type="radio" className="sr-only" checked={batch === b} onChange={() => onChange(b)} />
          {b ? "先规划一批" : "规划到源文结尾"}
        </label>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 重新规划（#2775 / #2795 / #2796）

export function ReplanDialog({ p, fromId, onClose }: { p: Proto; fromId: number | null; onClose: () => void }) {
  const titleId = useId();
  const [instr, setInstr] = useState("");
  const [batch, setBatch] = useState(false);
  const replaced = fromId != null ? M.cutEps(p.s).filter((e) => e.range![0] >= M.replanStart(p.s, fromId)) : [];
  const made = replaced.filter((e) => e.hasArtifacts);
  return (
    <GlassModal open={fromId != null} onClose={onClose} labelledBy={titleId} widthClassName="w-full max-w-lg">
      <div className="space-y-3 p-5 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
        <h3 id={titleId} className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>从{fromId != null ? M.label(p.s, fromId) : ""}起重新规划</h3>
        <p>AI 会先生成一份新的分集方案供你预览。在你确认采纳之前，现有分集不会有任何变化。</p>
        <p>采纳后，这一集及之后从整本源文切出的 {replaced.length} 集会被新方案替换{made.length ? `，其中 ${made.map((e) => M.label(p.s, e.id)).join("、")} 已开始制作，会保留已生成的内容、转为无原文的集。` : "。"}</p>
        <PlanModePicker batch={batch} onChange={setBatch} />
        <textarea className={INPUT_CLS} rows={3} placeholder="附加要求（可选）" value={instr} onChange={(e) => setInstr(e.target.value)} />
        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={onClose}>取消</SecondaryButton>
          <PrimaryButton size="sm" disabled={!!p.s.candidate} onClick={() => { if (fromId != null) p.beginReplan(fromId, instr, batch); onClose(); }}>开始规划</PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}

function Row({ label, value, warn }: { label: string; value: ReactNode; warn?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="w-[72px] shrink-0" style={{ color: "var(--color-text-4)" }}>{label}</span>
      <span style={{ color: warn ? REPLAN : "var(--color-text-2)" }}>{value}</span>
    </div>
  );
}

export function CandidateSummaryBlock({ p }: { p: Proto }) {
  const s = p.s;
  const sum = M.candidateSummary(s);
  const c = s.candidate;
  if (!sum || !c) return null;
  const lab = (eps: M.Ep[]) => eps.map((e) => M.label(s, e.id)).join("、");
  const oldChars = sum.replaced.reduce((n, e) => n + M.epChars(s, e), 0);
  const newChars = c.eps.reduce((n, e) => n + M.charsOf(s, e.range), 0);
  const avg = (n: number, k: number) => (k ? Math.round(n / k).toLocaleString() : "—");
  const settled = c.status !== "generating";
  return (
    <div className="space-y-1 text-[12px]">
      <Row label="集数" value={`${sum.replaced.length} 集 → ${sum.newCount} 集${settled ? "" : "（规划中）"}`} />
      <Row label="平均每集" value={`${avg(oldChars, sum.replaced.length)} 字 → ${avg(newChars, sum.newCount)} 字`} />
      <Row label="覆盖范围" value={c.reached >= M.L(s).len ? `${M.label(s, c.fromEp)}起至源文结尾` : `${M.label(s, c.fromEp)}起至源文 ${Math.round((c.reached / M.L(s).len) * 100)}% 处；之后的原文回到未分集`} />
      {settled && sum.retiredKept.length > 0 && <Row warn label="退下的集" value={`${lab(sum.retiredKept)} 已开始制作，保留已生成的内容，转为无原文的集并移到最后`} />}
      {settled && sum.removed.length > 0 && <Row label="将移除" value={`${lab(sum.removed)}（尚未开始制作）`} />}
      {settled && sum.moved.length > 0 && <Row label="位置变化" value={sum.moved.map((m) => `${m.ep.title} 第 ${m.from} → ${m.to} 集`).join("；")} />}
      {c.instructions && <Row label="附加要求" value={c.instructions} />}
    </div>
  );
}

export function CandidateActions({ p }: { p: Proto }) {
  const c = p.s.candidate;
  const sum = M.candidateSummary(p.s);
  const [del, setDel] = useState(false);
  if (!c || !sum) return null;
  if (c.status === "generating") return <SecondaryButton size="sm" onClick={p.stopCandidate}>停止</SecondaryButton>;
  return (
    <div className="space-y-2">
      {sum.retiredKept.length > 0 && (
        <label className="flex items-center gap-1.5 text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
          <input type="checkbox" checked={del} onChange={(e) => setDel(e.target.checked)} />
          一并删除退下的 {sum.retiredKept.length} 集及其已生成的内容
        </label>
      )}
      <div className="flex items-center gap-2">
        <SecondaryButton size="sm" onClick={p.discardCandidate}>放弃新方案</SecondaryButton>
        {c.status === "stopped" && <SecondaryButton size="sm" onClick={p.continueCandidate}>继续规划</SecondaryButton>}
        <PrimaryButton size="sm" disabled={!sum.adoptable} onClick={() => p.adoptCandidate(del)}>采纳新方案</PrimaryButton>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 新建一集（可插在任意一集之后，#2795）

export function PositionSelect({ s, value, onChange }: { s: M.ProtoState; value: number | null; onChange: (v: number | null) => void }) {
  return (
    <select
      aria-label="放在"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      className="max-w-[260px] rounded bg-transparent px-1.5 py-0.5 text-[12px] outline-none"
      style={{ border: "1px solid var(--color-hairline-strong)", color: "var(--color-text-2)" }}
    >
      <option value="">末尾</option>
      {s.episodes.map((e, i) => <option key={e.id} value={e.id}>第 {i + 1} 集《{e.title}》之后</option>)}
    </select>
  );
}

export function NewEpisodeDialog({ p, open, afterId, onClose }: { p: Proto; open: boolean; afterId: number | null; onClose: () => void }) {
  const titleId = useId();
  const [title, setTitle] = useState("");
  const [after, setAfter] = useState<number | null | undefined>(undefined);
  const pos = after === undefined ? afterId : after;
  const close = () => { setTitle(""); setAfter(undefined); onClose(); };
  const n = pos == null ? p.s.episodes.length + 1 : M.posOf(p.s, pos) + 1;
  return (
    <GlassModal open={open} onClose={close} labelledBy={titleId} widthClassName="w-full max-w-md">
      <div className="space-y-3 p-5 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
        <h3 id={titleId} className="display-serif text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>新建一集</h3>
        <input className={INPUT_CLS} placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <div className="flex items-center gap-2">放在 <PositionSelect s={p.s} value={pos} onChange={setAfter} /></div>
        <p className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>新的一集没有原文，可以在集页面补充原文或直接写脚本。将成为第 {n} 集，之后的集顺延。</p>
        <div className="flex justify-end gap-2">
          <SecondaryButton size="sm" onClick={close}>取消</SecondaryButton>
          <PrimaryButton size="sm" onClick={() => { p.addBlankEpisode(title, pos); close(); }}>新建为第 {n} 集</PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}

// ---------------------------------------------------------------- 状态面板

export function StateInspector({ p }: { p: Proto }) {
  const [open, setOpen] = useState(false);
  const s = p.s;
  return (
    <div className="fixed bottom-16 left-4 z-[99] w-[360px] text-[11px]">
      <button type="button" onClick={() => setOpen(!open)} className="block rounded-full px-3 py-1" style={{ background: "oklch(0.95 0.12 95)", color: "oklch(0.2 0 0)" }}>
        {open ? "收起账本" : "账本状态 / 模拟"}
      </button>
      {open && (
        <div className="mt-2 max-h-[55vh] overflow-auto rounded-lg p-3 font-mono" style={{ background: "oklch(0.12 0 0 / 0.95)", color: "oklch(0.85 0 0)" }}>
          <div className="mb-1 flex flex-wrap gap-2">
            <button type="button" className="underline" onClick={() => p.markMade(3)}>模拟：前 3 集已开始制作</button>
            {s.files.map((f) => (
              <button key={f.id} type="button" className="underline" onClick={() => p.simulateExternal(f.id)}>模拟外部改动 {f.name.slice(0, 8)}</button>
            ))}
          </div>
          <div>files: {M.L(s).files.map((f) => `${f.name.slice(0, 8)}[${f.start}–${f.end}]${f.pending ? "*" : ""}`).join(" · ")}</div>
          <div>cursor: {M.cursor(s)} / {M.L(s).len} · planning: {s.planning ? `${s.planning.gap ? "gap" : "tail"} @${s.planning.pos}→${s.planning.until}` : "—"}</div>
          <table className="mt-2 w-full">
            <tbody>
              {s.episodes.map((e, i) => (
                <tr key={e.id}>
                  <td>{i + 1}</td><td>#{e.id}</td><td>{e.origin}</td><td>{e.loc ? `${e.loc.file}:${e.loc.a}–${e.loc.b}` : "—"}</td>
                  <td>{e.hasArtifacts ? "产物" : ""}</td><td>{e.stale ? "stale" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 space-y-0.5" style={{ color: "oklch(0.65 0 0)" }}>
            {s.log.map((l, i) => <div key={i}>· {l}</div>)}
          </div>
          <button type="button" className="mt-2 underline" onClick={p.reset}>重置原型数据</button>
        </div>
      )}
    </div>
  );
}
