/* eslint-disable react-hooks/set-state-in-effect -- 原型代码，评审后整目录删除 */
// PROTOTYPE — wayfinder #2310 变体 A「文件柜」：左侧文件列表（索引置顶 + 主题文件），右侧原文编辑器。
// 记忆就是文件，frontmatter 原样可见；冲突在「保存」那一刻检测（mtime 不符），弹行内三选一。评审后整目录删除。
import { useEffect, useState } from "react";
import { AlertTriangle, FileText, Plus, RotateCcw, Save, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CARD_STYLE, INPUT_CLS } from "@/components/ui/darkroom-tokens";

import {
  INDEX_FILE,
  INDEX_LINE_LIMIT,
  LEVEL_COPY,
  buildTopic,
  indexStats,
  memoryActions,
  parseTopic,
  relTime,
  useMemoryLevel,
  type MemoryFile,
  type MemoryLevel,
} from "./memory-prototype-store";
import { AgentStatusLine, ClearMemoryDialog, GhostButton, Kicker, ProtoControls, TypeBadge, useTick } from "./memory-prototype-shared";

export function MemoryPrototypeA({ level }: { level: MemoryLevel }) {
  useTick();
  const st = useMemoryLevel(level);
  const copy = LEVEL_COPY[level];
  const index = st.files.find((f) => f.name === INDEX_FILE) ?? null;
  const topics = st.files.filter((f) => f.name !== INDEX_FILE).sort((a, b) => b.modifiedAt - a.modifiedAt);

  const [selected, setSelected] = useState<string | null>(index ? INDEX_FILE : (topics[0]?.name ?? null));
  const [clearOpen, setClearOpen] = useState(false);
  const [deleteName, setDeleteName] = useState<string | null>(null);

  useEffect(() => {
    if (selected && !st.files.some((f) => f.name === selected)) setSelected(st.files[0]?.name ?? null);
    if (!selected && st.files.length > 0) setSelected(st.files[0].name);
  }, [st.files, selected]);

  const file = st.files.find((f) => f.name === selected) ?? null;

  const createNew = () => {
    const n = topics.length + 1;
    const name = `new-memory-${n}.md`;
    memoryActions.create(level, name, buildTopic(`new-memory-${n}`, "一句话说明这条记忆是什么", level === "user" ? "user" : "project", "在这里写下要 Agent 记住的内容。"));
    setSelected(name);
  };

  return (
    <section>
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div>
          <Kicker>Agent Memory</Kicker>
          <h3 className="mt-1 text-[14.5px] font-medium text-text">{copy.title}</h3>
          <p className="mt-1 max-w-[560px] text-[12px] leading-[1.55] text-text-3">{copy.desc}</p>
          <p className="mt-1 font-mono text-[10.5px] text-text-4">{copy.path}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <GhostButton danger disabled={st.files.length === 0} onClick={() => setClearOpen(true)}>
            <Trash2 className="h-3.5 w-3.5" /> 清空记忆
          </GhostButton>
          <AgentStatusLine state={st} />
        </div>
      </div>

      <div className="mb-3">
        <ProtoControls level={level} currentFile={selected} />
      </div>

      <div className="grid overflow-hidden rounded-[10px] border border-hairline" style={{ ...CARD_STYLE, gridTemplateColumns: "220px minmax(0,1fr)", minHeight: 380 }}>
        {/* 文件列表 */}
        <aside className="flex min-h-0 flex-col border-r border-hairline-soft">
          {st.files.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-10 text-center">
              <FileText className="h-5 w-5 text-text-4" />
              <p className="text-[11.5px] leading-[1.55] text-text-4">目录是空的</p>
            </div>
          ) : (
            <ul className="flex-1 overflow-y-auto py-1.5">
              {index && <FileRow file={index} selected={selected === INDEX_FILE} onClick={() => setSelected(INDEX_FILE)} pinned />}
              {topics.length > 0 && (
                <li className="px-3 pb-1 pt-2.5">
                  <Kicker tone="muted">Topics · {topics.length}</Kicker>
                </li>
              )}
              {topics.map((f) => (
                <FileRow key={f.name} file={f} selected={selected === f.name} onClick={() => setSelected(f.name)} />
              ))}
            </ul>
          )}
          <div className="border-t border-hairline-soft p-2">
            <button type="button" onClick={createNew} className="flex w-full items-center gap-1.5 rounded-[6px] px-2 py-1.5 text-[12px] text-text-3 transition-colors hover:bg-bg-grad-a hover:text-text">
              <Plus className="h-3.5 w-3.5" /> 新建主题文件
            </button>
          </div>
        </aside>

        {/* 编辑器 */}
        {file ? (
          <Editor key={file.name} level={level} file={file} onDelete={() => setDeleteName(file.name)} />
        ) : (
          <div className="flex flex-col items-center justify-center gap-3 px-10 py-12 text-center">
            <p className="max-w-[380px] text-[12.5px] leading-[1.65] text-text-3">{copy.empty}</p>
            <GhostButton onClick={createNew}>
              <Plus className="h-3.5 w-3.5" /> 自己先写一条
            </GhostButton>
          </div>
        )}
      </div>

      <ClearMemoryDialog open={clearOpen} level={level} count={st.files.length} onCancel={() => setClearOpen(false)} onDone={() => setClearOpen(false)} />
      <ConfirmDialog
        open={deleteName !== null}
        tone="danger"
        title={`删除 ${deleteName ?? ""}？`}
        description={deleteName === INDEX_FILE ? "删除索引后 Agent 在会话开始时将看不到任何记忆条目；主题文件仍保留但不会被主动查阅。" : "索引里指向它的那一行不会自动删除，Agent 下次查阅时会发现文件不存在。"}
        confirmLabel="删除文件"
        onConfirm={() => {
          if (deleteName) memoryActions.remove(level, deleteName);
          setDeleteName(null);
        }}
        onCancel={() => setDeleteName(null)}
      />
    </section>
  );
}

function FileRow({ file, selected, onClick, pinned }: { file: MemoryFile; selected: boolean; onClick: () => void; pinned?: boolean }) {
  const parsed = pinned ? null : parseTopic(file);
  const stats = pinned ? indexStats(file.content) : null;
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={"flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors " + (selected ? "bg-accent-dim" : "hover:bg-bg-grad-a")}
        style={selected ? { boxShadow: "inset 2px 0 0 var(--color-accent)" } : undefined}
      >
        <span className="flex w-full items-center gap-1.5">
          <FileText className={"h-3.5 w-3.5 shrink-0 " + (selected ? "text-accent-2" : "text-text-4")} />
          <span className={"min-w-0 flex-1 truncate font-mono text-[11.5px] " + (selected ? "text-text" : "text-text-2")}>{file.name}</span>
          {parsed && <TypeBadge type={parsed.type} size="xs" />}
        </span>
        <span className="flex w-full items-center gap-1.5 pl-5 text-[10.5px] text-text-4">
          {pinned && stats ? (
            <span className={stats.over ? "text-danger-2" : ""}>索引 · {stats.lines}/{INDEX_LINE_LIMIT} 行</span>
          ) : (
            <span className="truncate">{parsed?.description || "（无说明）"}</span>
          )}
        </span>
        <span className="pl-5 text-[10px] text-text-4">
          {file.modifiedBy === "agent" ? "Agent" : "你"} · {relTime(file.modifiedAt)}
        </span>
      </button>
    </li>
  );
}

function Editor({ level, file, onDelete }: { level: MemoryLevel; file: MemoryFile; onDelete: () => void }) {
  const [draft, setDraft] = useState(file.content);
  const [loadedAt, setLoadedAt] = useState(file.modifiedAt);
  const [conflict, setConflict] = useState(false);
  const [showTheirs, setShowTheirs] = useState(false);
  const dirty = draft !== file.content && !(conflict && showTheirs);
  const changedUnderneath = file.modifiedAt !== loadedAt;
  const stats = file.name === INDEX_FILE ? indexStats(draft) : null;

  // 未改动时静默跟随外部更新；已改动则保留草稿，等保存时判冲突。
  useEffect(() => {
    if (!dirty && file.modifiedAt !== loadedAt) {
      setDraft(file.content);
      setLoadedAt(file.modifiedAt);
      setConflict(false);
    }
  }, [file.content, file.modifiedAt, dirty, loadedAt]);

  const save = (force = false) => {
    const r = memoryActions.save(level, file.name, draft, loadedAt, force);
    if (r === "conflict") {
      setConflict(true);
      return;
    }
    setConflict(false);
    setShowTheirs(false);
    setLoadedAt(Date.now());
  };
  const takeTheirs = () => {
    setDraft(file.content);
    setLoadedAt(file.modifiedAt);
    setConflict(false);
    setShowTheirs(false);
  };

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-hairline-soft px-3 py-2">
        <span className="font-mono text-[12px] text-text">{file.name}</span>
        <span className="text-[10.5px] text-text-4">
          {file.modifiedBy === "agent" ? "Agent" : "你"}写于 {relTime(file.modifiedAt)}
        </span>
        {stats && (
          <span className={"ml-1 font-mono text-[10px] " + (stats.over ? "text-danger-2" : "text-text-4")}>
            {stats.lines}/{INDEX_LINE_LIMIT} 行 · {(stats.bytes / 1024).toFixed(1)}/25 KB
          </span>
        )}
        <span className="flex-1" />
        <GhostButton onClick={() => setDraft(file.content)} disabled={!dirty}>
          <RotateCcw className="h-3.5 w-3.5" /> 撤销
        </GhostButton>
        <GhostButton danger onClick={onDelete}>
          <Trash2 className="h-3.5 w-3.5" />
        </GhostButton>
        <button
          type="button"
          onClick={() => save()}
          disabled={!dirty}
          className="inline-flex items-center gap-1.5 rounded-[8px] px-3 py-1.5 text-[12px] font-semibold disabled:opacity-40"
          style={{ color: "oklch(0.14 0 0)", background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))" }}
        >
          <Save className="h-3.5 w-3.5" /> 保存
        </button>
      </div>

      {(conflict || (dirty && changedUnderneath)) && (
        <div className="flex flex-wrap items-center gap-2 border-b border-warm/40 bg-warm/10 px-3 py-2 text-[11.5px] text-warm">
          <AlertTriangle className="h-3.5 w-3.5" />
          <span>Agent 在你编辑期间改过这个文件（{relTime(file.modifiedAt)}）。{conflict ? "刚才没有保存。" : ""}</span>
          <span className="flex-1" />
          <button type="button" className="underline underline-offset-2" onClick={() => setShowTheirs((v) => !v)}>
            {showTheirs ? "收起 Agent 版本" : "看 Agent 版本"}
          </button>
          <button type="button" className="underline underline-offset-2" onClick={() => save(true)}>
            以我的为准覆盖
          </button>
          <button type="button" className="underline underline-offset-2" onClick={takeTheirs}>
            丢弃我的，载入 Agent 版本
          </button>
        </div>
      )}

      <div className={"grid min-h-0 flex-1 " + (showTheirs ? "grid-cols-2" : "grid-cols-1")}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          className={INPUT_CLS + " min-h-[320px] resize-none rounded-none border-0 bg-transparent font-mono text-[12px] leading-[1.6] focus:border-0"}
        />
        {showTheirs && (
          <pre className="min-h-0 overflow-auto border-l border-hairline-soft bg-bg-grad-b/40 px-3 py-2 font-mono text-[12px] leading-[1.6] text-text-3">
            {file.content}
          </pre>
        )}
      </div>
    </div>
  );
}
