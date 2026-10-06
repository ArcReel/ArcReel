import { useCallback, useEffect, useId, useMemo, useRef, useState, type DragEvent } from "react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, FileText, GripVertical, Loader2, Lock, Upload, X } from "lucide-react";

import { API } from "@/api";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { errMsg } from "@/utils/async";
import { formatNameList } from "@/utils/list-format";
import { SOURCE_FILE_ACCEPT, SOURCE_FILE_FORMATS_LABEL, isSupportedSourceFile } from "@/utils/source-files";

import type { SourceKind } from "@/types/episodes-view";

import { SourceKindSelect } from "./SourceKindSelect";
import { isReservedEpisodeFileName, type SourceUploadMode } from "./episodes-view-model";
import { useSourceFileChange } from "./useSourceFileChange";

type Row =
  | { key: string; kind: "existing"; name: string; sourceKind?: SourceKind }
  | { key: string; kind: "new"; file: File; sourceKind: SourceKind };

export interface SourceUploadResult {
  /** 登记进整本源文的文件名（服务端落盘后的名字）。 */
  wholeSourceFiles: string[];
  /** 新登记的自带原文的集 ID，按播出顺序。 */
  episodes: number[];
}

interface SourceUploadDialogProps {
  projectName: string;
  initialMode?: SourceUploadMode;
  initialFiles?: File[];
  onClose: () => void;
  onUploaded?: (result: SourceUploadResult) => void;
}

function fileSizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function moveKey(rows: Row[], fromKey: string, toIndex: number): Row[] {
  const from = rows.findIndex((row) => row.key === fromKey);
  if (from < 0 || toIndex < 0 || toIndex >= rows.length || from === toIndex) return rows;
  const next = [...rows];
  const [moved] = next.splice(from, 1);
  next.splice(toIndex, 0, moved);
  return next;
}

/** 新选文件的行 key：同名文件可以重复加入，按加入顺序取号。 */
let newRowSeq = 0;

function toNewRows(files: File[], sourceKind: SourceKind = "novel"): { rows: Row[]; skipped: string[] } {
  const rows: Row[] = [];
  const skipped: string[] = [];
  for (const file of files) {
    if (!isSupportedSourceFile(file.name)) {
      skipped.push(file.name);
      continue;
    }
    newRowSeq += 1;
    rows.push({ key: `new:${newRowSeq}`, kind: "new", file, sourceKind });
  }
  return { rows, skipped };
}

/**
 * 上传原文：整本源文或逐集原文。
 *
 * - 整本源文：已有文件锁定显示，新文件默认排在最后、可以拖到任意位置，按列表顺序逐个登记到对应位置。
 * - 逐集原文：「文件 → 将成为第 N 集」，确认后按列表顺序追加到播出顺序末尾。
 *
 * 文件名不决定先后。上传中途失败时停下，已上传的文件保留，剩下的文件留在列表里。全部上传完成后关闭，
 * 只有服务端改了文件名（同名文件改名保存）时提示改成了什么。
 *
 * 剧情演绎项目逐个文件选源文件类型，缺省为小说；逐集原文另有一个整批选择，选一次套用到列表里的全部文件，
 * 之后加入的文件也取这个类型，单个文件仍可以再改。
 */
export function SourceUploadDialog({
  projectName,
  initialMode = "whole_source",
  initialFiles,
  onClose,
  onUploaded,
}: SourceUploadDialogProps) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const modeName = useId();
  const project = useProjectsStore((s) => s.currentProjectData);
  const episodeCount = project?.episodes?.length ?? 0;
  const withSourceKind = project?.content_mode === "drama";

  const [mode, setMode] = useState<SourceUploadMode>(initialMode);
  const [rows, setRows] = useState<Row[]>(() => {
    const existing: Row[] = (project?.whole_source_files ?? []).map(({ source_file, source_kind }) => {
      const name = source_file.replace(/^source\//, "");
      return { key: `existing:${name}`, kind: "existing", name, sourceKind: source_kind ?? "novel" };
    });
    return [...existing, ...toNewRows(initialFiles ?? []).rows];
  });
  const [skipped, setSkipped] = useState<string[]>(() =>
    (initialFiles ?? []).filter((file) => !isSupportedSourceFile(file.name)).map((file) => file.name),
  );
  const [batchKind, setBatchKind] = useState<SourceKind>("novel");
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [fileDragOver, setFileDragOver] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number; name: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const busy = progress !== null;
  const change = useSourceFileChange();

  // 对话框卸载（切换项目、离开页面）时中止在途上传，不再上传剩下的文件，也不再提示
  const unmountController = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    unmountController.current = controller;
    return () => controller.abort();
  }, []);

  const newRows = useMemo(() => rows.filter((row): row is Extract<Row, { kind: "new" }> => row.kind === "new"), [rows]);
  const visibleRows = mode === "whole_source" ? rows : newRows;
  const reserved = mode === "whole_source" && newRows.some((row) => isReservedEpisodeFileName(row.file.name));

  const addFiles = useCallback(
    (files: File[]) => {
      const added = toNewRows(files, mode === "episode" ? batchKind : "novel");
      setRows((prev) => [...prev, ...added.rows]);
      setSkipped(added.skipped);
    },
    [mode, batchKind],
  );

  const setRowKind = (key: string, sourceKind: SourceKind) =>
    setRows((prev) => prev.map((row) => (row.key === key && row.kind === "new" ? { ...row, sourceKind } : row)));

  const applyBatchKind = (sourceKind: SourceKind) => {
    setBatchKind(sourceKind);
    setRows((prev) => prev.map((row) => (row.kind === "new" ? { ...row, sourceKind } : row)));
  };

  const moveBy = (key: string, delta: number) => {
    setRows((prev) => {
      const visible = mode === "whole_source" ? prev : prev.filter((row) => row.kind === "new");
      const at = visible.findIndex((row) => row.key === key);
      const target = visible[at + delta];
      if (at < 0 || !target) return prev;
      return moveKey(prev, key, prev.findIndex((row) => row.key === target.key));
    });
  };

  const onRowDragOver = (event: DragEvent, overKey: string) => {
    if (dragKey === null || dragKey === overKey) return;
    event.preventDefault();
    setRows((prev) => moveKey(prev, dragKey, prev.findIndex((row) => row.key === overKey)));
  };

  const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer.types).includes("Files");

  const submit = async () => {
    const signal = unmountController.current?.signal;
    const queue = newRows;
    let working = rows;
    const result: SourceUploadResult = { wholeSourceFiles: [], episodes: [] };
    const renamed: string[] = [];
    let failure: { name: string; message: string } | null = null;
    let cancelled = false;
    for (const [index, row] of queue.entries()) {
      if (row.kind !== "new") continue;
      setProgress({ current: index + 1, total: queue.length, name: row.file.name });
      try {
        if (mode === "whole_source") {
          const position = working.findIndex((item) => item.key === row.key);
          const insertAt = working.slice(0, position).filter((item) => item.kind === "existing").length;
          // 插入处落在一个跨文件的集内部时，新文件归入那一集：先确认受影响集清单
          const res = await change.run(
            t("dashboard:source_file_insert_title", { name: row.file.name }),
            t("dashboard:source_file_insert_confirm"),
            (revision) =>
              API.uploadFile(projectName, "source", row.file, null, {
                role: "whole_source",
                onConflict: "rename",
                insertAt,
                sourceKind: withSourceKind ? row.sourceKind : undefined,
                revision: revision ?? undefined,
                signal,
              }),
          );
          if (res === null) {
            cancelled = true;
            break;
          }
          const saved = res.filename ?? row.file.name;
          const expected = `${row.file.name.replace(/\.[^.]*$/, "")}.txt`;
          if (saved !== expected) renamed.push(saved);
          result.wholeSourceFiles.push(saved);
          working = working.map((item) =>
            item.key === row.key
              ? { key: `existing:${saved}`, kind: "existing", name: saved, sourceKind: row.sourceKind }
              : item,
          );
        } else {
          const res = await API.uploadFile(projectName, "source", row.file, null, {
            role: "episode",
            sourceKind: withSourceKind ? row.sourceKind : undefined,
            signal,
          });
          if (res.episode !== undefined) result.episodes.push(res.episode);
          working = working.filter((item) => item.key !== row.key);
        }
        setRows(working);
      } catch (err) {
        if (signal?.aborted) return;
        failure = { name: row.file.name, message: errMsg(err) };
        break;
      }
    }
    const refreshed = await refreshAfterWrite(projectName, t);
    if (signal?.aborted) return;
    setProgress(null);
    if (cancelled) {
      // 取消了一次插入确认：已上传的保留，取消的与之后的文件仍在列表里
      if (refreshed === "success" && result.wholeSourceFiles.length > 0) onUploaded?.(result);
      return;
    }
    if (failure) {
      useAppStore.getState().pushToast(t("dashboard:source_upload_failed_partway", failure), "error");
      if (refreshed === "success" && (result.wholeSourceFiles.length > 0 || result.episodes.length > 0)) onUploaded?.(result);
      return;
    }
    if (refreshed === "success" && renamed.length > 0) {
      useAppStore.getState().pushToast(
        t("dashboard:source_upload_whole_done_renamed", {
          count: result.wholeSourceFiles.length,
          names: formatNameList(renamed, i18n.language),
        }),
        "info",
      );
    }
    if (refreshed === "success") onUploaded?.(result);
    onClose();
  };

  const firstPosition = episodeCount + 1;
  const lastPosition = episodeCount + newRows.length;
  const confirmLabel =
    mode === "whole_source"
      ? t("dashboard:source_upload_confirm_whole", { count: newRows.length })
      : newRows.length > 1
        ? t("dashboard:source_upload_confirm_episodes", { from: firstPosition, to: lastPosition })
        : t("dashboard:source_upload_confirm_episode", { position: firstPosition });

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        // 上传在途时不响应 Esc 与遮罩点击；关闭会中止剩下的上传
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent size="lg" showCloseButton={!busy}>
        <DialogHeader>
          <DialogTitle>{t("dashboard:source_upload_title")}</DialogTitle>
        </DialogHeader>
        <DialogBody
          onDragOver={(event) => {
            if (!hasFiles(event) || busy) return;
            event.preventDefault();
            setFileDragOver(true);
          }}
          onDragLeave={(event) => {
            if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
            setFileDragOver(false);
          }}
          onDrop={(event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            setFileDragOver(false);
            if (!busy) addFiles(Array.from(event.dataTransfer.files));
          }}
        >
          <div className="flex flex-col gap-3">
            <fieldset className="flex gap-2.5" disabled={busy}>
              <legend className="sr-only">{t("dashboard:source_upload_mode_legend")}</legend>
              {(["whole_source", "episode"] as const).map((value) => (
                <label
                  key={value}
                  className="flex flex-1 cursor-pointer flex-col gap-0.5 rounded-lg border px-3 py-2.5 text-left transition-colors duration-fast has-checked:border-primary has-checked:bg-primary/10 has-focus-visible:ring-3 has-focus-visible:ring-ring/50 has-disabled:cursor-not-allowed has-disabled:opacity-50"
                >
                  <input
                    type="radio"
                    name={modeName}
                    value={value}
                    checked={mode === value}
                    onChange={() => setMode(value)}
                    className="sr-only"
                  />
                  <span className="text-sm font-medium text-foreground">
                    {t(
                      value === "whole_source"
                        ? "dashboard:source_upload_mode_whole"
                        : "dashboard:source_upload_mode_episode",
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t(
                      value === "whole_source"
                        ? "dashboard:source_upload_mode_whole_hint"
                        : "dashboard:source_upload_mode_episode_hint",
                    )}
                  </span>
                </label>
              ))}
            </fieldset>

            <p className="text-muted-foreground">
              {mode === "whole_source"
                ? t("dashboard:source_upload_order_whole")
                : t("dashboard:source_upload_order_episode")}
            </p>
            {withSourceKind && mode === "episode" ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <span>{t("dashboard:source_upload_batch_kind")}</span>
                <SourceKindSelect
                  value={batchKind}
                  onChange={applyBatchKind}
                  disabled={busy}
                  label={t("dashboard:source_upload_batch_kind")}
                />
              </div>
            ) : null}

            <div
              className={cn(
                "min-h-36 rounded-lg border bg-muted/30 transition-colors duration-fast",
                fileDragOver && "border-dashed border-primary",
              )}
            >
              {visibleRows.length === 0 ? (
                <div className="grid min-h-36 place-items-center px-6 text-center text-xs text-muted-foreground">
                  {t("dashboard:source_upload_empty")}
                </div>
              ) : (
                <ol aria-label={t("dashboard:source_upload_list_label")} className="divide-y divide-border/50">
                  {visibleRows.map((row, index) => {
                    const isNew = row.kind === "new";
                    const name = isNew ? row.file.name : row.name;
                    const rowReserved = mode === "whole_source" && isNew && isReservedEpisodeFileName(name);
                    return (
                      <li
                        key={row.key}
                        draggable={isNew && !busy}
                        onDragStart={(event) => {
                          setDragKey(row.key);
                          event.dataTransfer.effectAllowed = "move";
                          event.dataTransfer.setData("text/plain", row.key);
                        }}
                        onDragOver={(event) => onRowDragOver(event, row.key)}
                        onDragEnd={() => setDragKey(null)}
                        className={cn("flex items-center gap-2.5 px-3 py-2", dragKey === row.key && "opacity-45")}
                      >
                        <span className="num w-6 shrink-0 text-right text-xs text-muted-foreground">{index + 1}</span>
                        {isNew ? (
                          <GripVertical className="size-3.5 shrink-0 cursor-grab text-muted-foreground" aria-hidden />
                        ) : (
                          <Lock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                        )}
                        <FileText
                          className={cn("size-3.5 shrink-0", isNew ? "text-primary" : "text-muted-foreground")}
                          aria-hidden
                        />
                        <div className="flex min-w-0 flex-1 flex-col">
                          <TruncatedText text={name} className={isNew ? "text-foreground" : "text-muted-foreground"} />
                          {rowReserved ? (
                            <span className="text-xs text-warn">{t("dashboard:source_upload_reserved_name")}</span>
                          ) : null}
                        </div>
                        {isNew ? (
                          <span className="num shrink-0 text-xs text-muted-foreground">{fileSizeLabel(row.file.size)}</span>
                        ) : (
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {withSourceKind
                              ? t("dashboard:source_upload_existing_kind", {
                                  kind: t(
                                    row.sourceKind === "screenplay"
                                      ? "dashboard:source_kind_screenplay"
                                      : "dashboard:source_kind_novel",
                                  ),
                                })
                              : t("dashboard:source_upload_existing")}
                          </span>
                        )}
                        {isNew && withSourceKind ? (
                          <SourceKindSelect
                            value={row.sourceKind}
                            onChange={(value) => setRowKind(row.key, value)}
                            disabled={busy}
                            label={t("dashboard:source_kind_of", { name })}
                          />
                        ) : null}
                        {isNew && mode === "episode" ? (
                          <span className="shrink-0 text-xs text-primary">
                            {t("dashboard:source_upload_becomes", { position: episodeCount + index + 1 })}
                          </span>
                        ) : null}
                        {isNew ? (
                          <span className="flex shrink-0 items-center">
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              disabled={busy || index === 0}
                              onClick={() => moveBy(row.key, -1)}
                              aria-label={t("dashboard:source_upload_move_up", { name })}
                            >
                              <ArrowUp aria-hidden />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              disabled={busy || index === visibleRows.length - 1}
                              onClick={() => moveBy(row.key, 1)}
                              aria-label={t("dashboard:source_upload_move_down", { name })}
                            >
                              <ArrowDown aria-hidden />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-xs"
                              disabled={busy}
                              onClick={() => setRows((prev) => prev.filter((item) => item.key !== row.key))}
                              aria-label={t("dashboard:source_upload_remove", { name })}
                            >
                              <X aria-hidden />
                            </Button>
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={SOURCE_FILE_ACCEPT}
                hidden
                aria-label={t("dashboard:source_upload_pick")}
                onChange={(event) => {
                  addFiles(Array.from(event.target.files ?? []));
                  event.target.value = "";
                }}
              />
              <Button variant="outline" size="sm" disabled={busy} onClick={() => inputRef.current?.click()}>
                <Upload aria-hidden data-icon="inline-start" />
                {t("dashboard:source_upload_pick")}
              </Button>
              <span className="text-xs text-muted-foreground">
                {t("dashboard:source_upload_pick_hint", { formats: SOURCE_FILE_FORMATS_LABEL })}
              </span>
            </div>
            {skipped.length > 0 ? (
              <p role="status" className="text-xs text-warn">
                {t("dashboard:source_upload_skipped", { names: formatNameList(skipped, i18n.language) })}
              </p>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          {progress ? (
            <span role="status" className="mr-auto flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
              <span className="truncate">{t("dashboard:source_upload_progress", progress)}</span>
            </span>
          ) : null}
          <DialogClose render={<Button variant="outline" disabled={busy} />}>{t("common:cancel")}</DialogClose>
          <Button onClick={() => void submit()} disabled={busy || newRows.length === 0 || reserved}>
            {confirmLabel}
          </Button>
        </DialogFooter>
        {change.dialog}
      </DialogContent>
    </Dialog>
  );
}
