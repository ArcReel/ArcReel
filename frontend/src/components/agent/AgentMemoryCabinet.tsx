import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Loader2, Plus, Trash2 } from "lucide-react";

import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { useAgentMemory } from "@/hooks/useAgentMemory";
import { useAppStore } from "@/stores/app-store";
import type { AgentMemoryFile, AgentMemoryScope, AgentMemoryType } from "@/types/agent-memory";
import { errMsg } from "@/utils/async";
import { formatShortDateTime, parseIsoTimestamp } from "@/utils/date-format";

/** 索引文件不出现在列表响应的 `files` 里，由 `index` 单列统计，前端据此把它置顶为一条虚拟行。 */
const INDEX_FILENAME = "MEMORY.md";
const INDEX_LINE_LIMIT = 200;

/** 与 `lib/agent_memory_store.py` 同一把尺子；本地预校验只为让重名与拼错就地可见，判据仍在服务端。 */
const FILENAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/;
const FILENAME_MAX_LENGTH = 100;

const TYPE_TONE: Record<AgentMemoryType, string> = {
  user: "#9565c7",
  feedback: "#c48225",
  project: "#248fcc",
  reference: "#339c6d",
};

const DANGER_GHOST_BTN_CLS = `${GHOST_BTN_CLS} enabled:hover:border-danger/50 enabled:hover:text-danger-2`;

function buildTemplate(filename: string, level: AgentMemoryScope["level"], description: string, body: string): string {
  const name = filename.replace(/\.md$/, "");
  return `---\nname: ${name}\ndescription: ${description}\ntype: ${level}\n---\n\n${body}\n`;
}

export interface AgentMemoryCabinetProps {
  scope: AgentMemoryScope;
  /**
   * `section`：自渲染 SectionShell 同款头部，用于全局设置页的 Agent 分区。
   * `card`：标题与说明由外层卡片提供，组件只补落盘路径与清空入口。
   */
  frame: "section" | "card";
}

/**
 * 两级 Agent 记忆共用的文件柜：左侧文件列表、右侧原文编辑器。
 *
 * 用户记忆与项目记忆只差 `scope`——文案与请求路径由它派生，交互与布局完全相同。
 */
export function AgentMemoryCabinet({ scope, frame }: AgentMemoryCabinetProps) {
  const { t } = useTranslation("dashboard");
  const { overview, loading, error, scope: target, reload } = useAgentMemory(scope);
  const level = target.level;

  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newNameError, setNewNameError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const newNameRef = useRef<HTMLInputElement | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const index = overview?.index ?? null;
  const topics = useMemo(
    () =>
      [...(overview?.files ?? [])].sort(
        (a, b) => parseIsoTimestamp(b.modified_at).getTime() - parseIsoTimestamp(a.modified_at).getTime(),
      ),
    [overview],
  );
  const names = useMemo(
    () => [...(index?.exists ? [INDEX_FILENAME] : []), ...topics.map((file) => file.name)],
    [index?.exists, topics],
  );
  // 选中项在渲染期收敛而不是用 effect 纠正：删除与清空之后被选文件可能已经不在列表里。
  const active = selected !== null && names.includes(selected) ? selected : (names[0] ?? null);

  const reportFailure = useCallback(
    (err: unknown) => {
      useAppStore.getState().pushToast(t("agent_memory_action_failed", { message: errMsg(err) }), "error");
    },
    [t],
  );

  const handleCreate = useCallback(async () => {
    const name = newName.trim();
    if (name.length > FILENAME_MAX_LENGTH || !FILENAME_PATTERN.test(name)) {
      setNewNameError(t("agent_memory_name_invalid"));
      return;
    }
    if (names.includes(name)) {
      setNewNameError(t("agent_memory_name_duplicate"));
      return;
    }
    setBusy(true);
    try {
      const template = buildTemplate(
        name,
        level === "user" ? "user" : "project",
        t("agent_memory_template_description"),
        t("agent_memory_template_body"),
      );
      await API.saveAgentMemoryFile(target, name, template);
      setCreating(false);
      setNewName("");
      setNewNameError(null);
      setSelected(name);
      await reload();
      useAppStore.getState().pushToast(t("agent_memory_created"), "success");
    } catch (err) {
      reportFailure(err);
    } finally {
      setBusy(false);
    }
  }, [level, names, newName, reload, reportFailure, t, target]);

  const handleDelete = useCallback(async () => {
    if (pendingDelete === null) return;
    setBusy(true);
    try {
      await API.deleteAgentMemoryFile(target, pendingDelete);
      setPendingDelete(null);
      await reload();
      useAppStore.getState().pushToast(t("agent_memory_deleted"), "success");
    } catch (err) {
      reportFailure(err);
    } finally {
      setBusy(false);
    }
  }, [pendingDelete, reload, reportFailure, t, target]);

  const handleClear = useCallback(async () => {
    setBusy(true);
    try {
      await API.clearAgentMemory(target);
      setClearOpen(false);
      await reload();
      useAppStore.getState().pushToast(t("agent_memory_cleared"), "success");
    } catch (err) {
      reportFailure(err);
    } finally {
      setBusy(false);
    }
  }, [reload, reportFailure, t, target]);

  // 新建行是就地展开的输入框，展开后把光标交给它；autoFocus 属性受 jsx-a11y 禁用。
  useEffect(() => {
    if (creating) newNameRef.current?.focus();
  }, [creating]);

  const startCreating = () => {
    setCreating(true);
    setNewName("");
    setNewNameError(null);
  };

  const clearButton = (
    <button
      type="button"
      className={`${DANGER_GHOST_BTN_CLS} shrink-0`}
      disabled={names.length === 0 || busy}
      onClick={() => setClearOpen(true)}
    >
      <Trash2 className="h-3.5 w-3.5" aria-hidden />
      {t("agent_memory_clear")}
    </button>
  );

  const path = overview?.path ?? "";
  const title = level === "user" ? t("agent_memory_user_title") : t("agent_memory_project_title");

  return (
    <section>
      {frame === "section" ? (
        <div className="mb-3.5 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
              Agent Memory
            </div>
            <h3 className="mt-1 text-[14.5px] font-medium text-text">{title}</h3>
            <p className="mt-1 max-w-[560px] text-[12px] leading-[1.55] text-text-3">
              {level === "user" ? t("agent_memory_user_desc") : t("agent_memory_project_desc")}
            </p>
            {path && <p className="mt-1 break-all font-mono text-[10.5px] text-text-4">{path}</p>}
          </div>
          {clearButton}
        </div>
      ) : (
        <div className="mb-3 flex items-start justify-between gap-3">
          <p className="min-w-0 break-all font-mono text-[10.5px] text-text-4">{path}</p>
          {clearButton}
        </div>
      )}

      {error !== null ? (
        <div
          className="flex flex-col items-start gap-2 rounded-[10px] border border-hairline p-4"
          style={CARD_STYLE}
        >
          <p className="text-[12px] text-danger-2">{t("agent_memory_load_failed", { message: error })}</p>
          <button type="button" className={GHOST_BTN_CLS} onClick={() => void reload()}>
            {t("common:retry")}
          </button>
        </div>
      ) : (
        <div
          className="grid overflow-hidden rounded-[10px] border border-hairline"
          style={{ ...CARD_STYLE, gridTemplateColumns: "220px minmax(0,1fr)", minHeight: 380 }}
        >
          <aside className="flex min-h-0 flex-col border-r border-hairline-soft">
            {loading && overview === null ? (
              <div className="flex flex-1 items-center justify-center gap-2 px-4 py-10 text-text-3">
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
                <span className="font-mono text-[11px] uppercase tracking-[0.14em]">{t("common:loading")}</span>
              </div>
            ) : names.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-10 text-center">
                <FileText className="h-5 w-5 text-text-4" aria-hidden />
                <p className="text-[11.5px] leading-[1.55] text-text-4">{t("agent_memory_files_empty")}</p>
              </div>
            ) : (
              <ul className="flex-1 overflow-y-auto py-1.5">
                {index?.exists && (
                  <IndexRow
                    lineCount={index.line_count}
                    overLimit={index.over_limit}
                    selected={active === INDEX_FILENAME}
                    onSelect={() => setSelected(INDEX_FILENAME)}
                  />
                )}
                {topics.length > 0 && (
                  <li className="px-3 pb-1 pt-2.5">
                    <span className="text-[10.5px] text-text-4">
                      {t("agent_memory_topics", { count: topics.length })}
                    </span>
                  </li>
                )}
                {topics.map((file) => (
                  <TopicRow
                    key={file.name}
                    file={file}
                    selected={active === file.name}
                    onSelect={() => setSelected(file.name)}
                  />
                ))}
              </ul>
            )}
            <div className="border-t border-hairline-soft p-2">
              {creating ? (
                <div className="space-y-1.5">
                  <input
                    ref={newNameRef}
                    value={newName}
                    aria-label={t("agent_memory_new_file")}
                    placeholder={t("agent_memory_new_file_placeholder")}
                    disabled={busy}
                    onChange={(e) => {
                      setNewName(e.target.value);
                      setNewNameError(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleCreate();
                      if (e.key === "Escape") setCreating(false);
                    }}
                    className={`${INPUT_CLS} px-2 py-1 font-mono text-[11.5px]`}
                  />
                  {newNameError !== null && (
                    <p className="text-[10.5px] leading-[1.5] text-danger-2">{newNameError}</p>
                  )}
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => void handleCreate()}
                      disabled={busy}
                      className={ACCENT_BTN_SM_CLS}
                      style={ACCENT_BUTTON_STYLE}
                    >
                      {t("agent_memory_create")}
                    </button>
                    <button type="button" onClick={() => setCreating(false)} className={GHOST_BTN_CLS}>
                      {t("common:cancel")}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={startCreating}
                  className="flex w-full items-center gap-1.5 rounded-[6px] px-2 py-1.5 text-[12px] text-text-3 transition-colors hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  <Plus className="h-3.5 w-3.5" aria-hidden />
                  {t("agent_memory_new_file")}
                </button>
              )}
            </div>
          </aside>

          {active !== null ? (
            <MemoryEditor
              key={active}
              scope={target}
              filename={active}
              lineCount={active === INDEX_FILENAME ? (index?.line_count ?? 0) : null}
              overLimit={active === INDEX_FILENAME && (index?.over_limit ?? false)}
              onSaved={reload}
              onDelete={() => setPendingDelete(active)}
              onFailure={reportFailure}
            />
          ) : (
            <div className="flex flex-col items-center justify-center gap-3 px-10 py-12 text-center">
              <p className="max-w-[380px] text-[12.5px] leading-[1.65] text-text-3">
                {t("agent_memory_empty_hint")}
              </p>
              <button type="button" onClick={startCreating} className={GHOST_BTN_CLS}>
                <Plus className="h-3.5 w-3.5" aria-hidden />
                {t("agent_memory_new_file")}
              </button>
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        tone="danger"
        title={t("agent_memory_delete_confirm_title", { name: pendingDelete ?? "" })}
        description={
          <div className="space-y-2">
            <p>
              {pendingDelete === INDEX_FILENAME
                ? t("agent_memory_delete_index_confirm_desc")
                : t("agent_memory_delete_confirm_desc")}
            </p>
            <p>{t("agent_memory_session_notice")}</p>
          </div>
        }
        confirmLabel={t("common:delete")}
        loading={busy}
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />
      <ConfirmDialog
        open={clearOpen}
        tone="danger"
        title={
          level === "user"
            ? t("agent_memory_clear_user_confirm_title")
            : t("agent_memory_clear_project_confirm_title")
        }
        description={
          <div className="space-y-2">
            <p>{t("agent_memory_clear_confirm_desc", { count: names.length })}</p>
            <p>{t("agent_memory_session_notice")}</p>
          </div>
        }
        confirmLabel={t("agent_memory_clear")}
        loading={busy}
        onConfirm={handleClear}
        onCancel={() => setClearOpen(false)}
      />
    </section>
  );
}

function rowClass(selected: boolean): string {
  return `flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
    selected ? "bg-accent-dim" : "hover:bg-bg-grad-a"
  }`;
}

const SELECTED_ROW_STYLE = { boxShadow: "inset 2px 0 0 var(--color-accent)" };

function IndexRow({
  lineCount,
  overLimit,
  selected,
  onSelect,
}: {
  lineCount: number;
  overLimit: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected}
        className={rowClass(selected)}
        style={selected ? SELECTED_ROW_STYLE : undefined}
      >
        <span className="flex w-full items-center gap-1.5">
          <FileText className={`h-3.5 w-3.5 shrink-0 ${selected ? "text-accent-2" : "text-text-4"}`} aria-hidden />
          <span className={`min-w-0 flex-1 truncate font-mono text-[11.5px] ${selected ? "text-text" : "text-text-2"}`}>
            {INDEX_FILENAME}
          </span>
        </span>
        <span className={`pl-5 text-[10.5px] ${overLimit ? "text-danger-2" : "text-text-4"}`}>
          {t("agent_memory_index_stats", { count: lineCount, limit: INDEX_LINE_LIMIT })}
        </span>
      </button>
    </li>
  );
}

function TopicRow({ file, selected, onSelect }: { file: AgentMemoryFile; selected: boolean; onSelect: () => void }) {
  const { t } = useTranslation("dashboard");
  const type = file.frontmatter?.type ?? null;
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected}
        className={rowClass(selected)}
        style={selected ? SELECTED_ROW_STYLE : undefined}
      >
        <span className="flex w-full items-center gap-1.5">
          <FileText className={`h-3.5 w-3.5 shrink-0 ${selected ? "text-accent-2" : "text-text-4"}`} aria-hidden />
          <span className={`min-w-0 flex-1 truncate font-mono text-[11.5px] ${selected ? "text-text" : "text-text-2"}`}>
            {file.name}
          </span>
          {type !== null && <TypeBadge type={type} />}
        </span>
        <span className="w-full truncate pl-5 text-left text-[10.5px] text-text-4">
          {file.frontmatter?.description ?? t("agent_memory_no_description")}
        </span>
        <span className="pl-5 text-[10px] text-text-4">{formatShortDateTime(file.modified_at) ?? ""}</span>
      </button>
    </li>
  );
}

function TypeBadge({ type }: { type: AgentMemoryType }) {
  const { t } = useTranslation("dashboard");
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-[4px] px-1 py-px font-mono text-[9px] font-bold uppercase tracking-[0.12em]"
      style={{ background: `${TYPE_TONE[type]}26`, color: TYPE_TONE[type] }}
    >
      {t(`agent_memory_type_${type}`)}
    </span>
  );
}

interface MemoryEditorProps {
  scope: AgentMemoryScope;
  filename: string;
  /** 索引文件的行数；主题文件为 null。 */
  lineCount: number | null;
  overLimit: boolean;
  onSaved: () => Promise<void>;
  onDelete: () => void;
  onFailure: (err: unknown) => void;
}

/**
 * 单个记忆文件的原文编辑器：含 frontmatter 的 Markdown 纯文本，不渲染。
 *
 * 由调用点按文件名 remount（`key`），草稿因此随选中项自然重置；保存是纯覆盖 PUT，
 * 服务端无冲突检测，后写胜出。
 */
function MemoryEditor({ scope, filename, lineCount, overLimit, onSaved, onDelete, onFailure }: MemoryEditorProps) {
  const { t } = useTranslation("dashboard");
  const [content, setContent] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const raw = await API.getAgentMemoryFile(scope, filename, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setContent(raw);
        setDraft(raw);
      } catch (err) {
        if (controller.signal.aborted) return;
        // 列表里的文件可能已被 Agent 删除或读不出来；不落错误态就会永远停在加载态。
        setLoadError(errMsg(err));
        onFailure(err);
      }
    };
    void load();
    return () => controller.abort();
  }, [attempt, filename, onFailure, scope]);

  const dirty = content !== null && draft !== content;

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await API.saveAgentMemoryFile(scope, filename, draft);
      setContent(draft);
      await onSaved();
      useAppStore.getState().pushToast(t("agent_memory_saved"), "success");
    } catch (err) {
      onFailure(err);
    } finally {
      setSaving(false);
    }
  }, [draft, filename, onFailure, onSaved, scope, t]);

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-hairline-soft px-3 py-2">
        <span className="font-mono text-[12px] text-text">{filename}</span>
        {lineCount !== null && (
          <span className={`font-mono text-[10px] ${overLimit ? "text-danger-2" : "text-text-4"}`}>
            {t("agent_memory_index_stats", { count: lineCount, limit: INDEX_LINE_LIMIT })}
          </span>
        )}
        <span className="flex-1" />
        <button type="button" onClick={onDelete} disabled={saving} className={DANGER_GHOST_BTN_CLS}>
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
          {t("common:delete")}
        </button>
        <button
          type="button"
          onClick={() => setDraft(content ?? "")}
          disabled={!dirty || saving}
          className={GHOST_BTN_CLS}
        >
          {t("agent_memory_discard")}
        </button>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={!dirty || saving}
          className={ACCENT_BTN_SM_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {saving && <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />}
          {t("common:save")}
        </button>
      </div>

      {loadError !== null ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-10 text-center">
          <p className="text-[12px] text-danger-2">{t("agent_memory_load_failed", { message: loadError })}</p>
          <button
            type="button"
            className={GHOST_BTN_CLS}
            onClick={() => {
              setLoadError(null);
              setAttempt((n) => n + 1);
            }}
          >
            {t("common:retry")}
          </button>
        </div>
      ) : content === null ? (
        <div className="flex flex-1 items-center justify-center gap-2 px-4 py-10 text-text-3">
          <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
          <span className="font-mono text-[11px] uppercase tracking-[0.14em]">{t("common:loading")}</span>
        </div>
      ) : (
        <textarea
          value={draft}
          aria-label={filename}
          spellCheck={false}
          disabled={saving}
          onChange={(e) => setDraft(e.target.value)}
          className={`${INPUT_CLS} min-h-[320px] flex-1 resize-none rounded-none border-0 bg-transparent font-mono text-[12px] leading-[1.6]`}
        />
      )}
    </div>
  );
}
