import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { ChevronRight, Download, File, Folder, FolderOpen, Loader2, RefreshCw, Trash2 } from "lucide-react";

import { API } from "@/api";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAppStore } from "@/stores/app-store";
import type { FileViewEntry } from "@/types";
import { errMsg } from "@/utils/async";
import { formatShortDateTime } from "@/utils/date-format";
import { downloadBlob } from "@/utils/download";
import { formatFileSize } from "@/utils/file-size";
import { cn } from "cn";

import { FilePreview } from "./FilePreview";
import { TrashSection } from "./TrashSection";
import { canDeleteFile, ownerTarget } from "./file-view-model";

type DirectoryState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; entries: FileViewEntry[] };

/**
 * Loaded directories keyed by project-relative paths, empty string for root. Fetch children on expansion; `refreshKey` refetches loaded directories.
 * Missing results display loading; refresh retains old lists until new results arrive. Callers mark loading before retries.
 */
function useDirectories(projectName: string, refreshKey: number) {
  const [directories, setDirectories] = useState<Record<string, DirectoryState>>({});
  const controllersRef = useRef(new Map<string, AbortController>());

  useEffect(() => {
    const controllers = controllersRef.current;
    return () => {
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
    };
  }, []);

  const loadedRef = useRef(new Set<string>());

  const load = useCallback(
    (path: string) => {
      loadedRef.current.add(path);
      controllersRef.current.get(path)?.abort();
      const controller = new AbortController();
      controllersRef.current.set(path, controller);
      const { signal } = controller;
      API.getFileView(projectName, path, { signal })
        .then((listing) => {
          if (signal?.aborted) return;
          setDirectories((current) => ({ ...current, [path]: { status: "ready", entries: listing.entries } }));
        })
        .catch((error: unknown) => {
          if (signal?.aborted) return;
          setDirectories((current) => ({ ...current, [path]: { status: "error", message: errMsg(error) } }));
        });
    },
    [projectName],
  );

  // Manual refresh/trash changes (including Agent deletion/restoration) may move files in/out of trees: refetch loaded directories.
  const trashRevision = useAppStore((s) => s.trashRevision);
  useEffect(() => {
    const paths = loadedRef.current.size > 0 ? [...loadedRef.current] : [""];
    for (const path of paths) load(path);
  }, [load, trashRevision, refreshKey]);

  const retry = useCallback(
    (path: string) => {
      setDirectories((current) => ({ ...current, [path]: { status: "loading" } }));
      load(path);
    },
    [load],
  );

  // Remove trashed files from parent directories immediately; waiting for refetch could permit repeated deletion after occupancy clears.
  // In-flight directory requests may predate deletion; cancel first and trust subsequent server refetches.
  const dropEntry = useCallback((path: string) => {
    const parent = parentPath(path);
    controllersRef.current.get(parent)?.abort();
    setDirectories((current) => {
      const directory = current[parent];
      if (directory?.status !== "ready") return current;
      return { ...current, [parent]: { status: "ready", entries: directory.entries.filter((entry) => entry.path !== path) } };
    });
  }, []);

  return { directories, load, retry, dropEntry };
}

function parentPath(path: string): string {
  return path.slice(0, Math.max(path.lastIndexOf("/"), 0));
}

function findEntry(directories: Record<string, DirectoryState>, path: string): FileViewEntry | null {
  const directory = directories[parentPath(path)];
  return directory?.status === "ready" ? (directory.entries.find((entry) => entry.path === path) ?? null) : null;
}

type FileViewSection = "files" | "trash";

/**
 * Project Files view with files/trash sections.
 * Files: on-demand directory tree left, selected-file role/preview/download/owning-interface entry right.
 * Only unreferenced files can enter trash here; referenced files edit through owners, whole-source-unregistered sources through Episodes.
 */
export function FileView({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [section, setSection] = useState<FileViewSection>("files");
  // Mount trash on first opening; afterward both sections stay mounted and only hide, preserving in-flight write occupancy.
  const [trashMounted, setTrashMounted] = useState(false);
  // Refresh refetches mounted sections; both combine it with trash-invalidation signals as fetch triggers.
  const [refreshKey, setRefreshKey] = useState(0);

  // Section panels sit outside Tabs; manually connect tab/panel associations.
  const id = useId();
  const tabId = (value: FileViewSection) => `${id}-${value}-tab`;
  const panelId = (value: FileViewSection) => `${id}-${value}-panel`;
  const panelProps = (value: FileViewSection) => ({
    id: panelId(value),
    role: "tabpanel",
    "aria-labelledby": tabId(value),
    hidden: section !== value,
    className: "flex min-h-0 flex-1 flex-col",
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Sections/refresh align left: narrow-screen Agent panels overlay right canvas controls. */}
      <header className="flex shrink-0 flex-col gap-2 border-b border-border px-6 py-3">
        <div className="min-w-0">
          <h1 className="text-base font-semibold">{t("file_view_title")}</h1>
          <p className="text-sm text-muted-foreground">{t("file_view_description")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tabs
            value={section}
            onValueChange={(value: FileViewSection) => {
              setSection(value);
              if (value === "trash") setTrashMounted(true);
            }}
          >
            <TabsList aria-label={t("file_view_sections_aria")}>
              <TabsTrigger value="files" id={tabId("files")} aria-controls={panelId("files")}>
                {t("file_view_section_files")}
              </TabsTrigger>
              <TabsTrigger value="trash" id={tabId("trash")} aria-controls={trashMounted ? panelId("trash") : undefined}>
                {t("file_view_section_trash")}
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <Button variant="outline" size="sm" onClick={() => setRefreshKey((key) => key + 1)}>
            <RefreshCw data-icon="inline-start" aria-hidden />
            {t("file_view_refresh")}
          </Button>
        </div>
      </header>
      <div {...panelProps("files")}>
        <FilesSection projectName={projectName} refreshKey={refreshKey} />
      </div>
      {trashMounted ? (
        <div {...panelProps("trash")}>
          <TrashSection projectName={projectName} refreshKey={refreshKey} />
        </div>
      ) : null}
    </div>
  );
}

function FilesSection({ projectName, refreshKey }: { projectName: string; refreshKey: number }) {
  const { t } = useTranslation("dashboard");
  const { directories, load, retry, dropEntry } = useDirectories(projectName, refreshKey);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  // Store paths only; resolve entries from latest directory lists so refreshed details update/disappear with files.
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const selected = selectedPath === null ? null : findEntry(directories, selectedPath);
  const [checked, setChecked] = useState<ReadonlyMap<string, FileViewEntry>>(new Map());
  const [deleting, setDeleting] = useState(false);

  const toggle = (path: string) => {
    const next = new Set(expanded);
    if (next.has(path)) next.delete(path);
    else {
      next.add(path);
      if (directories[path]?.status === "error") retry(path);
      else if (!directories[path]) load(path);
    }
    setExpanded(next);
  };

  const toggleChecked = (entry: FileViewEntry) => {
    setChecked((current) => {
      const next = new Map(current);
      if (next.has(entry.path)) next.delete(entry.path);
      else next.set(entry.path, entry);
      return next;
    });
  };

  // Submit/restore each file separately; keep failed selections checked for retries.
  const moveToTrash = async (entries: FileViewEntry[]) => {
    if (deleting) return;
    setDeleting(true);
    try {
      await Promise.all(entries.map(async (entry) => {
        try {
          const { record } = await API.deleteFileViewEntry(projectName, entry.path);
          dropEntry(entry.path);
          setSelectedPath((current) => (current === entry.path ? null : current));
          setChecked((current) => {
            const next = new Map(current);
            next.delete(entry.path);
            return next;
          });
          useAppStore.getState().pushToast(t("file_view_moved_to_trash", { name: entry.name }), "info", {
            action: {
              label: t("trash_restore"),
              onClick: () => {
                API.restoreTrashRecord(projectName, record.id)
                  .catch((error: unknown) => {
                    useAppStore.getState().pushToast(t("trash_action_failed", { message: errMsg(error) }), "error");
                  })
                  .finally(() => useAppStore.getState().invalidateTrash());
              },
            },
          });
        } catch (error) {
          useAppStore.getState().pushToast(t("file_view_delete_failed", { name: entry.name, message: errMsg(error) }), "error");
        }
      }));
    } finally {
      setDeleting(false);
      useAppStore.getState().invalidateTrash();
    }
  };

  return (
    <div className="flex min-h-0 flex-1">
      <nav
        aria-label={t("file_view_tree_label")}
        className="relative flex w-[clamp(240px,32cqw,360px)] shrink-0 flex-col overflow-y-auto border-r px-2 py-3 [scrollbar-gutter:stable]"
      >
        {checked.size > 0 ? (
          <div className="flex flex-wrap items-center gap-2 px-2 pb-3">
            <Button variant="outline" size="sm" disabled={deleting} onClick={() => void moveToTrash([...checked.values()])}>
              {deleting ? <Loader2 data-icon="inline-start" aria-hidden className="animate-spin" /> : <Trash2 data-icon="inline-start" aria-hidden />}
              {t("file_view_delete_selected", { total: checked.size })}
            </Button>
            <Button variant="ghost" size="sm" disabled={deleting} onClick={() => setChecked(new Map())}>
              {t("file_view_clear_selection")}
            </Button>
          </div>
        ) : null}
        <DirectoryList
          path=""
          directories={directories}
          expanded={expanded}
          selectedPath={selectedPath}
          onToggle={toggle}
          onSelect={(entry) => setSelectedPath(entry.path)}
          onRetry={retry}
          checked={checked}
          deleting={deleting}
          onCheck={toggleChecked}
        />
      </nav>
      {selected ? (
        <FileDetail key={selected.path} projectName={projectName} entry={selected} deleting={deleting} onDelete={() => void moveToTrash([selected])} />
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center">
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <File aria-hidden />
              </EmptyMedia>
              <EmptyTitle>{t("file_view_select_hint")}</EmptyTitle>
            </EmptyHeader>
          </Empty>
        </div>
      )}
    </div>
  );
}

interface DirectoryListProps {
  path: string;
  directories: Record<string, DirectoryState>;
  expanded: ReadonlySet<string>;
  selectedPath: string | null;
  onToggle: (path: string) => void;
  onSelect: (entry: FileViewEntry) => void;
  onRetry: (path: string) => void;
  checked: ReadonlyMap<string, FileViewEntry>;
  deleting: boolean;
  onCheck: (entry: FileViewEntry) => void;
}

function DirectoryList(props: DirectoryListProps) {
  const { t } = useTranslation("dashboard");
  const { path, directories, expanded, selectedPath, onToggle, onSelect, onRetry, checked, deleting, onCheck } = props;
  const state = directories[path];
  if (!state || state.status === "loading") {
    return <p className="px-2 py-1 text-xs text-muted-foreground">{t("file_view_loading")}</p>;
  }
  if (state.status === "error") {
    return (
      <div className="flex flex-col items-start gap-1 px-2 py-1 text-xs text-destructive">
        <p className="wrap-break-word">{t("file_view_dir_load_failed", { message: state.message })}</p>
        <Button variant="link" size="xs" onClick={() => onRetry(path)}>
          {t("file_view_retry")}
        </Button>
      </div>
    );
  }
  if (state.entries.length === 0) {
    return <p className="px-2 py-1 text-xs text-muted-foreground">{t("file_view_empty_dir")}</p>;
  }
  return (
    <ul className="flex flex-col gap-px">
      {state.entries.map((entry) => {
        const open = expanded.has(entry.path);
        const rowClass = "focus-ring flex w-full min-w-0 items-center gap-1.5 rounded-md px-2 py-1 text-left text-sm transition-colors duration-fast";
        if (entry.type === "directory") {
          const Icon = open ? FolderOpen : Folder;
          return (
            <li key={entry.path}>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => onToggle(entry.path)}
                className={cn(rowClass, "text-subtle-foreground hover:bg-muted/60 hover:text-foreground")}
              >
                <ChevronRight aria-hidden className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
                <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                <TruncatedText text={entry.name} focusable={false} className="flex-1" />
                <span className="num shrink-0 text-xs text-muted-foreground">{formatFileSize(entry.size)}</span>
              </button>
              {open ? (
                <div className="pl-4">
                  <DirectoryList {...props} path={entry.path} />
                </div>
              ) : null}
            </li>
          );
        }
        const active = entry.path === selectedPath;
        return (
          <li key={entry.path} className="flex min-w-0 items-center gap-1">
            {canDeleteFile(entry) ? (
              <Checkbox
                aria-label={t("file_view_select_file", { name: entry.name })}
                checked={checked.has(entry.path)}
                disabled={deleting}
                onCheckedChange={() => onCheck(entry)}
                className="ml-2"
              />
            ) : null}
            <button
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(entry)}
              className={cn(
                rowClass,
                canDeleteFile(entry) ? "flex-1" : "pl-7",
                active ? "bg-primary/15 text-foreground" : "text-subtle-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <File aria-hidden className="size-4 shrink-0 self-start mt-0.5 text-muted-foreground" />
              {/* Names get their own row, roles/sizes a second, preventing narrow columns from squeezing names away. */}
              <span className="flex min-w-0 flex-1 flex-col">
                <TruncatedText text={entry.name} focusable={false} />
                <span className="text-xs text-muted-foreground">
                  {entry.role ? `${t(`file_view_role_${entry.role}`)} · ` : null}
                  <span className="num">{formatFileSize(entry.size)}</span>
                  {entry.remote_only ? ` · ${t("file_view_remote_only")}` : null}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function FileDetail({
  projectName,
  entry,
  deleting,
  onDelete,
}: {
  projectName: string;
  entry: FileViewEntry;
  deleting: boolean;
  onDelete: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const [downloading, setDownloading] = useState(false);
  const target = ownerTarget(entry.owner, projectName);
  const unreferencedSource = entry.role === "unreferenced" && entry.owner?.kind === "source";
  // Only unreferenced files delete here; servers also validate, while this only controls button availability.
  const deletable = canDeleteFile(entry);
  const description = entry.role
    ? t(unreferencedSource ? "file_view_role_desc_unreferenced_source" : `file_view_role_desc_${entry.role}`)
    : null;
  const modified = formatShortDateTime(entry.modified_at);

  const download = async () => {
    setDownloading(true);
    try {
      downloadBlob(await API.getFileViewContent(projectName, entry.path, { download: true }), entry.name);
    } catch (error) {
      useAppStore.getState().pushToast(t("file_view_download_failed", { name: entry.name, message: errMsg(error) }), "error");
    } finally {
      setDownloading(false);
    }
  };

  const header = (
    <div className="flex flex-wrap items-start gap-3">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h2 className="min-w-0 text-base font-semibold">
          <TruncatedText text={entry.name} />
        </h2>
        <p className="min-w-0 text-xs text-muted-foreground">
          <TruncatedText text={entry.path} />
        </p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {entry.role ? <Badge variant="outline">{t(`file_view_role_${entry.role}`)}</Badge> : null}
          {entry.remote_only ? <Badge variant="secondary">{t("file_view_remote_only")}</Badge> : null}
          <span className="num">{formatFileSize(entry.size)}</span>
          {modified ? <span>{t("file_view_modified_at", { time: modified })}</span> : null}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {target ? (
          <Link
            href={target.href}
            className={buttonVariants({ variant: "outline", size: "sm" })}
            onClick={() => {
              if (target.focus) useAppStore.getState().triggerScrollTo(target.focus);
            }}
          >
            {t(target.labelKey)}
          </Link>
        ) : null}
        <Button variant="outline" size="sm" disabled={downloading} onClick={() => void download()}>
          {downloading ? (
            <Loader2 data-icon="inline-start" aria-hidden className="animate-spin" />
          ) : (
            <Download data-icon="inline-start" aria-hidden />
          )}
          {t("file_view_download")}
        </Button>
        {deletable ? (
          <Button variant="outline" size="sm" disabled={deleting} onClick={onDelete}>
            {deleting ? (
              <Loader2 data-icon="inline-start" aria-hidden className="animate-spin" />
            ) : (
              <Trash2 data-icon="inline-start" aria-hidden />
            )}
            {t("file_view_move_to_trash")}
          </Button>
        ) : null}
      </div>
    </div>
  );

  return (
    <DetailPane header={header}>
      <div className="flex flex-col gap-4 px-6 py-4">
        {description ? <p className="text-sm text-subtle-foreground">{description}</p> : null}
        {entry.remote_only ? <p className="text-sm text-subtle-foreground">{t("file_view_remote_only_desc")}</p> : null}
        {/* Same-path mtime/size changes remount previews: show loading for new contents rather than reusing old previews. */}
        <FilePreview key={`${entry.modified_at}:${entry.size}`} projectName={projectName} entry={entry} />
      </div>
    </DetailPane>
  );
}
