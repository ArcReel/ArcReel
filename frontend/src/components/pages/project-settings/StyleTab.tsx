import { TruncatedText } from "@/components/shared/TruncatedText";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ImageOff } from "lucide-react";

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
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import { STYLE_TEMPLATES } from "@/data/style-templates";

import type { ProjectStyle } from "./project-settings-form";
import { TabHeader } from "./SettingsBlock";

function toPickerValue(style: ProjectStyle): StylePickerValue {
  if (style.kind === "image") {
    return { mode: "custom", templateId: null, activeCategory: "live", uploadedFile: style.file, uploadedPreview: style.preview };
  }
  const templateId = style.kind === "template" ? style.templateId : null;
  const category = STYLE_TEMPLATES.find((tpl) => tpl.id === templateId)?.category ?? "live";
  return { mode: "template", templateId, activeCategory: category, uploadedFile: null, uploadedPreview: null };
}

/** Whether selectors have chosen styles: selected template cards or supplied reference images. */
function draftComplete(draft: StylePickerValue): boolean {
  return draft.mode === "template" ? !!draft.templateId : !!(draft.uploadedFile ?? draft.uploadedPreview);
}

/** Convert selections to project styles, null before selection. Call on adoption only; new references create preview URLs. */
function fromPickerValue(draft: StylePickerValue, current: ProjectStyle): ProjectStyle | null {
  if (draft.mode === "template") {
    return draft.templateId ? { kind: "template", templateId: draft.templateId } : null;
  }
  if (draft.uploadedFile) {
    if (current.kind === "image" && current.file === draft.uploadedFile) return current;
    // Selectors own preview URLs during their mounts only; URLs written into project styles belong to pages, revoked when no longer used.
    // react-doctor-disable-next-line react-doctor/no-create-object-url-without-revoke -- onChange stores URLs in forms; ProjectSettingsPage effects revoke on replacement, discard, save or unmount
    return { kind: "image", preview: URL.createObjectURL(draft.uploadedFile), description: "", file: draft.uploadedFile };
  }
  // Reuse saved reference images.
  return draft.uploadedPreview && current.kind === "image" ? current : null;
}

function CurrentStyle({ value: style }: { value: ProjectStyle }) {
  const { t } = useTranslation(["dashboard", "templates"]);
  const template = style.kind === "template" ? STYLE_TEMPLATES.find((tpl) => tpl.id === style.templateId) : undefined;
  const thumbnail = template?.thumbnail ?? (style.kind === "image" ? style.preview : null);

  let name: string;
  let summary: string;
  if (style.kind === "template") {
    name = t(`templates:name.${style.templateId}`);
    summary = t(`templates:tagline.${style.templateId}`, "");
  } else if (style.kind === "image") {
    name = t("style_custom");
    summary = style.file ? t("style_pending_upload_desc") : style.description;
  } else {
    name = t("style_not_set");
    summary = t("style_not_set_desc");
  }

  return (
    <div className="flex min-w-0 items-center gap-3">
      <div className="grid h-18 w-13.5 shrink-0 place-items-center overflow-hidden rounded-md border border-border bg-muted">
        {thumbnail ? (
          <img src={thumbnail} alt="" className="size-full object-cover" />
        ) : (
          <ImageOff aria-hidden className="size-4 text-muted-foreground" />
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <TruncatedText text={name} className="text-sm font-medium text-foreground" />
        {summary && <p className="line-clamp-2 max-w-[40em] text-sm text-muted-foreground">{summary}</p>}
      </div>
    </div>
  );
}

/** Style shows current styles only; Change opens dialogs, Use this style updates drafts, saved with other edits. */
export function StyleTab({ value: style, onChange }: { value: ProjectStyle; onChange: (next: ProjectStyle) => void }) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<StylePickerValue>(() => toPickerValue(style));

  return (
    <div className="flex flex-col gap-6">
      <TabHeader title={t("project_settings_tab_style")} description={t("project_style_desc")} />

      <div className="flex items-center justify-between gap-4">
        <CurrentStyle value={style} />
        <div className="flex shrink-0 items-center gap-2">
          {style.kind !== "none" && (
            <Button variant="ghost" onClick={() => onChange({ kind: "none" })}>
              {t("style_clear")}
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => {
              setDraft(toPickerValue(style));
              setOpen(true);
            }}
          >
            {t("style_change")}
          </Button>
        </div>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle>{t("style_dialog_title")}</DialogTitle>
            <DialogDescription>{t("style_dialog_desc")}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <StylePicker value={draft} onChange={setDraft} />
          </DialogBody>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>{t("common:cancel")}</DialogClose>
            <Button
              disabled={!draftComplete(draft)}
              onClick={() => {
                const chosen = fromPickerValue(draft, style);
                if (chosen) onChange(chosen);
                setOpen(false);
              }}
            >
              {t("style_dialog_apply")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
