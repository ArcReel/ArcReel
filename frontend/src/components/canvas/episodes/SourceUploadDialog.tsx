import { useCallback, useEffect, useId, useMemo, useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, FileText, GripVertical, Loader2, Lock, Upload, X } from "lucide-react";

import { API } from "@/api";
import { GlassModal } from "@/components/legacy/GlassModal";
import { ModalCloseButton } from "@/components/legacy/ModalCloseButton";
import { PrimaryButton } from "@/components/legacy/PrimaryButton";
import { SecondaryButton } from "@/components/legacy/SecondaryButton";
import { ICON_BTN_CLS, radioCardClass } from "@/components/shared/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
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
 * 文件名不决定先后。上传中途失败时停下，已上传的文件保留，剩下的文件留在列表里。
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
  const titleId = useId();
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
    await useProjectsStore.getState().refreshProject(projectName);
    if (signal?.aborted) return;
    setProgress(null);
    if (cancelled) {
      // 取消了一次插入确认：已上传的保留，取消的与之后的文件仍在列表里
      if (result.wholeSourceFiles.length > 0) onUploaded?.(result);
      return;
    }
    if (failure) {
      useAppStore.getState().pushToast(t("dashboard:source_upload_failed_partway", failure), "error");
      if (result.wholeSourceFiles.length > 0 || result.episodes.length > 0) onUploaded?.(result);
      return;
    }
    useAppStore
      .getState()
      .pushToast(
        mode === "episode"
          ? t("dashboard:source_upload_episodes_done", { count: result.episodes.length })
          : renamed.length > 0
            ? t("dashboard:source_upload_whole_done_renamed", {
                count: result.wholeSourceFiles.length,
                names: formatNameList(renamed, i18n.language),
              })
            : t("dashboard:source_upload_whole_done", { count: result.wholeSourceFiles.length }),
        "success",
      );
    onUploaded?.(result);
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
    <GlassModal
      open
      onClose={busy ? () => {} : onClose}
      labelledBy={titleId}
      widthClassName="w-full max-w-2xl"
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
    >
      <div
        className="flex max-h-[min(86vh,760px)] flex-col"
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
        <header className="flex items-start justify-between gap-3 px-6 pt-5">
          <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight text-foreground">
            {t("dashboard:source_upload_title")}
          </h2>
          <ModalCloseButton onClick={onClose} disabled={busy} />
        </header>

        <fieldset className="mt-4 flex gap-2.5 px-6" disabled={busy}>
          <legend className="sr-only">{t("dashboard:source_upload_mode_legend")}</legend>
          {(["whole_source", "episode"] as const).map((value) => (
            <label key={value} className={`${radioCardClass(mode === value)} text-left`}>
              <input
                type="radio"
                name={`${titleId}-mode`}
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
                className="sr-only"
              />
              <span className="block text-[13px] font-medium text-foreground">
                {t(value === "whole_source" ? "dashboard:source_upload_mode_whole" : "dashboard:source_upload_mode_episode")}
              </span>
              <span className="mt-0.5 block text-[11.5px] leading-[1.5] text-muted-foreground">
                {t(
                  value === "whole_source"
                    ? "dashboard:source_upload_mode_whole_hint"
                    : "dashboard:source_upload_mode_episode_hint",
                )}
              </span>
            </label>
          ))}
        </fieldset>

        <p className="mt-4 px-6 text-[12px] leading-[1.6] text-muted-foreground">
          {mode === "whole_source" ? t("dashboard:source_upload_order_whole") : t("dashboard:source_upload_order_episode")}
        </p>
        {withSourceKind && mode === "episode" ? (
          <div className="mt-2 flex items-center gap-2 px-6 text-[12px] text-muted-foreground">
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
          className="mx-6 mt-2 min-h-[140px] flex-1 overflow-y-auto rounded-lg border transition-colors"
          style={{
            borderColor: fileDragOver ? "var(--primary)" : "var(--border)",
            borderStyle: fileDragOver ? "dashed" : "solid",
            background: "oklch(0.17 0.010 265 / 0.55)",
          }}
        >
          {visibleRows.length === 0 ? (
            <div className="grid h-full min-h-[140px] place-items-center px-6 text-center text-[12px] text-muted-foreground">
              {t("dashboard:source_upload_empty")}
            </div>
          ) : (
            <ol aria-label={t("dashboard:source_upload_list_label")}>
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
                    className="flex items-center gap-2.5 px-3 py-2 text-[12.5px]"
                    style={{
                      borderTop: index === 0 ? "none" : "1px solid color-mix(in oklab, var(--border) 50%, transparent)",
                      opacity: dragKey === row.key ? 0.45 : 1,
                    }}
                  >
                    <span className="num w-6 shrink-0 text-right text-[10.5px] text-muted-foreground">{index + 1}</span>
                    {isNew ? (
                      <GripVertical
                        className="h-3.5 w-3.5 shrink-0 cursor-grab text-muted-foreground"
                        aria-hidden
                      />
                    ) : (
                      <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    <FileText className={`h-3.5 w-3.5 shrink-0 ${isNew ? "text-primary" : "text-muted-foreground"}`} aria-hidden />
                    <div className="min-w-0 flex-1">
                      <div className={`truncate ${isNew ? "text-foreground" : "text-muted-foreground"}`} title={name}>
                        {name}
                      </div>
                      {rowReserved ? (
                        <div className="mt-0.5 text-[11px] text-warn">
                          {t("dashboard:source_upload_reserved_name")}
                        </div>
                      ) : null}
                    </div>
                    {isNew ? (
                      <span className="num shrink-0 text-[10.5px] text-muted-foreground">{fileSizeLabel(row.file.size)}</span>
                    ) : (
                      <span className="shrink-0 text-[11px] text-muted-foreground">
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
                      <span className="shrink-0 text-[11.5px] text-primary">
                        {t("dashboard:source_upload_becomes", { position: episodeCount + index + 1 })}
                      </span>
                    ) : null}
                    {isNew ? (
                      <span className="flex shrink-0 items-center">
                        <button
                          type="button"
                          className={ICON_BTN_CLS}
                          disabled={busy || index === 0}
                          onClick={() => moveBy(row.key, -1)}
                          aria-label={t("dashboard:source_upload_move_up", { name })}
                        >
                          <ArrowUp className="h-3.5 w-3.5" aria-hidden />
                        </button>
                        <button
                          type="button"
                          className={ICON_BTN_CLS}
                          disabled={busy || index === visibleRows.length - 1}
                          onClick={() => moveBy(row.key, 1)}
                          aria-label={t("dashboard:source_upload_move_down", { name })}
                        >
                          <ArrowDown className="h-3.5 w-3.5" aria-hidden />
                        </button>
                        <button
                          type="button"
                          className={ICON_BTN_CLS}
                          disabled={busy}
                          onClick={() => setRows((prev) => prev.filter((item) => item.key !== row.key))}
                          aria-label={t("dashboard:source_upload_remove", { name })}
                        >
                          <X className="h-3.5 w-3.5" aria-hidden />
                        </button>
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3 px-6">
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={SOURCE_FILE_ACCEPT}
            className="hidden"
            aria-label={t("dashboard:source_upload_pick")}
            onChange={(event) => {
              addFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
          <SecondaryButton
            size="sm"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            leadingIcon={<Upload className="h-3.5 w-3.5" aria-hidden />}
          >
            {t("dashboard:source_upload_pick")}
          </SecondaryButton>
          <span className="text-[11.5px] text-muted-foreground">
            {t("dashboard:source_upload_pick_hint", { formats: SOURCE_FILE_FORMATS_LABEL })}
          </span>
        </div>
        {skipped.length > 0 ? (
          <p role="status" className="mt-2 px-6 text-[11.5px] text-warn">
            {t("dashboard:source_upload_skipped", { names: formatNameList(skipped, i18n.language) })}
          </p>
        ) : null}

        <footer className="mt-5 flex items-center justify-end gap-2 border-t border-border/50 px-6 py-4">
          {progress ? (
            <span role="status" className="mr-auto flex min-w-0 items-center gap-2 text-[12px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 shrink-0 motion-safe:animate-spin" aria-hidden />
              <span className="truncate">{t("dashboard:source_upload_progress", progress)}</span>
            </span>
          ) : null}
          <SecondaryButton size="sm" onClick={onClose} disabled={busy}>
            {t("common:cancel")}
          </SecondaryButton>
          <PrimaryButton
            size="sm"
            onClick={() => void submit()}
            disabled={busy || newRows.length === 0 || reserved}
          >
            {confirmLabel}
          </PrimaryButton>
        </footer>
      </div>
      {change.dialog}
    </GlassModal>
  );
}
