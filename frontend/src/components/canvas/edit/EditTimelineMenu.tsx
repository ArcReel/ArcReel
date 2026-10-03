import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { MoreHorizontal } from "lucide-react";

import { API } from "@/api";
import { ConfirmDialog } from "@/components/legacy/ConfirmDialog";
import { GlassPopover } from "@/components/legacy/GlassPopover";
import { useAppStore } from "@/stores/app-store";
import type { EditTimelineSummary } from "@/types/edit-timeline";
import { errMsg } from "@/utils/async";

/** 与服务端 `TIMELINE_NAME_MAX_LENGTH` 一致。 */
const NAME_MAX_LENGTH = 40;

const ITEM_CLS =
  "focus-ring block w-full px-3 py-2 text-left text-[12.5px] transition-colors hover:bg-[oklch(1_0_0_/_0.06)]";

interface EditTimelineMenuProps {
  projectName: string;
  /** 菜单作用的剪辑时间线，即当前选中的标签。 */
  timeline: EditTimelineSummary;
  onRenamed: () => void;
  onDeleted: () => void;
}

type Dialog = "rename" | "delete" | null;

/** 剪辑时间线标签旁的「更多操作」菜单：重命名、删除（二次确认）。复制与回滚只交给 Agent。 */
export function EditTimelineMenu({ projectName, timeline, onRenamed, onDeleted }: EditTimelineMenuProps) {
  const { t } = useTranslation("dashboard");
  const menuId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);

  useEffect(() => {
    if (open) menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);

  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };
  const choose = (next: Exclude<Dialog, null>) => {
    setOpen(false);
    setDialog(next);
  };
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(index + step + items.length) % items.length]?.focus();
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={t("edit_view_menu_aria", { name: timeline.name })}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        className="focus-ring ml-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[7px] text-text-3 transition-colors hover:bg-[oklch(1_0_0_/_0.06)] hover:text-text"
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden />
      </button>
      <GlassPopover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={triggerRef}
        sideOffset={4}
        width="w-36"
        showHairline={false}
      >
        <div
          id={menuId}
          ref={menuRef}
          role="menu"
          tabIndex={-1}
          aria-label={t("edit_view_menu_aria", { name: timeline.name })}
          className="py-1"
          onKeyDown={(event) => {
            if (event.key === "Escape") close();
            else onMenuKeyDown(event);
          }}
        >
          <button type="button" role="menuitem" className={`${ITEM_CLS} text-text-2`} onClick={() => choose("rename")}>
            {t("edit_view_menu_rename")}
          </button>
          <button type="button" role="menuitem" className={`${ITEM_CLS} text-danger-2`} onClick={() => choose("delete")}>
            {t("edit_view_menu_delete")}
          </button>
        </div>
      </GlassPopover>
      {dialog === "rename" && (
        <RenameDialog
          projectName={projectName}
          timeline={timeline}
          onClose={() => setDialog(null)}
          onRenamed={onRenamed}
        />
      )}
      {dialog === "delete" && (
        <DeleteDialog
          projectName={projectName}
          timeline={timeline}
          onClose={() => setDialog(null)}
          onDeleted={onDeleted}
        />
      )}
    </>
  );
}

interface DialogProps {
  projectName: string;
  timeline: EditTimelineSummary;
  onClose: () => void;
}

function RenameDialog({ projectName, timeline, onClose, onRenamed }: DialogProps & { onRenamed: () => void }) {
  const { t } = useTranslation("dashboard");
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(timeline.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = draft.trim();
  const unchanged = name === timeline.name;

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const submit = async () => {
    if (!name || saving) return;
    if (unchanged) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const renamed = await API.renameEditTimeline(projectName, timeline.id, name);
      useAppStore.getState().pushToast(t("edit_view_rename_success", { name: renamed.name }), "success");
      onRenamed();
      onClose();
    } catch (cause) {
      setError(t("edit_view_rename_failed", { message: errMsg(cause) }));
      setSaving(false);
    }
  };

  return (
    <ConfirmDialog
      open
      title={t("edit_view_rename_title")}
      description={
        <div className="flex flex-col gap-2">
          <label htmlFor={inputId} className="text-text-2">
            {t("edit_view_rename_label")}
          </label>
          <input
            id={inputId}
            ref={inputRef}
            value={draft}
            maxLength={NAME_MAX_LENGTH}
            disabled={saving}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
            }}
            className="focus-ring w-full rounded-md border border-hairline bg-bg-grad-a px-3 py-2 text-[13px] text-text"
          />
          <p>{t("edit_view_rename_hint")}</p>
          {error && (
            <p role="alert" className="text-danger-2">
              {error}
            </p>
          )}
        </div>
      }
      confirmLabel={t("edit_view_rename_confirm")}
      loadingLabel={t("edit_view_rename_loading")}
      loading={saving}
      confirmDisabled={!name}
      onConfirm={submit}
      onCancel={onClose}
    />
  );
}

function DeleteDialog({ projectName, timeline, onClose, onDeleted }: DialogProps & { onDeleted: () => void }) {
  const { t } = useTranslation("dashboard");
  const [deleting, setDeleting] = useState(false);

  const confirm = async () => {
    setDeleting(true);
    try {
      await API.deleteEditTimeline(projectName, timeline.id);
      useAppStore.getState().pushToast(t("edit_view_delete_success", { name: timeline.name }), "success");
      onDeleted();
    } catch (cause) {
      useAppStore.getState().pushToast(t("edit_view_delete_failed", { message: errMsg(cause) }), "error");
    } finally {
      onClose();
    }
  };

  return (
    <ConfirmDialog
      open
      tone="danger"
      title={t("edit_view_delete_title", { name: timeline.name })}
      description={t("edit_view_delete_description")}
      confirmLabel={t("edit_view_delete_confirm")}
      loadingLabel={t("edit_view_delete_loading")}
      loading={deleting}
      onConfirm={confirm}
      onCancel={onClose}
    />
  );
}
