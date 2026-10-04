import { useCallback, useMemo, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { FileText, ListTree, Loader2, Plus } from "lucide-react";

import { API } from "@/api";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import type { SecondaryRailGroup } from "@/components/shared/master-detail/SecondaryRail";
import { Button } from "@/components/ui/button";
import { useAgentMemory, type AgentMemoryState } from "@/hooks/useAgentMemory";
import type { AgentMemoryScope } from "@/types/agent-memory";

import { MemoryConfirmDialog, type MemoryConfirmRequest } from "./MemoryConfirmDialog";
import { MemoryCreateForm } from "./MemoryCreateForm";
import { MemoryFileEditor, type MemoryEditorLayout } from "./MemoryFileEditor";
import {
  INDEX_FILENAME,
  INDEX_LINE_LIMIT,
  MEMORY_FILE_PARAM,
  NEW_MEMORY_FILE,
  memoryEntries,
  resolveSelection,
  type MemoryEntry,
  type MemorySelection,
} from "./memory-files";

export interface MemoryWorkspace extends AgentMemoryState {
  entries: MemoryEntry[];
  selection: MemorySelection;
  /** 二级栏（或限宽页里的文件列表）的条目：索引置顶、主题文件按修改时间倒序，末尾是「新建记忆文件」。 */
  group: SecondaryRailGroup;
  activeId: string;
  hrefFor: (file: string) => string;
}

/**
 * 记忆文件区的数据与选中项：列表来自 `useAgentMemory`，选中文件记在地址的 `file=` 参数里，
 * 切换文件经路由，离开拦截因此覆盖切换。`hrefFor` 给出选中某个文件（或新建表单）的地址，需传稳定引用。
 */
export function useMemoryWorkspace(scope: AgentMemoryScope, hrefFor: (file: string) => string): MemoryWorkspace {
  const { t } = useTranslation("dashboard");
  const memory = useAgentMemory(scope);
  const search = useSearch();
  const entries = useMemo(() => memoryEntries(memory.overview), [memory.overview]);
  const selection = resolveSelection(new URLSearchParams(search).get(MEMORY_FILE_PARAM), entries);

  const group = useMemo<SecondaryRailGroup>(
    () => ({
      id: "files",
      label: t("agent_memory_files"),
      items: entries.map((entry) => ({
        id: entry.name,
        label: entry.name,
        description:
          entry.kind === "index"
            ? t(entry.overLimit ? "agent_memory_index_over_limit" : "agent_memory_index_stats", {
                count: entry.lineCount,
                limit: INDEX_LINE_LIMIT,
              })
            : (entry.description ?? t("agent_memory_no_description")),
        icon: entry.kind === "index" ? <ListTree className="size-4" /> : <FileText className="size-4" />,
        href: hrefFor(entry.name),
      })),
      action: {
        id: NEW_MEMORY_FILE,
        label: t("agent_memory_new_file"),
        icon: <Plus className="size-4" />,
        href: hrefFor(NEW_MEMORY_FILE),
      },
    }),
    [entries, hrefFor, t],
  );

  return {
    ...memory,
    entries,
    selection,
    group,
    activeId: selection.kind === "file" ? selection.entry.name : NEW_MEMORY_FILE,
    hrefFor,
  };
}

/** 文件列表加载失败或首次加载中；加载成功时返回 null，由调用方渲染文件区。 */
export function MemoryLoadState({ workspace, className }: { workspace: MemoryWorkspace; className?: string }) {
  const { t } = useTranslation("dashboard");
  if (workspace.error !== null) {
    return (
      <div role="alert" className={className}>
        <div className="flex flex-col items-start gap-3">
          <p className="text-sm text-destructive">{t("agent_memory_load_failed", { message: workspace.error })}</p>
          <Button variant="outline" size="sm" onClick={() => void workspace.reload()}>
            {t("common:retry")}
          </Button>
        </div>
      </div>
    );
  }
  if (workspace.overview === null) {
    return (
      <p className={className}>
        <span className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </span>
      </p>
    );
  }
  return null;
}

/**
 * 选中项的详情：文件编辑器或新建表单，以及删除、清空的确认。
 * 删除与清空之后不改地址，被删的文件从列表消失后选中项在渲染期落到第一项。
 */
export function MemoryDetail({ workspace, layout }: { workspace: MemoryWorkspace; layout: MemoryEditorLayout }) {
  const { t } = useTranslation("dashboard");
  const [, navigate] = useLocation();
  const [confirm, setConfirm] = useState<MemoryConfirmRequest | null>(null);
  const { scope, reload, entries, selection, hrefFor } = workspace;
  const dir = workspace.overview?.path ?? "";

  const onSaved = useCallback(() => void reload(), [reload]);

  const requestDelete = (name: string) =>
    setConfirm({
      title: t("agent_memory_delete_confirm_title", { name }),
      description:
        name === INDEX_FILENAME ? t("agent_memory_delete_index_confirm_desc") : t("agent_memory_delete_confirm_desc"),
      confirmLabel: t("common:delete"),
      run: async () => {
        await API.deleteAgentMemoryFile(scope, name);
        await reload();
      },
    });

  const requestClear = () =>
    setConfirm({
      title:
        scope.level === "user"
          ? t("agent_memory_clear_user_confirm_title")
          : t("agent_memory_clear_project_confirm_title"),
      description: t("agent_memory_clear_confirm_desc", { count: entries.length }),
      confirmLabel: t("agent_memory_clear"),
      run: async () => {
        await API.clearAgentMemory(scope);
        await reload();
      },
    });

  const heading = layout === "pane" ? "h2" : "h3";
  let detail;
  if (selection.kind === "file") {
    detail = (
      <MemoryFileEditor
        // 换文件时整块重建：未保存的修改与加载状态都属于上一个文件
        key={selection.entry.name}
        scope={scope}
        entry={selection.entry}
        dir={dir}
        layout={layout}
        onSaved={onSaved}
        onDelete={() => requestDelete(selection.entry.name)}
        onClear={requestClear}
      />
    );
  } else {
    const form = (
      <MemoryCreateForm
        scope={scope}
        entries={entries}
        empty={selection.empty}
        // 限宽页的分页页头已有说明，全出血分区没有页头
        intro={layout === "pane" ? t("agent_memory_user_desc") : undefined}
        heading={heading}
        onCreated={async (name, signal) => {
          // 列表先包含新文件再选中它，否则地址会先落到第一项
          await reload();
          if (!signal.aborted) navigate(hrefFor(name), { replace: true });
        }}
      />
    );
    detail =
      layout === "pane" ? (
        <DetailPane>
          <div className="px-6 py-6">{form}</div>
        </DetailPane>
      ) : (
        form
      );
  }

  return (
    <>
      {detail}
      <MemoryConfirmDialog request={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}
