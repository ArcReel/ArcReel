import { useState } from "react";
import { Scissors } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

export type ExportScope = "current" | "full";

interface ExportScopeDialogProps {
  open: boolean;
  onClose: () => void;
  /** On Export, invoke with the selected scope and immediately close; callers display progress. */
  onSelect: (scope: ExportScope) => void;
  /** Episode ID/name targeted by Open editing view; omission or null shows only the hint. */
  editViewEpisode?: { episode: number; name: string } | null;
  onOpenEditView?: (episode: number) => void;
}

/** Export-project scope selection offers project archives only; final cuts/Jianying drafts export from each episode's editing view. */
export function ExportScopeDialog({
  open,
  onClose,
  onSelect,
  editViewEpisode,
  onOpenEditView,
}: ExportScopeDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [scope, setScope] = useState<ExportScope>("current");

  const options: { value: ExportScope; title: string; hint: string; recommended?: boolean }[] = [
    {
      value: "current",
      title: t("dashboard:current_version_only"),
      hint: t("dashboard:small_size_hint"),
      recommended: true,
    },
    { value: "full", title: t("dashboard:all_data"), hint: t("dashboard:full_history_hint") },
  ];

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      // Reset to the recommended scope on every opening
      onOpenChangeComplete={(next) => {
        if (!next) setScope("current");
      }}
    >
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{t("dashboard:export_scope_title")}</DialogTitle>
          <DialogDescription>{t("dashboard:export_scope_description")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4">
            <RadioGroup
              aria-label={t("dashboard:export_scope_title")}
              value={scope}
              onValueChange={(next) => setScope(next as ExportScope)}
            >
              {options.map((option) => (
                // eslint-disable-next-line jsx-a11y/label-has-associated-control -- Control is a nested RadioGroupItem (Base UI radio), which the rule cannot detect
                <label
                  key={option.value}
                  className="flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors duration-fast hover:bg-muted/50 has-data-checked:border-primary"
                >
                  <RadioGroupItem value={option.value} className="mt-0.5" />
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="flex items-center gap-1.5 text-sm font-medium">
                      {option.title}
                      {option.recommended && <Badge variant="secondary">{t("dashboard:recommended")}</Badge>}
                    </span>
                    <span className="text-xs text-muted-foreground">{option.hint}</span>
                  </span>
                </label>
              ))}
            </RadioGroup>
            <div className="flex items-start gap-2 text-xs text-muted-foreground">
              <Scissors aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              <div className="flex min-w-0 flex-col items-start gap-1">
                <p>{t("dashboard:export_renders_moved_hint")}</p>
                {editViewEpisode && onOpenEditView && (
                  // Episode names can be long; let links wrap instead of using a nonwrapping Button
                  <button
                    type="button"
                    onClick={() => onOpenEditView(editViewEpisode.episode)}
                    className="rounded-sm text-left break-all text-primary underline underline-offset-2 focus-ring"
                  >
                    {t("dashboard:export_open_edit_view", { name: editViewEpisode.name })}
                  </button>
                )}
              </div>
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>{t("common:cancel")}</DialogClose>
          <Button onClick={() => onSelect(scope)}>{t("dashboard:export_scope_confirm")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
