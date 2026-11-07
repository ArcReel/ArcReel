import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Trash2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { TooltipIconButton } from "./TooltipIconButton";
import { useItemPositionLabel } from "@/components/shared/episode-item/useEpisodeItem";

type StructureContentMode = "narration" | "drama" | "ad";

/** Add a shot; null `afterId` appends (first entry in empty scripts), resolves success. */
export type InsertShotHandler = (afterId: string | null, novelText?: string) => Promise<boolean>;

interface InsertShotButtonProps {
  afterId: string | null;
  contentMode: StructureContentMode;
  onInsert: InsertShotHandler;
  label: string;
  disabled?: boolean;
  disabledHint?: string;
  /** icon for detail headers, compact for list headers, primary for empty-state actions. */
  variant: "icon" | "compact" | "primary";
}

/**
 * Add-shot button: drama/ads add blanks directly; narration bodies become voiceover, so ask for text before adding.
 */
export function InsertShotButton({
  afterId,
  contentMode,
  onInsert,
  label,
  disabled = false,
  disabledHint,
  variant,
}: InsertShotButtonProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const textareaId = useId();
  const [narrationOpen, setNarrationOpen] = useState(false);
  const [novelText, setNovelText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const run = async (text: string | undefined, close: () => void) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      if (await onInsert(afterId, text)) close();
    } finally {
      setSubmitting(false);
    }
  };

  const handleClick = () => {
    if (contentMode === "narration") {
      setNovelText("");
      setNarrationOpen(true);
      return;
    }
    void run(undefined, () => {});
  };

  const busy = disabled || submitting;
  const trigger =
    variant === "icon" ? (
      <TooltipIconButton label={label} hint={disabledHint} disabled={busy} onClick={handleClick}>
        <Plus aria-hidden />
      </TooltipIconButton>
    ) : (
      <Button
        variant={variant === "primary" ? "default" : "ghost"}
        size={variant === "primary" ? "default" : "xs"}
        disabled={busy}
        onClick={handleClick}
      >
        <Plus aria-hidden data-icon="inline-start" />
        {label}
      </Button>
    );

  return (
    <>
      {trigger}
      {contentMode === "narration" && (
        <Dialog
          open={narrationOpen}
          onOpenChange={(next) => {
            // Do not close during submission; keep the dialog unchanged until results arrive
            if (!next && submitting) return;
            setNarrationOpen(next);
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("shot_insert_narration_title")}</DialogTitle>
              <DialogDescription>{t("shot_insert_narration_desc")}</DialogDescription>
            </DialogHeader>
            <DialogBody>
              <div className="flex flex-col gap-2">
                <Label htmlFor={textareaId}>{t("shot_insert_narration_label")}</Label>
                <Textarea
                  id={textareaId}
                  value={novelText}
                  onChange={(e) => setNovelText(e.target.value)}
                  disabled={submitting}
                />
              </div>
            </DialogBody>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" disabled={submitting} />}>{t("common:cancel")}</DialogClose>
              <Button
                disabled={submitting || !novelText.trim()}
                onClick={() => void run(novelText, () => setNarrationOpen(false))}
              >
                {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                {t("shot_insert_narration_confirm")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

interface ShotStructureActionsProps {
  segmentId: string;
  contentMode: StructureContentMode;
  /** Same disabled conditions as shot switching: dirty/saving/reorder/add/remove requests. */
  disabled: boolean;
  disabledHint?: string;
  /** Removal-prohibited reason, e.g. running generation; supplying it disables removal and explains why. */
  removeBlockedHint?: string;
  onInsert?: InsertShotHandler;
  /** Remove current shot; resolves success. */
  onRemove?: (itemId: string) => Promise<boolean>;
}

/**
 * Shot-detail insert-after/remove actions; irreversible removal confirms first and explains artifact destinations.
 */
export function ShotStructureActions({
  segmentId,
  contentMode,
  disabled,
  disabledHint,
  removeBlockedHint,
  onInsert,
  onRemove,
}: ShotStructureActionsProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const itemLabel = useItemPositionLabel(segmentId);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removing, setRemoving] = useState(false);

  if (!onInsert && !onRemove) return null;

  const handleRemove = async () => {
    // New blockers after confirmation opens, e.g. generation elsewhere, still block; never trust only opening state.
    if (!onRemove || removing || removeBlockedHint !== undefined) return;
    setRemoving(true);
    try {
      if (await onRemove(segmentId)) setRemoveOpen(false);
    } finally {
      setRemoving(false);
    }
  };

  return (
    <>
      {onInsert && (
        <InsertShotButton
          afterId={segmentId}
          contentMode={contentMode}
          onInsert={onInsert}
          label={t("shot_insert_after")}
          disabled={disabled}
          disabledHint={disabledHint}
          variant="icon"
        />
      )}
      {onRemove && (
        <>
          <TooltipIconButton
            label={t("shot_remove")}
            hint={disabledHint ?? removeBlockedHint}
            disabled={disabled || removing || removeBlockedHint !== undefined}
            onClick={() => setRemoveOpen(true)}
          >
            <Trash2 aria-hidden />
          </TooltipIconButton>
          <AlertDialog
            open={removeOpen}
            onOpenChange={(next) => {
              if (!next && removing) return;
              setRemoveOpen(next);
            }}
          >
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("shot_remove_title", { id: itemLabel })}</AlertDialogTitle>
                <AlertDialogDescription>{t("shot_remove_desc")}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={removing}>{t("common:cancel")}</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  disabled={removing || removeBlockedHint !== undefined}
                  onClick={() => void handleRemove()}
                >
                  {removing ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                  {t("shot_remove_confirm")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}
    </>
  );
}
