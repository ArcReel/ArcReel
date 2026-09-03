/* eslint-disable jsx-a11y/no-autofocus -- 原型代码，评审后整目录删除 */
// PROTOTYPE — wayfinder #2310 变体 C「一页文档」：把整个记忆目录读成一篇文档——顶部是索引（目录），下面每个主题文件一节。
// 默认阅读态，逐节就地编辑、逐节保存（小爆炸半径）；冲突取「事后合并提示」：Agent 改过你正编辑的那节时在节内显示
// 它的版本并允许并入。frontmatter 阅读态隐藏、编辑态显示。评审后整目录删除。
import { useEffect, useState } from "react";
import { AlertTriangle, Bot, Hash, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CARD_STYLE, INPUT_CLS } from "@/components/ui/darkroom-tokens";

import {
  INDEX_FILE,
  INDEX_LINE_LIMIT,
  LEVEL_COPY,
  buildTopic,
  indexStats,
  memoryActions,
  parseIndex,
  parseTopic,
  relTime,
  useMemoryLevel,
  type MemoryFile,
  type MemoryLevel,
} from "./memory-prototype-store";
import { AgentStatusLine, ClearMemoryDialog, GhostButton, Kicker, ProtoControls, TypeBadge, useTick } from "./memory-prototype-shared";

export function MemoryPrototypeC({ level }: { level: MemoryLevel }) {
  useTick();
  const st = useMemoryLevel(level);
  const copy = LEVEL_COPY[level];
  const index = st.files.find((f) => f.name === INDEX_FILE) ?? null;
  const entries = index ? parseIndex(index.content) : [];
  // 文档顺序 = 索引顺序；未入索引的排最后。
  const ordered: MemoryFile[] = [
    ...entries.map((e) => st.files.find((f) => f.name === e.file)).filter((f): f is MemoryFile => !!f),
    ...st.files.filter((f) => f.name !== INDEX_FILE && !entries.some((e) => e.file === f.name)),
  ];
  const [menu, setMenu] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [deleteName, setDeleteName] = useState<string | null>(null);
  const [editingName, setEditingName] = useState<string | null>(null);

  const addSection = () => {
    const n = ordered.length + 1;
    const name = `section-${n}.md`;
    memoryActions.create(level, name, buildTopic(`section-${n}`, "新的一节", level === "user" ? "user" : "project", "写下要 Agent 记住的内容。"));
    const line = `- [新的一节](${name}) — 你手写的一节\n`;
    memoryActions.save(level, INDEX_FILE, (index?.content.replace(/\n*$/, "\n") ?? "") + line, index?.modifiedAt ?? 0, true);
    setEditingName(name);
  };

  return (
    <section>
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div>
          <Kicker>Agent Memory</Kicker>
          <h3 className="font-editorial mt-1 text-[22px] leading-[1.1] text-text" style={{ letterSpacing: "-0.012em" }}>{copy.title}</h3>
          <p className="mt-1.5 max-w-[560px] text-[12px] leading-[1.55] text-text-3">{copy.desc}</p>
        </div>
        <div className="relative flex shrink-0 flex-col items-end gap-2">
          <button type="button" aria-label="更多" onClick={() => setMenu((v) => !v)} className="rounded-[6px] border border-hairline p-1.5 text-text-3 hover:text-text">
            <MoreHorizontal className="h-4 w-4" />
          </button>
          {menu && (
            <div className="absolute right-0 top-9 z-10 w-[200px] rounded-[8px] border border-hairline p-1 shadow-xl shadow-black/40" style={CARD_STYLE}>
              <button type="button" onClick={() => { setMenu(false); addSection(); }} className="flex w-full items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-left text-[12px] text-text-2 hover:bg-bg-grad-a hover:text-text">
                <Plus className="h-3.5 w-3.5" /> 添加一节
              </button>
              <button type="button" disabled={st.files.length === 0} onClick={() => { setMenu(false); setClearOpen(true); }} className="flex w-full items-center gap-2 rounded-[6px] px-2.5 py-1.5 text-left text-[12px] text-danger-2 hover:bg-danger/10 disabled:opacity-40">
                <Trash2 className="h-3.5 w-3.5" /> 清空{copy.short}的记忆…
              </button>
            </div>
          )}
          <AgentStatusLine state={st} />
        </div>
      </div>

      <div className="mb-3">
        <ProtoControls level={level} currentFile={editingName} />
      </div>

      <article className="rounded-[10px] border border-hairline px-7 py-6" style={CARD_STYLE}>
        {st.files.length === 0 ? (
          <div className="py-6 text-center">
            <p className="mx-auto max-w-[440px] text-[12.5px] leading-[1.7] text-text-3">{copy.empty}</p>
            <p className="font-editorial mx-auto mt-6 max-w-[440px] text-[15px] italic leading-[1.6] text-text-4">
              「{level === "user" ? "创作者偏好竖屏 9:16，配音要女声、语速偏慢……" : "主角林昭：束发、靛蓝短打、左眉有疤……"}」
            </p>
            <p className="mt-1 font-mono text-[9.5px] uppercase tracking-[0.14em] text-text-4">— 将来这里会写着类似的话</p>
            <GhostButton className="mt-6" onClick={addSection}>
              <Plus className="h-3.5 w-3.5" /> 自己先写一节
            </GhostButton>
          </div>
        ) : (
          <>
            {index && <IndexSection level={level} file={index} onJump={(name) => document.getElementById(`mem-${name}`)?.scrollIntoView({ behavior: "smooth", block: "start" })} editing={editingName === INDEX_FILE} setEditing={(v) => setEditingName(v ? INDEX_FILE : null)} />}
            {ordered.map((f, i) => (
              <TopicSection key={f.name} level={level} file={f} n={i + 1} inIndex={entries.some((e) => e.file === f.name)} editing={editingName === f.name} setEditing={(v) => setEditingName(v ? f.name : null)} onDelete={() => setDeleteName(f.name)} />
            ))}
            <div className="mt-6 flex justify-center border-t border-hairline-soft pt-5">
              <GhostButton onClick={addSection}>
                <Plus className="h-3.5 w-3.5" /> 添加一节
              </GhostButton>
            </div>
          </>
        )}
      </article>

      <ClearMemoryDialog open={clearOpen} level={level} count={st.files.length} onCancel={() => setClearOpen(false)} onDone={() => setClearOpen(false)} />
      <ConfirmDialog
        open={deleteName !== null}
        tone="danger"
        title="删除这一节？"
        description="对应的主题文件与索引行都会移除。Agent 之后不会再记得这件事。"
        confirmLabel="删除"
        onConfirm={() => {
          if (deleteName) {
            memoryActions.remove(level, deleteName);
            if (index) memoryActions.save(level, INDEX_FILE, index.content.split("\n").filter((l) => !l.includes(`(${deleteName})`)).join("\n"), index.modifiedAt, true);
          }
          setDeleteName(null);
        }}
        onCancel={() => setDeleteName(null)}
      />
    </section>
  );
}

function IndexSection({ level, file, onJump, editing, setEditing }: { level: MemoryLevel; file: MemoryFile; onJump: (name: string) => void; editing: boolean; setEditing: (v: boolean) => void }) {
  const entries = parseIndex(file.content);
  const stats = indexStats(file.content);
  return (
    <section className="group mb-6">
      <SectionHead label="索引 · 每次会话开始先读这里" file={file} editing={editing} setEditing={setEditing} extra={<span className={"font-mono text-[10px] " + (stats.over ? "text-danger-2" : "text-text-4")}>{stats.lines}/{INDEX_LINE_LIMIT} 行</span>} />
      {editing ? (
        <SectionEditor level={level} file={file} raw onDone={() => setEditing(false)} />
      ) : (
        <ol className="mt-2 space-y-1">
          {entries.map((e) => (
            <li key={e.file} className="flex items-baseline gap-2 text-[12.5px]">
              <button type="button" onClick={() => onJump(e.file)} className="text-accent-2 underline-offset-2 hover:underline">{e.title}</button>
              <span className="text-text-3">{e.hook}</span>
            </li>
          ))}
          {entries.length === 0 && <li className="text-[12px] text-text-4">索引为空。</li>}
        </ol>
      )}
    </section>
  );
}

function TopicSection({ level, file, n, inIndex, editing, setEditing, onDelete }: { level: MemoryLevel; file: MemoryFile; n: number; inIndex: boolean; editing: boolean; setEditing: (v: boolean) => void; onDelete: () => void }) {
  const p = parseTopic(file);
  return (
    <section id={`mem-${file.name}`} className="group scroll-mt-4 border-t border-hairline-soft pt-5 mt-5">
      <SectionHead
        label={
          <span className="flex items-center gap-2">
            <span className="font-mono text-[10px] text-text-4"><Hash className="inline h-3 w-3" />{n}</span>
            <span className="text-[14px] text-text">{p.description || p.name}</span>
            <TypeBadge type={p.type} size="xs" />
            {!inIndex && <span className="rounded-full border border-warm/40 px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.12em] text-warm">未入索引</span>}
          </span>
        }
        file={file}
        editing={editing}
        setEditing={setEditing}
        extra={!editing && <button type="button" onClick={onDelete} aria-label="删除这一节" className="rounded-[5px] p-1 text-text-4 opacity-0 transition-opacity hover:text-danger-2 group-hover:opacity-100"><Trash2 className="h-3.5 w-3.5" /></button>}
      />
      {editing ? (
        <SectionEditor level={level} file={file} onDone={() => setEditing(false)} />
      ) : (
        <p className="mt-2 whitespace-pre-wrap text-[12.5px] leading-[1.7] text-text-2">{p.body}</p>
      )}
    </section>
  );
}

function SectionHead({ label, file, editing, setEditing, extra }: { label: React.ReactNode; file: MemoryFile; editing: boolean; setEditing: (v: boolean) => void; extra?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1 text-[12px] text-text-3">{label}</div>
      {extra}
      <span className="text-[10.5px] text-text-4">{file.modifiedBy === "agent" ? <Bot className="inline h-3 w-3" /> : "你"} {relTime(file.modifiedAt)}</span>
      {!editing && (
        <button type="button" onClick={() => setEditing(true)} aria-label="编辑这一节" className="rounded-[5px] p-1 text-text-4 opacity-0 transition-opacity hover:text-text group-hover:opacity-100">
          <Pencil className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

function SectionEditor({ level, file, raw, onDone }: { level: MemoryLevel; file: MemoryFile; raw?: boolean; onDone: () => void }) {
  const p = parseTopic(file);
  const initial = raw ? file.content : p.body;
  const [draft, setDraft] = useState(initial);
  const [loadedAt] = useState(file.modifiedAt);
  const theirs = file.modifiedAt !== loadedAt;
  const theirsText = raw ? file.content : parseTopic(file).body;
  const [fmOpen, setFmOpen] = useState(false);

  useEffect(() => {
    // 编辑期间 Agent 改动不覆盖草稿，只在节内提示。
  }, [theirs]);

  const save = () => {
    const content = raw ? draft : buildTopic(p.name, p.description, p.type, draft);
    memoryActions.save(level, file.name, content, file.modifiedAt, true);
    onDone();
  };
  const merge = () => setDraft(draft.replace(/\n*$/, "\n\n") + theirsText.slice(initial.length).trim());

  return (
    <div className="mt-2">
      {theirs && (
        <div className="mb-2 rounded-[6px] border border-warm/40 bg-warm/10 px-3 py-2 text-[11.5px] text-warm">
          <div className="flex flex-wrap items-center gap-2">
            <AlertTriangle className="h-3.5 w-3.5" /> Agent {relTime(file.modifiedAt)}改过这一节。
            <span className="flex-1" />
            <button type="button" className="underline underline-offset-2" onClick={merge}>把它新增的内容并进来</button>
            <button type="button" className="underline underline-offset-2" onClick={() => setDraft(theirsText)}>改用它的版本</button>
          </div>
          <pre className="mt-2 max-h-[140px] overflow-auto whitespace-pre-wrap rounded-[6px] bg-bg-grad-b/50 px-2.5 py-1.5 font-mono text-[11px] leading-[1.55] text-text-3">{theirsText}</pre>
        </div>
      )}
      {!raw && p.hasFrontmatter && (
        <button type="button" onClick={() => setFmOpen((v) => !v)} className="mb-1 font-mono text-[10px] text-text-4 hover:text-text-3">
          {fmOpen ? "▾" : "▸"} 元数据（name / description / type，通常不必改）
        </button>
      )}
      {fmOpen && <pre className="mb-2 rounded-[6px] border border-hairline-soft px-2.5 py-1.5 font-mono text-[10.5px] leading-[1.5] text-text-4">{`name: ${p.name}\ndescription: ${p.description}\nmetadata:\n  type: ${p.type}`}</pre>}
      <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={raw ? 8 : 6} spellCheck={false} className={INPUT_CLS + " resize-y " + (raw ? "font-mono text-[12px]" : "text-[12.5px]") + " leading-[1.6]"} autoFocus />
      <div className="mt-2 flex items-center gap-2">
        <span className="font-mono text-[10px] text-text-4">{file.name}</span>
        <span className="flex-1" />
        <GhostButton onClick={onDone}>取消</GhostButton>
        <button type="button" onClick={save} disabled={draft === initial && !theirs} className="rounded-[8px] px-3 py-1.5 text-[12px] font-semibold disabled:opacity-40" style={{ color: "oklch(0.14 0 0)", background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))" }}>
          保存这一节
        </button>
      </div>
    </div>
  );
}
