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
 * `pane`: detail columns in full-bleed sections; editors fill body height and scroll internally.
 * `flow`: scroll with width-limited page bodies; editors expand with content up to half viewport height, fitting even the shortest viewports.
 */
export type MemoryEditorLayout = "pane" | "flow";

interface MemoryFileEditorProps {
  scope: AgentMemoryScope;
  entry: MemoryEntry;
  /** Server memory-directory path, combined with filenames to display full paths. */
  dir: string;
  layout: MemoryEditorLayout;
  /** Refetch file lists after successful saves, updating descriptions, modification times and index line counts. */
  onSaved: () => void;
  onDelete: () => void;
  onClear: () => void;
}

/**
 * Raw editors for individual memory files: unrendered Markdown text including frontmatter. Each file is one edit unit;
 * inline bars save/discard unsaved changes, and leave guards ask before switching files/tabs or leaving.
 *
 * Callers remount by filename (`key`); unsaved changes and loading states belong to previous files.
 * Saves are overwrite-only PUTs without server conflict detection; last writers win.
 */
export function MemoryFileEditor({ scope, entry, dir, layout, onSaved, onDelete, onClear }: MemoryFileEditorProps) {
  const { t } = useTranslation("dashboard");
  const [content, setContent] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const filename = entry.name;

  // react-doctor-disable-next-line react-doctor/no-set-state-after-await-in-effect -- Each load owns an AbortController; both post-await state writes recheck signal.aborted, preventing stale results from writing back
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const raw = await API.getAgentMemoryFile(scope, filename, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setContent(raw);
      } catch (err) {
        if (controller.signal.aborted) return;
        // Listed files may have been deleted by Agent or become unreadable; without error states, loading would persist forever.
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

  // Detail bodies have no padding; editors supply it. On width-limited pages, bodies align with file headers.
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
      // Memory is raw Markdown; use monospace as for code inputs.
      mono
      value={unit.value}
      aria-label={filename}
      spellCheck={false}
      onChange={(event) => unit.setValue(event.target.value)}
      className={layout === "pane" ? "max-h-none min-h-40 flex-1" : "max-h-[50cqh] min-h-48"}
    />
  );

  const body = (
    // Limit editor width on wide windows so raw Markdown lines do not span entire columns.
    <div className={cn("flex flex-col gap-3", layout === "pane" && "h-full min-h-72 max-w-252 px-6 py-4")}>
      <div className="flex min-h-0 flex-1 flex-col">{editor}</div>
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
