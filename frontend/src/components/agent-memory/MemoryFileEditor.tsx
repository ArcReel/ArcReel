import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Eraser, Loader2, MoreHorizontal, Trash2 } from "lucide-react";

import { API } from "@/api";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import type { AgentMemoryScope } from "@/types/agent-memory";
import { errMsg } from "@/utils/async";
import { formatShortDateTime } from "@/utils/date-format";

import { INDEX_LINE_LIMIT, memoryFilePath, type MemoryEntry } from "./memory-files";

/**
 * `pane`：全出血区段的详情栏，编辑框占满正文高度、在框内滚动。
 * `flow`：限宽页里随页面主体滚动，编辑框随内容撑高到上限（半个视口高，整块编辑器在最矮的视口里也放得下）。
 */
export type MemoryEditorLayout = "pane" | "flow";

interface MemoryFileEditorProps {
  scope: AgentMemoryScope;
  entry: MemoryEntry;
  /** 记忆目录的服务端路径，与文件名拼成完整路径展示。 */
  dir: string;
  layout: MemoryEditorLayout;
  /** 保存成功后重取文件列表（说明、修改时间与索引行数随之更新）。 */
  onSaved: () => void;
  onDelete: () => void;
  onClear: () => void;
}

/**
 * 单个记忆文件的原文编辑器：含 frontmatter 的 Markdown 纯文本，不渲染。一个文件是一个编辑单元，
 * 未保存修改由内联提示条保存或放弃，切换文件、切换分页或离开时经离开拦截询问。
 *
 * 由调用点按文件名 remount（`key`），未保存修改与加载状态都属于上一个文件。
 * 保存是纯覆盖 PUT，服务端无冲突检测，后写者生效。
 */
export function MemoryFileEditor({ scope, entry, dir, layout, onSaved, onDelete, onClear }: MemoryFileEditorProps) {
  const { t } = useTranslation("dashboard");
  const [content, setContent] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const filename = entry.name;

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const raw = await API.getAgentMemoryFile(scope, filename, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setContent(raw);
      } catch (err) {
        if (controller.signal.aborted) return;
        // 列表里的文件可能已被 Agent 删除或读不出来；不落错误态就会永远停在加载态。
        setLoadError(errMsg(err));
      }
    };
    void load();
    return () => controller.abort();
  }, [attempt, filename, scope]);

  const header = (
    <EditorHeader
      entry={entry}
      path={memoryFilePath(dir, filename)}
      heading={layout === "pane" ? "h2" : "h3"}
      onDelete={onDelete}
      onClear={onClear}
    />
  );

  // 详情栏的正文没有内边距，由编辑器自己给；限宽页里正文贴齐文件头
  const inset = layout === "pane" ? "px-6 py-4" : "";
  let body: ReactNode;
  if (loadError !== null) {
    body = (
      <div role="alert" className={cn("flex flex-col items-start gap-3", inset)}>
        <p className="text-sm text-destructive">{t("agent_memory_load_failed", { message: loadError })}</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        >
          {t("common:retry")}
        </Button>
      </div>
    );
  } else if (content === null) {
    body = (
      <p className={cn("flex items-center gap-2 text-sm text-muted-foreground", inset)}>
        <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
        {t("common:loading")}
      </p>
    );
  } else {
    return (
      <LoadedMemoryEditor
        scope={scope}
        entry={entry}
        dir={dir}
        content={content}
        layout={layout}
        onSaved={onSaved}
        onDelete={onDelete}
        onClear={onClear}
      />
    );
  }

  if (layout === "pane") return <DetailPane header={header}>{body}</DetailPane>;
  return (
    <section className="flex min-w-0 flex-col gap-4">
      {header}
      {body}
    </section>
  );
}

function EditorHeader({
  entry,
  path,
  heading: Heading,
  saving = false,
  onDelete,
  onClear,
}: {
  entry: MemoryEntry;
  path: string;
  heading: "h2" | "h3";
  saving?: boolean;
  onDelete: () => void;
  onClear: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const meta =
    entry.kind === "index"
      ? entry.overLimit
        ? t("agent_memory_index_over_limit", { count: entry.lineCount, limit: INDEX_LINE_LIMIT })
        : t("agent_memory_index_stats", { count: entry.lineCount, limit: INDEX_LINE_LIMIT })
      : t("agent_memory_modified_at", { time: formatShortDateTime(entry.modifiedAt) ?? "" });

  return (
    <div className="flex items-start gap-3">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-2">
          <Heading className="flex min-w-0 font-mono text-base font-medium">
            <TruncatedText text={entry.name} />
          </Heading>
          {entry.kind === "topic" && entry.type !== null && (
            <Badge variant="secondary">{t(`agent_memory_type_${entry.type}`)}</Badge>
          )}
        </div>
        <p
          className={
            entry.kind === "index" && entry.overLimit ? "text-xs text-destructive" : "text-xs text-muted-foreground"
          }
        >
          {meta}
        </p>
        <TruncatedText text={path} className="font-mono text-xs text-muted-foreground" />
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button variant="outline" size="sm" disabled={saving} onClick={onDelete}>
          <Trash2 aria-hidden data-icon="inline-start" />
          {t("common:delete")}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            disabled={saving}
            render={<Button variant="ghost" size="icon-sm" aria-label={t("agent_memory_more_actions")} />}
          >
            <MoreHorizontal aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem variant="destructive" disabled={saving} onClick={onClear}>
              <Eraser aria-hidden />
              {t("agent_memory_clear_all")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

function LoadedMemoryEditor({
  scope,
  entry,
  dir,
  content,
  layout,
  onSaved,
  onDelete,
  onClear,
}: MemoryFileEditorProps & {
  content: string;
}) {
  const filename = entry.name;
  const { t } = useTranslation("dashboard");
  const save = useCallback(
    async (value: string) => {
      await API.saveAgentMemoryFile(scope, filename, value);
      onSaved();
    },
    [scope, filename, onSaved],
  );
  const unit = useEditUnit({
    source: content,
    save,
    leaveTitle: t("agent_memory_leave_title", { name: filename }),
  });

  const editor = (
    <Textarea
      value={unit.value}
      aria-label={filename}
      spellCheck={false}
      onChange={(event) => unit.setValue(event.target.value)}
      className={layout === "pane" ? "max-h-none min-h-40 flex-1" : "max-h-[50cqh] min-h-48"}
    />
  );

  const body = (
    // 宽窗口下编辑框限宽，Markdown 原文的行不会横跨整栏
    <div className={cn("flex flex-col gap-3", layout === "pane" && "h-full min-h-72 max-w-252 px-6 py-4")}>
      {/* 记忆是 Markdown 原文，等宽显示；Textarea 继承外层字体 */}
      <div className="flex min-h-0 flex-1 flex-col font-mono">{editor}</div>
      <UnsavedChangesBar unit={unit} className="shrink-0" />
    </div>
  );
  const header = (
    <EditorHeader
      entry={entry}
      path={memoryFilePath(dir, filename)}
      heading={layout === "pane" ? "h2" : "h3"}
      saving={unit.status === "saving"}
      onDelete={onDelete}
      onClear={onClear}
    />
  );
  if (layout === "pane") return <DetailPane header={header}>{body}</DetailPane>;
  return (
    <section className="flex min-w-0 flex-col gap-4">
      {header}
      {body}
    </section>
  );
}
