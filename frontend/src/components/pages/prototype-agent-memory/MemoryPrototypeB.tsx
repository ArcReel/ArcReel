/* eslint-disable react-hooks/set-state-in-effect, react-hooks/purity, jsx-a11y/no-autofocus -- 原型代码，评审后整目录删除 */
// PROTOTYPE — wayfinder #2310 变体 B「记忆卡片」：把索引行 + 主题文件合成一张张「记忆」卡，文件与 frontmatter 完全隐藏；
// 按类型筛选，卡片就地展开 / 编辑；「记一条」自动生成主题文件与索引行。冲突取「预防」：Agent 写入期间卡片锁定不可编辑，
// Agent 改过你正编辑的那条时在卡内并排给出它的版本。评审后整目录删除。
import { useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, Lock, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CARD_STYLE, INPUT_CLS } from "@/components/ui/darkroom-tokens";

import {
  INDEX_FILE,
  LEVEL_COPY,
  TYPE_LABELS,
  buildTopic,
  memoryActions,
  parseIndex,
  parseTopic,
  peekLevel,
  relTime,
  slugify,
  useMemoryLevel,
  type MemoryFile,
  type MemoryLevel,
  type MemoryType,
} from "./memory-prototype-store";
import { AgentStatusLine, ClearMemoryDialog, GhostButton, Kicker, ProtoControls, TypeBadge, useTick } from "./memory-prototype-shared";

interface Card {
  file: MemoryFile;
  title: string;
  hook: string;
  type: MemoryType;
  body: string;
  inIndex: boolean;
}

const FILTERS: Array<{ value: MemoryType | "all"; label: string }> = [
  { value: "all", label: "全部" },
  { value: "user", label: TYPE_LABELS.user },
  { value: "feedback", label: TYPE_LABELS.feedback },
  { value: "project", label: TYPE_LABELS.project },
  { value: "reference", label: TYPE_LABELS.reference },
];

export function MemoryPrototypeB({ level }: { level: MemoryLevel }) {
  useTick();
  const st = useMemoryLevel(level);
  const copy = LEVEL_COPY[level];
  const index = st.files.find((f) => f.name === INDEX_FILE);
  const entries = index ? parseIndex(index.content) : [];
  const cards: Card[] = st.files
    .filter((f) => f.name !== INDEX_FILE)
    .map((file) => {
      const p = parseTopic(file);
      const e = entries.find((x) => x.file === file.name);
      return { file, title: e?.title ?? p.description ?? p.name, hook: e?.hook ?? p.description, type: p.type, body: p.body, inIndex: !!e };
    })
    .sort((a, b) => b.file.modifiedAt - a.file.modifiedAt);

  const [filter, setFilter] = useState<MemoryType | "all">("all");
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [deleteName, setDeleteName] = useState<string | null>(null);
  const [showIndex, setShowIndex] = useState(false);

  const shown = cards.filter((c) => filter === "all" || c.type === filter);
  const counts = cards.reduce<Record<string, number>>((m, c) => ({ ...m, [c.type]: (m[c.type] ?? 0) + 1 }), {});

  return (
    <section>
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div>
          <Kicker>Agent Memory</Kicker>
          <h3 className="mt-1 text-[14.5px] font-medium text-text">{copy.title}</h3>
          <p className="mt-1 max-w-[560px] text-[12px] leading-[1.55] text-text-3">{copy.desc}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <GhostButton onClick={() => setAdding(true)} disabled={st.agentWriting}>
            <Plus className="h-3.5 w-3.5" /> 记一条
          </GhostButton>
          <AgentStatusLine state={st} />
        </div>
      </div>

      <div className="mb-3">
        <ProtoControls level={level} currentFile={open} />
      </div>

      {cards.length > 0 && (
        <div className="mb-3 flex items-center gap-1" role="tablist">
          {FILTERS.map((f) => {
            const n = f.value === "all" ? cards.length : (counts[f.value] ?? 0);
            const active = filter === f.value;
            return (
              <button
                key={f.value}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setFilter(f.value)}
                className={"rounded-full border px-2.5 py-0.5 text-[11.5px] transition-colors " + (active ? "border-accent/50 bg-accent-dim text-text" : "border-hairline text-text-3 hover:text-text")}
              >
                {f.label} <span className="num text-text-4">{n}</span>
              </button>
            );
          })}
        </div>
      )}

      {st.agentWriting && (
        <div className="mb-3 flex items-center gap-2 rounded-[8px] border border-accent/30 bg-accent-dim px-3 py-2 text-[11.5px] text-accent-2">
          <Lock className="h-3.5 w-3.5" /> Agent 正在整理记忆，卡片暂时只读，写完即解锁。
        </div>
      )}

      {adding && <AddCard level={level} onClose={() => setAdding(false)} />}

      {cards.length === 0 && !adding ? (
        <EmptyState level={level} onAdd={() => setAdding(true)} />
      ) : (
        <ul className="space-y-2">
          {shown.map((c) => (
            <MemoryCard key={c.file.name} level={level} card={c} open={open === c.file.name} locked={st.agentWriting} onToggle={() => setOpen(open === c.file.name ? null : c.file.name)} onDelete={() => setDeleteName(c.file.name)} />
          ))}
          {shown.length === 0 && <li className="px-2 py-6 text-center text-[12px] text-text-4">这一类还没有记忆。</li>}
        </ul>
      )}

      {cards.length > 0 && (
        <div className="mt-5 flex items-center justify-between border-t border-hairline-soft pt-3">
          <button type="button" onClick={() => setShowIndex((v) => !v)} className="flex items-center gap-1 text-[11px] text-text-4 hover:text-text-2">
            <ChevronDown className={"h-3 w-3 transition-transform " + (showIndex ? "rotate-180" : "")} /> 查看索引原文（{INDEX_FILE}）
          </button>
          <button type="button" onClick={() => setClearOpen(true)} className="text-[11px] text-text-4 underline-offset-2 hover:text-danger-2 hover:underline">
            清空{copy.short}的全部记忆…
          </button>
        </div>
      )}
      {showIndex && index && (
        <pre className="mt-2 overflow-auto rounded-[8px] border border-hairline-soft px-3 py-2 font-mono text-[11px] leading-[1.6] text-text-3" style={CARD_STYLE}>
          {index.content}
        </pre>
      )}

      <ClearMemoryDialog open={clearOpen} level={level} count={st.files.length} onCancel={() => setClearOpen(false)} onDone={() => setClearOpen(false)} />
      <ConfirmDialog
        open={deleteName !== null}
        tone="danger"
        title="删除这条记忆？"
        description="Agent 之后不会再记得这件事，除非你再次告诉它。索引里对应的一行会一并移除。"
        confirmLabel="删除"
        onConfirm={() => {
          if (deleteName) removeWithIndex(level, deleteName);
          setDeleteName(null);
        }}
        onCancel={() => setDeleteName(null)}
      />
    </section>
  );
}

function removeWithIndex(level: MemoryLevel, name: string) {
  memoryActions.remove(level, name);
  const idx = peekLevel(level).files.find((f) => f.name === INDEX_FILE);
  if (idx) {
    const next = idx.content.split("\n").filter((l) => !l.includes(`(${name})`)).join("\n");
    memoryActions.save(level, INDEX_FILE, next, idx.modifiedAt, true);
  }
}

function EmptyState({ level, onAdd }: { level: MemoryLevel; onAdd: () => void }) {
  const copy = LEVEL_COPY[level];
  const example = level === "user" ? { type: "user" as const, title: "画幅偏好", hook: "竖屏 9:16 优先，横屏只在明确要求时用" } : { type: "project" as const, title: "主角视觉锚点", hook: "束发、靛蓝短打、左眉有疤，所有资产图沿用" };
  return (
    <div className="rounded-[10px] border border-dashed border-hairline px-6 py-8 text-center">
      <Sparkles className="mx-auto h-5 w-5 text-accent-2" />
      <p className="mx-auto mt-3 max-w-[420px] text-[12.5px] leading-[1.65] text-text-3">{copy.empty}</p>
      <div className="mx-auto mt-5 max-w-[420px] rounded-[8px] border border-hairline-soft px-4 py-3 text-left opacity-60" style={CARD_STYLE}>
        <div className="mb-1 flex items-center gap-2">
          <TypeBadge type={example.type} size="xs" />
          <span className="text-[12.5px] text-text">{example.title}</span>
        </div>
        <p className="text-[11.5px] text-text-3">{example.hook}</p>
        <p className="mt-2 font-mono text-[9.5px] uppercase tracking-[0.14em] text-text-4">示例 · 记忆会长这样</p>
      </div>
      <GhostButton className="mt-5" onClick={onAdd}>
        <Plus className="h-3.5 w-3.5" /> 自己先记一条
      </GhostButton>
    </div>
  );
}

function AddCard({ level, onClose }: { level: MemoryLevel; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<MemoryType>(level === "user" ? "user" : "project");
  const [body, setBody] = useState("");
  const st = useMemoryLevel(level);
  const submit = () => {
    if (!title.trim() || !body.trim()) return;
    const name = slugify(title) + ".md";
    memoryActions.create(level, name, buildTopic(name.replace(/\.md$/, ""), title.trim(), type, body.trim()));
    const idx = st.files.find((f) => f.name === INDEX_FILE);
    const line = `- [${title.trim()}](${name}) — ${body.trim().split("\n")[0].slice(0, 60)}\n`;
    memoryActions.save(level, INDEX_FILE, (idx?.content.replace(/\n*$/, "\n") ?? "") + line, idx?.modifiedAt ?? 0, true);
    onClose();
  };
  return (
    <div className="mb-3 rounded-[10px] border border-accent/40 p-4" style={CARD_STYLE}>
      <div className="mb-2 flex items-center gap-2">
        <Kicker>New memory</Kicker>
        <span className="flex-1" />
        {(Object.keys(TYPE_LABELS) as MemoryType[]).map((t) => (
          <button key={t} type="button" onClick={() => setType(t)} className={"rounded-[4px] px-1 py-px transition-opacity " + (type === t ? "opacity-100 ring-1 ring-accent" : "opacity-50 hover:opacity-90")}>
            <TypeBadge type={t} size="xs" />
          </button>
        ))}
      </div>
      <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="一句话标题，例如「配音语速偏慢」" className={INPUT_CLS} autoFocus />
      <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="要 Agent 记住什么、怎么用。可以直接用日常语言写。" rows={3} className={INPUT_CLS + " mt-2 resize-y"} />
      <div className="mt-3 flex justify-end gap-2">
        <GhostButton onClick={onClose}>取消</GhostButton>
        <button type="button" onClick={submit} disabled={!title.trim() || !body.trim()} className="rounded-[8px] px-3 py-1.5 text-[12px] font-semibold disabled:opacity-40" style={{ color: "oklch(0.14 0 0)", background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))" }}>
          保存记忆
        </button>
      </div>
    </div>
  );
}

function MemoryCard({ level, card, open, locked, onToggle, onDelete }: { level: MemoryLevel; card: Card; open: boolean; locked: boolean; onToggle: () => void; onDelete: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(card.body);
  const [loadedAt, setLoadedAt] = useState(card.file.modifiedAt);
  const theirsChanged = editing && card.file.modifiedAt !== loadedAt;
  const fresh = Date.now() - card.file.modifiedAt < 60_000 && card.file.modifiedBy === "agent";

  useEffect(() => {
    if (!editing) {
      setDraft(card.body);
      setLoadedAt(card.file.modifiedAt);
    }
  }, [card.body, card.file.modifiedAt, editing]);
  useEffect(() => {
    if (locked) setEditing(false);
  }, [locked]);

  const save = () => {
    const p = parseTopic(card.file);
    memoryActions.save(level, card.file.name, buildTopic(p.name, p.description, p.type, draft), card.file.modifiedAt, true);
    setEditing(false);
  };

  return (
    <li className={"rounded-[10px] border transition-colors " + (open ? "border-hairline-strong" : "border-hairline hover:border-hairline-strong")} style={{ ...CARD_STYLE, boxShadow: fresh ? "0 0 0 1px oklch(0.7 0.15 295 / 0.45), 0 0 24px -10px var(--color-accent-glow)" : undefined }}>
      <button type="button" onClick={onToggle} className="flex w-full items-start gap-3 px-4 py-3 text-left">
        <TypeBadge type={card.type} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="text-[13px] text-text">{card.title}</span>
            {fresh && <span className="rounded-full bg-accent-dim px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.12em] text-accent-2">刚更新</span>}
            {!card.inIndex && <span className="rounded-full border border-warm/40 px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.12em] text-warm">未入索引</span>}
          </span>
          <span className="mt-0.5 block truncate text-[11.5px] text-text-3">{card.hook}</span>
        </span>
        <span className="shrink-0 pt-0.5 text-[10.5px] text-text-4">
          {card.file.modifiedBy === "agent" ? "Agent" : "你"} · {relTime(card.file.modifiedAt)}
        </span>
        <ChevronDown className={"mt-0.5 h-3.5 w-3.5 shrink-0 text-text-4 transition-transform " + (open ? "rotate-180" : "")} />
      </button>
      {open && (
        <div className="border-t border-hairline-soft px-4 py-3">
          {theirsChanged && (
            <div className="mb-2 rounded-[6px] border border-warm/40 bg-warm/10 px-3 py-2 text-[11.5px] text-warm">
              <div className="flex items-center gap-1.5"><AlertTriangle className="h-3.5 w-3.5" /> Agent 刚改过这条，它的版本在右侧。保存会以你的为准。</div>
            </div>
          )}
          {editing ? (
            <div className={"grid gap-3 " + (theirsChanged ? "grid-cols-2" : "grid-cols-1")}>
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={6} className={INPUT_CLS + " resize-y text-[12.5px] leading-[1.6]"} autoFocus />
              {theirsChanged && <pre className="whitespace-pre-wrap rounded-[8px] border border-hairline-soft bg-bg-grad-b/40 px-3 py-2 text-[12px] leading-[1.6] text-text-3">{card.body}</pre>}
            </div>
          ) : (
            <p className="whitespace-pre-wrap text-[12.5px] leading-[1.65] text-text-2">{card.body}</p>
          )}
          <div className="mt-3 flex items-center gap-2">
            <span className="font-mono text-[10px] text-text-4">{card.file.name}</span>
            <span className="flex-1" />
            {editing ? (
              <>
                {theirsChanged && (
                  <GhostButton onClick={() => { setEditing(false); }}>
                    用 Agent 的版本
                  </GhostButton>
                )}
                <GhostButton onClick={() => setEditing(false)}>取消</GhostButton>
                <button type="button" onClick={save} className="rounded-[8px] px-3 py-1.5 text-[12px] font-semibold" style={{ color: "oklch(0.14 0 0)", background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))" }}>
                  保存
                </button>
              </>
            ) : (
              <>
                <GhostButton danger onClick={onDelete} disabled={locked}>
                  <Trash2 className="h-3.5 w-3.5" /> 删除
                </GhostButton>
                <GhostButton onClick={() => setEditing(true)} disabled={locked}>
                  {locked ? <Lock className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />} 编辑
                </GhostButton>
              </>
            )}
          </div>
        </div>
      )}
    </li>
  );
}
