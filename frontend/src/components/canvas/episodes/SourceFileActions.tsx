import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, FileUp, Loader2, MoreHorizontal, PencilLine, Trash2, Upload } from "lucide-react";

import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodesViewFile, SourceFileChangeResponse, SourceKind } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { SOURCE_FILE_ACCEPT, SOURCE_FILE_FORMATS_LABEL } from "@/utils/source-files";

import { ImpactConfirmDialog } from "./ImpactConfirmDialog";
import { SourceKindSelect } from "./SourceKindSelect";
import { useSourceFileChange } from "./useSourceFileChange";

interface SourceFileActionsProps {
  projectName: string;
  file: EpisodesViewFile;
  index: number;
  total: number;
}

type Editor = "edit" | "replace" | "delete" | null;

/**
 * 文件条上的整本源文文件操作：上移、下移、编辑原文、替换为新文件、删除文件。
 *
 * 波及切出集的改动先呈现服务端成文的受影响集清单，确认后才执行；没有受影响的集时直接执行。
 * 删除一个不含切出集的文件不波及任何集，改由本地确认框提醒删除不可恢复。
 * 文件在 ArcReel 之外被改动过、还没有更新分集账本时，只有删除可用。执行结果直接体现在原文里，不另行提示。
 */
export function SourceFileActions({ projectName, file, index, total }: SourceFileActionsProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const change = useSourceFileChange();
  const [editor, setEditor] = useState<Editor>(null);
  const hasEpisodes = file.segments.some((segment) => segment.kind === "episode");
  const paused = change.busy || file.changed_outside;

  /** 跑一次改动；执行后刷新项目，返回是否已执行。 */
  const perform = async (
    title: string,
    confirmLabel: string,
    call: (revision: string | null) => Promise<SourceFileChangeResponse>,
  ): Promise<boolean> => {
    try {
      const reply = await change.run(title, confirmLabel, call);
      if (reply === null) return false;
      await useProjectsStore.getState().refreshProject(projectName);
      return true;
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("dashboard:source_file_change_failed", { name: file.name, message: errMsg(err) }), "error");
      return false;
    }
  };

  const move = (direction: "up" | "down") =>
    void perform(
      t("dashboard:source_file_move_title", { name: file.name }),
      t("dashboard:source_file_move_confirm"),
      (revision) => API.moveSourceFile(projectName, file.name, direction, revision),
    );

  const remove = () =>
    perform(
      t("dashboard:source_file_delete_title", { name: file.name }),
      t("dashboard:source_file_delete_confirm"),
      (revision) => API.deleteWholeSourceFile(projectName, file.name, revision),
    );

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("dashboard:source_file_actions_label", { name: file.name })}
            />
          }
        >
          <MoreHorizontal aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-44">
          <DropdownMenuItem disabled={index === 0 || paused} onClick={() => move("up")}>
            <ArrowUp aria-hidden />
            {t("dashboard:source_file_move_up")}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={index === total - 1 || paused} onClick={() => move("down")}>
            <ArrowDown aria-hidden />
            {t("dashboard:source_file_move_down")}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={file.missing || paused} onClick={() => setEditor("edit")}>
            <PencilLine aria-hidden />
            {t("dashboard:source_file_edit")}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={file.missing || paused} onClick={() => setEditor("replace")}>
            <FileUp aria-hidden />
            {t("dashboard:source_file_replace")}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            disabled={change.busy}
            onClick={() => (hasEpisodes ? void remove() : setEditor("delete"))}
          >
            <Trash2 aria-hidden />
            {t("dashboard:source_file_delete")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <EditSourceFileDialog
        open={editor === "edit"}
        projectName={projectName}
        file={file}
        onClose={() => setEditor(null)}
        onSave={(text) =>
          perform(
            t("dashboard:source_file_edit_title", { name: file.name }),
            t("dashboard:source_file_edit_save"),
            (revision) => API.editSourceFile(projectName, file.name, text, revision),
          )
        }
      />
      <ReplaceSourceFileDialog
        open={editor === "replace"}
        file={file}
        onClose={() => setEditor(null)}
        onReplace={(upload, sourceKind) =>
          perform(
            t("dashboard:source_file_replace_title", { name: file.name }),
            t("dashboard:source_file_replace_confirm"),
            (revision) => API.replaceSourceFile(projectName, file.name, upload, { sourceKind, revision }),
          )
        }
      />
      <ImpactConfirmDialog
        request={
          editor === "delete"
            ? {
                title: t("dashboard:source_file_delete_title", { name: file.name }),
                body: <p>{t("dashboard:source_file_delete_desc")}</p>,
                confirmLabel: t("dashboard:source_file_delete_confirm"),
                destructive: true,
              }
            : null
        }
        busy={change.busy}
        onConfirm={() => {
          setEditor(null);
          void remove();
        }}
        onCancel={() => setEditor(null)}
      />
      {change.dialog}
    </>
  );
}

/** 编辑整本源文文件的全文。保存时波及切出集的，先确认受影响集清单；执行后关闭。 */
function EditSourceFileDialog({
  open,
  projectName,
  file,
  onClose,
  onSave,
}: {
  open: boolean;
  projectName: string;
  file: EpisodesViewFile;
  onClose: () => void;
  onSave: (text: string) => Promise<boolean>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // 保存在途时不响应 Esc 与遮罩点击
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent size="xl" showCloseButton={!busy}>
        {open ? (
          <EditSourceFileForm
            projectName={projectName}
            file={file}
            busy={busy}
            setBusy={setBusy}
            onSave={onSave}
            onDone={onClose}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function EditSourceFileForm({
  projectName,
  file,
  busy,
  setBusy,
  onSave,
  onDone,
}: {
  projectName: string;
  file: EpisodesViewFile;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onSave: (text: string) => Promise<boolean>;
  onDone: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const formId = useId();
  const fieldId = useId();
  const [text, setText] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    API.getSourceContent(projectName, file.name, { signal: controller.signal })
      .then((content) => {
        if (!controller.signal.aborted) setText(content);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setLoadError(errMsg(err));
      });
    return () => controller.abort();
  }, [projectName, file.name]);

  const save = async () => {
    if (text === null || busy) return;
    setBusy(true);
    const done = await onSave(text);
    setBusy(false);
    if (done) onDone();
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("dashboard:source_file_edit_title", { name: file.name })}</DialogTitle>
        <DialogDescription>{t("dashboard:source_file_edit_hint")}</DialogDescription>
      </DialogHeader>
      <DialogBody>
        <form
          id={formId}
          className="flex flex-col gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          {loadError !== null ? (
            <p role="alert" className="text-destructive">
              {t("dashboard:source_file_edit_load_failed", { message: loadError })}
            </p>
          ) : text === null ? (
            <p role="status" className="text-muted-foreground">
              {t("dashboard:source_file_edit_loading")}
            </p>
          ) : (
            <>
              <Label htmlFor={fieldId} className="sr-only">
                {t("dashboard:source_file_edit_label", { name: file.name })}
              </Label>
              <Textarea
                id={fieldId}
                value={text}
                onChange={(event) => setText(event.target.value)}
                disabled={busy}
                className="min-h-80"
              />
            </>
          )}
        </form>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" disabled={busy} />}>{t("common:cancel")}</DialogClose>
        <Button type="submit" form={formId} disabled={busy || text === null || text.trim() === ""}>
          {busy ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
          {t("dashboard:source_file_edit_save")}
        </Button>
      </DialogFooter>
    </>
  );
}

/** 用新文件替换整本源文文件：保留文件名与位置。剧情演绎项目可以改源文件类型，预填为原类型。 */
function ReplaceSourceFileDialog({
  open,
  file,
  onClose,
  onReplace,
}: {
  open: boolean;
  file: EpisodesViewFile;
  onClose: () => void;
  onReplace: (upload: File, sourceKind: SourceKind | undefined) => Promise<boolean>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent showCloseButton={!busy}>
        {open ? (
          <ReplaceSourceFileForm file={file} busy={busy} setBusy={setBusy} onReplace={onReplace} onDone={onClose} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function ReplaceSourceFileForm({
  file,
  busy,
  setBusy,
  onReplace,
  onDone,
}: {
  file: EpisodesViewFile;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onReplace: (upload: File, sourceKind: SourceKind | undefined) => Promise<boolean>;
  onDone: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const formId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<File | null>(null);
  const [sourceKind, setSourceKind] = useState<SourceKind | null>(file.source_kind);

  const submit = async () => {
    if (upload === null || busy) return;
    setBusy(true);
    const kindChanged = sourceKind !== null && sourceKind !== file.source_kind;
    const done = await onReplace(upload, kindChanged ? sourceKind : undefined);
    setBusy(false);
    if (done) onDone();
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("dashboard:source_file_replace_title", { name: file.name })}</DialogTitle>
        <DialogDescription>{t("dashboard:source_file_replace_hint", { name: file.name })}</DialogDescription>
      </DialogHeader>
      <DialogBody>
        <form
          id={formId}
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <div className="flex min-w-0 items-center gap-3">
              <input
                ref={inputRef}
                type="file"
                accept={SOURCE_FILE_ACCEPT}
                hidden
                aria-label={t("dashboard:source_file_replace_pick")}
                onChange={(event) => {
                  setUpload(event.target.files?.[0] ?? null);
                  event.target.value = "";
                }}
              />
              <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => inputRef.current?.click()}>
                <Upload aria-hidden data-icon="inline-start" />
                {t("dashboard:source_file_replace_pick")}
              </Button>
              <span className="min-w-0 truncate text-subtle-foreground">
                {upload?.name ?? t("dashboard:source_file_replace_none")}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("dashboard:source_upload_pick_hint", { formats: SOURCE_FILE_FORMATS_LABEL })}
            </p>
          </div>
          {sourceKind !== null ? (
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">{t("dashboard:source_file_replace_kind")}</span>
              <SourceKindSelect
                value={sourceKind}
                onChange={setSourceKind}
                disabled={busy}
                label={t("dashboard:source_file_replace_kind")}
              />
            </div>
          ) : null}
        </form>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" disabled={busy} />}>{t("common:cancel")}</DialogClose>
        <Button type="submit" form={formId} disabled={busy || upload === null}>
          {busy ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
          {t("dashboard:source_file_replace_confirm")}
        </Button>
      </DialogFooter>
    </>
  );
}
