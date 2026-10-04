import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, FileUp, MoreHorizontal, PencilLine, Trash2, Upload } from "lucide-react";

import { API } from "@/api";
import { ActionMenu } from "@/components/legacy/ActionMenu";
import { ConfirmDialog } from "@/components/legacy/ConfirmDialog";
import { FieldLabel } from "@/components/shared/FieldLabel";
import { GlassModal } from "@/components/legacy/GlassModal";
import { ModalCloseButton } from "@/components/legacy/ModalCloseButton";
import { PrimaryButton } from "@/components/legacy/PrimaryButton";
import { SecondaryButton } from "@/components/legacy/SecondaryButton";
import { INPUT_CLS } from "@/components/shared/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodesViewFile, SourceFileChangeResponse, SourceKind } from "@/types/episodes-view";
import { errMsg } from "@/utils/async";
import { SOURCE_FILE_ACCEPT, SOURCE_FILE_FORMATS_LABEL } from "@/utils/source-files";

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
 * 文件在 ArcReel 之外被改动过、还没有更新分集账本时，只有删除可用。
 */
export function SourceFileActions({ projectName, file, index, total }: SourceFileActionsProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const change = useSourceFileChange();
  const [editor, setEditor] = useState<Editor>(null);
  const hasEpisodes = file.segments.some((segment) => segment.kind === "episode");
  const paused = change.busy || file.changed_outside;

  /** 跑一次改动；执行后刷新项目并提示，返回是否已执行。 */
  const perform = async (
    title: string,
    confirmLabel: string,
    call: (revision: string | null) => Promise<SourceFileChangeResponse>,
    doneMessage: string,
  ): Promise<boolean> => {
    try {
      const reply = await change.run(title, confirmLabel, call);
      if (reply === null) return false;
      await useProjectsStore.getState().refreshProject(projectName);
      useAppStore.getState().pushToast(doneMessage, "success");
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
      t("dashboard:source_file_change_done", { name: file.name }),
    );

  const remove = () =>
    perform(
      t("dashboard:source_file_delete_title", { name: file.name }),
      t("dashboard:source_file_delete_confirm"),
      (revision) => API.deleteWholeSourceFile(projectName, file.name, revision),
      t("dashboard:source_file_delete_done", { name: file.name }),
    );

  return (
    <>
      <ActionMenu
        label={t("dashboard:source_file_actions_label", { name: file.name })}
        triggerClassName="focus-ring grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted-foreground hover:text-foreground disabled:opacity-45"
        triggerStyle={{ background: "oklch(0.25 0.012 265 / 0.9)" }}
        items={[
          {
            key: "up",
            label: t("dashboard:source_file_move_up"),
            icon: ArrowUp,
            disabled: index === 0 || paused,
            onSelect: () => move("up"),
          },
          {
            key: "down",
            label: t("dashboard:source_file_move_down"),
            icon: ArrowDown,
            disabled: index === total - 1 || paused,
            onSelect: () => move("down"),
          },
          {
            key: "edit",
            label: t("dashboard:source_file_edit"),
            icon: PencilLine,
            disabled: file.missing || paused,
            onSelect: () => setEditor("edit"),
          },
          {
            key: "replace",
            label: t("dashboard:source_file_replace"),
            icon: FileUp,
            disabled: file.missing || paused,
            onSelect: () => setEditor("replace"),
          },
          {
            key: "delete",
            label: t("dashboard:source_file_delete"),
            icon: Trash2,
            danger: true,
            disabled: change.busy,
            onSelect: () => (hasEpisodes ? void remove() : setEditor("delete")),
          },
        ]}
      >
        <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
      </ActionMenu>
      {editor === "edit" ? (
        <EditSourceFileDialog
          projectName={projectName}
          file={file}
          onClose={() => setEditor(null)}
          onSave={(text) =>
            perform(
              t("dashboard:source_file_edit_title", { name: file.name }),
              t("dashboard:source_file_edit_save"),
              (revision) => API.editSourceFile(projectName, file.name, text, revision),
              t("dashboard:source_file_change_done", { name: file.name }),
            )
          }
        />
      ) : null}
      {editor === "replace" ? (
        <ReplaceSourceFileDialog
          file={file}
          onClose={() => setEditor(null)}
          onReplace={(upload, sourceKind) =>
            perform(
              t("dashboard:source_file_replace_title", { name: file.name }),
              t("dashboard:source_file_replace_confirm"),
              (revision) => API.replaceSourceFile(projectName, file.name, upload, { sourceKind, revision }),
              t("dashboard:source_file_change_done", { name: file.name }),
            )
          }
        />
      ) : null}
      <ConfirmDialog
        open={editor === "delete"}
        tone="danger"
        title={t("dashboard:source_file_delete_title", { name: file.name })}
        description={t("dashboard:source_file_delete_desc")}
        confirmLabel={t("dashboard:source_file_delete_confirm")}
        loadingLabel={t("dashboard:source_file_change_running")}
        loading={change.busy}
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
  projectName,
  file,
  onClose,
  onSave,
}: {
  projectName: string;
  file: EpisodesViewFile;
  onClose: () => void;
  onSave: (text: string) => Promise<boolean>;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const titleId = useId();
  const fieldId = useId();
  const [text, setText] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    if (text === null) return;
    setBusy(true);
    const done = await onSave(text);
    setBusy(false);
    if (done) onClose();
  };

  return (
    <GlassModal
      open
      onClose={busy ? () => {} : onClose}
      labelledBy={titleId}
      closeOnBackdrop={false}
      closeOnEscape={!busy}
    >
      <form
        className="flex max-h-[min(86vh,820px)] flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) void save();
        }}
      >
        <header className="flex items-start justify-between gap-3 px-6 pt-5">
          <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight text-foreground">
            {t("dashboard:source_file_edit_title", { name: file.name })}
          </h2>
          <ModalCloseButton onClick={onClose} disabled={busy} />
        </header>
        <div className="mt-4 flex min-h-0 flex-1 flex-col px-6">
          <p className="mb-2 text-[11.5px] leading-[1.6] text-muted-foreground">{t("dashboard:source_file_edit_hint")}</p>
          {loadError !== null ? (
            <p role="alert" className="text-[12.5px] text-warn">
              {t("dashboard:source_file_edit_load_failed", { message: loadError })}
            </p>
          ) : text === null ? (
            <p role="status" className="text-[12.5px] text-muted-foreground">
              {t("dashboard:source_file_edit_loading")}
            </p>
          ) : (
            <textarea
              id={fieldId}
              aria-label={t("dashboard:source_file_edit_label", { name: file.name })}
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={18}
              className={`${INPUT_CLS} min-h-0 flex-1 resize-y leading-[1.8]`}
              disabled={busy}
            />
          )}
        </div>
        <footer className="flex justify-end gap-2 px-6 pb-5 pt-4">
          <SecondaryButton type="button" onClick={onClose} disabled={busy}>
            {t("common:cancel")}
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={busy || text === null || text.trim() === ""}>
            {t("dashboard:source_file_edit_save")}
          </PrimaryButton>
        </footer>
      </form>
    </GlassModal>
  );
}

/** 用新文件替换整本源文文件：保留文件名与位置。剧情演绎项目可以改源文件类型，预填为原类型。 */
function ReplaceSourceFileDialog({
  file,
  onClose,
  onReplace,
}: {
  file: EpisodesViewFile;
  onClose: () => void;
  onReplace: (upload: File, sourceKind: SourceKind | undefined) => Promise<boolean>;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const titleId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<File | null>(null);
  const [sourceKind, setSourceKind] = useState<SourceKind | null>(file.source_kind);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (upload === null) return;
    setBusy(true);
    const kindChanged = sourceKind !== null && sourceKind !== file.source_kind;
    const done = await onReplace(upload, kindChanged ? sourceKind : undefined);
    setBusy(false);
    if (done) onClose();
  };

  return (
    <GlassModal open onClose={busy ? () => {} : onClose} labelledBy={titleId} closeOnBackdrop={!busy} closeOnEscape={!busy}>
      <form
        className="flex flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) void submit();
        }}
      >
        <header className="flex items-start justify-between gap-3 px-6 pt-5">
          <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight text-foreground">
            {t("dashboard:source_file_replace_title", { name: file.name })}
          </h2>
          <ModalCloseButton onClick={onClose} disabled={busy} />
        </header>
        <div className="mt-4 space-y-4 px-6">
          <p className="text-[12.5px] leading-[1.7] text-muted-foreground">
            {t("dashboard:source_file_replace_hint", { name: file.name })}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={inputRef}
              type="file"
              accept={SOURCE_FILE_ACCEPT}
              className="hidden"
              aria-label={t("dashboard:source_file_replace_pick")}
              onChange={(event) => {
                setUpload(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />
            <SecondaryButton
              size="sm"
              type="button"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
              leadingIcon={<Upload className="h-3.5 w-3.5" aria-hidden />}
            >
              {t("dashboard:source_file_replace_pick")}
            </SecondaryButton>
            <span className="min-w-0 truncate text-[12px] text-subtle-foreground">
              {upload?.name ?? t("dashboard:source_file_replace_none")}
            </span>
          </div>
          <p className="text-[11.5px] text-muted-foreground">
            {t("dashboard:source_upload_pick_hint", { formats: SOURCE_FILE_FORMATS_LABEL })}
          </p>
          {sourceKind !== null ? (
            <div>
              <FieldLabel>{t("dashboard:source_file_replace_kind")}</FieldLabel>
              <SourceKindSelect
                value={sourceKind}
                onChange={setSourceKind}
                disabled={busy}
                label={t("dashboard:source_file_replace_kind")}
              />
            </div>
          ) : null}
        </div>
        <footer className="flex justify-end gap-2 px-6 pb-5 pt-4">
          <SecondaryButton type="button" onClick={onClose} disabled={busy}>
            {t("common:cancel")}
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={busy || upload === null}>
            {t("dashboard:source_file_replace_confirm")}
          </PrimaryButton>
        </footer>
      </form>
    </GlassModal>
  );
}
